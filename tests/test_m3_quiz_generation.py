import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_openai import ChatOpenAI
from test_llm import completion
from test_m3_quiz_config import complete_input
from test_m3_quiz_config import quiz_system as quiz_system
from test_m3_quiz_contract import draft_case as draft_case

from final_review.api import create_app
from final_review.config import ChatModelConfig
from final_review.domain import DomainConflict, DomainNotFound, DomainService
from final_review.llm import ReviewModel
from final_review.quiz_generation import QuizGenerationError, generate_quiz, read_quiz
from final_review.quiz_jobs import enqueue_quiz, process_quiz_job, retry_quiz
from final_review.storage import stable_key


def model_for(plan, payload, *, verdict=None, repaired=None):
    return SimpleNamespace(
        quiz_draft_plan=lambda *_args, **_kwargs: deepcopy(plan),
        quiz_draft=lambda *_args: deepcopy(payload),
        review_quiz_draft=verdict
        or (
            lambda _ctx, draft: {
                "items": [
                    {"question_id": q["id"], "supported": True, "issues": []}
                    for q in draft["questions"]
                ]
            }
        ),
        repair_quiz_draft=repaired or (lambda *_args: deepcopy(payload)),
    )


def queue_case(case, *, conversation=False):
    system, context, plan, payload = case
    if conversation:
        system.store.put(
            "conversation",
            stable_key("net", "original"),
            {
                "user_id": "local-user",
                "course_id": "net",
                "conversation_id": "original",
                "title": "原对话",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
    queued = enqueue_quiz(
        system.store,
        context.config,
        model_id="default",
        conversation_id="original" if conversation else None,
        idempotency_key="request-one",
    )
    return system, context, model_for(plan, payload), system.store.claim_quiz_job(), queued


def test_generation_publishes_complete_draft_in_original_conversation(draft_case):
    system, _, model, job, queued = queue_case(draft_case, conversation=True)
    process_quiz_job(system.store, model, system.settings, job)
    saved = system.store.get("quiz_job", queued["job_id"])
    assert saved["status"] == "succeeded"
    result = saved["result"]
    paper = read_quiz(system.store, "local-user", "net", result["asset_id"], result["revision_id"])
    assert paper["asset"]["current_revision_id"] is None
    assert paper["revision"]["state"] == "draft"
    question = paper["revision"]["questions"][0]
    assert question["order"] == 1 and question["score"] == 10
    assert question["reference_answer"] and question["must_include"]
    assert question["source_label"] == "老师PPT" and question["references"][0]["available"]
    refs = system.store.scan("source_reference", {"course_id": "net"})
    assert refs[0]["question_revision_id"] == question["question_revision_id"]
    assert paper["revision"]["review_audit"][-1]["review"]["items"][0]["supported"]
    conversation = system.store.get("conversation", stable_key("net", "original"))
    assert conversation["active_quiz"] is None
    with TestClient(create_app(system.settings, system)) as client:
        history = client.get("/api/courses/net/conversations/original/messages").json()
        assert history["items"][-1]["quiz_draft"] == result
        assert (
            client.get("/api/courses/net/quizzes").json()["items"][0]["asset_id"]
            == result["asset_id"]
        )
        assert (
            client.get(
                f"/api/courses/net/quizzes/{result['asset_id']}/revisions/{result['revision_id']}"
            ).status_code
            == 200
        )
        confirm = client.post(
            f"/api/assets/{result['asset_id']}/revisions/{result['revision_id']}/confirm"
        )
        assert confirm.status_code == 409
    # A duplicate delivery after success cannot republish or append a second message.
    process_quiz_job(system.store, model, system.settings, job)
    assert len(system.store.scan("learning_asset", {"course_id": "net"})) == 1
    assert len(system.store.scan("message", {"course_id": "net"})) == 1


def test_rule_failure_repaired_then_semantically_reviewed(draft_case):
    system, context, plan, good = draft_case
    bad = deepcopy(good)
    bad["questions"][0]["score"] = 9
    calls = []
    model = model_for(plan, bad, repaired=lambda *_args: calls.append("repair") or deepcopy(good))
    generated = generate_quiz(system.store, model, context.config)
    assert generated["draft"]["questions"][0]["score"] == 10
    assert calls == ["repair"]
    assert generated["audit"][-2]["issues"][0]["code"] == "total_score"


@pytest.mark.parametrize("kind", ["false", "missing", "duplicate", "inconsistent"])
def test_semantic_failure_is_bounded_and_never_publishes(draft_case, kind):
    system, context, plan, payload = draft_case
    calls = []

    def verdict(_ctx, _draft):
        calls.append("review")
        item = {"question_id": "q1", "supported": False, "issues": ["答案不由原文支持"]}
        if kind == "missing":
            item["question_id"] = "another"
        if kind == "inconsistent":
            item["supported"] = True
        return {"items": [item, item] if kind == "duplicate" else [item]}

    with pytest.raises(QuizGenerationError):
        generate_quiz(
            system.store, model_for(plan, payload, verdict=verdict), context.config, max_repairs=2
        )
    assert len(calls) == 3
    assert not system.store.scan("learning_asset", {"course_id": "net"})


def test_invalid_plan_is_rejected_before_candidate_call(draft_case):
    system, context, plan, payload = draft_case
    plan["allocations"][0]["question_count"] = 2
    model = model_for(plan, payload)
    model.quiz_draft = lambda *_: pytest.fail("invalid plan must not generate questions")
    with pytest.raises(QuizGenerationError, match="计划"):
        generate_quiz(system.store, model, context.config)


def test_publish_failure_rolls_back_assets_questions_sources_and_completion(
    draft_case, monkeypatch
):
    system, _, model, job, _ = queue_case(draft_case)
    original = system.store.put

    def fail(table, key, value):
        if table == "quiz_job" and value["status"] == "succeeded":
            raise RuntimeError("simulate completion write failure")
        original(table, key, value)

    monkeypatch.setattr(system.store, "put", fail)
    process_quiz_job(system.store, model, system.settings, job)
    assert system.store.get("quiz_job", job["job_id"])["status"] == "failed"
    for table in (
        "learning_asset",
        "asset_revision",
        "question_revision",
        "quiz_revision_payload",
        "source_reference",
    ):
        assert not system.store.scan(table, {"course_id": "net"})


@pytest.mark.parametrize("change", ["version", "deleted", "exam"])
def test_changes_during_generation_prevent_publication(draft_case, change):
    system, context, plan, payload = draft_case
    if change == "exam":
        exam = DomainService(system.store, "local-user").create_exam("net", {"name": "期末"})
        data = context.config.model_dump(mode="json")
        data.update(exam_id=exam["exam_id"], exam_updated_at=exam["updated_at"])
        context.config = type(context.config).model_validate(data)
    queued = enqueue_quiz(system.store, context.config, model_id="default")
    job = system.store.claim_quiz_job()

    def verdict(_ctx, draft):
        if change == "exam":
            system.store.put("exam", exam["exam_id"], {**exam, "status": "archived"})
        else:
            doc = system.store.get("document", "m3-ppt")
            doc.update(material_version_id="new-version") if change == "version" else doc.update(
                parse_status="deleted"
            )
            system.store.put("document", "m3-ppt", doc)
        return {"items": [{"question_id": "q1", "supported": True, "issues": []}]}

    process_quiz_job(system.store, model_for(plan, payload, verdict=verdict), system.settings, job)
    assert system.store.get("quiz_job", queued["job_id"])["status"] == "failed"
    assert not system.store.scan("learning_asset", {"course_id": "net"})


def test_expired_worker_cannot_publish_or_mark_new_attempt_failed(draft_case):
    system, _, model, old, _ = queue_case(draft_case)
    current = system.store.get("quiz_job", old["job_id"])
    current["lease_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    system.store.put("quiz_job", old["job_id"], current)
    new = system.store.claim_quiz_job()
    assert new["attempts"] == old["attempts"] + 1
    process_quiz_job(system.store, model, system.settings, old)
    assert system.store.get("quiz_job", new["job_id"])["status"] == "running"
    assert not system.store.scan("learning_asset", {"course_id": "net"})
    process_quiz_job(system.store, model, system.settings, new)
    assert system.store.get("quiz_job", new["job_id"])["status"] == "succeeded"


def test_failed_job_retry_is_monotonic_and_rejects_changed_config(draft_case):
    system, context, model, job, _ = queue_case(draft_case)
    broken = SimpleNamespace(
        quiz_draft_plan=lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError())
    )
    process_quiz_job(system.store, broken, system.settings, job)
    retry_quiz(system.store, "local-user", "net", job["job_id"])
    new = system.store.claim_quiz_job()
    assert new["attempts"] > job["attempts"]
    process_quiz_job(system.store, model, system.settings, job)
    process_quiz_job(system.store, model, system.settings, new)
    assert system.store.get("quiz_job", new["job_id"])["status"] == "succeeded"
    with pytest.raises(DomainConflict):
        retry_quiz(system.store, "local-user", "net", new["job_id"])


def test_queue_api_idempotency_scope_owner_and_restart(draft_case):
    system, context, _, _ = draft_case
    body = {"quiz_input": complete_input(total_score=10).model_dump(mode="json")}
    with TestClient(create_app(system.settings, system)) as client:
        first = client.post(
            "/api/courses/net/quiz-jobs", json=body, headers={"Idempotency-Key": "one"}
        )
        assert first.status_code == 202
        second = client.post(
            "/api/courses/net/quiz-jobs", json=body, headers={"Idempotency-Key": "one"}
        )
        assert second.json()["job_id"] == first.json()["job_id"]
        changed = deepcopy(body)
        changed["quiz_input"]["total_score"] = 20
        assert (
            client.post(
                "/api/courses/net/quiz-jobs", json=changed, headers={"Idempotency-Key": "one"}
            ).status_code
            == 409
        )
        assert client.post("/api/courses/net/quiz-jobs", json={"quiz_input": {}}).status_code == 422
        job_id = first.json()["job_id"]
    with TestClient(create_app(system.settings, system)) as restarted:
        assert restarted.get(f"/api/courses/net/quiz-jobs/{job_id}").json()["status"] == "queued"
    with pytest.raises(DomainNotFound):
        from final_review.quiz_jobs import owned_job

        owned_job(system.store, "other-user", "net", job_id)
    # A conversation restriction is an upper bound for the direct job endpoint too.
    system.store.put(
        "conversation",
        stable_key("net", "restricted"),
        {
            "user_id": "local-user",
            "course_id": "net",
            "conversation_id": "restricted",
            "chat_scope": {"source_document_ids": [], "chapter": "UDP"},
        },
    )
    with TestClient(create_app(system.settings, system)) as client:
        assert (
            client.post(
                "/api/courses/net/quiz-jobs", json={**body, "conversation_id": "restricted"}
            ).status_code
            == 409
        )


def test_removed_source_keeps_question_reference_excerpt(draft_case):
    system, _, model, job, _ = queue_case(draft_case)
    process_quiz_job(system.store, model, system.settings, job)
    result = system.store.get("quiz_job", job["job_id"])["result"]
    domain = DomainService(system.store, "local-user")
    preview = domain.material_deletion_preview("m3-ppt", "net")
    domain.delete_material("m3-ppt", preview["confirmation_id"], "normal")
    paper = read_quiz(system.store, "local-user", "net", result["asset_id"], result["revision_id"])
    ref = paper["revision"]["questions"][0]["references"][0]
    assert ref["quote"] == "同步双方初始序列号" and not ref["available"]


def test_all_six_types_and_ai_supplement_policy_use_generation_contract(draft_case):
    system, context, _, _ = draft_case
    data = context.config.model_dump(mode="json")
    data.update(
        blueprint=[
            {"question_type": kind, "question_count": 1}
            for kind in (
                "choice",
                "true_false",
                "fill_blank",
                "short_answer",
                "calculation",
                "proof",
            )
        ],
        question_count=6,
        total_score=60,
    )
    config = type(context.config).model_validate(data)
    generated = generate_quiz(system.store, system.model, config)
    assert len(generated["draft"]["questions"]) == 6
    assert sum(q["score"] for q in generated["draft"]["questions"]) == 60


def test_real_sdk_generation_review_and_repair_roundtrip(draft_case):
    system, context, plan, payload = draft_case
    tool_calls = []

    def handler(request):
        request_data = json.loads(request.content)
        name = request_data["tools"][0]["function"]["name"]
        tool_calls.append(name)
        if name == "QuizDraftPlan":
            value = plan
        elif name == "QuizDraftPayload":
            value = payload
        else:
            # Force a semantic repair once; all calls use actual LangChain/SDK encoding.
            failed = tool_calls.count("QuizSemanticReview") == 1
            value = {
                "items": [
                    {
                        "question_id": "q1",
                        "supported": not failed,
                        "issues": ["解析需明确序列号同步"] if failed else [],
                    }
                ]
            }
        return completion(name, value)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = ReviewModel(
            ChatOpenAI(
                model="fixture",
                api_key="test-key",
                base_url="https://example.invalid/v1",
                http_client=client,
                max_retries=0,
            )
        )
        result = generate_quiz(system.store, model, context.config)
    assert tool_calls == [
        "QuizDraftPlan",
        "QuizDraftPayload",
        "QuizSemanticReview",
        "QuizDraftPayload",
        "QuizSemanticReview",
    ]
    assert result["audit"][-1]["issues"] == []


def test_authorized_ai_supplement_is_labelled_and_still_reviewed(draft_case):
    system, context, plan, payload = draft_case
    config = context.config.model_copy(update={"allow_ai_supplement": True})
    payload["questions"][0].update(provenance="ai_supplement", citations=[])
    calls = []

    def review(_ctx, draft):
        calls.append(draft["questions"][0]["source_label"])
        return {"items": [{"question_id": "q1", "supported": True, "issues": []}]}

    generated = generate_quiz(system.store, model_for(plan, payload, verdict=review), config)
    assert calls == ["AI补充"]
    assert generated["draft"]["questions"][0]["references"] == []
    with pytest.raises(QuizGenerationError):
        generate_quiz(system.store, model_for(plan, payload), context.config)


def test_plan_and_candidate_share_one_repair_budget(draft_case):
    system, context, plan, payload = draft_case
    plans, reviews = [], []
    model = model_for(plan, payload)

    def make_plan(*_args, **_kwargs):
        plans.append(1)
        candidate = deepcopy(plan)
        if len(plans) == 1:
            candidate["allocations"][0]["question_count"] = 2
        return candidate

    def review(*_args):
        reviews.append(1)
        return {"items": [{"question_id": "q1", "supported": False, "issues": ["答案错误"]}]}

    model.quiz_draft_plan = make_plan
    model.review_quiz_draft = review
    with pytest.raises(QuizGenerationError):
        generate_quiz(system.store, model, context.config, max_repairs=2)
    assert len(plans) == 2 and len(reviews) == 2


def test_lost_lease_during_review_cannot_publish(draft_case):
    system, _, model, job, _ = queue_case(draft_case)

    def verdict(*_args):
        current = system.store.get("quiz_job", job["job_id"])
        current["attempts"] += 1
        system.store.put("quiz_job", job["job_id"], current)
        return {"items": [{"question_id": "q1", "supported": True, "issues": []}]}

    model.review_quiz_draft = verdict
    process_quiz_job(system.store, model, system.settings, job)
    assert not system.store.scan("learning_asset", {"course_id": "net"})
    assert system.store.get("quiz_job", job["job_id"])["status"] == "running"


def test_repeated_chat_submission_returns_completed_job_without_restoring_active_pointer(
    draft_case,
):
    system, _, plan, payload = draft_case
    system.settings.chat_models = [
        ChatModelConfig(
            id="test",
            label="测试模型",
            model="test-model",
            base_url="https://example.invalid/v1",
            api_key="test-key",
        )
    ]
    body = {
        "course_id": "net",
        "conversation_id": "repeat",
        "message": "确认试题配置",
        "quiz_input": complete_input(total_score=10).model_dump(mode="json"),
    }
    with TestClient(create_app(system.settings, system)) as client:
        first = client.post("/api/chat/dispatch", json=body, headers={"Idempotency-Key": "repeat"})
        assert first.json()["status"] == "queued"
        process_quiz_job(
            system.store, model_for(plan, payload), system.settings, system.store.claim_quiz_job()
        )
        second = client.post("/api/chat/dispatch", json=body, headers={"Idempotency-Key": "repeat"})
        assert second.json()["status"] == "succeeded"
        assert first.json()["quiz_job"]["job_id"] == second.json()["quiz_job"]["job_id"]
        history = client.get("/api/courses/net/conversations/repeat/messages").json()
        assert history["active_quiz"] is None
        assert len(history["items"]) == 3
