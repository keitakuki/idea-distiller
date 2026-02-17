from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import frontmatter
import yaml

from src.config import settings
from src.llm.anthropic_provider import AnthropicProvider
from src.llm.models import ProcessedCampaign
from src.llm.openai_provider import OpenAIProvider
from src.llm.provider import LLMProvider
from src.obsidian.reader import read_inbox_notes, read_tags_yaml, update_tags_yaml
from src.obsidian.writer import write_campaign_note
from src.storage.files import load_json, save_json

logger = logging.getLogger(__name__)


def create_provider() -> LLMProvider:
    """Create the configured LLM provider."""
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.anthropic_model)
    return OpenAIProvider(api_key=settings.openai_api_key, model=settings.openai_model)


def load_prompt_template(name: str) -> dict:
    """Load a prompt template from the prompts directory."""
    path = settings.prompts_dir / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def _build_tags_context(tags_data: dict[str, list[str]]) -> str:
    """Build the existing tags context string for prompt injection."""
    if not any(tags_data.values()):
        return ""

    lines = [
        "## 既存タグリスト（以下のタグを優先的に使用してください。新規タグは本当に必要な場合のみ作成してください）"
    ]
    if tags_data.get("techniques"):
        lines.append(f"既存テクニック: {', '.join(tags_data['techniques'])}")
    if tags_data.get("technologies"):
        lines.append(f"既存テクノロジー: {', '.join(tags_data['technologies'])}")
    if tags_data.get("themes"):
        lines.append(f"既存テーマ: {', '.join(tags_data['themes'])}")
    if tags_data.get("tags"):
        lines.append(f"既存タグ: {', '.join(tags_data['tags'])}")

    return "\n".join(lines)


def render_prompt(template_str: str, campaign_data: dict, tags_context: str = "") -> str:
    """Render a prompt template with campaign data."""
    data = dict(campaign_data)
    # Generate human-readable awards summary from awards list
    awards = data.get("awards", [])
    if awards:
        parts = []
        for a in awards:
            s = a.get("level", "")
            if a.get("category"):
                s += f" in {a['category']}"
            if a.get("subcategory"):
                s += f" / {a['subcategory']}"
            if a.get("festival"):
                s += f" at {a['festival']} {a.get('year', '')}"
            parts.append(s.strip())
        data["awards_summary"] = "; ".join(parts)
    else:
        data["awards_summary"] = "N/A"

    result = template_str
    # Replace tags context placeholder
    result = result.replace("{existing_tags_context}", tags_context)

    for key, value in data.items():
        placeholder = "{" + key + "}"
        result = result.replace(placeholder, str(value or "N/A"))
    return result


@dataclass
class ProcessProgress:
    total: int = 0
    completed: int = 0
    failed: int = 0
    current_file: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def percent(self) -> int:
        if self.total == 0:
            return 0
        return int((self.completed + self.failed) / self.total * 100)


