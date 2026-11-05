"""Large-paper, truncation, resume and lease-fencing regression proofs."""

from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from langchain_openai import ChatOpenAI
from quiz_fixture import fixture_draft, fixture_plan
from test_m3_quiz_config import quiz_system as quiz_system
from test_m3_quiz_contract import draft_case as draft_case

from final_review.config import ChatModelConfig, Settings
from final_review.llm import ModelOutputLimitError, ReviewModel, build_review_model
from final_review.quiz_generation import QuizGenerationError, generate_quiz
from final_review.quiz_jobs import enqueue_quiz, process_quiz_job, retry_quiz


def paper_config(context, count=15):
    data = context.config.model_dump(mode="json")
    data.update(
        blueprint=[{"question_type": "choice", "question_count": count}],
        question_count=count,
        total_score=20,
    )
    return type(context.config).model_validate(data)


def adapter(make_draft=fixture_draft):
    return SimpleNamespace(
        quiz_draft_plan=fixture_plan,
        quiz_draft=make_draft,
        review_quiz_draft=lambda _ctx, draft: {
            "items": [
                {"question_id": q["id"], "supported": True, "issues": []}
                for q in draft["questions"]
            ]
        },
        repair_quiz_draft=lambda ctx, plan, *_: fixture_draft(
            ctx,
            type(
                "Plan", (), {"allocations": [SimpleNamespace(**a) for a in plan["allocations"]]}
            )(),
        ),
    )


@pytest.mark.parametrize("count", [15, 60, 100])
def test_large_paper_has_fixed_ids_scores_and_bounded_calls(draft_case, count):
    system, context, *_ = draft_case
    calls = []

    def draft(ctx, plan):
        calls.append(ctx.config.question_count)
        assert ctx.config.question_count <= 5
        return fixture_draft(ctx, plan)

    model = adapter(draft)
    model.quiz_draft_plan = lambda ctx, **_: fixture_plan(ctx)
    result = generate_quiz(system.store, model, paper_config(context, count))
    questions = result["draft"]["questions"]
    assert len(questions) == count and len(calls) == (count + 4) // 5
    assert [q["id"] for q in questions] == [f"q{i}" for i in range(1, count + 1)]
    assert [q["order"] for q in questions] == list(range(1, count + 1))
    from decimal import Decimal

    assert sum(Decimal(str(q["score"])) for q in questions) == Decimal("20")
    reviews = [item for a in result["audit"] for item in (a.get("review") or {}).get("items", [])]
    assert [r["question_id"] for r in reviews] == [q["id"] for q in questions]


def test_output_truncation_splits_only_pending_batch(draft_case):
    system, context, *_ = draft_case
    sizes, saved = [], []

    def draft(ctx, plan):
        sizes.append(ctx.config.question_count)
        if ctx.config.question_count > 2:
            raise ModelOutputLimitError("truncated")
        return fixture_draft(ctx, plan)

    model = adapter(draft)
    model.quiz_draft_plan = lambda ctx, **_: fixture_plan(ctx)
    result = generate_quiz(
        system.store,
        model,
        paper_config(context, 5),
        save_checkpoint=lambda state: saved.append(deepcopy(state)),
    )
    assert sizes == [5, 2, 3, 1, 2]
    assert len(result["draft"]["questions"]) == 5
    assert len(saved[-1]["completed"]) == 3 and not saved[-1]["pending"]


def test_failed_job_retry_reuses_verified_batches(draft_case):
    system, context, *_ = draft_case
    calls = []
    failing = [True]

    def draft(ctx, plan):
        first = ctx.question_slots[0]["order"]
        calls.append(first)
        if first == 6 and failing[0]:
            raise RuntimeError("provider down")
        return fixture_draft(ctx, plan)

    model = adapter(draft)
    model.quiz_draft_plan = lambda ctx, **_: fixture_plan(ctx)
    config = paper_config(context, 10)
    queued = enqueue_quiz(system.store, config, model_id="default")
    process_quiz_job(system.store, model, system.settings, system.store.claim_quiz_job())
    failed = system.store.get("quiz_job", queued["job_id"])
    assert failed["status"] == "failed" and failed["progress"]["completed_questions"] == 5
    assert not system.store.scan("learning_asset", {"course_id": "net"})
    failing[0] = False
    retry_quiz(system.store, "local-user", "net", queued["job_id"])
    model.quiz_draft_plan = lambda *_args, **_: pytest.fail("plan must be reused")
    process_quiz_job(system.store, model, system.settings, system.store.claim_quiz_job())
    done = system.store.get("quiz_job", queued["job_id"])
    assert done["status"] == "succeeded" and done["progress"]["completed_questions"] == 10
    assert calls == [1, 6, 6]


