"""M4 first-round projection, six filters, mastery and practice API contracts."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from test_m3_formal_attempt import confirmed_case
from test_m3_quiz_config import quiz_system as quiz_system
from test_m3_quiz_contract import draft_case as draft_case

from final_review.api import create_app
from final_review.domain import DomainNotFound
from final_review.formal_attempt import start_formal_attempt, submit_formal_attempt
from final_review.formal_grading import grade_formal_attempt
from final_review.mistakes import (
    list_mistakes,
    mark_mastery,
    read_mistake,
    start_practice,
    submit_practice,
    sync_mistakes_for_attempt,
)


def wrong_attempt(case):
    system, card = confirmed_case(case)
    started = start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    attempt_id = started["attempt"]["attempt_id"]
    submit_formal_attempt(system.store, "local-user", "net", attempt_id)
    grade_formal_attempt(system.store, "local-user", "net", attempt_id, None, "default", "v1")
    return system, card, attempt_id


def test_grading_projects_once_and_manual_mastery_keeps_evidence(draft_case):
    system, card, attempt_id = wrong_attempt(draft_case)
    store = system.store
    before_attempt = deepcopy(store.get("attempt", attempt_id))
    before_grade = deepcopy(store.scan("question_grade", {"course_id": "net"}))
    items = list_mistakes(store, "local-user", course_id="net")["items"]
    assert len(items) == 1
    item = items[0]
    assert item["attempt_id"] == attempt_id
    assert item["quiz_revision_id"] == card["revision_id"]
    assert item["question_revision_id"] == before_grade[0]["question_revision_id"]
    assert item["question"]["stem"] and item["grade"]["student_answer"] is None
    assert item["question"]["references"]
    assert item["mastery_status"] == "unmastered"
    assert sync_mistakes_for_attempt(store, "local-user", "net", attempt_id) == []
    marked = mark_mastery(store, "local-user", "net", item["mistake_id"], "mastered")
    assert marked["mastery_status"] == "mastered"
    assert mark_mastery(store, "local-user", "net", item["mistake_id"], "mastered") == marked
    grade_formal_attempt(store, "local-user", "net", attempt_id, None, "default", "v1")
    assert (
        read_mistake(store, "local-user", "net", item["mistake_id"])["mastery_status"] == "mastered"
    )
    assert len(store.scan("mistake", {"course_id": "net"})) == 1
    assert len(store.scan("mastery_observation", {"course_id": "net"})) == 2
    assert store.get("attempt", attempt_id) == before_attempt
    assert store.scan("question_grade", {"course_id": "net"}) == before_grade


def test_six_filters_compose_and_owner_is_required(draft_case):
    system, _, _ = wrong_attempt(draft_case)
    store = system.store
    original = list_mistakes(store, "local-user")["items"][0]
    variant = {
        **original,
        "mistake_id": "mistake-other",
        "exam_id": "exam-other",
        "chapter": "other",
        "knowledge_point": "other",
        "question_type": "choice",
        "mastery_status": "mastered",
    }
    store.put("mistake", variant["mistake_id"], variant)
    selected = list_mistakes(
        store,
        "local-user",
        course_id="net",
        exam_id=original["exam_id"],
        chapter=original["chapter"],
        knowledge_point=original["knowledge_point"],
        question_type=original["question_type"],
        mastery_status="unmastered",
    )["items"]
    assert [item["mistake_id"] for item in selected] == [original["mistake_id"]]
    assert list_mistakes(store, "local-user", course_id="net", chapter="missing")["items"] == []
    with pytest.raises(DomainNotFound):
        read_mistake(store, "someone-else", "net", original["mistake_id"])
    with pytest.raises(DomainNotFound):
        mark_mastery(store, "someone-else", "net", original["mistake_id"], "mastered")
    with pytest.raises(DomainNotFound):
        start_practice(store, "local-user", "other-course", original["mistake_id"])


def test_practice_is_real_record_and_subjective_is_self_review(draft_case):
    system, _, _ = wrong_attempt(draft_case)
    store = system.store
    mistake = list_mistakes(store, "local-user")["items"][0]
    original_attempt = deepcopy(store.get("attempt", mistake["attempt_id"]))
    created = start_practice(store, "local-user", "net", mistake["mistake_id"])
    practice = created["practice"]
    assert practice["attempt_id"] == original_attempt["attempt_id"]
    assert practice["quiz_revision_id"] == mistake["quiz_revision_id"]
    assert practice["question_revision_id"] == mistake["question_revision_id"]
    finished = submit_practice(
        store, "local-user", "net", practice["practice_id"], "同步初始序列号"
    )
    assert finished["practice"]["result"]["grading_mode"] == "self_review"
    assert finished["practice"]["result"]["score"] is None
    assert store.get("attempt", mistake["attempt_id"]) == original_attempt
    assert store.get("mistake", mistake["mistake_id"])["mastery_status"] == "unmastered"
    with pytest.raises(ValueError, match="已提交"):
        submit_practice(store, "local-user", "net", practice["practice_id"], "changed")


def test_mistake_http_flow_and_cross_course_rejection(draft_case):
    system, _, _ = wrong_attempt(draft_case)
    with TestClient(create_app(system.settings, system)) as client:
        response = client.get("/api/mistakes?course_id=net&chapter=TCP&mastery_status=unmastered")
        assert response.status_code == 200
        item = response.json()["items"][0]
        root = f"/api/courses/net/mistakes/{item['mistake_id']}"
        assert client.get(root).status_code == 200
        assert client.get(f"/api/courses/else/mistakes/{item['mistake_id']}").status_code == 404
        assert client.put(root + "/mastery", json={"status": "mastered"}).status_code == 200
        assert (
            client.get("/api/mistakes?course_id=net&mastery_status=unmastered").json()["items"]
            == []
        )
        created = client.post(root + "/practices")
        assert created.status_code == 200
        task_id = created.json()["practice"]["practice_id"]
        task_root = f"/api/courses/net/mistake-practices/{task_id}"
        assert client.get(task_root).status_code == 200
        assert client.get(root + "/practices").json()["items"][0]["practice_id"] == task_id
        assert (
            client.post(task_root + "/submit", json={"answer": "同步初始序列号"}).status_code == 200
        )
        assert client.post(task_root + "/submit", json={"answer": "twice"}).status_code == 409
        assert client.get(f"/api/courses/else/mistake-practices/{task_id}").status_code == 404
