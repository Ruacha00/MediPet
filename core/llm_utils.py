"""LLM request and response helpers for the Anthropic-compatible protocol."""
import os
from typing import Any, Iterable, List


def llm_request_options() -> dict[str, Any]:
    """Explicitly disable thinking when configured; unset preserves SDK defaults."""
    thinking = os.getenv("MEDIPET_THINKING", "").strip().lower()
    if not thinking:
        return {}
    if thinking != "disabled":
        raise ValueError("MEDIPET_THINKING must be disabled or empty")
    return {"extra_body": {"thinking": {"type": "disabled"}}}


def extract_text_content(content: Iterable[Any]) -> str:
    """Return text blocks from Anthropic-style response content."""
    texts: List[str] = []
    for block in content or []:
        if isinstance(block, str):
            texts.append(block)
            continue

        block_type = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if isinstance(block, dict):
            block_type = block.get("type", block_type)
            text = block.get("text", text)

        if isinstance(text, str) and (block_type in (None, "text")):
            texts.append(text)

    return "\n".join(t for t in texts if t)
