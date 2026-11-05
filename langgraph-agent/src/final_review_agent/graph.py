"""Compile the final-review StateGraph."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from final_review_agent.config import Settings, get_settings
from final_review_agent.nodes import (
    clarify_exam,
    clean_markdown,
    convert_markdown,
    extract_knowledge,
    format_output,
    generate_explanations,
    generate_questions,
)
from final_review_agent.skill_loader import load_skills
from final_review_agent.state import AgentState

logger = logging.getLogger(__name__)


def load_skill(state: AgentState) -> dict[str, Any]:
    """Node: load SKILL.md / AGENT.md (local-first, optional remote)."""
    settings = get_settings().with_overrides(
        skill_url=state.get("skill_url"),
        agent_url=state.get("agent_url"),
        output_format=state.get("output_format"),
    )
    # Allow per-run URL override from state
    if state.get("skill_url"):
        settings.skill_url = state["skill_url"]
    if state.get("agent_url"):
        settings.agent_url = state["agent_url"]

    try:
        loaded = load_skills(settings)
        return {
            "skill_md": loaded["skill_md"],
            "agent_md": loaded["agent_md"],
            "skill_url": loaded["skill_url"],
            "agent_url": loaded["agent_url"],
            "messages": list(state.get("messages") or [])
            + [
                f"load_skill: loaded skill={loaded['skill_url']}",
                f"load_skill: loaded agent={loaded['agent_url']}",
            ],
            "meta": {
                **(state.get("meta") or {}),
                "skill_chars": len(loaded["skill_md"]),
                "agent_chars": len(loaded["agent_md"]),
                "mock_llm": settings.use_mock_llm,
            },
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("load_skill failed")
        fallback_skill = (
            "# Skill 加载失败（离线回退）\n"
            "题源优先级：历年真题 > 老师PPT > 平时作业 > 速成课 > AI补充\n"
            "流程：转 Markdown → 清洗 → 提知识点 → 出题 → 解析 → 格式化\n"
        )
        fallback_agent = (
            "## 离线 AGENT 回退\n"
            "解析要得分导向；客观题补核心知识点；主观题补答题核心点/必须出现/常见失分点。\n"
        )
        return {
            "skill_md": fallback_skill,
            "agent_md": fallback_agent,
            "errors": list(state.get("errors") or []) + [f"load_skill: {exc}"],
            "messages": list(state.get("messages") or [])
            + [f"load_skill: FAILED ({exc}); using offline fallback"],
        }


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("load_skill", load_skill)
    g.add_node("clarify_exam", clarify_exam)
    g.add_node("convert_markdown", convert_markdown)
    g.add_node("clean_markdown", clean_markdown)
    g.add_node("extract_knowledge", extract_knowledge)
    g.add_node("generate_questions", generate_questions)
    g.add_node("generate_explanations", generate_explanations)
    g.add_node("format_output", format_output)

    # Pipeline order per skill: clarify can run after convert so it sees material
    g.add_edge(START, "load_skill")
    g.add_edge("load_skill", "convert_markdown")
    g.add_edge("convert_markdown", "clarify_exam")
    g.add_edge("clarify_exam", "clean_markdown")
    g.add_edge("clean_markdown", "extract_knowledge")
    g.add_edge("extract_knowledge", "generate_questions")
    g.add_edge("generate_questions", "generate_explanations")
    g.add_edge("generate_explanations", "format_output")
    g.add_edge("format_output", END)

    return g.compile()


def run_agent(
    *,
    input_path: str | None = None,
    input_text: str | None = None,
    output_format: str = "text",
    skill_url: str | None = None,
    agent_url: str | None = None,
    question_types: list[str] | None = None,
    user_notes: str = "",
    settings: Settings | None = None,
) -> AgentState:
    """Convenience runner used by CLI."""
    settings = settings or get_settings()
    settings = settings.with_overrides(
        skill_url=skill_url,
        agent_url=agent_url,
        output_format=output_format,
    )
    app = build_graph()
    initial: AgentState = {
        "output_format": "html" if settings.output_format == "html" else "text",
        "skill_url": settings.skill_url,
        "agent_url": settings.agent_url,
        "user_notes": user_notes,
        "messages": [],
        "errors": [],
        "meta": {},
    }
    if input_path:
        initial["input_path"] = input_path
    if input_text:
        initial["input_text"] = input_text
    if question_types:
        initial["question_types"] = question_types

    result = app.invoke(initial)
    return result  # type: ignore[return-value]
