"""Deterministic objective grading and audited reference-only subjective grading."""

from datetime import UTC, datetime
from decimal import Decimal
from unicodedata import normalize

from pydantic import Field, ValidationError

from .domain import DomainConflict
from .formal_attempt import _owned_attempt, _public
from .quiz_generation import paper_hash
from .schemas import Identifier, Model, Text
from .storage import stable_key

DISCLAIMER = "仅供学习参考，以教师评分为准"
GRADING_VERSION = "formal-grading-v1"


class CriterionVerdict(Model):
    criterion_id: int = Field(ge=0, le=29, strict=True)
    met: bool = Field(strict=True)
    reason: Text


class SubjectiveVerdict(Model):
    question_revision_id: Identifier
    criteria: list[CriterionVerdict] = Field(min_length=1, max_length=30)
    feedback: Text
    improvement_advice: Text


class SubjectiveReview(Model):
    items: list[SubjectiveVerdict] = Field(min_length=1, max_length=100)


def _text_key(value: str) -> str:
    return " ".join(normalize("NFKC", value).split()).casefold()


def objective_correct(question: dict, answer) -> bool:
    """Compare structured answers without a model or locale-dependent fuzzy matching."""
    kind = question["question_type"]
    if kind == "choice":
        return type(answer) is int and answer == question["correct_option"]
    if kind == "true_false":
        return type(answer) is bool and answer is question["boolean_answer"]
    if kind == "fill_blank":
        return isinstance(answer, str) and _text_key(answer) in {
            _text_key(item) for item in question["accepted_answers"]
        }
    raise ValueError("主观题不能用客观题规则评分")


def _questions(domain, attempt):
    payload = domain._owned("quiz_revision_payload", attempt["quiz_revision_id"])
    if payload.get("course_id") != attempt["course_id"]:
        raise DomainConflict("作答试卷版本不属于此课程")
    questions = []
    for slot in payload["questions"]:
        item = domain._owned("question_revision", slot["question_revision_id"])
        if item.get("quiz_revision_id") != attempt["quiz_revision_id"]:
            raise DomainConflict("题目版本与作答试卷不一致")
        questions.append({**item, **slot})
    return sorted(questions, key=lambda item: item["order"]), payload


def _subjective_input(questions, answers):
    return [
        {
            "question_revision_id": question["question_revision_id"],
            "question_type": question["question_type"],
            "stem": question["stem"],
            "student_answer": answers[question["question_revision_id"]]["answer"],
            "reference_answer": question["reference_answer"],
            "criteria": list(enumerate(question["must_include"])),
            "scoring_tips": question["scoring_tips"],
            "explanation": question["explanation"],
            "max_score": question["score"],
        }
        for question in questions
        if question["question_type"] not in {"choice", "true_false", "fill_blank"}
        and question["question_revision_id"] in answers
    ]


def _subjective_grade(question, verdict):
    criteria = question["must_include"]
    by_id = {item["criterion_id"]: item for item in verdict["criteria"]}
    if len(by_id) != len(criteria) or set(by_id) != set(range(len(criteria))):
        raise DomainConflict("主观题评分遗漏或重复得分点")
    max_score = Decimal(str(question["score"]))
    met = sum(by_id[index]["met"] for index in range(len(criteria)))
    earned = max_score * Decimal(met) / Decimal(len(criteria))
    return {
        "score": float(earned),
        "correct": met == len(criteria),
        "feedback": verdict["feedback"],
        "missing_points": [
            criteria[index] for index in range(len(criteria)) if not by_id[index]["met"]
        ],
        "improvement_advice": verdict["improvement_advice"],
        "rubric": [
            {
                "criterion_id": index,
                "criterion": criterion,
                "met": by_id[index]["met"],
                "reason": by_id[index]["reason"],
                "max_score": float(max_score / Decimal(len(criteria))),
                "score": float(max_score / Decimal(len(criteria))) if by_id[index]["met"] else 0,
            }
            for index, criterion in enumerate(criteria)
        ],
    }


