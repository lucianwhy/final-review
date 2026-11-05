"""Node: clarify_exam — infer exam formats / question types."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from final_review_agent.config import get_settings
from final_review_agent.llm import get_llm
from final_review_agent.prompts import CLARIFY_PROMPT, SYSTEM_PREFIX
from final_review_agent.state import AgentState

logger = logging.getLogger(__name__)


def _parse_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
        if m:
            return json.loads(m.group(0))
        raise


def clarify_exam(state: AgentState) -> dict[str, Any]:
    settings = get_settings().with_overrides(
        skill_url=state.get("skill_url"),
        output_format=state.get("output_format"),
    )
    # If user already provided types, skip LLM
    if state.get("question_types"):
        return {
            "needs_clarify": False,
            "messages": list(state.get("messages") or [])
            + ["clarify_exam: skipped (question_types already set)"],
        }

    llm = get_llm(settings)
    preview = (state.get("raw_markdown") or state.get("input_text") or "")[:2000]
    system = SYSTEM_PREFIX.format(
        skill_md=state.get("skill_md") or "",
        agent_md=state.get("agent_md") or "",
    )
    human = CLARIFY_PROMPT.format(
        user_notes=state.get("user_notes") or "",
        markdown_preview=preview,
    )
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
    data = _parse_json(str(resp.content))
    return {
        "exam_formats": data.get("exam_formats") or ["闭卷笔试"],
        "question_types": data.get("question_types")
        or ["选择题", "判断题", "简答题"],
        "needs_clarify": bool(data.get("needs_clarify", False)),
        "user_notes": data.get("notes") or state.get("user_notes") or "",
        "messages": list(state.get("messages") or [])
        + [f"clarify_exam: types={data.get('question_types')}"],
    }
