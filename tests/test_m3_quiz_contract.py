import json
from copy import deepcopy

import httpx
import pytest
from langchain_openai import ChatOpenAI
from pydantic import ValidationError
from test_m3_quiz_config import complete_input
from test_m3_quiz_config import quiz_system as quiz_system

from final_review.course_chat import CourseMaterials
from final_review.domain import DomainConflict
from final_review.llm import ReviewModel
from final_review.quiz_config import resolve_quiz_config
from final_review.quiz_contract import (
    QuizDraftPlan,
    QuizGenerationContext,
    build_quiz_context,
    validate_quiz_draft,
)
from final_review.schemas import MaterialInput


@pytest.fixture
def draft_case(quiz_system):
    config = resolve_quiz_config(
        quiz_system.store, "local-user", "net", complete_input(total_score=10)
    ).config
    materials = CourseMaterials(quiz_system.store, "net", "local-user")
    evidence = [
        item.model_dump(mode="json") for item in materials.chunks(materials.select(["m3-ppt"]))
    ]
    context = build_quiz_context(quiz_system.store, config, evidence)
    plan = {
        "allocations": [
            {
                "knowledge_point": "三次握手",
                "question_type": "short_answer",
                "question_count": 1,
                "importance": 5,
                "allocation_reason": "老师课件强调同步初始序列号，属于核心考点。",
            }
        ]
    }
    payload = {
        "title": "TCP模拟卷",
        "questions": [
            {
                "id": "q1",
                "order": 1,
                "knowledge_point": "三次握手",
                "question_type": "short_answer",
                "stem": "三次握手的作用是什么？",
                "score": 10,
                "reference_answer": "同步初始序列号。",
                "explanation": "建立连接前需要同步序列号。",
                "core_point": "连接建立",
                "must_include": ["同步初始序列号"],
                "common_mistakes": ["只写建立连接"],
                "scoring_tips": "写出同步序列号这一关键词。",
                "provenance": "source",
                "citations": [{"chunk_id": evidence[0]["chunk_id"], "quote": "同步双方初始序列号"}],
            }
        ],
    }
    return quiz_system, context, plan, payload


def test_valid_draft_has_server_derived_sources_and_no_writes(draft_case):
    system, context, plan, payload = draft_case
    result = validate_quiz_draft(context, plan, payload)
    assert result.valid and not result.issues
    question = result.validated_draft["questions"][0]
    assert question["source_label"] == "老师PPT"
    assert question["references"][0]["file_name"] == "TCP课件"
    assert (
        question["references"][0]["material_version_id"] == context.evidence[0].material_version_id
    )
    assert not system.store.scan("quiz_revision_payload", {"course_id": "net"})


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"question_type": "proof"}, "question_count"),
        ({"knowledge_point": "UDP"}, "allocation_count"),
        ({"score": 9}, "total_score"),
        ({"score": float("nan")}, "schema"),
        ({"score": 0}, "schema"),
        ({"order": 2}, "order"),
        ({"citations": []}, "provenance"),
        ({"citations": [{"chunk_id": "outside", "quote": "原文"}]}, "citation"),
        ({"provenance": "ai_supplement", "citations": []}, "ai_policy"),
        ({"provenance": "synthesis"}, "provenance"),
        ({"source_label": "历年真题"}, "schema"),
        ({"options": ["A", "B"]}, "options"),
        ({"reference_answer": ""}, "schema"),
    ],
)
def test_invalid_draft_never_returns_publishable_payload(draft_case, changes, code):
    _, context, plan, payload = draft_case
    payload["questions"][0].update(changes)
    result = validate_quiz_draft(context, plan, payload)
    assert not result.valid and result.validated_draft is None
    assert code in {issue.code for issue in result.issues}


def test_invalid_quote_and_duplicate_questions(draft_case):
    _, context, plan, payload = draft_case
    payload["questions"][0]["citations"][0]["quote"] = "不存在的原文"
    payload["questions"].append(deepcopy(payload["questions"][0]))
    result = validate_quiz_draft(context, plan, payload)
    assert {"citation", "duplicate_id", "duplicate_stem", "order"} <= {
        issue.code for issue in result.issues
    }


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"question_count": 2}, "plan_count"),
        ({"allocation_reason": ""}, "schema"),
        ({"importance": 6}, "schema"),
    ],
)
def test_plan_cannot_override_configuration(draft_case, changes, code):
    _, context, plan, payload = draft_case
    plan["allocations"][0].update(changes)
    result = validate_quiz_draft(context, plan, payload)
    assert code in {issue.code for issue in result.issues}


@pytest.mark.parametrize(
    "kind,fields,code",
    [
        ("choice", {"options": ["同步", "无关"], "correct_option": 0}, None),
        ("choice", {"options": ["同步", "无关"], "correct_option": 5}, "choice_structure"),
        ("true_false", {"boolean_answer": True}, None),
        ("true_false", {}, "boolean_answer"),
        ("fill_blank", {"accepted_answers": ["同步初始序列号"]}, None),
        ("fill_blank", {}, "accepted_answers"),
        ("calculation", {}, None),
        ("proof", {}, None),
    ],
)
def test_question_types_have_required_structure(draft_case, kind, fields, code):
    _, context, plan, payload = draft_case
    data = context.model_dump(mode="json")
    data["config"]["blueprint"][0]["question_type"] = kind
    context = QuizGenerationContext.model_validate(data)
    plan["allocations"][0]["question_type"] = kind
    payload["questions"][0].update(question_type=kind, **fields)
    result = validate_quiz_draft(context, plan, payload)
    if code:
        assert code in {issue.code for issue in result.issues}
    else:
        assert result.valid


