"""Node: clean_markdown — remove noise/dupes; keep exam-relevant content."""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from final_review_agent.config import get_settings
from final_review_agent.llm import get_llm
from final_review_agent.prompts import CLEAN_PROMPT, SYSTEM_PREFIX
from final_review_agent.state import AgentState

logger = logging.getLogger(__name__)


def _heuristic_clean(raw: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        # drop obvious noise
        if re.match(r"^[\-=_]{4,}$", stripped):
            continue
        if stripped in seen and (
            stripped.startswith("#") or len(stripped) < 80
        ):
            continue
        seen.add(stripped)
        lines.append(line.rstrip())
    return "\n".join(lines).strip() + "\n"


def clean_markdown(state: AgentState) -> dict[str, Any]:
    settings = get_settings().with_overrides(
        skill_url=state.get("skill_url"),
        output_format=state.get("output_format"),
    )
    raw = state.get("raw_markdown") or ""
    llm = get_llm(settings)
    system = SYSTEM_PREFIX.format(
        skill_md=state.get("skill_md") or "",
        agent_md=state.get("agent_md") or "",
    )
    human = CLEAN_PROMPT.format(raw_markdown=raw[:20000])
    try:
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
        cleaned = str(resp.content).strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:markdown|md)?\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
        if len(cleaned) < 20:
            cleaned = _heuristic_clean(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("clean LLM failed, heuristic: %s", exc)
        cleaned = _heuristic_clean(raw)

    return {
        "cleaned_markdown": cleaned,
        "messages": list(state.get("messages") or [])
        + [f"clean_markdown: {len(raw)} -> {len(cleaned)} chars"],
    }
