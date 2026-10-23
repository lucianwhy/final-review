"""Version-bound formal quiz attempts with server-side timing and resumable answers."""

from datetime import UTC, datetime, timedelta

from .domain import DomainConflict, DomainNotFound, DomainService, _id
from .quiz_edit import _locked
from .storage import stable_key

_VISIBLE_QUESTION_FIELDS = (
    "question_revision_id",
    "question_type",
    "knowledge_point",
    "stem",
    "options",
    "order",
    "score",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _questions(domain, course_id, revision_id, payload):
    questions = []
    for slot in payload["questions"]:
        question = domain._owned("question_revision", slot["question_revision_id"])
        if (
            question.get("quiz_revision_id") != revision_id
            or question.get("course_id") != course_id
        ):
            raise DomainConflict("试题与试卷版本关系无效")
        combined = {**question, **slot}
        questions.append({key: combined[key] for key in _VISIBLE_QUESTION_FIELDS})
    return sorted(questions, key=lambda item: item["order"])


def _owned_attempt(store, user_id, course_id, attempt_id, *, lock=False):
    domain = DomainService(store, user_id)
    domain.course(course_id)
    attempt = _locked(store, "attempt", attempt_id) if lock else store.get("attempt", attempt_id)
    if (
        attempt is None
        or attempt.get("user_id") != user_id
        or attempt.get("course_id") != course_id
        or not attempt.get("quiz_revision_id")
    ):
        raise DomainNotFound(attempt_id)
    return domain, attempt


def _finish(attempt: dict, now: datetime, reason: str) -> dict:
    started = datetime.fromisoformat(attempt["started_at"])
    deadline = attempt.get("deadline_at")
    effective = min(now, datetime.fromisoformat(deadline)) if deadline else now
    attempt.update(
        status="submitted",
        submitted_at=now.isoformat(),
        elapsed_seconds=max(0, int((effective - started).total_seconds())),
        submission_reason=reason,
        version=attempt["version"] + 1,
        updated_at=now.isoformat(),
    )
    return attempt


def _expired(attempt: dict, now: datetime) -> bool:
    return bool(
        attempt.get("deadline_at") and now >= datetime.fromisoformat(attempt["deadline_at"])
    )


def _public(domain, attempt):
    revision_id = attempt["quiz_revision_id"]
    payload = domain._owned("quiz_revision_payload", revision_id)
    if payload.get("course_id") != attempt["course_id"]:
        raise DomainConflict("作答关联的试卷版本无效")
    return {
        "attempt": attempt,
        "quiz": {"title": payload["title"], "total_score": payload["total_score"]},
        "questions": _questions(domain, attempt["course_id"], revision_id, payload),
    }


def start_formal_attempt(store, user_id: str, course_id: str, asset_id: str):
    """Bind an Attempt to the current confirmed revision, never to a draft."""
    domain = DomainService(store, user_id)
    with store.transaction():
        course = _locked(store, "course", course_id)
        if course is None or course.get("user_id") != user_id:
            raise DomainNotFound(course_id)
        domain.course(course_id, writable=True)
        asset = domain._locked_asset(asset_id)
        if asset.get("course_id") != course_id or asset.get("asset_type") != "quiz":
            raise DomainNotFound(asset_id)
        revision_id = asset.get("current_revision_id")
        if asset.get("status") != "confirmed" or not revision_id:
            raise DomainConflict("只能从已确认的正式试卷开始测试")
        revision = domain._owned("asset_revision", revision_id)
        payload = domain._owned("quiz_revision_payload", revision_id)
        if (
            revision.get("state") != "confirmed"
            or revision.get("asset_id") != asset_id
            or payload.get("state") != "confirmed"
            or payload.get("asset_id") != asset_id
            or payload.get("course_id") != course_id
        ):
            raise DomainConflict("当前正式试卷版本无效")
        questions = _questions(domain, course_id, revision_id, payload)
        if not questions:
            raise DomainConflict("试卷没有可作答的题目")
        now = _now()
        duration = payload.get("duration_minutes")
        attempt = {
            "attempt_id": _id("attempt"),
            "user_id": user_id,
            "course_id": course_id,
            "asset_id": asset_id,
            "quiz_revision_id": revision_id,
            "legacy_session_id": None,
            "status": "in_progress",
            "started_at": now.isoformat(),
            "deadline_at": (now + timedelta(minutes=duration)).isoformat() if duration else None,
            "duration_minutes": duration,
            "answers": {},
            "question_revision_ids": [question["question_revision_id"] for question in questions],
            "version": 0,
            "submitted_at": None,
            "submission_reason": None,
            "elapsed_seconds": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        store.put("attempt", attempt["attempt_id"], attempt)
        domain._audit(course_id, "quiz.attempt_started", "attempt", attempt["attempt_id"])
        return {
            "attempt": attempt,
            "quiz": {"title": payload["title"], "total_score": payload["total_score"]},
            "questions": questions,
        }


def read_formal_attempt(store, user_id: str, course_id: str, attempt_id: str):
    with store.transaction():
        domain, attempt = _owned_attempt(store, user_id, course_id, attempt_id, lock=True)
        now = _now()
        if attempt["status"] == "in_progress" and _expired(attempt, now):
            _finish(attempt, now, "timeout")
            store.put("attempt", attempt_id, attempt)
        return _public(domain, attempt)


def _validated_answer(question: dict, answer):
    if answer is None:
        return None
    kind = question["question_type"]
    if kind == "choice":
        if type(answer) is not int or not 0 <= answer < len(question["options"]):
            raise ValueError("选择题答案必须是有效选项索引")
    elif kind == "true_false":
        if type(answer) is not bool:
            raise ValueError("判断题答案必须是布尔值")
    elif not isinstance(answer, str) or not 0 < len(answer.strip()) <= 20000:
        raise ValueError("文字答案不能为空且不得超过 20000 字符")
    return answer.strip() if isinstance(answer, str) else answer


def save_formal_answer(
    store,
    user_id: str,
    course_id: str,
    attempt_id: str,
    question_revision_id: str,
    answer,
    expected_version: int,
):
    """Serialize autosaves using an optimistic version on the locked Attempt row."""
    expired = False
    with store.transaction():
        domain, attempt = _owned_attempt(store, user_id, course_id, attempt_id, lock=True)
        now = _now()
        if attempt["status"] != "in_progress":
            raise DomainConflict("此作答已经交卷，不能修改答案")
        if _expired(attempt, now):
            _finish(attempt, now, "timeout")
            store.put("attempt", attempt_id, attempt)
            expired = True
        else:
            if attempt["version"] != expected_version:
                raise DomainConflict("作答已在其他位置更新，请重新加载")
            if question_revision_id not in attempt["question_revision_ids"]:
                raise DomainNotFound(question_revision_id)
            question = domain._owned("question_revision", question_revision_id)
            if question.get("quiz_revision_id") != attempt["quiz_revision_id"]:
                raise DomainConflict("试题不属于本次作答的试卷版本")
            value = _validated_answer(question, answer)
            if value is None:
                attempt["answers"].pop(question_revision_id, None)
            else:
                attempt["answers"][question_revision_id] = {
                    "answer": value,
                    "saved_at": now.isoformat(),
                }
            attempt["version"] += 1
            attempt["updated_at"] = now.isoformat()
            store.put("attempt", attempt_id, attempt)
            answer_key = "attempt-answer-" + stable_key(attempt_id, question_revision_id)[:32]
            prior = store.get("attempt_answer", answer_key)
            store.put(
                "attempt_answer",
                answer_key,
                {
                    "attempt_id": attempt_id,
                    "question_revision_id": question_revision_id,
                    "user_id": user_id,
                    "course_id": course_id,
                    "answer": value,
                    "version": attempt["version"],
                    "saved_at": now.isoformat(),
                    "created_at": prior["created_at"] if prior else now.isoformat(),
                },
            )
        result = _public(domain, attempt)
    if expired:
        raise DomainConflict("作答时间已到，系统已自动交卷")
    return result


def submit_formal_attempt(store, user_id: str, course_id: str, attempt_id: str):
    with store.transaction():
        domain, attempt = _owned_attempt(store, user_id, course_id, attempt_id, lock=True)
        if attempt["status"] == "in_progress":
            now = _now()
            _finish(attempt, now, "timeout" if _expired(attempt, now) else "manual")
            store.put("attempt", attempt_id, attempt)
            domain._audit(course_id, "quiz.attempt_submitted", "attempt", attempt_id)
        return _public(domain, attempt)


def list_formal_attempts(store, user_id: str, course_id: str):
    domain = DomainService(store, user_id)
    domain.course(course_id)
    return {
        "items": sorted(
            [
                {
                    key: attempt.get(key)
                    for key in (
                        "attempt_id",
                        "asset_id",
                        "quiz_revision_id",
                        "status",
                        "started_at",
                        "submitted_at",
                        "elapsed_seconds",
                        "total_score",
                    )
                }
                for attempt in store.scan("attempt", {"course_id": course_id})
                if attempt.get("user_id") == user_id and attempt.get("quiz_revision_id")
            ],
            key=lambda item: item["started_at"],
            reverse=True,
        )
    }
