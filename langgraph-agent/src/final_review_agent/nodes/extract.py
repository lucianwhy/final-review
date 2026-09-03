"""Node: extract_knowledge — priority-aware knowledge points."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from final_review_agent.config import get_settings
from final_review_agent.llm import get_llm
from final_review_agent.prompts import EXTRACT_PROMPT, SYSTEM_PREFIX
from final_review_agent.state import AgentState, KnowledgePoint

logger = logging.getLogger(__name__)

PRIORITY_ORDER = ["历年真题", "老师PPT", "平时作业", "速成课", "AI补充"]


def _parse_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[[\s\S]*\]", text)
        if m:
            return json.loads(m.group(0))
        raise


def _sort_kps(kps: list[KnowledgePoint]) -> list[KnowledgePoint]:
    def key(kp: KnowledgePoint) -> tuple[int, str]:
        src = kp.get("source_priority", "AI补充")
        try:
            idx = next(i for i, p in enumerate(PRIORITY_ORDER) if p in src)
        except StopIteration:
            idx = len(PRIORITY_ORDER)
        density_rank = {"high": 0, "medium": 1, "low": 2}.get(kp.get("density", "medium"), 1)
        return (idx, str(density_rank), kp.get("name", ""))

    return sorted(kps, key=key)


def extract_knowledge(state: AgentState) -> dict[str, Any]:
    settings = get_settings().with_overrides(
        skill_url=state.get("skill_url"),
        output_format=state.get("output_format"),
    )
    llm = get_llm(settings)
    system = SYSTEM_PREFIX.format(
        skill_md=state.get("skill_md") or "",
        agent_md=state.get("agent_md") or "",
    )
    qtypes = state.get("question_types") or ["选择题", "简答题"]
    human = EXTRACT_PROMPT.format(
        question_types=", ".join(qtypes),
        cleaned_markdown=(state.get("cleaned_markdown") or "")[:18000],
    )
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
    data = _parse_json(str(resp.content))
    if not isinstance(data, list):
        data = [data] if isinstance(data, dict) else []
    kps: list[KnowledgePoint] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        kps.append(
            KnowledgePoint(
                name=item.get("name", "未命名"),
                summary=item.get("summary", ""),
                source_priority=item.get("source_priority", "AI补充"),
                exam_styles=list(item.get("exam_styles") or qtypes[:2]),
                density=item.get("density", "medium"),
            )
        )
    kps = _sort_kps(kps)
    return {
        "knowledge_points": kps,
        "messages": list(state.get("messages") or [])
        + [f"extract_knowledge: {len(kps)} KPs"],
    }