async def process_from_vault(
    vault_path: Path,
    template_name: str = "summarize",
    provider: LLMProvider | None = None,
    db=None,
) -> AsyncIterator[tuple[ProcessedCampaign | None, ProcessProgress]]:
    """Process inbox notes from Obsidian vault through LLM.

    Reads vault/inbox/ (status: raw) → calls LLM → writes vault/campaigns/.
    Also reads _tags.yaml for tag consistency and updates it with new tags.
    """
    provider = provider or create_provider()
    template = load_prompt_template(template_name)
    progress = ProcessProgress()

    # Read existing tags for prompt injection
    tags_data = read_tags_yaml(vault_path)
    tags_context = _build_tags_context(tags_data)

    # Read inbox notes
    inbox_notes = read_inbox_notes(vault_path, status="raw")

    # Skip already processed (check slug in campaign frontmatter)
    campaigns_dir = vault_path / "campaigns"
    already_done = set()
    if campaigns_dir.exists():
        for md_file in campaigns_dir.glob("*.md"):
            try:
                post = frontmatter.load(str(md_file))
                s = post.metadata.get("slug", md_file.stem)
                already_done.add(s)
            except Exception:
                already_done.add(md_file.stem)
    inbox_notes = [n for n in inbox_notes if n["metadata"].get("slug", n["path"].stem) not in already_done]

    progress.total = len(inbox_notes)
    logger.info(f"Processing {progress.total} inbox notes with {provider.provider_name}/{provider.model_name}")

    for note in inbox_notes:
        meta = note["metadata"]
        content = note["content"]
        slug = meta.get("slug", note["path"].stem)
        progress.current_file = slug

        try:
            # Build campaign data dict for prompt rendering
            campaign_data = {
                "title": meta.get("title", slug),
                "brand": meta.get("brand", ""),
                "agency": meta.get("agency", ""),
                "country": meta.get("country", ""),
                "awards": meta.get("awards", []),
                "description": content,  # Full note content as description
                "case_study_text": "",   # Already part of content for inbox notes
            }

            # If note content has structured sections, extract them
            if "## Description" in content and "## Case Study" in content:
                desc_start = content.index("## Description") + len("## Description")
                case_start = content.index("## Case Study")
                campaign_data["description"] = content[desc_start:case_start].strip()
                case_rest = content[case_start + len("## Case Study"):]
                # Find next section or end
                next_section = case_rest.find("\n## ")
                if next_section > 0:
                    campaign_data["case_study_text"] = case_rest[:next_section].strip()
                else:
                    campaign_data["case_study_text"] = case_rest.strip()

            # Render prompts
            system_prompt = render_prompt(template["system_prompt"], campaign_data, tags_context)
            user_prompt = render_prompt(template["user_prompt"], campaign_data, tags_context)

            # Call LLM
            start = time.monotonic()
            response = await provider.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=template.get("max_tokens", 4096),
                temperature=template.get("temperature", 0.3),
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            # Parse LLM response
            resp_content = response.content.strip()
            if "```json" in resp_content:
                resp_content = resp_content.split("```json")[1].split("```")[0].strip()
            elif "```" in resp_content:
                resp_content = resp_content.split("```")[1].split("```")[0].strip()

            parsed = json.loads(resp_content)
            processed = ProcessedCampaign(
                campaign_id=slug,
                **{k: v for k, v in parsed.items() if k in ProcessedCampaign.model_fields},
            )

            # Write campaign note to vault
            raw_data = dict(meta)
            raw_data["slug"] = slug
            # Extract image filenames from inbox note content (![[filename]])
            if "image_paths" not in raw_data:
                import re
                image_embeds = re.findall(r"!\[\[([^\]]+\.(?:webp|png|jpg|jpeg|gif))\]\]", content)
                if image_embeds:
                    raw_data["image_paths"] = image_embeds
            write_campaign_note(raw_data, processed.model_dump(), vault_path)

            # Update _tags.yaml with any new tags
            update_tags_yaml(vault_path, {
                "techniques": processed.techniques,
                "technologies": processed.technologies,
                "themes": processed.themes,
                "tags": processed.tags,
            })

            # Mark inbox note as processed by updating its status
            _update_inbox_status(note["path"], "processed")

            # Log LLM usage
            cost = provider.estimate_cost(response.input_tokens, response.output_tokens)
            if db:
                await db.log_llm_call(
                    campaign_id=slug,
                    template_name=template_name,
                    provider=response.provider,
                    model=response.model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cost_usd=cost,
                    duration_ms=duration_ms,
                )

            progress.completed += 1
            logger.info(
                f"[{progress.completed}/{progress.total}] Processed: {meta.get('title', slug)} "
                f"(${cost:.4f}, {duration_ms}ms)"
            )
            yield processed, progress

        except Exception as e:
            progress.failed += 1
            error_msg = f"Failed to process {slug}: {e}"
            progress.errors.append(error_msg)
            logger.error(error_msg)
            yield None, progress


