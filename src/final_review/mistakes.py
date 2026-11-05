"""Version-bound mistakes, manual mastery events and single-question practice."""

from datetime import UTC, datetime

from .domain import DomainConflict, DomainNotFound, DomainService, _id
from .formal_attempt import _owned_attempt, _validated_answer
from .formal_grading import objective_correct
from .quiz_edit import _locked
from .storage import stable_key


def _now():
    return datetime.now(UTC).isoformat()


def _owned_mistake(store, user_id, course_id, mistake_id, *, lock=False):
    domain = DomainService(store, user_id)
    domain.course(course_id)
    mistake = _locked(store, "mistake", mistake_id) if lock else store.get("mistake", mistake_id)
    if not mistake or mistake.get("user_id") != user_id or mistake.get("course_id") != course_id:
        raise DomainNotFound(mistake_id)
    return domain, mistake


def sync_mistakes_for_attempt(store, user_id, course_id, attempt_id):
    """Idempotently project a frozen grading snapshot into independent records."""
    with store.transaction():
        domain, attempt = _owned_attempt(store, user_id, course_id, attempt_id, lock=True)
        snapshot = attempt.get("grading_snapshot")
        if attempt["status"] != "submitted" or not snapshot:
            raise DomainConflict("只能从已完成评分的正式作答生成错题")
        revision_id = attempt["quiz_revision_id"]
        payload = domain._owned("quiz_revision_payload", revision_id)
        if payload.get("course_id") != course_id or payload.get("asset_id") != attempt["asset_id"]:
            raise DomainConflict("试卷版本与作答不一致")
        slots = {item["question_revision_id"]: item for item in payload["questions"]}
        exam_id = payload.get("exam_id")
        exam = store.get("exam", exam_id) if exam_id else None
        created = []
        for grade in snapshot["items"]:
            if grade["correct"]:
                continue
            question_id = grade["question_revision_id"]
            slot = slots.get(question_id)
            question = domain._owned("question_revision", question_id)
            if (
                not slot
                or question.get("quiz_revision_id") != revision_id
                or question.get("course_id") != course_id
            ):
                raise DomainConflict("评分题目与作答版本不一致")
            mistake_id = "mistake-" + stable_key(attempt_id, question_id)[:32]
            if store.get("mistake", mistake_id):
                continue
            now = _now()
            question_snapshot = {**question, **slot}
            reason = grade.get("feedback") or "答案不正确或未作答"
            if grade.get("missing_points"):
                reason += "；缺失点：" + "、".join(grade["missing_points"])
            item = {
                "mistake_id": mistake_id,
                "user_id": user_id,
                "course_id": course_id,
                "attempt_id": attempt_id,
                "asset_id": attempt["asset_id"],
                "quiz_revision_id": revision_id,
                "question_revision_id": question_id,
                "exam_id": exam_id,
                "exam_name": exam.get("name") if exam else None,
                "quiz_title": payload["title"],
                "chapter": payload.get("config", {}).get("chapter")
                or question.get("chapter")
                or "",
                "knowledge_point": question["knowledge_point"],
                "question_type": question["question_type"],
                "question": question_snapshot,
                "grade": grade,
                "reason": reason,
                "mastery_status": "unmastered",
                "mastery_version": 0,
                "graded_at": snapshot["graded_at"],
                "created_at": now,
                "updated_at": now,
            }
            store.put("mistake", mistake_id, item)
            observation_id = "mastery-" + stable_key(mistake_id, "initial")[:32]
            store.put(
                "mastery_observation",
                observation_id,
                {
                    "observation_id": observation_id,
                    "user_id": user_id,
                    "course_id": course_id,
                    "mistake_id": mistake_id,
                    "status": "unmastered",
                    "source": "grading",
                    "created_at": now,
                },
            )
            created.append(item)
        if created:
            domain._audit(course_id, "mistakes.generated", "attempt", attempt_id)
        return created


def list_mistakes(
    store,
    user_id,
    *,
    course_id=None,
    exam_id=None,
    chapter=None,
    knowledge_point=None,
    question_type=None,
    mastery_status=None,
):
    if course_id:
        DomainService(store, user_id).course(course_id)
    filters = {"course_id": course_id} if course_id else {}
    items = [item for item in store.scan("mistake", filters) if item.get("user_id") == user_id]
    values = {
        "exam_id": exam_id,
        "chapter": chapter,
        "knowledge_point": knowledge_point,
        "question_type": question_type,
        "mastery_status": mastery_status,
    }
    items = [
        item
        for item in items
        if all(value is None or item.get(key) == value for key, value in values.items())
    ]
    return {
        "items": sorted(
            items, key=lambda item: (item["created_at"], item["mistake_id"]), reverse=True
        )
    }


