"""Save-only M3-03 tests. Confirmation and printing are separate slices."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from test_m3_quiz_config import quiz_system as quiz_system
from test_m3_quiz_contract import draft_case as draft_case

from final_review.api import create_app
from final_review.domain import DomainConflict, DomainNotFound
from final_review.quiz_contract import validate_quiz_draft
from final_review.quiz_edit import save_quiz_revision
from final_review.quiz_edit_contract import QuizRevisionEdit
from final_review.quiz_generation import publish_quiz, read_quiz
from final_review.schemas import ResolvedQuizConfig


def generated_quiz(case):
    system, context, plan, candidate = case
    validated = validate_quiz_draft(context, plan, candidate)
    assert validated.valid, validated.issues
    card = publish_quiz(
        system.store,
        {
            "context": context,
            "plan": plan,
            "draft": validated.validated_draft,
            "coverage": {"read_chunks": 1, "available_chunks": 1, "partial": False},
            "audit": [],
        },
        "edit-workflow",
        "fixture",
    )
    return system, card


def request_for(paper, **changes):
    questions = []
    for question in paper["revision"]["questions"]:
        value = {
            key: question[key]
            for key in (
                "question_revision_id",
                "question_type",
                "order",
                "score",
                "stem",
                "options",
                "correct_option",
                "boolean_answer",
                "accepted_answers",
                "explanation",
                "provenance",
                "citations",
            )
        }
        if question["question_type"] not in {"choice", "true_false", "fill_blank"}:
            value["reference_answer"] = question["reference_answer"]
        questions.append(value)
    questions[0].update(changes)
    return QuizRevisionEdit(
        base_revision_id=paper["revision"]["quiz_revision_id"], questions=questions
    )


def read(system, card):
    return read_quiz(system.store, "local-user", "net", card["asset_id"], card["revision_id"])


def test_edit_creates_complete_draft_and_keeps_old_question_and_source(draft_case):
    system, card = generated_quiz(draft_case)
    before = read(system, card)
    old_question = before["revision"]["questions"][0]
    old_row = deepcopy(system.store.get("question_revision", old_question["question_revision_id"]))
    old_refs = deepcopy(system.store.scan("source_reference", {"course_id": "net"}))
    changed = save_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        request_for(before, stem="请说明 TCP 为什么要同步初始序列号？"),
    )
    new_id = changed["revision"]["quiz_revision_id"]
    new_question = changed["revision"]["questions"][0]
    assert new_id != card["revision_id"]
    assert changed["revision"]["state"] == "draft"
    assert changed["asset"]["latest_revision_id"] == new_id
    assert changed["asset"]["current_revision_id"] is None
    assert new_question["question_id"] == old_question["question_id"]
    assert new_question["question_revision_id"] != old_question["question_revision_id"]
    assert new_question["source_review_status"] == "pending"
    assert new_question["source_label"] == "来源待复核"
    assert new_question["references"] == []
    assert new_question["candidate_references"][0]["file_name"]
    assert system.store.get("question_revision", old_question["question_revision_id"]) == old_row
    assert read(system, card)["revision"]["questions"][0]["source_label"] == "老师PPT"
    for row in old_refs:
        assert system.store.get("source_reference", row["source_reference_id"]) == row
    new_refs = [
        row
        for row in system.store.scan("source_reference", {"course_id": "net"})
        if row["revision_id"] == new_id
    ]
    assert len(new_refs) == 1
    assert new_refs[0]["question_revision_id"] == new_question["question_revision_id"]


def test_second_draft_carries_pending_review_and_stale_base_is_rejected(draft_case):
    system, card = generated_quiz(draft_case)
    first = read(system, card)
    second = save_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        request_for(first, explanation="增加学生解释。"),
    )
    third = save_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        request_for(second, score=12),
    )
    assert third["revision"]["total_score"] == 12
    assert third["revision"]["confirmation_issues"]
    assert len(third["revision"]["pending_semantic_review_question_ids"]) == 1
    assert third["revision"]["questions"][0]["references"] == []
    with pytest.raises(DomainConflict, match="已有更新"):
        save_quiz_revision(system.store, "local-user", "net", card["asset_id"], request_for(first))


def test_edit_confirmed_base_leaves_current_pointer_and_history_unchanged(draft_case):
    system, card = generated_quiz(draft_case)
    revision = system.store.get("asset_revision", card["revision_id"])
    payload = system.store.get("quiz_revision_payload", card["revision_id"])
    asset = system.store.get("learning_asset", card["asset_id"])
    revision.update(state="confirmed", confirmed_at="2026-10-11T00:00:00+00:00")
    payload["state"] = "confirmed"
    asset.update(status="confirmed", current_revision_id=card["revision_id"])
    system.store.put("asset_revision", card["revision_id"], revision)
    system.store.put("quiz_revision_payload", card["revision_id"], payload)
    system.store.put("learning_asset", card["asset_id"], asset)
    saved = save_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        request_for(read(system, card), score=12),
    )
    assert saved["asset"]["status"] == "confirmed"
    assert saved["asset"]["current_revision_id"] == card["revision_id"]
    assert saved["asset"]["latest_revision_id"] != card["revision_id"]
    assert system.store.get("asset_revision", card["revision_id"]) == revision
    assert system.store.get("quiz_revision_payload", card["revision_id"]) == payload


def test_source_and_owner_failures_never_publish_partial_revision(draft_case, monkeypatch):
    system, card = generated_quiz(draft_case)
    paper = read(system, card)
    before = deepcopy(system.store.tables)
    with pytest.raises(DomainNotFound):
        save_quiz_revision(system.store, "other-user", "net", card["asset_id"], request_for(paper))
    with pytest.raises(DomainNotFound):
        save_quiz_revision(
            system.store, "local-user", "other-course", card["asset_id"], request_for(paper)
        )
    with pytest.raises(ValueError, match="引用摘录"):
        save_quiz_revision(
            system.store,
            "local-user",
            "net",
            card["asset_id"],
            request_for(
                paper,
                citations=[
                    {
                        "chunk_id": paper["revision"]["questions"][0]["citations"][0]["chunk_id"],
                        "quote": "虚构引用",
                    }
                ],
            ),
        )
    document = system.store.get("document", "m3-ppt")
    document["parse_status"] = "failed"
    system.store.put("document", "m3-ppt", document)
    with pytest.raises(ValueError, match="来源片段不可用"):
        save_quiz_revision(system.store, "local-user", "net", card["asset_id"], request_for(paper))
    system.store.put("document", "m3-ppt", before["document"]["m3-ppt"])
    assert (
        system.store.get("learning_asset", card["asset_id"])["latest_revision_id"]
        == card["revision_id"]
    )
    original_put = system.store.put

    def fail_source(table, key, value):
        if table == "source_reference" and value["revision_id"] != card["revision_id"]:
            raise RuntimeError("simulated source write failure")
        return original_put(table, key, value)

    monkeypatch.setattr(system.store, "put", fail_source)
    with pytest.raises(RuntimeError, match="simulated"):
        save_quiz_revision(system.store, "local-user", "net", card["asset_id"], request_for(paper))
    assert system.store.tables == before


def test_api_saves_draft_and_rejects_wrong_course(draft_case):
    system, card = generated_quiz(draft_case)
    paper = read(system, card)
    request = request_for(paper, explanation="编辑后的解析。")
    with TestClient(create_app(system.settings, system)) as client:
        response = client.post(
            f"/api/courses/net/quizzes/{card['asset_id']}/revisions",
            json=request.model_dump(mode="json"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["revision"]["questions"][0]["explanation"] == "编辑后的解析。"
        assert (
            client.post(
                f"/api/courses/other-course/quizzes/{card['asset_id']}/revisions",
                json=request.model_dump(mode="json"),
            ).status_code
            == 404
        )


def test_parallel_saves_from_same_base_only_publish_one(draft_case):
    system, card = generated_quiz(draft_case)
    paper = read(system, card)
    request = request_for(paper, explanation="并发编辑。")

    def save(_):
        try:
            return save_quiz_revision(system.store, "local-user", "net", card["asset_id"], request)
        except DomainConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, DomainConflict) for result in results) == 1
    assert len(system.store.scan("quiz_revision_payload", {"course_id": "net"})) == 2


def test_reordering_is_reflected_in_saved_read_order(draft_case):
    system, context, plan, candidate = draft_case
    config_data = context.config.model_dump(mode="json")
    config_data.update(
        blueprint=[{"question_type": "short_answer", "question_count": 2}], question_count=2
    )
    context = context.model_copy(update={"config": ResolvedQuizConfig.model_validate(config_data)})
    plan["allocations"][0]["question_count"] = 2
    candidate["questions"][0]["score"] = 5
    second = deepcopy(candidate["questions"][0])
    second.update(id="q2", order=2, stem="第二题：描述 TCP 建立连接。")
    candidate["questions"].append(second)
    validated = validate_quiz_draft(context, plan, candidate)
    assert validated.valid, validated.issues
    card = publish_quiz(
        system.store,
        {
            "context": context,
            "plan": plan,
            "draft": validated.validated_draft,
            "coverage": {"read_chunks": 1, "available_chunks": 1, "partial": False},
            "audit": [],
        },
        "reorder-workflow",
        "fixture",
    )
    before = read(system, card)
    request = request_for(before)
    request.questions[0].order = 2
    request.questions[1].order = 1
    saved = save_quiz_revision(system.store, "local-user", "net", card["asset_id"], request)
    assert [q["id"] for q in saved["revision"]["questions"]] == ["q2", "q1"]
    assert [q["order"] for q in saved["revision"]["questions"]] == [1, 2]
    assert [q["id"] for q in read(system, card)["revision"]["questions"]] == ["q1", "q2"]


def test_changed_material_version_marks_unchanged_question_pending(draft_case):
    system, card = generated_quiz(draft_case)
    before = read(system, card)
    document = system.store.get("document", "m3-ppt")
    document["material_version_id"] = "new-material-version"
    system.store.put("document", "m3-ppt", document)
    saved = save_quiz_revision(
        system.store, "local-user", "net", card["asset_id"], request_for(before)
    )
    question = saved["revision"]["questions"][0]
    assert question["source_review_status"] == "pending"
    assert question["candidate_references"][0]["material_version_id"] == "new-material-version"
