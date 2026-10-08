"""Real PostgreSQL proof for M0-R1.

Set TEST_DATABASE_URL to an isolated disposable database.  The fixture drops
only its public schema, so this suite can never accidentally target DATABASE_URL.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest
from conftest import ScriptedModel, TestEmbeddings
from fastapi.testclient import TestClient
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from final_review.agent import FinalReviewAgent
from final_review.api import create_app
from final_review.checkpoints import SurrealSaver
from final_review.config import ChatModelConfig, Settings
from final_review.domain import DomainConflict, DomainNotFound, DomainService
from final_review.llm import ModelError
from final_review.material_jobs import process_material_job
from final_review.migrations import apply_migrations
from final_review.postgres import PostgresStore
from final_review.rag import KnowledgeBase
from final_review.schemas import AgentRequest, MaterialInput, NoteInput
from final_review.storage import stable_key

pytestmark = pytest.mark.integration
TEST_URL = os.environ.get("TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).parents[1] / "db" / "migrations"
USER = UUID("00000000-0000-0000-0000-000000000001")
SECOND_USER = UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def database_url():
    if not TEST_URL:
        pytest.skip("set TEST_DATABASE_URL to run real PostgreSQL migration tests")
    with psycopg.connect(TEST_URL, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    return TEST_URL


def _sql(connection, name: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
    connection.commit()


def _seed_course(connection, *, legacy: bool = False) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,%s)",
            (USER, "migration@example.test", "hash"),
        )
        cursor.execute(
            "INSERT INTO courses(record_key,user_id,course_id,data) VALUES ('course-key',%s,'course-1',%s)",
            (USER, Jsonb({"course_id": "course-1", "user_id": str(USER)})),
        )
        if legacy:
            cursor.execute(
                "INSERT INTO fast_quiz_sessions(record_key,user_id,course_id,data) VALUES ('legacy-key',%s,'course-1',%s)",
                (USER, Jsonb({"session_id": "legacy-session"})),
            )
            cursor.execute(
                "INSERT INTO attempts(record_key,user_id,course_id,data) VALUES ('attempt-key',%s,'course-1',%s)",
                (USER, Jsonb({"session_id": "legacy-session"})),
            )
    connection.commit()


def test_empty_database_runner_is_versioned_and_repeatable(database_url):
    assert apply_migrations(database_url) == [
        "001_database_auth.sql",
        "002_m0_domain_contracts.sql",
        "003_m0_database_hardening.sql",
        "004_m1_material_jobs.sql",
        "005_note_jobs.sql",
        "006_external_upload_dedup.sql",
        "007_note_review.sql",
        "008_note_exports.sql",
        "009_quiz_drafts.sql",
    ]
    assert apply_migrations(database_url) == []
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
        assert [row[0] for row in cursor.fetchall()] == [
            "001_database_auth.sql",
            "002_m0_domain_contracts.sql",
            "003_m0_database_hardening.sql",
            "004_m1_material_jobs.sql",
            "005_note_jobs.sql",
            "006_external_upload_dedup.sql",
            "007_note_review.sql",
            "008_note_exports.sql",
            "009_quiz_drafts.sql",
        ]


def test_note_worker_sees_job_created_after_empty_poll(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
    worker = PostgresStore(database_url)
    writer = PostgresStore(database_url)
    worker_token = worker.bind_user(str(USER))
    writer_token = writer.bind_user(str(USER))
    try:
        assert worker.claim_note_job() is None
        assert worker.connection.info.transaction_status == TransactionStatus.IDLE
        available_at = datetime.now(UTC) + timedelta(milliseconds=50)
        writer.put(
            "note_job",
            "late-note-job",
            {
                "job_id": "late-note-job",
                "user_id": str(USER),
                "course_id": "course-1",
                "conversation_id": "conversation-1",
                "status": "queued",
                "attempts": 0,
                "available_at": available_at.isoformat(),
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        time.sleep(0.08)
        claimed = worker.claim_note_job()
        assert claimed is not None
        assert claimed["job_id"] == "late-note-job"
        assert claimed["status"] == "running"
        assert worker.connection.info.transaction_status == TransactionStatus.IDLE
    finally:
        worker.reset_user(worker_token)
        writer.reset_user(writer_token)
        worker.close()
        writer.close()


def test_same_name_and_content_upload_is_unique_under_concurrency(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)

    def upload(index):
        store = PostgresStore(database_url)
        token = store.bind_user(str(USER))
        try:
            job = store.create_material_job(
                {
                    "document_id": f"new-document-{index}",
                    "course_id": "course-1",
                    "user_id": str(USER),
                    "title": "chapter.md",
                    "file_name": "chapter.md",
                    "content_sha256": "same-content-hash",
                    "source_type": "external_upload",
                    "parse_status": "queued",
                },
                {
                    "job_id": f"new-job-{index}",
                    "document_id": f"new-document-{index}",
                    "course_id": "course-1",
                    "idempotency_key": None,
                    "fingerprint": f"fingerprint-{index}",
                },
            )
            return job["document_id"]
        finally:
            store.reset_user(token)
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        document_ids = list(executor.map(upload, range(2)))
    assert document_ids[0] == document_ids[1]
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM documents WHERE course_id='course-1' "
                "AND data->>'file_name'='chapter.md'"
            )
            assert cursor.fetchone()[0] == 1


def test_legacy_uploaded_file_without_hash_can_be_reused(database_url, tmp_path):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
    raw = b"legacy file content"
    source = tmp_path / "legacy.md"
    source.write_bytes(raw)
    store = PostgresStore(database_url)
    token = store.bind_user(str(USER))
    try:
        document = {
            "document_id": "legacy-document",
            "course_id": "course-1",
            "user_id": str(USER),
            "file_name": "legacy.md",
            "file_path": str(source),
            "parse_status": "ready",
            "title": "legacy.md",
            "source_type": "teacher_ppt",
        }
        store.put("document", document["document_id"], document)
        found = store.find_duplicate_material("course-1", "legacy.md", sha256(raw).hexdigest())
        assert found["document"]["document_id"] == document["document_id"]
        assert found["job"] is None
        store.put("document", document["document_id"], {**document, "parse_status": "deleted"})
        assert (
            store.find_duplicate_material("course-1", "legacy.md", sha256(raw).hexdigest()) is None
        )
    finally:
        store.reset_user(token)
        store.close()


def test_note_cancel_and_publish_are_fenced_in_postgres(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
    store = PostgresStore(database_url)
    token = store.bind_user(str(USER))
    try:
        for status in ("queued", "running"):
            job_id = f"cancel-{status}"
            store.put(
                "note_job",
                job_id,
                {
                    "job_id": job_id,
                    "user_id": str(USER),
                    "course_id": "course-1",
                    "conversation_id": "conversation-1",
                    "session_id": job_id,
                    "status": status,
                    "attempts": 1 if status == "running" else 0,
                    "available_at": datetime.now(UTC).isoformat(),
                },
            )
            assert store.cancel_note_job(job_id) == "cancelled"
            assert not store.begin_note_publish(job_id, 1)
            assert store.get("note_job", job_id)["status"] == "cancelled"
        assert store.claim_note_job() is None

        store.put(
            "note_job",
            "publishing",
            {
                "job_id": "publishing",
                "user_id": str(USER),
                "course_id": "course-1",
                "conversation_id": "conversation-1",
                "session_id": "publishing",
                "status": "running",
                "attempts": 1,
                "available_at": datetime.now(UTC).isoformat(),
            },
        )
        assert store.begin_note_publish("publishing", 1)
        assert store.cancel_note_job("publishing") == "publishing"
        assert store.get("note_job", "publishing")["status"] == "running"
    finally:
        store.reset_user(token)
        store.close()


def test_checkpoint_thread_does_not_share_draft_transaction_connection(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
    store = PostgresStore(database_url)
    draft_open = Event()
    release_draft = Event()

    def save_draft():
        token = store.bind_user(str(USER))
        try:
            with store.transaction():
                store.put(
                    "course",
                    "course-key",
                    {
                        "course_id": "course-1",
                        "user_id": str(USER),
                        "status": "active",
                    },
                )
                draft_open.set()
                assert release_draft.wait(10)
            return store.connection.info.backend_pid
        finally:
            store.reset_user(token)

    def write_checkpoint():
        assert draft_open.wait(10)
        token = store.bind_user(str(USER))
        try:
            saver = SurrealSaver(store)
            saver.put_writes(
                {
                    "configurable": {
                        "thread_id": "note-thread",
                        "checkpoint_ns": "",
                        "checkpoint_id": "checkpoint-one",
                    }
                },
                [("note_output", {"saved": True})],
                "task-one",
            )
            return store.connection.info.backend_pid
        finally:
            store.reset_user(token)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            draft = executor.submit(save_draft)
            checkpoint = executor.submit(write_checkpoint)
            try:
                checkpoint_pid = checkpoint.result(timeout=10)
            finally:
                release_draft.set()
            assert draft.result(timeout=10) != checkpoint_pid
        token = store.bind_user(str(USER))
        try:
            assert len(store.scan("pending_write", {"thread_id": "note-thread"})) == 1
            with pytest.raises(RuntimeError, match="rollback probe"):
                with store.transaction():
                    store.put(
                        "course",
                        "course-key",
                        {
                            "course_id": "course-1",
                            "user_id": str(USER),
                            "status": "active",
                            "name": "should roll back",
                        },
                    )
                    raise RuntimeError("rollback probe")
            assert store.get("course", "course-key").get("name") is None
        finally:
            store.reset_user(token)
    finally:
        store.close()


@pytest.mark.parametrize("fallback_first", [False, True])
def test_four_note_batches_save_one_draft_and_checkpoint_on_first_run(
    database_url,
    fallback_first,
):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,%s)",
                (USER, "four-batches@example.test", "hash"),
            )
            cursor.execute(
                "INSERT INTO courses(record_key,user_id,course_id,data) "
                "VALUES ('course-1',%s,'course-1',%s)",
                (USER, Jsonb({"course_id": "course-1", "user_id": str(USER), "status": "active"})),
            )
    store = PostgresStore(database_url)
    token = store.bind_user(str(USER))
    try:
        document_id = "four-batch-source"
        store.ingest(
            {
                "document_id": document_id,
                "course_id": "course-1",
                "title": "TCP 讲义",
                "file_name": "tcp.pptx",
                "chapter": "TCP",
                "source_type": "teacher_ppt",
                "cleaned_markdown": "TCP 连接建立",
                "parse_status": "ready",
            },
            [
                {
                    "chunk_id": stable_key(document_id, str(index)),
                    "document_id": document_id,
                    "course_id": "course-1",
                    "title": "TCP 讲义",
                    "chapter": "TCP",
                    "source_type": "teacher_ppt",
                    "chunk_ordinal": index,
                    "content": f"TCP 三次握手同步双方初始序列号，片段 {index}。",
                    "embedding": [1.0, 0.1, 0.0],
                }
                for index in range(40)
            ],
        )
        model = ScriptedModel()
        note_calls = []
        original_note = model.note

        def capture_note(data):
            note_calls.append(data["batch_index"])
            if fallback_first and data["batch_index"] == 1:
                raise ModelError("模型连续返回无法解析的结构化内容")
            return original_note(data)

        model.note = capture_note
        settings = Settings(_env_file=None, embedding_dimensions=3)
        agent = FinalReviewAgent(
            store, KnowledgeBase(store, TestEmbeddings(), settings), model, settings
        )
        result = agent.invoke(
            AgentRequest(
                course_id="course-1",
                session_id="four-batch-note",
                message="生成笔记",
                intent="note",
                note_input=NoteInput(
                    note_type="key_points",
                    duration_minutes=10,
                    source_document_ids=[document_id],
                ),
            ),
            str(USER),
        )
        assert result.status == "completed"
        if fallback_first:
            assert "资料原文摘录" in result.answer
        assert note_calls == [1, 2, 3, 4]
        assert len(store.scan("learning_asset", {"course_id": "course-1"})) == 1
        assert len(store.scan("asset_revision", {"course_id": "course-1"})) == 1
        thread_id = store.session_key("course-1", "four-batch-note")
        assert store.scan("checkpoint", {"thread_id": thread_id})
        assert store.scan("pending_write", {"thread_id": thread_id})
        assert agent.recover("course-1", "four-batch-note").draft == result.draft
        assert len(store.scan("learning_asset", {"course_id": "course-1"})) == 1

        original_put = store.put
        interrupted = False

        def fail_after_draft(table, key, data):
            nonlocal interrupted
            if (
                table == "pending_write"
                and not interrupted
                and len(store.scan("learning_asset", {"course_id": "course-1"})) == 2
            ):
                interrupted = True
                raise RuntimeError("checkpoint interrupted after draft")
            return original_put(table, key, data)

        store.put = fail_after_draft
        try:
            with pytest.raises(RuntimeError, match="checkpoint interrupted after draft"):
                agent.invoke(
                    AgentRequest(
                        course_id="course-1",
                        session_id="recover-after-draft",
                        message="生成笔记",
                        intent="note",
                        note_input=NoteInput(
                            note_type="key_points",
                            duration_minutes=10,
                            source_document_ids=[document_id],
                        ),
                    ),
                    str(USER),
                )
        finally:
            store.put = original_put
        assert interrupted
        assert len(store.scan("learning_asset", {"course_id": "course-1"})) == 2
        recovered = agent.recover("course-1", "recover-after-draft")
        assert recovered.status == "completed"
        assert len(store.scan("learning_asset", {"course_id": "course-1"})) == 2
    finally:
        store.reset_user(token)
        store.close()


def test_note_draft_persists_point_locator_in_postgres(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,%s)",
                (USER, "note@example.test", "hash"),
            )
            cursor.execute(
                "INSERT INTO courses(record_key,user_id,course_id,data) "
                "VALUES ('course-1',%s,'course-1',%s)",
                (USER, Jsonb({"course_id": "course-1", "user_id": str(USER), "status": "active"})),
            )
    store = PostgresStore(database_url)
    token = store.bind_user(str(USER))
    try:
        settings = Settings(_env_file=None, embedding_dimensions=3)
        kb = KnowledgeBase(store, TestEmbeddings(), settings)
        ingested = kb.ingest(
            MaterialInput(
                course_id="course-1",
                title="TCP 讲义",
                chapter="TCP",
                source_type="teacher_ppt",
                markdown="TCP 三次握手同步双方初始序列号并确认双方收发能力。",
            ),
            user_id=str(USER),
        )
        agent = FinalReviewAgent(store, kb, ScriptedModel(), settings)
        result = agent.invoke(
            AgentRequest(
                course_id="course-1",
                session_id="m2-note",
                message="生成笔记",
                intent="note",
                note_input=NoteInput(
                    note_type="key_points",
                    scope="TCP",
                    duration_minutes=10,
                    source_document_ids=[ingested["document_id"]],
                ),
            ),
            str(USER),
        )
        assert result.draft
        draft = DomainService(store, str(USER)).note_draft(
            result.draft["asset_id"], result.draft["revision_id"]
        )
        assert draft["revision"]["state"] == "draft"
        assert draft["references"][0]["locator_id"] != "document"
        with store.connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM material_locators WHERE user_id=%s AND locator_id=%s",
                (USER, draft["references"][0]["locator_id"]),
            )
            assert cursor.fetchone()
    finally:
        store.reset_user(token)
        store.close()


def test_conversation_history_and_rename_are_scoped_to_owner_and_course(database_url, monkeypatch):
    apply_migrations(database_url)
    calls = []

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=f"答复 {len(calls)}"))]
            )

    monkeypatch.setattr("final_review.api.OpenAI", FakeOpenAI)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        auth_cookie_secure=False,
        llm_api_key="",
        embedding_api_key="",
        chat_models=[
            ChatModelConfig(
                id="acceptance",
                label="验收模型",
                model="fixture-model",
                base_url="https://example.invalid/v1",
                api_key="fixture-key",
            )
        ],
    )
    app = create_app(settings)
    with TestClient(app) as owner:
        other = TestClient(app)
        assert (
            owner.post(
                "/api/auth/sign-up",
                json={"email": "owner@example.test", "password": "test-password-123"},
            ).status_code
            == 200
        )
        assert (
            other.post(
                "/api/auth/sign-up",
                json={"email": "other@example.test", "password": "test-password-123"},
            ).status_code
            == 200
        )
        math = owner.post("/api/courses", json={"name": "数学"}).json()["course_id"]
        physics = owner.post("/api/courses", json={"name": "物理"}).json()["course_id"]
        other_course = other.post("/api/courses", json={"name": "他人课程"}).json()
        assert other_course["course_id"] not in {math, physics}

        for index in range(7):
            sent = owner.post(
                "/api/chat",
                json={
                    "course_id": math,
                    "conversation_id": f"chat-{index}",
                    "message": f"问题 {index}",
                    "mode": "direct",
                    "model_id": "acceptance",
                },
            )
            assert sent.status_code == 200, sent.text
        assert (
            owner.post(
                "/api/chat",
                json={
                    "course_id": physics,
                    "conversation_id": "physics-chat",
                    "message": "物理问题",
                    "mode": "direct",
                    "model_id": "acceptance",
                },
            ).status_code
            == 200
        )
        listing = owner.get(f"/api/courses/{math}/conversations").json()["items"]
        assert len(listing) == 7
        assert {item["conversation_id"] for item in listing[:5]} == {
            f"chat-{index}" for index in range(2, 7)
        }
        assert all(item["course_id"] == math for item in listing)

        continued = owner.post(
            "/api/chat",
            json={
                "course_id": math,
                "conversation_id": "chat-0",
                "message": "接着说",
                "mode": "direct",
                "model_id": "acceptance",
            },
        )
        assert continued.status_code == 200
        assert [item["role"] for item in calls[-1]["messages"]] == [
            "system",
            "user",
            "assistant",
            "user",
        ]
        assert calls[-1]["messages"][1]["content"] == "问题 0"
        assert calls[-1]["messages"][-1]["content"] == "接着说"
        history = owner.get(f"/api/courses/{math}/conversations/chat-0/messages")
        assert [item["content"] for item in history.json()["items"]] == [
            "问题 0",
            "答复 1",
            "接着说",
            "答复 9",
        ]
        assert (
            owner.get(f"/api/courses/{math}/conversations").json()["items"][0]["conversation_id"]
            == "chat-0"
        )
        renamed = owner.patch(
            f"/api/courses/{math}/conversations/chat-0", json={"title": "期末重点"}
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "期末重点"

        assert other.get(f"/api/courses/{math}/conversations").status_code == 404
        assert other.get(f"/api/courses/{math}/conversations/chat-0/messages").status_code == 404
        assert (
            other.patch(
                f"/api/courses/{math}/conversations/chat-0", json={"title": "越权"}
            ).status_code
            == 404
        )
        assert owner.get(f"/api/courses/{physics}/conversations/chat-0/messages").status_code == 404
        assert (
            owner.patch(
                f"/api/courses/{physics}/conversations/chat-0", json={"title": "错课程"}
            ).status_code
            == 404
        )

    with TestClient(create_app(settings)) as reopened:
        assert (
            reopened.post(
                "/api/auth/sign-in",
                json={"email": "owner@example.test", "password": "test-password-123"},
            ).status_code
            == 200
        )
        restored = reopened.get(f"/api/courses/{math}/conversations/chat-0/messages")
        assert restored.status_code == 200
        assert len(restored.json()["items"]) == 4
        assert (
            reopened.get(f"/api/courses/{math}/conversations").json()["items"][0]["title"]
            == "期末重点"
        )


def test_material_job_claim_publish_and_ready_only_search(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
    store = PostgresStore(database_url)
    token = store.bind_user(str(USER))
    try:
        document = {
            "document_id": "material-1",
            "course_id": "course-1",
            "user_id": str(USER),
            "title": "Job 讲义",
            "source_type": "homework",
            "chapter": "",
            "parse_status": "queued",
            "chunk_count": 0,
        }
        submitted = store.create_material_job(
            document,
            {
                "job_id": "job-1",
                "document_id": "material-1",
                "course_id": "course-1",
                "idempotency_key": "first",
                "fingerprint": "same-content",
            },
        )
        assert submitted["status"] == "queued"
        assert store.search([1.0, 0.0, 0.0], "course-1", "", 5) == []
        claimed = store.claim_material_job()
        assert claimed["job_id"] == "job-1" and claimed["attempts"] == 1
        assert store.claim_material_job() is None
        ready = {**document, "parse_status": "ready", "chunk_count": 1}
        chunk = {
            "chunk_id": "chunk-1",
            "document_id": "material-1",
            "course_id": "course-1",
            "source_type": "homework",
            "chapter": "",
            "content": "TCP 三次握手",
            "embedding": [1.0, 0.0, 0.0],
        }
        assert store.publish_material_job("job-1", 1, ready, [chunk])
        assert store.get_material_job("job-1")["status"] == "succeeded"
        assert store.get("document", "material-1")["parse_status"] == "ready"
        assert len(store.search([1.0, 0.0, 0.0], "course-1", "", 5)) == 1
        version = store.ensure_material_source(ready)
        assert store.list_material_chunks("material-1")[0]["chunk_id"] == "chunk-1"
        with store.connection.cursor() as cursor:
            cursor.execute(
                "SELECT locator_id FROM material_locators WHERE user_id=%s "
                "AND material_version_id=%s ORDER BY locator_id",
                (USER, version["material_version_id"]),
            )
            assert {row["locator_id"] for row in cursor.fetchall()} == {"document", "chunk-1"}
        other = store.bind_user(str(SECOND_USER))
        try:
            assert store.list_material_chunks("material-1") == []
            assert store.get("material_version", version["material_version_id"]) is None
        finally:
            store.reset_user(other)
        assert not store.publish_material_job("job-1", 1, ready, [chunk])
    finally:
        store.reset_user(token)
        store.close()


def test_material_metadata_edit_updates_pgvector_chunk_metadata(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
    store = PostgresStore(database_url)
    token = store.bind_user(str(USER))
    try:
        store.ingest(
            {
                "document_id": "edit-1",
                "course_id": "course-1",
                "user_id": str(USER),
                "title": "旧标题",
                "chapter": "旧章节",
                "source_type": "homework",
                "parse_status": "ready",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            [
                {
                    "chunk_id": "edit-chunk",
                    "document_id": "edit-1",
                    "course_id": "course-1",
                    "title": "旧标题",
                    "chapter": "旧章节",
                    "source_type": "homework",
                    "content": "网络知识",
                    "embedding": [1.0, 0.0, 0.0],
                }
            ],
        )
        updated = store.update_material_metadata(
            "edit-1",
            {
                "title": "新标题",
                "chapter": "新章节",
                "source_type": "teacher_ppt",
                "expected_updated_at": "2026-01-01T00:00:00+00:00",
            },
        )
        assert updated["title"] == "新标题"
        assert (
            store.update_material_metadata(
                "edit-1",
                {
                    "title": "过期修改",
                    "chapter": "",
                    "source_type": "homework",
                    "expected_updated_at": "2026-01-01T00:00:00+00:00",
                },
            )
            is None
        )
        hits = store.search([1.0, 0.0, 0.0], "course-1", "新章节", 5)
        assert len(hits) == 1
        assert hits[0]["title"] == "新标题"
        assert hits[0]["source_type"] == "teacher_ppt"
        assert store.search([1.0, 0.0, 0.0], "course-1", "旧章节", 5) == []
    finally:
        store.reset_user(token)
        store.close()


def test_material_job_failure_and_retry_are_persistent(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
    store = PostgresStore(database_url)
    token = store.bind_user(str(USER))
    try:
        document = {
            "document_id": "material-2",
            "course_id": "course-1",
            "user_id": str(USER),
            "title": "损坏资料",
            "source_type": "homework",
            "parse_status": "queued",
            "chunk_count": 0,
        }
        job_data = {
            "job_id": "job-2",
            "document_id": "material-2",
            "course_id": "course-1",
            "idempotency_key": "same-request",
            "fingerprint": "same-content",
        }
        store.create_material_job(document, job_data)
        assert (
            store.create_material_job(
                {**document, "document_id": "duplicate"},
                {**job_data, "job_id": "duplicate", "document_id": "duplicate"},
            )["job_id"]
            == "job-2"
        )
        claimed = store.claim_material_job()
        assert claimed["job_id"] == "job-2"
        with store.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE material_jobs SET lease_until=now()-interval '1 second' "
                "WHERE job_id='job-2'"
            )
        store.connection.commit()
        reclaimed = store.claim_material_job()
        assert reclaimed["job_id"] == "job-2" and reclaimed["attempts"] == 2
        assert not store.publish_material_job("job-2", 1, {**document, "parse_status": "ready"}, [])
        assert store.fail_material_job(
            "job-2", 2, code="invalid_material", message="图片损坏", retry=False
        )
        assert store.get_material_job("job-2")["status"] == "failed"
        assert store.get("document", "material-2")["parse_status"] == "failed"
        assert store.search([1.0, 0.0, 0.0], "course-1", "", 5) == []
        assert store.retry_material_job("job-2")["status"] == "queued"
        assert store.claim_material_job()["job_id"] == "job-2"
        assert store.get("document", "material-2")["parse_status"] == "running"
        store.put("document", "material-2", {**document, "parse_status": "deleted"})
        assert not store.publish_material_job("job-2", 1, {**document, "parse_status": "ready"}, [])
        assert store.get_material_job("job-2")["status"] == "failed"
        assert store.get("document", "material-2")["parse_status"] == "deleted"
    finally:
        store.reset_user(token)
        store.close()


def test_material_worker_processes_real_postgres_job(database_url, tmp_path):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
    source = tmp_path / "lecture.md"
    source.write_text("TCP 三次握手同步初始序列号。", encoding="utf-8")
    store = PostgresStore(database_url)
    token = store.bind_user(str(USER))
    try:
        store.create_material_job(
            {
                "document_id": "material-3",
                "course_id": "course-1",
                "user_id": str(USER),
                "title": "讲义",
                "source_type": "homework",
                "source_origin": "user_upload",
                "chapter": "",
                "file_name": "lecture.md",
                "file_path": str(source),
                "parse_status": "queued",
                "chunk_count": 0,
            },
            {
                "job_id": "job-3",
                "document_id": "material-3",
                "course_id": "course-1",
                "idempotency_key": None,
                "fingerprint": "content",
            },
        )
        settings = Settings(_env_file=None, embedding_dimensions=3)
        kb = KnowledgeBase(store, TestEmbeddings(), settings)
        job = store.claim_material_job()
        process_material_job(store, kb, job, settings.max_upload_mb * 1024 * 1024)
        assert store.get_material_job("job-3")["status"] == "succeeded"
        assert store.get("document", "material-3")["parse_status"] == "ready"
        assert len(store.search([1.0, 0.1, 0.0], "course-1", "", 5)) == 1
    finally:
        store.reset_user(token)
        store.close()


def test_existing_001_002_with_legacy_attempt_upgrades(database_url):
    with psycopg.connect(database_url) as connection:
        _sql(connection, "001_database_auth.sql")
        _sql(connection, "002_m0_domain_contracts.sql")
        _seed_course(connection, legacy=True)
    assert apply_migrations(database_url) == [
        "003_m0_database_hardening.sql",
        "004_m1_material_jobs.sql",
        "005_note_jobs.sql",
        "006_external_upload_dedup.sql",
        "007_note_review.sql",
        "008_note_exports.sql",
        "009_quiz_drafts.sql",
    ]
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT legacy_session_id FROM attempts WHERE record_key='attempt-key'")
        assert cursor.fetchone()[0] == "legacy-session"


def test_database_rejects_m0_relation_and_immutability_violations(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO documents(record_key,user_id,course_id,document_id,data) VALUES ('doc-key',%s,'course-1','doc-1','{}')",
                (USER,),
            )
            cursor.execute(
                "INSERT INTO learning_assets(record_key,user_id,course_id,asset_id,data) VALUES ('asset-key',%s,'course-1','asset-1','{}')",
                (USER,),
            )
            cursor.execute(
                "INSERT INTO asset_revisions(record_key,user_id,course_id,revision_id,asset_id,revision_no,state,content_hash,data) VALUES ('revision-key',%s,'course-1','revision-1','asset-1',1,'confirmed','h',%s)",
                (
                    USER,
                    Jsonb(
                        {
                            "title": "locked",
                            "markdown": "body",
                            "source_document_ids": [],
                            "state": "confirmed",
                        }
                    ),
                ),
            )
            cursor.execute(
                "INSERT INTO material_versions(record_key,user_id,course_id,document_id,material_version_id,data) VALUES ('version-key',%s,'course-1','doc-1','version-1','{}')",
                (USER,),
            )
            cursor.execute(
                "INSERT INTO material_locators(user_id,course_id,material_version_id,locator_id,locator_kind,ordinal) VALUES (%s,'course-1','version-1','document','document',0)",
                (USER,),
            )
        connection.commit()

        def rejected(statement, params=()):
            with pytest.raises(psycopg.Error):
                with connection.cursor() as cursor:
                    cursor.execute(statement, params)
            connection.rollback()

        rejected(
            "INSERT INTO asset_revisions(record_key,user_id,course_id,revision_id,asset_id,revision_no,state,content_hash,data) VALUES ('duplicate',%s,'course-1','revision-2','asset-1',1,'draft','x','{}')",
            (USER,),
        )
        rejected(
            "INSERT INTO attempts(record_key,user_id,course_id,data) VALUES ('no-parent',%s,'course-1','{}')",
            (USER,),
        )
        rejected(
            "INSERT INTO source_references(record_key,user_id,course_id,asset_revision_id,material_version_id,locator_id,data) VALUES ('bad-source',%s,'course-1','missing','version-1','document','{}')",
            (USER,),
        )
        rejected(
            "UPDATE asset_revisions SET data=%s WHERE revision_id='revision-1'",
            (
                Jsonb(
                    {
                        "title": "rewritten",
                        "markdown": "body",
                        "source_document_ids": [],
                        "state": "confirmed",
                    }
                ),
            ),
        )


def test_postgres_owner_predicate_and_confirmation_consumption_are_atomic(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        for user_id, email in ((USER, "owner@example.test"), (SECOND_USER, "other@example.test")):
            cursor.execute(
                "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,%s)",
                (user_id, email, "hash"),
            )
        connection.commit()

    first = PostgresStore(database_url)
    other = PostgresStore(database_url)
    first_token = first.bind_user(str(USER))
    try:
        first.put(
            "course",
            "course-1",
            {
                "course_id": "course-1",
                "user_id": str(USER),
                "name": "private",
                "status": "active",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        )
        owner_domain = DomainService(first, str(USER))
        preview = owner_domain.deletion_preview("course-1")

        other_token = other.bind_user(str(SECOND_USER))
        try:
            with pytest.raises(DomainNotFound):
                DomainService(other, str(SECOND_USER)).course("course-1")
        finally:
            other.reset_user(other_token)

        def consume(_):
            store = PostgresStore(database_url)
            thread_token = store.bind_user(str(USER))
            try:
                service = DomainService(store, str(USER))
                with service._transaction():
                    service._consume(
                        preview["confirmation_id"],
                        action="course.delete",
                        resource_type="course",
                        resource_id="course-1",
                        payload={
                            "materials": 0,
                            "exams": 0,
                            "learning_assets": 0,
                            "attempts": 0,
                        },
                    )
                return "deleted"
            except DomainConflict:
                return "conflict"
            finally:
                store.reset_user(thread_token)
                store.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(consume, range(2)))
        assert sorted(results) == ["conflict", "deleted"]

        rollback_preview = owner_domain.deletion_preview("course-1")
        with pytest.raises(RuntimeError, match="force rollback"):
            with owner_domain._transaction():
                owner_domain._consume(
                    rollback_preview["confirmation_id"],
                    action="course.delete",
                    resource_type="course",
                    resource_id="course-1",
                    payload={
                        "materials": 0,
                        "exams": 0,
                        "learning_assets": 0,
                        "attempts": 0,
                    },
                )
                raise RuntimeError("force rollback")
        assert first.get("confirmation", rollback_preview["confirmation_id"])["consumed_at"] is None
    finally:
        first.reset_user(first_token)
        first.close()
        other.close()


def test_database_api_returns_404_for_another_users_course(database_url, tmp_path):
    apply_migrations(database_url)
    settings = Settings(
        _env_file=None,
        auth_mode="database",
        auth_cookie_secure=False,
        database_url=database_url,
        llm_api_key="test-key",
        embedding_api_key="test-key",
        embedding_dimensions=3,
        uploads_dir=str(tmp_path / "uploads"),
    )
    with TestClient(create_app(settings)) as owner, TestClient(create_app(settings)) as other:
        assert (
            owner.post(
                "/api/auth/sign-up",
                json={"email": "owner-api@example.test", "password": "password1"},
            ).status_code
            == 200
        )
        course = owner.post("/api/courses", json={"name": "private course"}).json()
        conversation = owner.post(
            f"/api/courses/{course['course_id']}/conversations", json={"title": "private chat"}
        ).json()
        conversation_base = (
            f"/api/courses/{course['course_id']}/conversations/{conversation['conversation_id']}"
        )
        assert owner.get(conversation_base + "/messages").status_code == 200
        owner_id = owner.get("/api/auth/me").json()["id"]
        source = tmp_path / "uploads" / "lecture.md"
        source.parent.mkdir()
        source.write_text("private excerpt", encoding="utf-8")
        material_store = PostgresStore(database_url)
        owner_token = material_store.bind_user(owner_id)
        try:
            material_store.ingest(
                {
                    "document_id": "private-material",
                    "course_id": course["course_id"],
                    "user_id": owner_id,
                    "title": "private lecture",
                    "file_name": "lecture.md",
                    "file_path": str(source),
                    "source_type": "homework",
                    "cleaned_markdown": "private excerpt",
                    "parse_status": "ready",
                },
                [
                    {
                        "chunk_id": "private-chunk",
                        "document_id": "private-material",
                        "course_id": course["course_id"],
                        "title": "private lecture",
                        "chapter": "",
                        "source_type": "homework",
                        "content": "private excerpt",
                        "embedding": [1.0, 0.0, 0.0],
                    }
                ],
            )
        finally:
            material_store.reset_user(owner_token)
            material_store.close()
        material_base = f"/api/courses/{course['course_id']}/documents/private-material"
        assert owner.get(material_base + "/chunks").status_code == 200
        assert owner.get(material_base + "/chunks/private-chunk").status_code == 200
        assert owner.get(material_base + "/download").content == b"private excerpt"
        assert (
            other.post(
                "/api/auth/sign-up",
                json={"email": "other-api@example.test", "password": "password1"},
            ).status_code
            == 200
        )
        assert other.post(f"/api/courses/{course['course_id']}/deletion-preview").status_code == 404
        assert other.get(material_base + "/chunks").status_code == 404
        assert other.get(material_base + "/chunks/private-chunk").status_code == 404
        assert other.get(material_base + "/download").status_code == 404
        assert other.get(f"/api/courses/{course['course_id']}/conversations").status_code == 404
        assert other.get(conversation_base + "/messages").status_code == 404
        assert other.patch(conversation_base, json={"title": "stolen"}).status_code == 404


def test_course_and_exams_work_without_model_keys(database_url):
    apply_migrations(database_url)
    settings = Settings(
        _env_file=None,
        auth_mode="database",
        auth_cookie_secure=False,
        database_url=database_url,
        llm_api_key="",
        embedding_api_key="",
    )
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/api/auth/sign-up",
                json={"email": "no-models@example.test", "password": "password1"},
            ).status_code
            == 200
        )
        assert client.app.state.agent is None

        created = client.post("/api/courses", json={"name": "高等数学"})
        assert created.status_code == 200
        course_id = created.json()["course_id"]
        assert [item["course_id"] for item in client.get("/api/courses").json()["items"]] == [
            course_id
        ]

        for name in ("期中考试", "期末考试"):
            response = client.post(f"/api/courses/{course_id}/exams", json={"name": name})
            assert response.status_code == 200
        exams = client.get(f"/api/courses/{course_id}/exams")
        assert exams.status_code == 200
        assert {item["name"] for item in exams.json()["items"]} == {"期中考试", "期末考试"}


def test_postgres_snapshot_delete_removes_retrieval_chunks(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,%s)",
            (USER, "snapshot@example.test", "hash"),
        )
        connection.commit()

    store = PostgresStore(database_url)
    token = store.bind_user(str(USER))
    try:
        store.put(
            "course",
            "course-1",
            {
                "course_id": "course-1",
                "user_id": str(USER),
                "name": "course",
                "status": "active",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        )
        store.ingest(
            {
                "document_id": "document-1",
                "course_id": "course-1",
                "user_id": str(USER),
                "title": "source",
                "file_name": "source.md",
                "source_type": "teacher_ppt",
                "parse_status": "ready",
                "cleaned_markdown": "retrieval source",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "document-1",
                    "course_id": "course-1",
                    "title": "source",
                    "chapter": "",
                    "source_type": "teacher_ppt",
                    "content": "retrieval source",
                    "embedding": [1.0, 0.0, 0.0],
                }
            ],
        )
        domain = DomainService(store, str(USER))
        created = domain.create_asset(
            "course-1",
            {
                "asset_type": "note",
                "title": "note",
                "markdown": "# note",
                "source_document_ids": ["document-1"],
            },
        )
        domain.confirm_revision(created["asset"]["asset_id"], created["revision"]["revision_id"])
        preview = domain.material_deletion_preview("document-1")
        with pytest.raises(DomainConflict, match="资料仍被正式资产引用"):
            domain.delete_material("document-1", preview["confirmation_id"], "block")
        assert store.get("document", "document-1")["parse_status"] == "ready"
        preview = domain.material_deletion_preview("document-1")
        assert domain.delete_material(
            "document-1", preview["confirmation_id"], "retain_source_snapshot"
        ) == {"deleted": True, "retained_source_snapshot": True}
        # The API removes the original file after this returns. Another connection
        # must already see the deletion; otherwise a later rollback leaves a
        # searchable document pointing to a missing file.
        observer = PostgresStore(database_url)
        observer_token = observer.bind_user(str(USER))
        try:
            assert observer.get("document", "document-1")["parse_status"] == "deleted"
            assert observer.scan("source_snapshot", {"course_id": "course-1"})
            assert observer.search([1.0, 0.0, 0.0], "course-1", "", 1) == []
        finally:
            observer.reset_user(observer_token)
            observer.close()
        assert store.scan("source_snapshot", {"course_id": "course-1"})
        assert store.get("document", "document-1")["parse_status"] == "deleted"
        assert store.search([1.0, 0.0, 0.0], "course-1", "", 1) == []
    finally:
        store.reset_user(token)
        store.close()
