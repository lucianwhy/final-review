"""LangGraph shared state."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


class KnowledgePoint(TypedDict):
    name: str
    summary: str
    source_priority: str  # 历年真题 / 老师PPT / ...
    exam_styles: list[str]
    density: str  # high | medium | low


class QuestionItem(TypedDict):
    id: str
    kp: str
    qtype: str
    stem: str
    options: NotRequired[list[str]]
    source_tag: str


class ExplanationItem(TypedDict):
    id: str
    answer: str
    explanation: str
    is_objective: bool


class AgentState(TypedDict, total=False):
    # inputs
    input_path: str
    input_text: str
    output_format: Literal["text", "html"]
    skill_url: str
    agent_url: str
    exam_formats: list[str]
    question_types: list[str]
    needs_clarify: bool
    user_notes: str

    # loaded skill
    skill_md: str
    agent_md: str

    # pipeline artifacts
    raw_markdown: str
    cleaned_markdown: str
    knowledge_points: list[KnowledgePoint]
    questions: list[QuestionItem]
    explanations: list[ExplanationItem]

    # output
    final_output: str
    messages: list[str]
    errors: list[str]
    meta: dict[str, Any]
