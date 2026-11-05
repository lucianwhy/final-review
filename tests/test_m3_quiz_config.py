import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from final_review.agent import FinalReviewAgent, SessionConflict
from final_review.api import create_app
from final_review.config import ChatModelConfig
from final_review.domain import DomainConflict, DomainNotFound, DomainService
from final_review.quiz_config import merge_quiz_input, resolve_quiz_config
from final_review.schemas import (
    AgentRequest,
    ExamCreate,
    MaterialInput,
    QuizInput,
    ResumeQuizRequest,
)
from final_review.storage import stable_key


@pytest.fixture
def quiz_system(system):
    system.store.put(
        "course",
        "net",
        {
            "course_id": "net",
            "user_id": "local-user",
            "name": "网络",
            "status": "active",
        },
    )
    system.kb.ingest(
        MaterialInput(
            document_id="m3-ppt",
            course_id="net",
            title="TCP课件",
            chapter="TCP",
            source_type="teacher_ppt",
            markdown="TCP 三次握手同步双方初始序列号。",
        ),
        user_id="local-user",
    )
    return system


def complete_input(**changes):
    return QuizInput.model_validate(
        {
            "scope_mode": "chapter",
            "chapter": "TCP",
            "blueprint": [{"question_type": "short_answer", "question_count": 1}],
            "duration_mode": "untimed",
            "difficulty": "basic",
            "include_imported_questions": False,
            "source_document_ids": ["m3-ppt"],
            "allow_ai_supplement": False,
            **changes,
        }
    )


def resolve(system, input, **kwargs):
    return resolve_quiz_config(system.store, "local-user", "net", input, **kwargs)


def test_missing_boundaries_do_not_invent_defaults(quiz_system):
    result = resolve(quiz_system, QuizInput(chapter="TCP"))
    assert result.status == "needs_clarification"
    assert set(result.missing) == {
        "blueprint",
        "duration_mode",
        "difficulty",
        "include_imported_questions",
        "source_document_ids",
        "allow_ai_supplement",
    }
    assert result.config is None
    assert quiz_system.model.retrieval_calls == 0


@pytest.mark.parametrize(
    "changes,missing,conflict",
    [
        ({"duration_mode": None}, "duration_mode", None),
        ({"duration_mode": "timed"}, "duration_minutes", None),
        ({"duration_mode": "untimed", "duration_minutes": 30}, None, "不限时"),
        ({"emphasis": ["TCP"], "excluded_topics": ["TCP"]}, None, "冲突"),
        ({"scope_mode": "knowledge_points", "knowledge_points": []}, "knowledge_points", None),
        ({"include_imported_questions": True}, None, "尚不支持"),
        ({"source_types": ["past_exam"]}, None, "没有可用资料"),
        ({"source_types": ["ai_supplement"]}, None, "不是资料类型"),
    ],
)
def test_missing_and_conflicting_input(quiz_system, changes, missing, conflict):
    result = resolve(quiz_system, complete_input(**changes))
    assert result.status == "needs_clarification"
    if missing:
        assert missing in result.missing
    if conflict:
        assert any(conflict in item for item in result.conflicts)


@pytest.mark.parametrize(
    "blueprint",
    [
        [{"question_type": "short_answer", "question_count": 0}],
        [{"question_type": "short_answer", "question_count": True}],
        [{"question_type": "short_answer", "question_count": 101}],
        [
            {"question_type": "choice", "question_count": 60},
            {"question_type": "proof", "question_count": 60},
        ],
        [
            {"question_type": "choice", "question_count": 1},
            {"question_type": "choice", "question_count": 1},
        ],
    ],
)
def test_invalid_blueprints_rejected(blueprint):
    with pytest.raises(ValidationError):
        QuizInput(blueprint=blueprint)


def test_exam_prefill_explicit_input_wins_and_exam_can_be_partial(quiz_system):
    exam = DomainService(quiz_system.store, "local-user").create_exam(
        "net",
        ExamCreate(
            name="期末",
            blueprint=[{"question_type": "choice", "question_count": 8, "score": 2}],
            emphasis=["TCP"],
            generation_preferences={"difficulty": "advanced", "duration_minutes": 30},
        ).model_dump(mode="json"),
    )
    result = resolve(quiz_system, complete_input(exam_id=exam["exam_id"], emphasis=[]))
    assert result.config.difficulty == "basic"
    assert result.config.duration_mode == "untimed"
    assert result.config.blueprint[0].question_type == "short_answer"
    assert result.config.emphasis == []
    assert result.config.exam_updated_at == exam["updated_at"]
    partial = resolve(quiz_system, QuizInput(exam_id=exam["exam_id"]))
    assert partial.status == "needs_clarification"
    assert "blueprint" not in partial.missing
    assert partial.quiz_input["difficulty"] == "advanced"
    exam2 = DomainService(quiz_system.store, "local-user").create_exam(
        "net", ExamCreate(name="补考").model_dump(mode="json")
    )
    assert "blueprint" in resolve(quiz_system, QuizInput(exam_id=exam2["exam_id"])).missing