def _update_inbox_status(note_path: Path, new_status: str) -> None:
    """Update the status field in an inbox note's frontmatter."""
    import frontmatter

    try:
        post = frontmatter.load(str(note_path))
        post.metadata["status"] = new_status
        note_path.write_text(frontmatter.dumps(post), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to update inbox status for {note_path}: {e}")


async def process_campaigns(
    raw_dir: Path,
    output_dir: Path | None = None,
    template_name: str = "summarize",
    provider: LLMProvider | None = None,
    db=None,
) -> AsyncIterator[tuple[ProcessedCampaign | None, ProcessProgress]]:
    """Process all raw campaign JSON files through LLM.

    Legacy mode: reads from data/raw/ JSON files.
    Yields (processed_campaign, progress) tuples.
    """
    provider = provider or create_provider()
    template = load_prompt_template(template_name)
    output_dir = output_dir or settings.processed_dir / raw_dir.name
    progress = ProcessProgress()

    # Read existing tags if vault is configured
    tags_context = ""
    if settings.obsidian_vault_path:
        tags_data = read_tags_yaml(settings.vault_path)
        tags_context = _build_tags_context(tags_data)

    json_files = sorted(raw_dir.glob("*.json"))
    # Skip already processed
    already_done = {p.stem for p in output_dir.glob("*.json")} if output_dir.exists() else set()
    json_files = [f for f in json_files if f.stem not in already_done]
    progress.total = len(json_files)

    logger.info(f"Processing {progress.total} campaigns with {provider.provider_name}/{provider.model_name}")

    for json_file in json_files:
        progress.current_file = json_file.name
        try:
            campaign_data = load_json(json_file)

            # Render prompts
            system_prompt = render_prompt(template["system_prompt"], campaign_data, tags_context)
            user_prompt = render_prompt(template["user_prompt"], campaign_data, tags_context)

            # Call LLM
            start = time.monotonic()
            response = await provider.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=template.get("max_tokens", 4096),
                temperature=template.get("temperature", 0.3),
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            # Parse LLM response
            resp_content = response.content.strip()
            if "```json" in resp_content:
                resp_content = resp_content.split("```json")[1].split("```")[0].strip()
            elif "```" in resp_content:
                resp_content = resp_content.split("```")[1].split("```")[0].strip()

            parsed = json.loads(resp_content)
            processed = ProcessedCampaign(
                campaign_id=json_file.stem,
                **{k: v for k, v in parsed.items() if k in ProcessedCampaign.model_fields},
            )

            # Save processed data (merge with original campaign data)
            output_data = {**campaign_data, **processed.model_dump()}
            save_json(output_dir / json_file.name, output_data)

            # Update tags if vault is configured
            if settings.obsidian_vault_path:
                update_tags_yaml(settings.vault_path, {
                    "techniques": processed.techniques,
                    "themes": processed.themes,
                    "tags": processed.tags,
                })

            # Log LLM usage
            cost = provider.estimate_cost(response.input_tokens, response.output_tokens)
            if db:
                await db.log_llm_call(
                    campaign_id=json_file.stem,
                    template_name=template_name,
                    provider=response.provider,
                    model=response.model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cost_usd=cost,
                    duration_ms=duration_ms,
                )

            progress.completed += 1
            logger.info(
                f"[{progress.completed}/{progress.total}] Processed: {campaign_data.get('title', json_file.stem)} "
                f"(${cost:.4f}, {duration_ms}ms)"
            )
            yield processed, progress

        except Exception as e:
            progress.failed += 1
            error_msg = f"Failed to process {json_file.name}: {e}"
            progress.errors.append(error_msg)
            logger.error(error_msg)
            yield None, progress


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m src.llm.processor --vault <vault_path>   # Process from Obsidian vault")
        print("  python -m src.llm.processor <raw_dir> [output_dir] # Process from raw JSON")
        sys.exit(1)

    if sys.argv[1] == "--vault":
        vault = Path(sys.argv[2]) if len(sys.argv) > 2 else settings.vault_path

        async def _main_vault():
            async for processed, progress in process_from_vault(vault):
                if processed:
                    print(
                        f"  [{progress.completed}/{progress.total}] "
                        f"{processed.campaign_id}: {processed.summary[:80]}"
                    )
                else:
                    print(f"  [FAILED] {progress.errors[-1]}")
            print(f"\nDone: {progress.completed} processed, {progress.failed} failed")

        asyncio.run(_main_vault())
    else:
        raw_path = Path(sys.argv[1])
        out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

        async def _main():
            async for processed, progress in process_campaigns(raw_path, out_path):
                if processed:
                    print(
                        f"  [{progress.completed}/{progress.total}] "
                        f"{processed.campaign_id}: {processed.summary[:80]}"
                    )
                else:
                    print(f"  [FAILED] {progress.errors[-1]}")

        asyncio.run(_main())
