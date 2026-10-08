"""Formal quiz proofs in an explicitly disposable PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from conftest import ScriptedModel
from fastapi.testclient import TestClient
from test_postgres_migrations import USER, _seed_course, database_url  # noqa: F401

from final_review.api import create_app
from final_review.config import ChatModelConfig, Settings
from final_review.domain import DomainConflict, DomainService
from final_review.migrations import apply_migrations
from final_review.postgres import PostgresStore
from final_review.quiz_config import resolve_quiz_config
from final_review.quiz_generation import read_quiz
from final_review.quiz_jobs import enqueue_quiz, process_quiz_job, retry_quiz
from final_review.schemas import QuizInput
from final_review.storage import StorageError, stable_key

pytestmark = pytest.mark.integration


def seed_quiz(store, owner=str(USER), course_id="course-1"):
    store.ingest(
        {
            "document_id": "quiz-lecture",
            "course_id": course_id,
            "user_id": owner,
            "title": "TCP课件",
            "file_name": "TCP.md",
            "source_type": "teacher_ppt",
            "chapter": "TCP",
            "parse_status": "ready",
            "cleaned_markdown": "TCP同步初始序列号。",
        },
        [
            {
                "chunk_id": "quiz-chunk",
                "document_id": "quiz-lecture",
                "course_id": course_id,
                "chapter": "TCP",
                "title": "TCP课件",
                "source_type": "teacher_ppt",
                "content": "TCP同步初始序列号。",
                "embedding": [1.0, 0.0, 0.0],
            }
        ],
    )
    partial = QuizInput(
        scope_mode="chapter",
        chapter="TCP",
        blueprint=[{"question_type": "short_answer", "question_count": 1}],
        duration_mode="untimed",
        difficulty="basic",
        total_score=10,
        source_document_ids=["quiz-lecture"],
        include_imported_questions=False,
        allow_ai_supplement=False,
    )
    return resolve_quiz_config(store, owner, course_id, partial).config


def prepared(db_url):
    apply_migrations(db_url)
    with psycopg.connect(db_url) as connection:
        _seed_course(connection)
        connection.execute("UPDATE courses SET record_key='course-1' WHERE record_key='course-key'")
        connection.commit()
    store = PostgresStore(db_url)
    store.bind_user(str(USER))
    return store, seed_quiz(store)


def test_quiz_atomic_publish_relations_immutability_and_draft_attempt_block(database_url):  # noqa: F811
    store, config = prepared(database_url)
    try:
        queued = enqueue_quiz(store, config, model_id="default")
        process_quiz_job(store, ScriptedModel(), Settings(_env_file=None), store.claim_quiz_job())
        job = store.get("quiz_job", queued["job_id"])
        assert job["status"] == "succeeded", job["error"]
        result = job["result"]
        paper = read_quiz(store, str(USER), "course-1", result["asset_id"], result["revision_id"])
        question = paper["revision"]["questions"][0]
        assert question["references"][0]["available"]
        assert paper["asset"]["current_revision_id"] is None
        with pytest.raises(StorageError):
            store.put(
                "attempt",
                "draft-attempt",
                {
                    "course_id": "course-1",
                    "quiz_revision_id": result["revision_id"],
                    "legacy_session_id": None,
                },
            )
        with pytest.raises(StorageError):
            store.put(
                "question_revision",
                question["question_revision_id"],
                {
                    **store.get("question_revision", question["question_revision_id"]),
                    "stem": "篡改",
                },
            )
        with pytest.raises(DomainConflict):
            DomainService(store, str(USER)).confirm_revision(
                result["asset_id"], result["revision_id"]
            )
        with pytest.raises(StorageError):
            ref = store.scan("source_reference", {"course_id": "course-1"})[0]
            store.put(
                "source_reference",
                "invalid-question-ref",
                {**ref, "question_revision_id": "unknown"},
            )
        assert store.get("attempt", "draft-attempt") is None
    finally:
        store.close()


def test_quiz_queue_concurrent_idempotency_claim_expiry_and_retry(database_url):  # noqa: F811
    store, config = prepared(database_url)
    try:
        assert store.claim_quiz_job() is None
        store.put(
            "conversation",
            stable_key("course-1", "shared"),
            {
                "course_id": "course-1",
                "conversation_id": "shared",
                "user_id": str(USER),
                "title": "Shared quiz",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

        def enqueue(score):
            token = store.bind_user(str(USER))
            try:
                return enqueue_quiz(
                    store,
                    config.model_copy(update={"total_score": score}),
                    model_id="default",
                    idempotency_key="same",
                    conversation_id="shared",
                )
            finally:
                store.reset_user(token)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(enqueue, [10, 10]))
        assert results[0]["job_id"] == results[1]["job_id"]
        assert len(store.scan("quiz_job", {})) == 1
        with pytest.raises(DomainConflict):
            enqueue(20)
        old = store.claim_quiz_job()
        assert store.claim_quiz_job() is None
        row = store.get("quiz_job", old["job_id"])
        row["lease_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        store.put("quiz_job", old["job_id"], row)
        new = store.claim_quiz_job()
        assert new["attempts"] == old["attempts"] + 1
        process_quiz_job(store, ScriptedModel(), Settings(_env_file=None), old)
        assert store.get("quiz_job", new["job_id"])["status"] == "running"
        process_quiz_job(store, ScriptedModel(), Settings(_env_file=None), new)
        assert store.get("quiz_job", new["job_id"])["status"] == "succeeded"
    finally:
        store.close()


def test_quiz_publish_failure_rolls_back_real_database(database_url, monkeypatch):  # noqa: F811
    store, config = prepared(database_url)
    try:
        enqueue_quiz(store, config, model_id="default")
        job = store.claim_quiz_job()
        original = store.put

        def fail(table, key, value):
            if table == "quiz_job" and value["status"] == "succeeded":
                raise RuntimeError("simulated write failure")
            original(table, key, value)

        monkeypatch.setattr(store, "put", fail)
        process_quiz_job(store, ScriptedModel(), Settings(_env_file=None), job)
        assert store.get("quiz_job", job["job_id"])["status"] == "failed"
        for table in (
            "learning_asset",
            "asset_revision",
            "question_revision",
            "quiz_revision_payload",
            "source_reference",
        ):
            assert not store.scan(table, {})
        monkeypatch.setattr(store, "put", original)
        retry_quiz(store, str(USER), "course-1", job["job_id"])
        process_quiz_job(store, ScriptedModel(), Settings(_env_file=None), store.claim_quiz_job())
        assert store.get("quiz_job", job["job_id"])["status"] == "succeeded"
    finally:
        store.close()


def test_quiz_http_isolates_two_authenticated_users_and_survives_restart(database_url):  # noqa: F811
    apply_migrations(database_url)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        auth_cookie_secure=False,
        chat_models=[
            ChatModelConfig(
                id="test",
                label="Test",
                model="fixture",
                api_key="fixture-key",
                base_url="https://example.invalid/v1",
            )
        ],
    )
    with TestClient(create_app(settings)) as owner, TestClient(create_app(settings)) as other:
        owner.post(
            "/api/auth/sign-up", json={"email": "quiz-owner@example.test", "password": "password1"}
        )
        other.post(
            "/api/auth/sign-up", json={"email": "quiz-other@example.test", "password": "password1"}
        )
        user_id = owner.get("/api/auth/me").json()["id"]
        course = owner.post("/api/courses", json={"name": "Quiz course"}).json()
        course_id = course["course_id"]
        store = PostgresStore(database_url)
        store.bind_user(user_id)
        try:
            config = seed_quiz(store, user_id, course_id)
            fields = {
                k: v
                for k, v in config.model_dump(mode="json").items()
                if k in QuizInput.model_fields
            }
            queued = owner.post(f"/api/courses/{course_id}/quiz-jobs", json={"quiz_input": fields})
            assert queued.status_code == 202, queued.text
            job_id = queued.json()["job_id"]
            assert other.get(f"/api/courses/{course_id}/quiz-jobs/{job_id}").status_code == 404
            process_quiz_job(store, ScriptedModel(), settings, store.claim_quiz_job())
            result = owner.get(f"/api/courses/{course_id}/quiz-jobs/{job_id}").json()["result"]
            url = (
                f"/api/courses/{course_id}/quizzes/{result['asset_id']}"
                f"/revisions/{result['revision_id']}"
            )
            assert other.get(url).status_code == 404
            assert (
                other.post(f"/api/courses/{course_id}/quiz-jobs/{job_id}/retry").status_code == 404
            )
            assert owner.get(url).status_code == 200
            with TestClient(create_app(settings)) as restarted:
                restarted.cookies.update(owner.cookies)
                assert restarted.get(url).json()["revision"]["questions"][0]["score"] == 10
        finally:
            store.close()
