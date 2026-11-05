"""Results and follow-ups stay bound to the submitted question and owned sources."""

import pytest
from conftest import ScriptedModel
from fastapi.testclient import TestClient
from test_m3_formal_attempt import confirmed_case
from test_m3_quiz_config import quiz_system as quiz_system
from test_m3_quiz_contract import draft_case as draft_case

from final_review.api import create_app
from final_review.domain import DomainConflict, DomainNotFound
from final_review.formal_attempt import (
    save_formal_answer,
    start_formal_attempt,
    submit_formal_attempt,
)
from final_review.formal_grading import grade_formal_attempt
from final_review.formal_result import ask_quiz_followup, read_formal_result


def graded_case(case):
    system, card = confirmed_case(case)
    started = start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    attempt_id = started["attempt"]["attempt_id"]
    question_id = started["questions"][0]["question_revision_id"]
    save_formal_answer(
        system.store, "local-user", "net", attempt_id, question_id, "同步初始序列号", 0
    )
    submit_formal_attempt(system.store, "local-user", "net", attempt_id)
    grade_formal_attempt(
        system.store, "local-user", "net", attempt_id, ScriptedModel(), "fixture", "v1"
    )
    return system, card, attempt_id, question_id


def test_result_requires_submission_and_grading(draft_case):
    system, card = confirmed_case(draft_case)
    started = start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    attempt_id = started["attempt"]["attempt_id"]
    with pytest.raises(DomainConflict, match="交卷后"):
        read_formal_result(system.store, "local-user", "net", attempt_id)
    submit_formal_attempt(system.store, "local-user", "net", attempt_id)
    with pytest.raises(DomainConflict, match="评分尚未"):
        read_formal_result(system.store, "local-user", "net", attempt_id)


def test_submitted_result_contains_feedback_knowledge_and_owned_sources(draft_case):
    system, card, attempt_id, question_id = graded_case(draft_case)
    result = read_formal_result(system.store, "local-user", "net", attempt_id)
    assert result["quiz_revision_id"] == card["revision_id"]
    assert result["total_score"] == 10
    item = result["items"][0]
    assert item["question_revision_id"] == question_id
    assert item["knowledge_point"] == "三次握手"
    assert item["explanation"]
    assert item["reference_answer"]
    assert item["references"][0]["file_name"] == "TCP课件"
    assert item["references"][0]["available"]
    with pytest.raises(DomainNotFound):
        read_formal_result(system.store, "other-user", "net", attempt_id)
    with pytest.raises(DomainNotFound):
        read_formal_result(system.store, "local-user", "other-course", attempt_id)


def test_followup_rejects_foreign_question_and_forged_source(draft_case):
    system, _, attempt_id, question_id = graded_case(draft_case)

    class ForgedModel:
        def follow_up_quiz_question(self, context, message):
            return {"answer": "伪造引用", "cited_chunk_ids": ["foreign-course-chunk"]}

    with pytest.raises(DomainNotFound):
        ask_quiz_followup(
            system.store,
            "local-user",
            "net",
            attempt_id,
            "other-question",
            "为什么？",
            ForgedModel(),
            "fixture",
            "v1",
        )
    with pytest.raises(DomainConflict, match="授权来源"):
        ask_quiz_followup(
            system.store,
            "local-user",
            "net",
            attempt_id,
            question_id,
            "为什么？",
            ForgedModel(),
            "fixture",
            "v1",
        )
    assert not system.store.get("attempt", attempt_id).get("followups")


def test_followup_uses_one_question_context_and_is_saved(draft_case):
    system, _, attempt_id, question_id = graded_case(draft_case)
    seen = []

    class ScopedModel:
        def follow_up_quiz_question(self, context, message):
            seen.append((context, message))
            return {
                "answer": "因为双方需要同步序列号。",
                "cited_chunk_ids": [context["sources"][0]["chunk_id"]],
            }

    answer = ask_quiz_followup(
        system.store,
        "local-user",
        "net",
        attempt_id,
        question_id,
        "为什么要同步？",
        ScopedModel(),
        "fixture",
        "v1",
    )
    assert seen[0][0]["question_revision_id"] == question_id
    assert seen[0][0]["sources"][0]["file_name"] == "TCP课件"
    assert answer["citations"][0]["chunk_id"] == seen[0][0]["sources"][0]["chunk_id"]
    assert read_formal_result(system.store, "local-user", "net", attempt_id)["followups"] == [
        answer
    ]


def test_result_and_followup_http_routes(draft_case):
    system, _, attempt_id, question_id = graded_case(draft_case)
    root = f"/api/courses/net/formal-attempts/{attempt_id}/result"
    with TestClient(create_app(system.settings, system)) as client:
        result = client.get(root)
        assert result.status_code == 200
        assert result.json()["items"][0]["question_revision_id"] == question_id
        reply = client.post(
            root + f"/questions/{question_id}/follow-up",
            json={"message": "为什么要同步序列号？"},
        )
        assert reply.status_code == 200
        assert reply.json()["citations"][0]["chunk_id"]
