"""Node: generate_questions — 1–6 per KP, exam styles, source tags."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from final_review_agent.config import get_settings
from final_review_agent.llm import get_llm
from final_review_agent.prompts import GENERATE_QUESTIONS_PROMPT, SYSTEM_PREFIX
from final_review_agent.state import AgentState, QuestionItem

logger = logging.getLogger(__name__)


def _parse_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[[\s\S]*\]", text)
        if m:
            return json.loads(m.group(0))
        raise


def generate_questions(state: AgentState) -> dict[str, Any]:
    settings = get_settings().with_overrides(
        skill_url=state.get("skill_url"),
        output_format=state.get("output_format"),
    )
    llm = get_llm(settings)
    system = SYSTEM_PREFIX.format(
        skill_md=state.get("skill_md") or "",
        agent_md=state.get("agent_md") or "",
    )
    kps = state.get("knowledge_points") or []
    human = GENERATE_QUESTIONS_PROMPT.format(
        question_types=", ".join(state.get("question_types") or []),
        knowledge_points=json.dumps(kps, ensure_ascii=False, indent=2),
        cleaned_markdown=(state.get("cleaned_markdown") or "")[:12000],
    )
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
    data = _parse_json(str(resp.content))
    if not isinstance(data, list):
        data = [data] if isinstance(data, dict) else []
    questions: list[QuestionItem] = []
    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        qid = item.get("id") or f"Q{i}"
        q: QuestionItem = {
            "id": qid,
            "kp": item.get("kp", ""),
            "qtype": item.get("qtype", "简答题"),
            "stem": item.get("stem", ""),
            "source_tag": item.get("source_tag", "题源：AI补充"),
        }
        if item.get("options"):
            q["options"] = list(item["options"])
        questions.append(q)

    return {
        "questions": questions,
        "messages": list(state.get("messages") or [])
        + [f"generate_questions: {len(questions)} items"],
    }
