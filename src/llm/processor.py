from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import yaml

from src.config import settings
from src.llm.anthropic_provider import AnthropicProvider
from src.llm.models import ProcessedCampaign
from src.llm.openai_provider import OpenAIProvider
from src.llm.provider import LLMProvider
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


def render_prompt(template_str: str, campaign_data: dict) -> str:
    """Render a prompt template with campaign data."""
    # Use safe format that ignores missing keys and preserves JSON braces
    result = template_str
    for key, value in campaign_data.items():
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


async def process_campaigns(
    raw_dir: Path,
    output_dir: Path | None = None,
    template_name: str = "summarize",
    provider: LLMProvider | None = None,
    db=None,
) -> AsyncIterator[tuple[ProcessedCampaign | None, ProcessProgress]]:
    """Process all raw campaign JSON files through LLM.

    Yields (processed_campaign, progress) tuples.
    """
    provider = provider or create_provider()
    template = load_prompt_template(template_name)
    output_dir = output_dir or settings.processed_dir / raw_dir.name
    progress = ProcessProgress()

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
            system_prompt = template["system_prompt"]
            user_prompt = render_prompt(template["user_prompt"], campaign_data)

            # Call LLM
            start = time.monotonic()
            response = await provider.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=template.get("max_tokens", 2048),
                temperature=template.get("temperature", 0.3),
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            # Parse LLM response
            content = response.content.strip()
            # Extract JSON from markdown code block if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            parsed = json.loads(content)
            processed = ProcessedCampaign(
                campaign_id=json_file.stem,
                **{k: v for k, v in parsed.items() if k in ProcessedCampaign.model_fields},
            )

            # Save processed data (merge with original campaign data)
            output_data = {**campaign_data, **processed.model_dump()}
            save_json(output_dir / json_file.name, output_data)

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
        print("Usage: python -m src.llm.processor <raw_dir> [output_dir]")
        sys.exit(1)

    raw_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    async def _main():
        async for processed, progress in process_campaigns(raw_path, out_path):
            if processed:
                print(f"  [{progress.completed}/{progress.total}] {processed.campaign_id}: {processed.summary[:80]}")
            else:
                print(f"  [FAILED] {progress.errors[-1]}")

    asyncio.run(_main())