def test_synthesis_retains_all_sources_instead_of_highest_priority(draft_case):
    system, context, plan, payload = draft_case
    system.kb.ingest(
        MaterialInput(
            document_id="m3-exam",
            course_id="net",
            title="历年考试",
            chapter="TCP",
            source_type="past_exam",
            markdown="真题要求解释三次握手同步双方初始序列号。",
        ),
        user_id="local-user",
    )
    config = resolve_quiz_config(
        system.store,
        "local-user",
        "net",
        complete_input(
            source_document_ids=["m3-ppt", "m3-exam"],
            total_score=10,
        ),
    ).config
    materials = CourseMaterials(system.store, "net", "local-user")
    evidence = [
        item.model_dump(mode="json")
        for item in materials.chunks(materials.select(config.source_document_ids))
    ]
    context = build_quiz_context(system.store, config, evidence)
    payload["questions"][0].update(
        provenance="synthesis",
        citations=[
            {"chunk_id": item["chunk_id"], "quote": "同步双方初始序列号"} for item in evidence
        ],
    )
    result = validate_quiz_draft(context, plan, payload)
    assert result.valid
    question = result.validated_draft["questions"][0]
    assert question["source_label"].startswith("综合改编")
    assert set(question["source_types"]) == {"past_exam", "teacher_ppt"}
    assert len(question["references"]) == 2


def test_ai_supplement_requires_opt_in_and_never_has_material_references(draft_case):
    _, context, plan, payload = draft_case
    data = context.model_dump(mode="json")
    data["config"]["allow_ai_supplement"] = True
    context = QuizGenerationContext.model_validate(data)
    payload["questions"][0].update(provenance="ai_supplement", citations=[])
    result = validate_quiz_draft(context, plan, payload)
    assert result.valid
    assert result.validated_draft["questions"][0]["source_label"] == "AI补充"
    assert result.validated_draft["questions"][0]["references"] == []
    payload["questions"][0]["citations"] = [
        {
            "chunk_id": context.evidence[0].chunk_id,
            "quote": "同步双方初始序列号",
        }
    ]
    assert not validate_quiz_draft(context, plan, payload).valid


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", "foreign"),
        ("course_id", "foreign"),
        ("document_id", "foreign"),
        ("material_version_id", "stale"),
    ],
)
def test_context_rejects_out_of_scope_evidence(draft_case, field, value):
    _, context, _, _ = draft_case
    data = context.model_dump(mode="json")
    data["evidence"][0][field] = value
    with pytest.raises(ValidationError):
        QuizGenerationContext.model_validate(data)


def test_context_factory_uses_stored_metadata_and_rejects_changed_text(draft_case):
    system, context, _, _ = draft_case
    evidence = [item.model_dump(mode="json") for item in context.evidence]
    evidence[0]["source_type"] = "past_exam"
    rebuilt = build_quiz_context(system.store, context.config, evidence)
    assert rebuilt.evidence[0].source_type == "teacher_ppt"
    evidence[0]["content"] = "捏造内容"
    with pytest.raises(DomainConflict, match="原文"):
        build_quiz_context(system.store, context.config, evidence)
    document = system.store.get("document", "m3-ppt")
    document["material_version_id"] = "new-version"
    system.store.put("document", "m3-ppt", document)
    with pytest.raises(DomainConflict, match="版本"):
        build_quiz_context(
            system.store,
            context.config,
            [item.model_dump(mode="json") for item in context.evidence],
        )


def test_knowledge_scope_and_exclusions_checked(draft_case):
    _, context, plan, payload = draft_case
    data = context.model_dump(mode="json")
    data["config"].update(scope_mode="knowledge_points", knowledge_points=["UDP"])
    context = QuizGenerationContext.model_validate(data)
    result = validate_quiz_draft(context, plan, payload)
    assert "knowledge_scope" in {item.code for item in result.issues}
    data["config"].update(scope_mode="chapter", knowledge_points=[], excluded_topics=["三次握手"])
    context = QuizGenerationContext.model_validate(data)
    result = validate_quiz_draft(context, plan, payload)
    assert "excluded_topic" in {item.code for item in result.issues}


def test_formal_llm_contract_roundtrip_uses_frozen_config(draft_case):
    _, context, plan, payload = draft_case
    requests = []

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        tool_name = data["tools"][0]["function"]["name"]
        assert tool_name in {"QuizDraftPlan", "QuizDraftPayload"}
        assert '"contract_version": 1' in data["messages"][-1]["content"]
        assert "material_versions" in data["messages"][-1]["content"]
        return httpx.Response(
            200,
            json={
                "id": "test-formal",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-test",
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": json.dumps(
                                            plan if tool_name == "QuizDraftPlan" else payload,
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = ReviewModel(
            ChatOpenAI(
                model="test",
                api_key="test",
                base_url="http://test/v1",
                http_client=client,
            )
        )
        generated_plan = model.quiz_draft_plan(context)
        generated = model.quiz_draft(context, QuizDraftPlan.model_validate(generated_plan))
    assert len(requests) == 2
    assert validate_quiz_draft(context, generated_plan, generated).valid
