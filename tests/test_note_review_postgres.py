"""M2-03 on a disposable PostgreSQL database, including real owner isolation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_postgres_migrations import USER, _seed_course, database_url  # noqa: F401, F811

from final_review.api import create_app
from final_review.config import Settings
from final_review.domain import DomainConflict, DomainService
from final_review.migrations import apply_migrations
from final_review.postgres import PostgresStore

pytestmark = pytest.mark.integration


def seed_note(store, owner, course_id):
    store.ingest(
        {
            "document_id": "lecture",
            "course_id": course_id,
            "user_id": owner,
            "title": "TCP",
            "source_type": "teacher_ppt",
            "file_name": "TCP.md",
            "cleaned_markdown": "TCP 同步初始序列号。",
            "parse_status": "ready",
        },
        [
            {
                "chunk_id": "tcp-chunk",
                "document_id": "lecture",
                "course_id": course_id,
                "title": "TCP",
                "chapter": "TCP",
                "source_type": "teacher_ppt",
                "content": "TCP 同步初始序列号。",
                "embedding": [1.0, 0.0, 0.0],
            }
        ],
    )
    return DomainService(store, owner).create_note_draft(
        course_id,
        {
            "title": "TCP 考点",
            "note_type": "key_points",
            "markdown": "# TCP 考点",
            "points": [
                {
                    "point_id": "p1",
                    "heading": "三次握手",
                    "content": "同步初始序列号。",
                    "provenance": "source",
                    "references": [
                        {
                            "document_id": "lecture",
                            "chunk_id": "tcp-chunk",
                            "quote": "TCP 同步初始序列号。",
                            "file_name": "TCP.md",
                            "source_type": "teacher_ppt",
                        }
                    ],
                }
            ],
        },
        "test-note",
    )


def test_real_note_api_owner_scope_confirm_history_and_snapshot(database_url):  # noqa: F811
    apply_migrations(database_url)
    settings = Settings(
        _env_file=None,
        auth_mode="database",
        auth_cookie_secure=False,
        database_url=database_url,
        embedding_dimensions=3,
    )
    with TestClient(create_app(settings)) as owner, TestClient(create_app(settings)) as other:
        assert (
            owner.post(
                "/api/auth/sign-up",
                json={"email": "note-owner@example.test", "password": "password1"},
            ).status_code
            == 200
        )
        assert (
            other.post(
                "/api/auth/sign-up",
                json={"email": "note-other@example.test", "password": "password1"},
            ).status_code
            == 200
        )
        user_id = owner.get("/api/auth/me").json()["id"]
        course_id = owner.post("/api/courses", json={"name": "网络"}).json()["course_id"]
        second_course = owner.post("/api/courses", json={"name": "数学"}).json()["course_id"]
        store = PostgresStore(database_url)
        token = store.bind_user(user_id)
        try:
            generated = seed_note(store, user_id, course_id)
            asset_id = generated["asset"]["asset_id"]
            first = generated["revision"]["revision_id"]
            base = f"/api/notes/{asset_id}"
            assert owner.get(f"/api/courses/{second_course}/notes").json()["items"] == []
            assert other.get(f"/api/courses/{course_id}/notes").status_code == 404
            assert other.get(f"/api/assets/{asset_id}/revisions/{first}").status_code == 404
            points = generated["revision"]["points"]
            for point in points:
                point["references"] = [
                    {key: ref[key] for key in ("document_id", "chunk_id", "quote")}
                    for ref in point["references"]
                ]
            payload = {"base_revision_id": first, "title": "学生编辑", "points": points}
            assert other.post(base + "/revisions", json=payload).status_code == 404
            assert (
                other.post(base + "/confirm-preview", json={"revision_id": first}).status_code
                == 404
            )
            saved = owner.post(base + "/revisions", json=payload)
            assert saved.status_code == 200
            second = saved.json()["revision"]["revision_id"]
            preview = owner.post(base + "/confirm-preview", json={"revision_id": second}).json()
            confirm = {"revision_id": second, "confirmation_id": preview["confirmation_id"]}
            assert other.post(base + "/confirm", json=confirm).status_code == 404
            assert owner.post(base + "/confirm", json=confirm).status_code == 200
            assert owner.post(base + "/confirm", json=confirm).status_code == 409
            assert owner.get(f"/api/assets/{asset_id}/revisions/{second}").status_code == 200
            payload["base_revision_id"] = second
            payload["title"] = "第二轮复习"
            third = owner.post(base + "/revisions", json=payload).json()["revision"]["revision_id"]
            preview = owner.post(base + "/confirm-preview", json={"revision_id": third}).json()
            assert preview["replaces_confirmed"]
            assert (
                owner.post(
                    base + "/confirm",
                    json={"revision_id": third, "confirmation_id": preview["confirmation_id"]},
                ).status_code
                == 200
            )
            old = owner.get(f"/api/assets/{asset_id}/revisions/{second}").json()["revision"]
            assert old["state"] == "superseded" and old["title"] == "学生编辑"
            service = DomainService(store, user_id)
            deletion = service.material_deletion_preview("lecture", course_id)
            service.delete_material(
                "lecture", deletion["confirmation_id"], "retain_source_snapshot"
            )
            ref = owner.get(f"/api/assets/{asset_id}/revisions/{third}").json()["revision"][
                "points"
            ][0]["references"][0]
            assert not ref["available"] and ref["snapshot"]["excerpt"] == ref["quote"]
        finally:
            store.reset_user(token)
            store.close()


def test_row_lock_prevents_two_saves_from_same_base(database_url):  # noqa: F811
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
        connection.execute("UPDATE courses SET record_key='course-1' WHERE record_key='course-key'")
    store = PostgresStore(database_url)
    token = store.bind_user(str(USER))
    try:
        generated = seed_note(store, str(USER), "course-1")
    finally:
        store.reset_user(token)
        store.close()
    barrier = Barrier(2)

    def save(title):
        client = PostgresStore(database_url)
        client_token = client.bind_user(str(USER))
        try:
            barrier.wait(timeout=10)
            DomainService(client, str(USER)).edit_note(
                generated["asset"]["asset_id"],
                {
                    "base_revision_id": generated["revision"]["revision_id"],
                    "title": title,
                    "points": generated["revision"]["points"],
                },
            )
            return "saved"
        except DomainConflict:
            return "conflict"
        finally:
            client.reset_user(client_token)
            client.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, ["编辑一", "编辑二"])) == ["conflict", "saved"]


def test_note_migration_backfills_latest_without_mutating_revision(database_url):  # noqa: F811
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
        connection.execute("UPDATE courses SET record_key='course-1' WHERE record_key='course-key'")
    store = PostgresStore(database_url)
    token = store.bind_user(str(USER))
    try:
        generated = seed_note(store, str(USER), "course-1")
        asset_id = generated["asset"]["asset_id"]
        revision_id = generated["revision"]["revision_id"]
        before = store.get("asset_revision", revision_id)
        with psycopg.connect(database_url) as connection:
            connection.execute(
                "UPDATE learning_assets SET data=data-'latest_revision_id'-'generation_method'"
            )
            connection.execute("DELETE FROM schema_migrations WHERE version='007_note_review.sql'")
        assert apply_migrations(database_url) == ["007_note_review.sql"]
        asset = store.get("learning_asset", asset_id)
        assert asset["latest_revision_id"] == revision_id and asset["generation_method"] == "ai"
        assert store.get("asset_revision", revision_id) == before
    finally:
        store.reset_user(token)
        store.close()
