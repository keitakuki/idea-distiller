from __future__ import annotations

import asyncio
import json
import logging
import re
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


def _build_methods_context(tags_data: dict) -> str:
    """Build the methods master list context for prompt injection."""
    methods = tags_data.get("methods", {})
    if not methods:
        return ""

    lines = [
        "### メソッド・マスターリスト（以下から1-2個選択。どうしても当てはまらない場合のみ新規作成可）"
    ]
    for name, definition in methods.items():
        if definition:
            lines.append(f"- \"{name}\" — {definition}")
        else:
            lines.append(f"- \"{name}\"")

    return "\n".join(lines)


def _build_tags_context(tags_data: dict) -> str:
    """Build the existing tags context string for prompt injection."""
    tags = tags_data.get("tags", [])
    if not tags:
        return ""

    lines = [
        "### 既存タグリスト（以下から優先的に選択。なければ新規作成可）"
    ]
    lines.append(", ".join(tags))

    return "\n".join(lines)


def render_prompt(
    template_str: str,
    campaign_data: dict,
    methods_context: str = "",
    tags_context: str = "",
) -> str:
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
    # Replace context placeholders
    result = result.replace("{existing_methods_context}", methods_context)
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
    job_id: str | None = None,
) -> AsyncIterator[tuple[ProcessedCampaign | None, ProcessProgress]]:
    """Process inbox notes from Obsidian vault through LLM.

    Reads vault/inbox/ (status: raw) → calls LLM → writes vault/campaigns/.
    Also reads _tags.yaml for tag consistency and updates it with new tags.

    Args:
        job_id: If provided, only process inbox/{job_id}/ and write to campaigns/{job_id}/.
    """
    provider = provider or create_provider()
    template = load_prompt_template(template_name)
    progress = ProcessProgress()

    # Read existing tags for prompt injection
    tags_data = read_tags_yaml(vault_path)
    methods_context = _build_methods_context(tags_data)
    tags_context = _build_tags_context(tags_data)

    # Read inbox notes
    inbox_notes = read_inbox_notes(vault_path, status="raw", job_id=job_id)

    # Skip already processed (check slug in campaign frontmatter)
    campaigns_dir = vault_path / "campaigns"
    if job_id:
        campaigns_dir = campaigns_dir / job_id
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

        # Content gate: skip notes with no real content (prevents LLM fabrication)
        if "## Description" not in content and "## Case Study" not in content:
            # Strip non-content elements: H1 title, image embeds, section headers
            stripped = re.sub(r"^#\s+.*$", "", content, count=1, flags=re.MULTILINE)
            stripped = re.sub(r"^##\s+.*$", "", stripped, flags=re.MULTILINE)
            stripped = re.sub(r"!\[\[.*?\]\]", "", stripped)
            stripped = re.sub(r"!\[.*?\]\(.*?\)", "", stripped)
            stripped = re.sub(r"\[.*?\]\(.*?\)", "", stripped)
            stripped = stripped.strip()
            if len(stripped) < 100:
                logger.warning(f"Skipping {slug}: no real content (likely failed scrape)")
                progress.failed += 1
                progress.errors.append(f"Empty content: {slug}")
                _revert_to_retry(note["path"])
                yield None, progress
                continue

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
            system_prompt = render_prompt(template["system_prompt"], campaign_data, methods_context, tags_context)
            user_prompt = render_prompt(template["user_prompt"], campaign_data, methods_context, tags_context)

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
            # Fallback: if LLM returns "techniques" instead of "methods", remap
            if "techniques" in parsed and "methods" not in parsed:
                parsed["methods"] = parsed.pop("techniques")
            # Fallback: merge legacy technologies/themes into tags with prefixes
            if "technologies" in parsed:
                tech_tags = [f"tech/{t.lower().replace(' ', '-')}" for t in parsed.pop("technologies")]
                parsed.setdefault("tags", []).extend(tech_tags)
            if "themes" in parsed:
                theme_tags = [f"theme/{t.lower().replace(' ', '-')}" for t in parsed.pop("themes")]
                parsed.setdefault("tags", []).extend(theme_tags)
            # Deduplicate tags
            if "tags" in parsed:
                parsed["tags"] = list(dict.fromkeys(parsed["tags"]))
            # Build method_definitions from _tags.yaml, not from LLM output
            existing_methods = tags_data.get("methods", {})
            parsed["method_definitions"] = {
                m: existing_methods.get(m, "")
                for m in parsed.get("methods", [])
            }
            processed = ProcessedCampaign(
                campaign_id=slug,
                **{k: v for k, v in parsed.items() if k in ProcessedCampaign.model_fields},
            )

            # Write campaign note to vault
            raw_data = dict(meta)
            raw_data["slug"] = slug
            # Extract image filenames from inbox note content (![[filename]])
            if "image_paths" not in raw_data:
                image_embeds = re.findall(r"!\[\[([^\]]+\.(?:webp|png|jpg|jpeg|gif))\]\]", content)
                if image_embeds:
                    raw_data["image_paths"] = image_embeds
            write_campaign_note(raw_data, processed.model_dump(), vault_path, job_id=job_id)

            # Update _tags.yaml with any new tags
            update_tags_yaml(vault_path, {
                "methods": processed.methods,
                "method_definitions": processed.method_definitions,
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


def _revert_to_retry(path: Path) -> None:
    """Revert an inbox note status from raw to retry.

    Used when content is too thin for LLM processing (likely failed scrape).
    """
    try:
        post = frontmatter.load(str(path))
        if post.metadata.get("status") == "raw":
            post.metadata["status"] = "retry"
            path.write_text(frontmatter.dumps(post), encoding="utf-8")
            logger.info(f"Reverted to retry: {path.name}")
    except Exception as e:
        logger.warning(f"Failed to revert {path.name}: {e}")


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
    methods_context = ""
    tags_context = ""
    if settings.obsidian_vault_path:
        tags_data = read_tags_yaml(settings.vault_path)
        methods_context = _build_methods_context(tags_data)
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
            system_prompt = render_prompt(template["system_prompt"], campaign_data, methods_context, tags_context)
            user_prompt = render_prompt(template["user_prompt"], campaign_data, methods_context, tags_context)

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
            # Fallback: if LLM returns "techniques" instead of "methods", remap
            if "techniques" in parsed and "methods" not in parsed:
                parsed["methods"] = parsed.pop("techniques")
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
                    "methods": processed.methods,
                    "method_definitions": processed.method_definitions,
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
        vault = settings.vault_path
        job_id = None
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--job" and i + 1 < len(args):
                job_id = args[i + 1]
                i += 2
            else:
                vault = Path(args[i])
                i += 1

        async def _main_vault():
            async for processed, progress in process_from_vault(vault, job_id=job_id):
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