def grade_formal_attempt(store, user_id, course_id, attempt_id, model, model_id, model_version):
    """Freeze one grading snapshot; retry only when no successful snapshot exists."""
    with store.transaction():
        domain, attempt = _owned_attempt(store, user_id, course_id, attempt_id, lock=True)
        if attempt["status"] != "submitted":
            raise DomainConflict("请先交卷，再评分")
        if attempt.get("grading_snapshot"):
            from .mistakes import sync_mistakes_for_attempt

            sync_mistakes_for_attempt(store, user_id, course_id, attempt_id)
            return _public(domain, attempt)
        questions, payload = _questions(domain, attempt)
        answers = attempt["answers"]
        fingerprint = paper_hash(
            {"quiz_revision_id": attempt["quiz_revision_id"], "answers": answers}
        )
        subjective = _subjective_input(questions, answers)

    verdicts = {}
    if subjective:
        if model is None:
            raise DomainConflict("主观题评分模型不可用，请稍后重试")
        try:
            review = SubjectiveReview.model_validate(
                model.grade_formal_subjective(
                    {"quiz_revision_id": attempt["quiz_revision_id"], "questions": subjective}
                )
            ).model_dump(mode="json")
        except ValidationError as exc:
            raise DomainConflict("主观题评分结果结构无效") from exc
        ids = [item["question_revision_id"] for item in review["items"]]
        expected = {item["question_revision_id"] for item in subjective}
        if len(ids) != len(expected) or set(ids) != expected:
            raise DomainConflict("主观题评分遗漏或重复题目")
        verdicts = {item["question_revision_id"]: item for item in review["items"]}

    grades = []
    for question in questions:
        question_id = question["question_revision_id"]
        answer = answers.get(question_id, {}).get("answer")
        maximum = question["score"]
        if question["question_type"] in {"choice", "true_false", "fill_blank"}:
            correct = objective_correct(question, answer)
            grade = {
                "score": maximum if correct else 0,
                "correct": correct,
                "feedback": "答案正确" if correct else "答案不正确或未作答",
                "missing_points": [] if correct else [question["reference_answer"]],
                "improvement_advice": "" if correct else "对照参考答案和解析复习。",
                "rubric": [],
            }
        elif question_id in verdicts:
            grade = _subjective_grade(question, verdicts[question_id])
        else:
            grade = {
                "score": 0,
                "correct": False,
                "feedback": "未作答",
                "missing_points": question["must_include"],
                "improvement_advice": "先尝试按得分点完成文字作答。",
                "rubric": [],
            }
        grades.append(
            {
                "question_revision_id": question_id,
                "question_id": question["question_id"],
                "question_type": question["question_type"],
                "max_score": maximum,
                "student_answer": answer,
                "reference_answer": question["reference_answer"],
                **grade,
            }
        )
    total = sum((Decimal(str(item["score"])) for item in grades), Decimal(0))
    snapshot = {
        "version": GRADING_VERSION,
        "quiz_revision_id": attempt["quiz_revision_id"],
        "answer_hash": fingerprint,
        "model_id": model_id if subjective else None,
        "model_version": model_version if subjective else None,
        "graded_at": datetime.now(UTC).isoformat(),
        "disclaimer": DISCLAIMER
        if any(q["question_type"] not in {"choice", "true_false", "fill_blank"} for q in questions)
        else None,
        "total_score": float(total),
        "max_score": payload["total_score"],
        "items": grades,
    }
    with store.transaction():
        domain, current = _owned_attempt(store, user_id, course_id, attempt_id, lock=True)
        if current.get("grading_snapshot"):
            from .mistakes import sync_mistakes_for_attempt

            sync_mistakes_for_attempt(store, user_id, course_id, attempt_id)
            return _public(domain, current)
        if (
            current["status"] != "submitted"
            or paper_hash(
                {"quiz_revision_id": current["quiz_revision_id"], "answers": current["answers"]}
            )
            != fingerprint
        ):
            raise DomainConflict("评分期间作答内容发生变化，请重试")
        current.update(
            grading_snapshot=snapshot,
            model_version=model_version if subjective else None,
            grading_disclaimer_version=GRADING_VERSION if snapshot["disclaimer"] else None,
            graded_at=snapshot["graded_at"],
            updated_at=snapshot["graded_at"],
        )
        store.put("attempt", attempt_id, current)
        for grade in grades:
            grade_key = (
                "question-grade-" + stable_key(attempt_id, grade["question_revision_id"])[:32]
            )
            store.put(
                "question_grade",
                grade_key,
                {
                    "attempt_id": attempt_id,
                    "question_revision_id": grade["question_revision_id"],
                    "user_id": user_id,
                    "course_id": course_id,
                    "created_at": snapshot["graded_at"],
                    "grading_version": GRADING_VERSION,
                    "model_id": snapshot["model_id"],
                    "model_version": snapshot["model_version"],
                    "disclaimer": snapshot["disclaimer"],
                    **grade,
                },
            )
        domain._audit(course_id, "quiz.attempt_graded", "attempt", attempt_id)
        from .mistakes import sync_mistakes_for_attempt

        sync_mistakes_for_attempt(store, user_id, course_id, attempt_id)
        return _public(domain, current)