def read_mistake(store, user_id, course_id, mistake_id):
    return _owned_mistake(store, user_id, course_id, mistake_id)[1]


def mark_mastery(store, user_id, course_id, mistake_id, status):
    if status not in {"mastered", "unmastered"}:
        raise ValueError("掌握状态无效")
    with store.transaction():
        domain, item = _owned_mistake(store, user_id, course_id, mistake_id, lock=True)
        if item["mastery_status"] == status:
            return item
        now = _now()
        item.update(
            mastery_status=status, mastery_version=item["mastery_version"] + 1, updated_at=now
        )
        store.put("mistake", mistake_id, item)
        observation_id = _id("mastery")
        store.put(
            "mastery_observation",
            observation_id,
            {
                "observation_id": observation_id,
                "user_id": user_id,
                "course_id": course_id,
                "mistake_id": mistake_id,
                "status": status,
                "source": "manual",
                "created_at": now,
            },
        )
        domain._audit(course_id, "mistake.mastery_marked", "mistake", mistake_id)
        return item


def start_practice(store, user_id, course_id, mistake_id):
    with store.transaction():
        domain, mistake = _owned_mistake(store, user_id, course_id, mistake_id)
        domain.course(course_id, writable=True)
        now = _now()
        practice_id = _id("mistake-practice")
        item = {
            "practice_id": practice_id,
            "mistake_id": mistake_id,
            "user_id": user_id,
            "course_id": course_id,
            "attempt_id": mistake["attempt_id"],
            "quiz_revision_id": mistake["quiz_revision_id"],
            "question_revision_id": mistake["question_revision_id"],
            "status": "in_progress",
            "answer": None,
            "result": None,
            "created_at": now,
            "submitted_at": None,
        }
        store.put("mistake_practice", practice_id, item)
        domain._audit(course_id, "mistake.practice_started", "mistake_practice", practice_id)
        return {"practice": item, "mistake": mistake}


def _owned_practice(store, user_id, course_id, practice_id, *, lock=False):
    domain = DomainService(store, user_id)
    domain.course(course_id)
    practice = (
        _locked(store, "mistake_practice", practice_id)
        if lock
        else store.get("mistake_practice", practice_id)
    )
    if not practice or practice.get("user_id") != user_id or practice.get("course_id") != course_id:
        raise DomainNotFound(practice_id)
    mistake = _owned_mistake(store, user_id, course_id, practice["mistake_id"])[1]
    if (
        practice["attempt_id"] != mistake["attempt_id"]
        or practice["question_revision_id"] != mistake["question_revision_id"]
    ):
        raise DomainConflict("重练任务与原错题不一致")
    return domain, practice, mistake


def read_practice(store, user_id, course_id, practice_id):
    _, practice, mistake = _owned_practice(store, user_id, course_id, practice_id)
    return {"practice": practice, "mistake": mistake}


def list_practices(store, user_id, course_id, mistake_id):
    _owned_mistake(store, user_id, course_id, mistake_id)
    items = [
        item
        for item in store.scan("mistake_practice", {"course_id": course_id})
        if item.get("user_id") == user_id and item.get("mistake_id") == mistake_id
    ]
    return {"items": sorted(items, key=lambda item: item["created_at"], reverse=True)}


def submit_practice(store, user_id, course_id, practice_id, answer):
    with store.transaction():
        domain, practice, mistake = _owned_practice(
            store, user_id, course_id, practice_id, lock=True
        )
        if practice["status"] != "in_progress":
            raise DomainConflict("重练已提交，不能重复修改")
        question = mistake["question"]
        value = _validated_answer(question, answer)
        if value is None:
            raise ValueError("请先填写重练答案")
        objective = question["question_type"] in {"choice", "true_false", "fill_blank"}
        correct = objective_correct(question, value) if objective else None
        now = _now()
        practice.update(
            status="submitted",
            answer=value,
            submitted_at=now,
            result={
                "correct": correct,
                "score": question["score"] if correct else (0 if objective else None),
                "reference_answer": question["reference_answer"],
                "explanation": question["explanation"],
                "grading_mode": "deterministic" if objective else "self_review",
            },
        )
        store.put("mistake_practice", practice_id, practice)
        domain._audit(course_id, "mistake.practice_submitted", "mistake_practice", practice_id)
        return {"practice": practice, "mistake": mistake}
