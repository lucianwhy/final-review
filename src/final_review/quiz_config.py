"""M3-01: shared, owner-checked resolution of partial formal quiz configuration."""

from typing import Literal

from pydantic import ValidationError

from .course_chat import CourseMaterials
from .domain import DomainConflict, DomainNotFound, DomainService
from .schemas import Model, QuizInput, ResolvedQuizConfig
from .source_locators import material_version


class QuizConfigResult(Model):
    status: Literal["ready", "needs_clarification"]
    quiz_input: dict
    missing: list[str]
    conflicts: list[str]
    prompt: dict | None = None
    config: ResolvedQuizConfig | None = None


LABELS = {
    "scope_mode": "范围（全课程、章节或知识点）",
    "chapter": "章节",
    "knowledge_points": "知识点",
    "blueprint": "题型及每种题型的数量（总计最多 100 题）",
    "duration_mode": "限时或不限时",
    "duration_minutes": "限时分钟数",
    "difficulty": "难度（基础、标准、进阶或混合）",
    "include_imported_questions": "是否纳入导入题（当前仅支持不纳入）",
    "source_document_ids": "资料选择（空列表明确表示当前范围内全部资料）",
    "allow_ai_supplement": "是否允许 AI 补充题",
}


def merge_quiz_input(previous: dict, additions: QuizInput) -> QuizInput:
    return QuizInput.model_validate(
        {**previous, **additions.model_dump(mode="json", exclude_unset=True)}
    )


