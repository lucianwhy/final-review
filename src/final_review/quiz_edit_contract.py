"""M3-03 quiz edit contract. Validation here has no persistence or model calls."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .schemas import Citation, Evidence, Identifier, Model, QuestionType, Text

Provenance = Literal["source", "synthesis", "ai_supplement"]
_OPTION_PREFIX = re.compile(r"^[A-H][.．、:：]\s*", re.IGNORECASE)
_SEMANTIC_FIELDS = (
    "stem",
    "options",
    "correct_option",
    "boolean_answer",
    "accepted_answers",
    "reference_answer",
    "explanation",
    "provenance",
    "citations",
)


def _option_text(value: str) -> str:
    return _OPTION_PREFIX.sub("", value).strip()


# 判断单道题目修改是否符合要求
class QuizQuestionEdit(Model):
    """One existing question; IDs and question types are checked against the base revision."""

    question_revision_id: Identifier
    question_type: QuestionType
    order: int = Field(ge=1, le=100, strict=True)
    score: float = Field(gt=0, le=10000, allow_inf_nan=False)
    stem: Text
    options: list[Text] = Field(default_factory=list, max_length=8)
    correct_option: int | None = Field(default=None, ge=0, le=7, strict=True)
    boolean_answer: bool | None = Field(default=None, strict=True)
    accepted_answers: list[Text] = Field(default_factory=list, max_length=30)
    # Objective answer text is derived from the structured answer, never accepted separately.
    reference_answer: Text | None = None
    explanation: Text
    provenance: Provenance
    citations: list[Citation] = Field(max_length=30)

    @field_validator("score", mode="before")
    @classmethod
    def reject_boolean_score(cls, value):
        if isinstance(value, bool):
            raise ValueError("分值必须是数字")
        return value

    @model_validator(mode="after")
    def check_structure(self):
        kind = self.question_type
        if kind == "choice":
            options = [_option_text(value) for value in self.options]
            if (
                len(options) < 2
                or any(not value for value in options)
                or len({value.casefold() for value in options}) != len(options)
                or self.correct_option is None
                or self.correct_option >= len(options)
            ):
                raise ValueError("选择题需要不同的非空选项及有效正确选项")
            if self.boolean_answer is not None or self.accepted_answers:
                raise ValueError("选择题不能包含判断或填空答案")
        elif kind == "true_false":
            if self.boolean_answer is None:
                raise ValueError("判断题需要布尔答案")
            if self.options or self.correct_option is not None or self.accepted_answers:
                raise ValueError("判断题不能包含其他题型的选项或答案")
        elif kind == "fill_blank":
            answers = [value.strip() for value in self.accepted_answers]
            if not answers or len({value.casefold() for value in answers}) != len(answers):
                raise ValueError("填空题需要不重复的可接受答案")
            if self.options or self.correct_option is not None or self.boolean_answer is not None:
                raise ValueError("填空题不能包含其他题型的选项或答案")
        elif (
            self.options
            or self.correct_option is not None
            or self.boolean_answer is not None
            or self.accepted_answers
        ):
            raise ValueError("主观题不能包含客观题选项或答案")

        if kind in {"choice", "true_false", "fill_blank"}:
            if self.reference_answer is not None:
                raise ValueError("客观题显示答案由结构化答案生成，不能单独提交")
        elif self.reference_answer is None:
            raise ValueError("主观题需要参考答案")

        chunks = [citation.chunk_id for citation in self.citations]
        if len(chunks) != len(set(chunks)):
            raise ValueError("同一题不能重复引用同一资料片段")
        if self.provenance == "ai_supplement" and chunks:
            raise ValueError("AI 补充题不能附资料引用")
        if self.provenance != "ai_supplement" and not chunks:
            raise ValueError("资料题必须提供来源引用")
        return self


# 判断整张试卷修改是否符合要求
class QuizRevisionEdit(Model):
    base_revision_id: Identifier
    questions: list[QuizQuestionEdit] = Field(min_length=1, max_length=100)


# 校验成功后返回结果
@dataclass(frozen=True)
class QuizEditValidation:
    questions: list[dict]
    total_score: Decimal
    confirmation_issues: list[str]
    semantic_review_question_ids: list[str]


def _display_answer(question: QuizQuestionEdit) -> str:
    if question.question_type == "choice":
        index = question.correct_option
        return f"{chr(65 + index)}. {_option_text(question.options[index])}"
    if question.question_type == "true_false":
        return "正确" if question.boolean_answer else "错误"
    return "；".join(question.accepted_answers)


# 校验整张试卷修改是否符合要求
def validate_quiz_edit(
    edit: QuizRevisionEdit,
    base_questions: Sequence[dict],
    *,
    expected_total_score: float | None = None,
) -> QuizEditValidation:

    base = {question["question_revision_id"]: question for question in base_questions}
    ids = [question.question_revision_id for question in edit.questions]
    if len(base) != len(base_questions) or len(ids) != len(set(ids)) or set(ids) != set(base):
        raise ValueError("编辑必须完整保留本试卷的题目，不能增删或重复")
    if sorted(question.order for question in edit.questions) != list(range(1, len(ids) + 1)):
        raise ValueError("题目顺序必须从 1 连续排列且不能重复")
    stems = ["".join(question.stem.split()).casefold() for question in edit.questions]
    if len(stems) != len(set(stems)):
        raise ValueError("题干不能重复")

    normalized = []
    changed = []
    for question in edit.questions:
        original = base[question.question_revision_id]
        if question.question_type != original["question_type"]:
            raise ValueError("不能通过编辑改变题型")
        value = question.model_dump(mode="json")
        if question.question_type in {"choice", "true_false", "fill_blank"}:
            answer_fields = ("options", "correct_option", "boolean_answer", "accepted_answers")
            answer_changed = any(
                value[field]
                != original.get(field, [] if field in {"options", "accepted_answers"} else None)
                for field in answer_fields
            )
            value["reference_answer"] = (
                _display_answer(question) if answer_changed else original["reference_answer"]
            )
        if any(value[field] != original.get(field) for field in _SEMANTIC_FIELDS):
            changed.append(question.question_revision_id)
        normalized.append(value)

    total = sum((Decimal(str(question.score)) for question in edit.questions), Decimal(0))
    issues = []
    if expected_total_score is not None and total != Decimal(str(expected_total_score)):
        issues.append("题目分值合计与已保存的出题配置总分不一致")
    return QuizEditValidation(
        questions=normalized,
        total_score=total,
        confirmation_issues=issues,
        semantic_review_question_ids=changed,
    )


def validate_quiz_edit_sources(
    edit: QuizRevisionEdit,
    evidence: Mapping[str, Evidence],
    *,
    course_id: str,
    allow_ai_supplement: bool,
) -> None:
    """Check source structure using evidence already authenticated by the caller.

    Evidence presence and verbatim quotes are necessary but do not prove semantic support.
    """
    for question in edit.questions:
        if question.provenance == "ai_supplement":
            if not allow_ai_supplement:
                raise ValueError("本试卷配置不允许 AI 补充题")
            continue
        documents = set()
        for citation in question.citations:
            source = evidence.get(citation.chunk_id)
            if (
                source is None
                or source.course_id != course_id
                or source.source_type == "ai_supplement"
                or citation.quote not in source.content
            ):
                raise ValueError("来源片段不可用或引用摘录与原文不符")
            documents.add(source.document_id)
        if question.provenance == "source" and len(documents) != 1:
            raise ValueError("资料来源题必须引用一份资料")
        if question.provenance == "synthesis" and len(documents) < 2:
            raise ValueError("综合改编题必须引用至少两份资料")
