"""M2-05/06 acceptance against an explicitly disposable PostgreSQL database."""

import os
from types import SimpleNamespace

import pytest
from conftest import ScriptedModel, TestEmbeddings
from fastapi.testclient import TestClient
from test_postgres_migrations import database_url  # noqa: F401

from final_review.agent import FinalReviewAgent
from final_review.api import create_app
from final_review.config import ChatModelConfig, Settings
from final_review.llm import build_review_model
from final_review.migrations import apply_migrations
from final_review.note_jobs import process_note_job
from final_review.postgres import PostgresStore
from final_review.rag import KnowledgeBase
from final_review.schemas import MaterialInput

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("live_model", [False, True], ids=["scripted", "live"])
def test_note_history_restart_owner_scope_and_cancel(database_url, monkeypatch, live_model):  # noqa: F811
    if live_model and os.environ.get("RUN_M2_LIVE") != "1":
        pytest.skip("set RUN_M2_LIVE=1 to verify configured live note generation")
    apply_migrations(database_url)

    class ChatFixture:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="可以继续聊天"))]
            )

    monkeypatch.setattr("final_review.api.OpenAI", ChatFixture)
    monkeypatch.setattr(
        "final_review.api.build_models",
        lambda *_args, **_kwargs: (ScriptedModel(), TestEmbeddings()),
    )
    monkeypatch.setattr(
        "final_review.api.build_review_model", lambda *_args, **_kwargs: ScriptedModel()
    )
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        auth_cookie_secure=False,
        embedding_dimensions=3,
        material_vision_enabled=False,
        llm_api_key="fixture",
        embedding_api_key="fixture",
        chat_models=[
            ChatModelConfig(
                id="fixture",
                label="Fixture",
                model="fixture",
                base_url="https://example.invalid/v1",
                api_key="fixture",
            )
        ],
    )
    model = ScriptedModel()
    if live_model:
        configured = Settings()
        config = ChatModelConfig(
            id="acceptance",
            label=configured.llm_model,
            model=configured.llm_model,
            base_url=configured.llm_base_url,
            api_key=configured.llm_api_key,
        )
        model = build_review_model(config, configured, note_generation=True)
        monkeypatch.setattr(
            "final_review.api.build_models", lambda *_args, **_kwargs: (model, TestEmbeddings())
        )
        monkeypatch.setattr("final_review.api.build_review_model", lambda *_args, **_kwargs: model)

    def application():
        store = PostgresStore(database_url)
        kb = KnowledgeBase(store, TestEmbeddings(), settings)
        agent = FinalReviewAgent(store, kb, model, settings)
        return create_app(settings), store, agent

    app, store, agent = application()
    try:
        with TestClient(app) as owner, TestClient(app) as other:
            for client, email in [(owner, "owner@m2.test"), (other, "other@m2.test")]:
                assert (
                    client.post(
                        "/api/auth/sign-up",
                        json={
                            "email": email,
                            "password": "acceptance-password",
                        },
                    ).status_code
                    == 200
                )
            user_id = owner.get("/api/auth/me").json()["id"]
            course = owner.post("/api/courses", json={"name": "网络"}).json()["course_id"]
            second = owner.post("/api/courses", json={"name": "数学"}).json()["course_id"]
            token = store.bind_user(user_id)
            try:
                doc = agent.kb.ingest(
                    MaterialInput(
                        course_id=course,
                        title="TCP 讲义",
                        chapter="TCP",
                        source_type="teacher_ppt",
                        markdown="# TCP\nTCP 三次握手同步双方初始序列号并确认双方收发能力。",
                    ),
                    user_id=user_id,
                )
            finally:
                store.reset_user(token)
            payload = {
                "course_id": course,
                "session_id": "m2-note",
                "message": "生成笔记",
                "intent": "note",
            }
            start = owner.post("/agent/invoke?conversation_id=mixed", json=payload)
            assert start.status_code == 200, start.text
            assert start.json()["status"] == "needs_input"
            cookie = dict(owner.cookies)

        # A new application/agent loads the persisted interrupt and timeline.
        restarted_app, restarted_store, restarted_agent = application()
        try:
            with TestClient(restarted_app) as owner, TestClient(app) as other:
                owner.cookies.update(cookie)
                assert (
                    other.post(
                        "/api/auth/sign-in",
                        json={
                            "email": "other@m2.test",
                            "password": "acceptance-password",
                        },
                    ).status_code
                    == 200
                )
                history_url = f"/api/courses/{course}/conversations/mixed/messages"
                assert owner.get(history_url).json()["active_note"]["status"] == "needs_input"
                resume = {
                    "course_id": course,
                    "session_id": "m2-note",
                    "note_input": {
                        "note_type": "key_points",
                        "duration_minutes": 10,
                        "source_document_ids": [doc["document_id"]],
                    },
                }
                for client, target_course in [(other, course), (owner, second)]:
                    assert (
                        client.get(
                            f"/api/courses/{target_course}/conversations/mixed/messages"
                        ).status_code
                        == 404
                    )
                    for endpoint, body in [
                        (
                            "/agent/resume-note?conversation_id=mixed",
                            {**resume, "course_id": target_course},
                        ),
                        (
                            f"/agent/recover?course_id={target_course}&session_id=m2-note&conversation_id=mixed",
                            None,
                        ),
                        (
                            "/agent/cancel-note?conversation_id=mixed",
                            {"course_id": target_course, "session_id": "m2-note"},
                        ),
                    ]:
                        assert client.post(endpoint, json=body).status_code == 404
                queued = owner.post(
                    "/agent/queue-note?conversation_id=mixed&event_id=submit", json=resume
                )
                assert queued.status_code == 200, queued.text
                assert (
                    owner.post(
                        "/agent/queue-note?conversation_id=mixed&event_id=submit", json=resume
                    ).json()
                    == queued.json()
                )
                job = restarted_store.claim_note_job()
                assert job["job_id"] == queued.json()["job_id"]
                process_note_job(restarted_store, restarted_agent, job)
                history = owner.get(history_url).json()
                assert history["active_note"] is None
                draft = history["items"][-1]["draft"]
                assert (
                    owner.get(
                        f"/api/assets/{draft['asset_id']}/revisions/{draft['revision_id']}"
                    ).status_code
                    == 200
                )
                count = len(history["items"])
                recovered = owner.post(
                    f"/agent/recover?course_id={course}&session_id=m2-note&conversation_id=mixed"
                )
                assert recovered.status_code == 200, recovered.text
                assert recovered.json()["draft"] == draft
                assert len(owner.get(history_url).json()["items"]) == count
                assert (
                    owner.post(
                        "/api/chat",
                        json={
                            "course_id": course,
                            "conversation_id": "mixed",
                            "message": "继续讨论",
                            "mode": "direct",
                            "model_id": "fixture",
                        },
                    ).status_code
                    == 200
                )
                history = owner.get(history_url).json()
                assert [item["role"] for item in history["items"]] == [
                    "user",
                    "assistant",
                    "user",
                    "assistant",
                    "user",
                    "assistant",
                ]
                assert "可以继续聊天" in history["items"][-1]["content"]
                assert "通用知识" in history["items"][-1]["content"]
                assert history["items"][3]["draft"] == draft
                assert (
                    owner.get(f"/api/courses/{course}/conversations").json()["items"][0][
                        "conversation_id"
                    ]
                    == "mixed"
                )
                assert (
                    other.get(
                        f"/api/assets/{draft['asset_id']}/revisions/{draft['revision_id']}"
                    ).status_code
                    == 404
                )
                assert (
                    owner.post(
                        "/agent/invoke?conversation_id=mixed",
                        json={**payload, "session_id": "cancel-me"},
                    ).status_code
                    == 200
                )
                cancelled = {"course_id": course, "session_id": "cancel-me"}
                assert owner.post(
                    "/agent/cancel-note?conversation_id=mixed", json=cancelled
                ).json() == {"cancelled": True}
                assert owner.get(history_url).json()["active_note"] is None
                assert (
                    owner.post(
                        f"/agent/recover?course_id={course}&session_id=cancel-me&conversation_id=mixed"
                    ).status_code
                    == 409
                )
                assert (
                    owner.post(
                        "/agent/invoke?conversation_id=mixed",
                        json={**payload, "session_id": "new-note"},
                    ).json()["status"]
                    == "needs_input"
                )
                token = restarted_store.bind_user(user_id)
                try:
                    assert len(restarted_store.scan("learning_asset", {"course_id": course})) == 1
                finally:
                    restarted_store.reset_user(token)
        finally:
            restarted_store.close()
    finally:
        store.close()
