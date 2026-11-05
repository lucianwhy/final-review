"""Node: generate_explanations — score-oriented explanations."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from final_review_agent.config import get_settings
from final_review_agent.llm import get_llm
from final_review_agent.prompts import EXPLAIN_PROMPT, SYSTEM_PREFIX
from final_review_agent.state import AgentState, ExplanationItem

logger = logging.getLogger(__name__)

OBJECTIVE_TYPES = {"选择题", "判断题", "填空题", "单选题", "多选题"}


def _parse_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[[\s\S]*\]", text)
        if m:
            return json.loads(m.group(0))
        raise


def generate_explanations(state: AgentState) -> dict[str, Any]:
    settings = get_settings().with_overrides(
        skill_url=state.get("skill_url"),
        output_format=state.get("output_format"),
    )
    llm = get_llm(settings)
    system = SYSTEM_PREFIX.format(
        skill_md=state.get("skill_md") or "",
        agent_md=state.get("agent_md") or "",
    )
    questions = state.get("questions") or []
    human = EXPLAIN_PROMPT.format(
        questions=json.dumps(questions, ensure_ascii=False, indent=2),
    )
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
    data = _parse_json(str(resp.content))
    if not isinstance(data, list):
        data = [data] if isinstance(data, dict) else []
    by_id = {item.get("id"): item for item in data if isinstance(item, dict)}

    explanations: list[ExplanationItem] = []
    for q in questions:
        item = by_id.get(q["id"], {})
        is_obj = bool(
            item.get("is_objective")
            if "is_objective" in item
            else q.get("qtype") in OBJECTIVE_TYPES
        )
        explanations.append(
            ExplanationItem(
                id=q["id"],
                answer=item.get("answer", "（待补充）"),
                explanation=item.get("explanation", ""),
                is_objective=is_obj,
            )
        )

    return {
        "explanations": explanations,
        "messages": list(state.get("messages") or [])
        + [f"generate_explanations: {len(explanations)} items"],
    }
