"""Submitted quiz results and question-scoped follow-up with source snapshots."""

from datetime import UTC, datetime

from pydantic import Field, ValidationError

from .domain import DomainConflict, DomainNotFound, _id
from .formal_attempt import _owned_attempt
from .quiz_generation import read_quiz
from .schemas import Identifier, Model, Text


class QuizFollowUpReply(Model):
    answer: Text
    cited_chunk_ids: list[Identifier] = Field(default_factory=list, max_length=10)


def _result(store, user_id, course_id, attempt_id):
    _, attempt = _owned_attempt(store, user_id, course_id, attempt_id)
    if attempt["status"] != "submitted":
        raise DomainConflict("交卷后才能查看逐题结果")
    snapshot = attempt.get("grading_snapshot")
    if not snapshot:
        raise DomainConflict("评分尚未完成，请稍后重试")
    paper = read_quiz(store, user_id, course_id, attempt["asset_id"], attempt["quiz_revision_id"])
    questions = {
        question["question_revision_id"]: question for question in paper["revision"]["questions"]
    }
    items = []
    for grade in snapshot["items"]:
        question = questions.get(grade["question_revision_id"])
        if question is None:
            raise DomainConflict("评分题目与试卷版本不一致")
        items.append(
            {
                "question_revision_id": question["question_revision_id"],
                "order": question["order"],
                "question_type": question["question_type"],
                "stem": question["stem"],
                "options": question.get("options", []),
                "knowledge_point": question["knowledge_point"],
                "explanation": question["explanation"],
                "source_label": question["source_label"],
                "references": question["references"],
                **grade,
            }
        )
    return attempt, {
        "attempt_id": attempt_id,
        "quiz_revision_id": attempt["quiz_revision_id"],
        "total_score": snapshot["total_score"],
        "max_score": snapshot["max_score"],
        "disclaimer": snapshot["disclaimer"],
        "items": sorted(items, key=lambda item: item["order"]),
        "followups": attempt.get("followups", []),
    }


def read_formal_result(store, user_id, course_id, attempt_id):
    return _result(store, user_id, course_id, attempt_id)[1]


def ask_quiz_followup(
    store,
    user_id,
    course_id,
    attempt_id,
    question_revision_id,
    message,
    model,
    model_id,
    model_version,
):
    """Use only the submitted question and its owned stored source snapshots."""
    with store.transaction():
        attempt, result = _result(store, user_id, course_id, attempt_id)
        question = next(
            (
                item
                for item in result["items"]
                if item["question_revision_id"] == question_revision_id
            ),
            None,
        )
        if question is None:
            raise DomainNotFound(question_revision_id)
        sources = [
            {
                "chunk_id": ref["chunk_id"],
                "document_id": ref["document_id"],
                "file_name": ref["file_name"],
                "quote": ref["quote"],
                "source_type": ref["source_type"],
            }
            for ref in question["references"]
        ]
        grade_hash = attempt["grading_snapshot"]["answer_hash"]
        context = {
            "quiz_revision_id": attempt["quiz_revision_id"],
            "question_revision_id": question_revision_id,
            "stem": question["stem"],
            "question_type": question["question_type"],
            "student_answer": question["student_answer"],
            "reference_answer": question["reference_answer"],
            "explanation": question["explanation"],
            "feedback": question["feedback"],
            "missing_points": question["missing_points"],
            "sources": sources,
        }

    try:
        reply = QuizFollowUpReply.model_validate(
            model.follow_up_quiz_question(context, message)
        ).model_dump(mode="json")
    except ValidationError as exc:
        raise DomainConflict("追问回答结构无效") from exc
    allowed = {source["chunk_id"]: source for source in sources}
    if len(reply["cited_chunk_ids"]) != len(set(reply["cited_chunk_ids"])) or not set(
        reply["cited_chunk_ids"]
    ) <= set(allowed):
        raise DomainConflict("追问引用超出本题授权来源")
    now = datetime.now(UTC).isoformat()
    saved = {
        "followup_id": _id("quiz-followup"),
        "question_revision_id": question_revision_id,
        "message": message,
        "answer": reply["answer"],
        "citations": [allowed[chunk_id] for chunk_id in reply["cited_chunk_ids"]],
        "model_id": model_id,
        "model_version": model_version,
        "created_at": now,
    }
    with store.transaction():
        domain, current = _owned_attempt(store, user_id, course_id, attempt_id, lock=True)
        if (
            current["status"] != "submitted"
            or not current.get("grading_snapshot")
            or current["grading_snapshot"]["answer_hash"] != grade_hash
            or current["quiz_revision_id"] != context["quiz_revision_id"]
        ):
            raise DomainConflict("追问期间作答结果已变化，请重新加载")
        current.setdefault("followups", []).append(saved)
        current["updated_at"] = now
        store.put("attempt", attempt_id, current)
        domain._audit(course_id, "quiz.followed_up", "attempt", attempt_id)
    return saved