def test_checkpoint_with_changed_context_is_rejected(draft_case):
    system, context, *_ = draft_case
    with pytest.raises(QuizGenerationError, match="变更"):
        generate_quiz(
            system.store,
            adapter(),
            context.config,
            checkpoint={"version": 1, "context_hash": "old"},
        )


def test_fractional_total_is_distributed_without_zero_or_negative_scores(draft_case):
    from decimal import Decimal

    system, context, *_ = draft_case
    config = paper_config(context, 100).model_copy(update={"total_score": 3.5})
    model = adapter()
    model.quiz_draft_plan = lambda ctx, **_: fixture_plan(ctx)
    questions = generate_quiz(system.store, model, config)["draft"]["questions"]
    scores = [Decimal(str(q["score"])) for q in questions]
    assert sum(scores) == Decimal("3.5")
    assert set(scores) == {Decimal("0.03"), Decimal("0.04")}


def test_length_response_is_classified_before_same_budget_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "id": "truncated",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "length",
                        "message": {"role": "assistant", "content": ""},
                    }
                ],
            },
        )

    from final_review.quiz_contract import QuizDraftPayload

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = ReviewModel(ChatOpenAI(model="test", api_key="test", http_client=client))
        with pytest.raises(ModelOutputLimitError, match="长度上限"):
            model.structured(QuizDraftPayload, "generate", {})
    assert len(calls) == 1


def test_quiz_worker_sets_explicit_budget_without_changing_chat():
    config = ChatModelConfig(
        id="test", label="test", model="test", base_url="http://test", api_key="test"
    )
    settings = Settings(_env_file=None, quiz_max_output_tokens=16384)
    assert build_review_model(config, settings, quiz_generation=True).model.max_tokens == 16384
    assert build_review_model(config, settings).model.max_tokens is None


def test_paper_review_regenerates_only_affected_batch(draft_case):
    system, context, *_ = draft_case
    calls, reviews = [], []

    def draft(ctx, plan):
        calls.append(ctx.question_slots[0]["order"])
        return fixture_draft(ctx, plan)

    def review(_ctx, _draft):
        reviews.append(1)
        return {
            "supported": len(reviews) > 1,
            "issues": [] if len(reviews) > 1 else ["第6题未覆盖要求的重点"],
            "affected_question_ids": [] if len(reviews) > 1 else ["q6"],
        }

    model = adapter(draft)
    model.quiz_draft_plan = lambda ctx, **_: fixture_plan(ctx)
    model.review_quiz_paper = review
    result = generate_quiz(system.store, model, paper_config(context, 10))
    assert calls == [1, 6, 6] and len(reviews) == 2
    assert [q["order"] for q in result["draft"]["questions"]] == list(range(1, 11))


def test_expired_worker_cannot_save_batch_checkpoint(draft_case):
    from datetime import UTC, datetime, timedelta

    system, context, *_ = draft_case
    queued = enqueue_quiz(system.store, paper_config(context, 10), model_id="default")
    old = system.store.claim_quiz_job()

    def draft(ctx, plan):
        current = system.store.get("quiz_job", old["job_id"])
        current["lease_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        system.store.put("quiz_job", old["job_id"], current)
        system.store.claim_quiz_job()
        return fixture_draft(ctx, plan)

    model = adapter(draft)
    model.quiz_draft_plan = lambda ctx, **_: fixture_plan(ctx)
    process_quiz_job(system.store, model, system.settings, old)
    current = system.store.get("quiz_job", queued["job_id"])
    assert current["status"] == "running" and current["attempts"] == 2
    assert not current["checkpoint"]["completed"]
    assert not system.store.scan("learning_asset", {"course_id": "net"})


def test_single_question_truncation_preserves_clear_failure(draft_case):
    system, context, *_ = draft_case

    def draft(*_):
        raise ModelOutputLimitError("truncated")

    model = adapter(draft)
    model.quiz_draft_plan = lambda ctx, **_: fixture_plan(ctx)
    with pytest.raises(QuizGenerationError, match="单题输出"):
        generate_quiz(system.store, model, paper_config(context, 1))