@pytest.mark.parametrize("resource", ["course", "exam", "document"])
def test_foreign_resources_return_not_found(quiz_system, resource):
    if resource in {"course", "document"}:
        key = "net" if resource == "course" else "m3-ppt"
        row = quiz_system.store.get(resource, key)
        row["user_id"] = "other-user"
        quiz_system.store.put(resource, key, row)
        input = complete_input()
    else:
        quiz_system.store.put(
            "exam",
            "foreign",
            {
                "user_id": "other-user",
                "course_id": "net",
                "status": "active",
            },
        )
        input = complete_input(exam_id="foreign")
    with pytest.raises(DomainNotFound):
        resolve(quiz_system, input)


def test_cross_course_exam_deleted_course_and_archived_exam(quiz_system):
    quiz_system.store.put(
        "exam",
        "wrong-course",
        {
            "user_id": "local-user",
            "course_id": "elsewhere",
            "status": "active",
        },
    )
    quiz_system.store.put(
        "course",
        "elsewhere",
        {
            "user_id": "local-user",
            "course_id": "elsewhere",
            "status": "active",
        },
    )
    with pytest.raises(DomainNotFound):
        resolve(quiz_system, complete_input(exam_id="wrong-course"))
    exam = DomainService(quiz_system.store, "local-user").create_exam(
        "net", ExamCreate(name="期末").model_dump(mode="json")
    )
    DomainService(quiz_system.store, "local-user").archive_exam(exam["exam_id"])
    with pytest.raises(DomainConflict, match="归档"):
        resolve(quiz_system, complete_input(exam_id=exam["exam_id"]))
    course = quiz_system.store.get("course", "net")
    course["status"] = "deleted"
    quiz_system.store.put("course", "net", course)
    with pytest.raises(DomainConflict, match="删除"):
        resolve(quiz_system, complete_input())


def test_all_sources_resolve_within_conversation_ceiling(quiz_system):
    scope = {"source_document_ids": ["m3-ppt"], "chapter": "TCP"}
    result = resolve(quiz_system, complete_input(source_document_ids=[]), scope=scope)
    assert result.config.source_document_ids == ["m3-ppt"]
    assert set(result.config.material_versions) == {"m3-ppt"}
    narrower = resolve(
        quiz_system,
        complete_input(
            scope_mode="knowledge_points",
            knowledge_points=["三次握手"],
        ),
        scope=scope,
    )
    assert narrower.status == "ready"
    forbidden = resolve(quiz_system, complete_input(chapter="UDP"), scope=scope)
    assert forbidden.status == "needs_clarification"
    assert any("章节" in item for item in forbidden.conflicts)
    document = quiz_system.store.get("document", "m3-ppt")
    document["parse_status"] = "running"
    quiz_system.store.put("document", "m3-ppt", document)
    with pytest.raises(DomainConflict, match="就绪"):
        resolve(quiz_system, complete_input())


def test_agent_multiturn_restart_and_no_generation(quiz_system):
    result = quiz_system.invoke(
        AgentRequest(
            course_id="net",
            session_id="formal",
            message="正式试卷",
            quiz_input={"chapter": "TCP"},
        ),
        "local-user",
    )
    assert result.status == "needs_input"
    restarted = FinalReviewAgent(
        quiz_system.store, quiz_system.kb, quiz_system.model, quiz_system.settings
    )
    result = restarted.resume_quiz(
        ResumeQuizRequest(
            course_id="net",
            session_id="formal",
            quiz_input={"difficulty": "basic"},
        ),
        "local-user",
    )
    assert result.quiz_config["difficulty"] == "basic"
    assert "difficulty" not in result.prompt["required"]
    assert restarted.read("net", "formal").quiz_config["difficulty"] == "basic"
    result = restarted.resume_quiz(
        ResumeQuizRequest(
            course_id="net",
            session_id="formal",
            quiz_input=complete_input(),
        ),
        "local-user",
    )
    assert result.status == "configured"
    assert result.quiz_config["question_count"] == 1
    assert not result.questions and not result.draft
    assert quiz_system.model.retrieval_calls == 0
    assert not quiz_system.store.scan("quiz_revision_payload", {"course_id": "net"})
    assert restarted.recover("net", "formal") == result


def test_bad_resume_preserves_pending_configuration(quiz_system):
    quiz_system.invoke(
        AgentRequest(
            course_id="net",
            session_id="formal",
            message="试卷",
            intent="quiz",
            quiz_input={},
        ),
        "local-user",
    )
    with pytest.raises(SessionConflict, match="所有者"):
        quiz_system.resume_quiz(
            ResumeQuizRequest(
                course_id="net",
                session_id="formal",
                quiz_input={},
            ),
            "other-user",
        )
    with pytest.raises(DomainNotFound):
        quiz_system.resume_quiz(
            ResumeQuizRequest(
                course_id="net",
                session_id="formal",
                quiz_input={"source_document_ids": ["missing"]},
            ),
            "local-user",
        )
    assert quiz_system.recover("net", "formal").status == "needs_input"


