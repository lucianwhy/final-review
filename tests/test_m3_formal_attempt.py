"""Formal Attempts are version-bound, resumable, timed and isolated from drafts."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_m3_quiz_config import quiz_system as quiz_system
from test_m3_quiz_contract import draft_case as draft_case
from test_m3_quiz_edit import generated_quiz, read, request_for

from final_review.api import create_app
from final_review.domain import DomainConflict, DomainNotFound
from final_review.formal_attempt import (
    _validated_answer,
    read_formal_attempt,
    save_formal_answer,
    start_formal_attempt,
    submit_formal_attempt,
)
from final_review.quiz_edit import save_quiz_revision
from final_review.quiz_review import (
    confirm_quiz_revision,
    quiz_confirm_preview,
    review_quiz_revision,
)


def confirmed_case(case):
    system, card = generated_quiz(case)
    token = quiz_confirm_preview(
        system.store, "local-user", "net", card["asset_id"], card["revision_id"]
    )
    confirm_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        card["revision_id"],
        token["confirmation_id"],
    )
    return system, card


def test_draft_cannot_start_formal_attempt(draft_case):
    system, card = generated_quiz(draft_case)
    with pytest.raises(DomainConflict, match="已确认"):
        start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    assert not system.store.scan("attempt", {"course_id": "net"})


def test_save_resume_submit_and_reject_stale_or_late_answers(draft_case):
    system, card = confirmed_case(draft_case)
    started = start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    attempt = started["attempt"]
    attempt_id = attempt["attempt_id"]
    question_id = started["questions"][0]["question_revision_id"]
    assert attempt["quiz_revision_id"] == card["revision_id"]
    assert "reference_answer" not in started["questions"][0]
    assert "explanation" not in started["questions"][0]
    saved = save_formal_answer(
        system.store,
        "local-user",
        "net",
        attempt_id,
        question_id,
        "  同步序列号  ",
        0,
    )
    assert saved["attempt"]["version"] == 1
    assert saved["attempt"]["answers"][question_id]["answer"] == "同步序列号"
    answer_rows = system.store.scan("attempt_answer", {"course_id": "net"})
    assert len(answer_rows) == 1
    assert answer_rows[0]["question_revision_id"] == question_id
    assert answer_rows[0]["answer"] == "同步序列号"
    with pytest.raises(DomainConflict, match="其他位置更新"):
        save_formal_answer(system.store, "local-user", "net", attempt_id, question_id, "旧答案", 0)
    resumed = read_formal_attempt(system.store, "local-user", "net", attempt_id)
    assert resumed["attempt"]["answers"] == saved["attempt"]["answers"]
    submitted = submit_formal_attempt(system.store, "local-user", "net", attempt_id)
    assert submitted["attempt"]["status"] == "submitted"
    assert submit_formal_attempt(system.store, "local-user", "net", attempt_id) == submitted
    with pytest.raises(DomainConflict, match="已经交卷"):
        save_formal_answer(
            system.store, "local-user", "net", attempt_id, question_id, "迟到答案", 1
        )


def test_timeout_is_decided_by_server_clock(draft_case, monkeypatch):
    system, card = confirmed_case(draft_case)
    started = start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    attempt_id = started["attempt"]["attempt_id"]
    item = system.store.get("attempt", attempt_id)
    deadline = datetime.fromisoformat(item["started_at"]) + timedelta(minutes=10)
    item["deadline_at"] = deadline.isoformat()
    item["duration_minutes"] = 10
    system.store.put("attempt", attempt_id, item)
    monkeypatch.setattr("final_review.formal_attempt._now", lambda: deadline + timedelta(seconds=3))
    expired = read_formal_attempt(system.store, "local-user", "net", attempt_id)
    assert expired["attempt"]["status"] == "submitted"
    assert expired["attempt"]["submission_reason"] == "timeout"
    assert expired["attempt"]["elapsed_seconds"] == 10 * 60


def test_old_attempt_remains_bound_after_quiz_is_reedited(draft_case):
    system, card = confirmed_case(draft_case)
    started = start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    attempt_id = started["attempt"]["attempt_id"]
    original_id = started["questions"][0]["question_revision_id"]
    saved = save_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        request_for(read(system, card), stem="第二版试卷题干"),
    )
    revision_id = saved["revision"]["quiz_revision_id"]

    class Reviewer:
        def review_quiz_edit(self, context, draft):
            return {
                "items": [
                    {"question_id": item, "supported": True, "issues": []}
                    for item in context["review_question_ids"]
                ]
            }

    review_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        revision_id,
        Reviewer(),
        "fixture",
    )
    token = quiz_confirm_preview(system.store, "local-user", "net", card["asset_id"], revision_id)
    confirm_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        revision_id,
        token["confirmation_id"],
    )
    resumed = read_formal_attempt(system.store, "local-user", "net", attempt_id)
    assert resumed["attempt"]["quiz_revision_id"] == card["revision_id"]
    assert resumed["questions"][0]["question_revision_id"] == original_id
    assert resumed["questions"][0]["stem"] != "第二版试卷题干"
    assert (
        save_formal_answer(
            system.store, "local-user", "net", attempt_id, original_id, "旧卷答案", 0
        )["attempt"]["version"]
        == 1
    )


def test_formal_answer_types_and_owner_scope(draft_case):
    system, card = confirmed_case(draft_case)
    started = start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    attempt_id = started["attempt"]["attempt_id"]
    with pytest.raises(DomainNotFound):
        read_formal_attempt(system.store, "other-user", "net", attempt_id)
    with pytest.raises(DomainNotFound):
        read_formal_attempt(system.store, "local-user", "other-course", attempt_id)
    with pytest.raises(ValueError, match="文字答案"):
        save_formal_answer(
            system.store,
            "local-user",
            "net",
            attempt_id,
            started["questions"][0]["question_revision_id"],
            True,
            0,
        )
    assert _validated_answer({"question_type": "choice", "options": ["A", "B"]}, 1) == 1
    with pytest.raises(ValueError, match="选择题"):
        _validated_answer({"question_type": "choice", "options": ["A", "B"]}, True)
    assert _validated_answer({"question_type": "true_false"}, False) is False


def test_formal_attempt_http_flow(draft_case):
    system, card = confirmed_case(draft_case)
    with TestClient(create_app(system.settings, system)) as client:
        started = client.post(f"/api/courses/net/quizzes/{card['asset_id']}/attempts")
        assert started.status_code == 200
        attempt = started.json()["attempt"]
        question_id = started.json()["questions"][0]["question_revision_id"]
        root = f"/api/courses/net/formal-attempts/{attempt['attempt_id']}"
        saved = client.put(
            root + "/answers",
            json={
                "question_revision_id": question_id,
                "answer": "同步序列号",
                "expected_version": 0,
            },
        )
        assert saved.status_code == 200
        assert client.get(root).json()["attempt"]["answers"][question_id]["answer"] == "同步序列号"
        assert client.post(root + "/submit").json()["attempt"]["status"] == "submitted"