def resolve_quiz_config(
    store, user_id: str, course_id: str, quiz_input: QuizInput, *, scope: dict | None = None
) -> QuizConfigResult:
    """Explicit input wins over exam defaults; a conversation restriction is always a ceiling."""
    domain = DomainService(store, user_id)
    domain.course(course_id, writable=True)
    scope = scope or {}
    explicit = quiz_input.model_dump(mode="json", exclude_unset=True)
    defaults = {}
    exam = None
    if quiz_input.exam_id:
        exam = domain.exam(quiz_input.exam_id)
        if exam["course_id"] != course_id:
            raise DomainNotFound(quiz_input.exam_id)
        if exam.get("status") != "active":
            raise DomainConflict("归档考试不能用于新试卷配置")
        preferences = exam.get("generation_preferences", {})
        # Exam defaults cannot silently choose files or permit AI supplementation.
        defaults = {
            key: value
            for key, value in preferences.items()
            if key in {"duration_mode", "duration_minutes", "difficulty"}
        }
        if exam.get("blueprint"):
            defaults["blueprint"] = [
                {"question_type": item["question_type"], "question_count": item["question_count"]}
                for item in exam["blueprint"]
            ]
        for name in ("emphasis", "exclusions", "total_score"):
            if exam.get(name) is not None:
                defaults["excluded_topics" if name == "exclusions" else name] = exam[name]
    if scope.get("chapter"):
        defaults.update(scope_mode="chapter", chapter=scope["chapter"])
    if scope.get("source_document_ids") is not None:
        defaults["source_document_ids"] = scope["source_document_ids"]
    values = {**defaults, **explicit}
    if explicit.get("duration_mode") == "untimed" and "duration_minutes" not in explicit:
        values["duration_minutes"] = None
    if values.get("scope_mode") is None:
        if values.get("chapter"):
            values["scope_mode"] = "chapter"
        elif values.get("knowledge_points"):
            values["scope_mode"] = "knowledge_points"
    if values.get("duration_mode") is None and values.get("duration_minutes") is not None:
        values["duration_mode"] = "timed"
    try:
        partial = QuizInput.model_validate(values)
    except ValidationError as exc:
        # Exam schemas intentionally allow incomplete/broader blueprints and free-form preferences.
        # Bad inherited defaults are a configuration question, not a model/provider failure.
        fields = sorted(
            {str(error["loc"][0]) if error["loc"] else "blueprint" for error in exc.errors()}
        )
        message = "考试预填配置无效，请明确：" + "、".join(fields) + "（本次总计最多 100 题）"
        return QuizConfigResult(
            status="needs_clarification",
            quiz_input=explicit,
            missing=fields,
            conflicts=[message],
            prompt={"message": message, "required": fields, "conflicts": [message]},
        )
    data = partial.model_dump(mode="json")
    missing = [
        name
        for name in (
            "scope_mode",
            "blueprint",
            "duration_mode",
            "difficulty",
            "include_imported_questions",
            "source_document_ids",
            "allow_ai_supplement",
        )
        if data[name] is None
    ]
    if partial.scope_mode == "chapter" and not partial.chapter:
        missing.append("chapter")
    if partial.scope_mode == "knowledge_points" and not partial.knowledge_points:
        missing.append("knowledge_points")
    if partial.duration_mode == "timed" and partial.duration_minutes is None:
        missing.append("duration_minutes")
    conflicts = []
    if partial.duration_mode == "untimed" and partial.duration_minutes is not None:
        conflicts.append("选择不限时后，请清空限时分钟数")
    if set(partial.emphasis) & set(partial.excluded_topics):
        conflicts.append("重点与排除内容冲突，请明确取舍")
    if set(partial.knowledge_points) & set(partial.excluded_topics):
        conflicts.append("所选知识点与排除内容冲突")
    if partial.scope_mode == "course" and (partial.chapter or partial.knowledge_points):
        conflicts.append("全课程范围不能同时限定章节或知识点，请明确范围")
    if (
        partial.scope_mode == "knowledge_points"
        and partial.blueprint
        and (
            sum(item.question_count for item in partial.blueprint)
            > len(partial.knowledge_points) * 6
        )
    ):
        conflicts.append("每个知识点最多 6 题，请增加知识点或减少总题量")
    if partial.include_imported_questions:
        conflicts.append("当前尚不支持纳入独立导入题，请选择不纳入")
    if "ai_supplement" in partial.source_types:
        conflicts.append("AI 补充不是资料类型，请使用 allow_ai_supplement 开关")

    materials = CourseMaterials(store, course_id, user_id)
    # Check IDs strictly before the convenience selector (which also supports legacy metadata).
    for document_id in partial.source_document_ids or []:
        document = store.get("document", document_id)
        if (
            not document
            or document.get("user_id") != user_id
            or document.get("course_id") != course_id
        ):
            raise DomainNotFound(document_id)
    allowed = materials.select(scope.get("source_document_ids"), scope.get("chapter", ""))
    allowed_ids = {item["document_id"] for item in allowed}
    documents = []
    if partial.source_document_ids is not None:
        requested = set(partial.source_document_ids) or allowed_ids
        if not requested <= allowed_ids:
            raise DomainConflict("所选资料未就绪或超出当前对话的资料/章节范围")
        if scope.get("chapter") and (
            partial.scope_mode not in {"chapter", "knowledge_points"}
            or partial.chapter != scope["chapter"]
        ):
            conflicts.append("当前对话限定了章节，不能扩大或更换章节范围")
        documents = materials.select(sorted(requested), partial.chapter)
        if partial.source_types:
            documents = [d for d in documents if d["source_type"] in partial.source_types]
        documents = [d for d in documents if d.get("user_id") == user_id]
        if not documents:
            conflicts.append("当前范围与来源约束下没有可用资料，请补充资料或调整选择")
        elif len(documents) > 100:
            conflicts.append("本次最多选择 100 份资料，请缩小选择范围")
    if missing or conflicts:
        questions = [LABELS[name] for name in missing] + conflicts
        return QuizConfigResult(
            status="needs_clarification",
            quiz_input=values,
            missing=missing,
            conflicts=conflicts,
            prompt={
                "message": "请补充或调整：" + "；".join(questions),
                "required": missing,
                "conflicts": conflicts,
            },
        )
    resolved = ResolvedQuizConfig.model_validate(
        {
            **data,
            "course_id": course_id,
            "user_id": user_id,
            "question_count": sum(item.question_count for item in partial.blueprint),
            "source_document_ids": [d["document_id"] for d in documents],
            "material_versions": {
                d["document_id"]: material_version(d)["material_version_id"] for d in documents
            },
            "exam_updated_at": exam.get("updated_at") if exam else None,
        }
    )
    return QuizConfigResult(
        status="ready", quiz_input=values, missing=[], conflicts=[], config=resolved
    )