def test_explicit_false_and_null_are_preserved_when_merging():
    merged = merge_quiz_input(
        {"allow_ai_supplement": True, "duration_minutes": 30},
        QuizInput(allow_ai_supplement=False, duration_minutes=None),
    )
    assert merged.allow_ai_supplement is False
    assert merged.duration_minutes is None


def test_resolve_api_and_chat_configuration_survive_reload(quiz_system):
    quiz_system.settings.chat_models = [
        ChatModelConfig(
            id="test",
            label="测试模型",
            model="test-model",
            base_url="https://example.invalid/v1",
            api_key="test-key",
        )
    ]
    with TestClient(create_app(quiz_system.settings, quiz_system)) as client:
        result = client.post("/api/courses/net/quiz-config/resolve", json={"quiz_input": {}})
        assert result.status_code == 200 and result.json()["status"] == "needs_clarification"
        assert (
            client.post(
                "/api/courses/net/quiz-config/resolve",
                json={
                    "quiz_input": {
                        "blueprint": [{"question_type": "choice", "question_count": 101}]
                    },
                },
            ).status_code
            == 422
        )
        result = client.post(
            "/api/chat/dispatch",
            json={
                "course_id": "net",
                "conversation_id": "formal-chat",
                "message": "出一套试卷",
                "quiz_input": {"chapter": "TCP"},
            },
        )
        assert result.status_code == 200 and result.json()["status"] == "needs_input"
        conversation = quiz_system.store.get("conversation", stable_key("net", "formal-chat"))
        assert conversation["pending_quiz"]["quiz_input"] == {
            "chapter": "TCP",
            "scope_mode": "chapter",
        }
    with TestClient(create_app(quiz_system.settings, quiz_system)) as client:
        additions = complete_input().model_dump(mode="json", exclude_unset=True)
        additions.pop("chapter")
        response = client.post(
            "/api/chat/dispatch",
            json={
                "course_id": "net",
                "conversation_id": "formal-chat",
                "message": "补充配置",
                "quiz_input": additions,
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "queued"
        conversation = quiz_system.store.get("conversation", stable_key("net", "formal-chat"))
        assert conversation["quiz_config"]["chapter"] == "TCP"
        assert conversation["pending_quiz"] is None


def test_agent_natural_formal_request_cannot_use_legacy_profile(quiz_system):
    result = quiz_system.invoke(
        AgentRequest(
            course_id="net",
            session_id="formal-natural",
            intent="quiz",
            message="请出一套模拟卷",
            exam_profile={"question_types": ["short_answer"]},
        ),
        "local-user",
    )
    assert result.status == "needs_input"
    assert "blueprint" in result.prompt["required"]
    assert quiz_system.model.retrieval_calls == 0


@pytest.mark.parametrize(
    "preferences,blueprint,field",
    [
        ({"difficulty": "arbitrary"}, [], "difficulty"),
        (
            {},
            [
                {"question_type": "choice", "question_count": 60, "score": 1},
                {"question_type": "proof", "question_count": 60, "score": 1},
            ],
            "blueprint",
        ),
    ],
)
def test_invalid_exam_defaults_clarify_instead_of_provider_error(
    quiz_system, preferences, blueprint, field
):
    exam = DomainService(quiz_system.store, "local-user").create_exam(
        "net",
        ExamCreate(
            name="期末",
            blueprint=blueprint,
            generation_preferences=preferences,
        ).model_dump(mode="json"),
    )
    result = resolve(quiz_system, QuizInput(exam_id=exam["exam_id"]))
    assert result.status == "needs_clarification" and field in result.missing
    override = resolve(quiz_system, complete_input(exam_id=exam["exam_id"]))
    assert override.status == "ready"


def test_resolve_api_cannot_change_conversation_scope_on_failure(quiz_system):
    quiz_system.kb.ingest(
        MaterialInput(
            document_id="m3-outside",
            course_id="net",
            title="另一份课件",
            chapter="TCP",
            source_type="teacher_ppt",
            markdown="另一份课件讨论TCP连接。",
        ),
        user_id="local-user",
    )
    key = stable_key("net", "restricted")
    scope = {"source_document_ids": ["m3-ppt"], "chapter": "TCP"}
    quiz_system.store.put(
        "conversation",
        key,
        {
            "course_id": "net",
            "conversation_id": "restricted",
            "user_id": "local-user",
            "chat_scope": scope,
        },
    )
    with TestClient(create_app(quiz_system.settings, quiz_system)) as client:
        response = client.post(
            "/api/chat/dispatch",
            json={
                "course_id": "net",
                "conversation_id": "restricted",
                "message": "配置试卷",
                "source_document_ids": ["m3-outside"],
                "quiz_input": complete_input(source_document_ids=["m3-outside"]).model_dump(
                    mode="json"
                ),
            },
        )
        assert response.status_code == 409
        assert quiz_system.store.get("conversation", key)["chat_scope"] == scope
