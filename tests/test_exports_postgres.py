"""Exports on a disposable PostgreSQL database: HTTP owner isolation and lease fencing."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from test_note_review_postgres import seed_note
from test_postgres_migrations import database_url  # noqa: F401

from final_review.api import create_app
from final_review.config import Settings
from final_review.domain import DomainService
from final_review.export_jobs import process_export_job
from final_review.exports import create_export
from final_review.migrations import apply_migrations
from final_review.postgres import PostgresStore

pytestmark = pytest.mark.integration


def test_export_real_http_owners_lease_and_migration(database_url, tmp_path):  # noqa: F811
    apply_migrations(database_url)
    assert apply_migrations(database_url) == []
    settings = Settings(
        _env_file=None,
        auth_cookie_secure=False,
        database_url=database_url,
        embedding_dimensions=3,
        exports_dir=str(tmp_path),
    )
    with TestClient(create_app(settings)) as owner, TestClient(create_app(settings)) as other:
        for client, email in (
            (owner, "export-owner@example.test"),
            (other, "export-other@example.test"),
        ):
            assert (
                client.post(
                    "/api/auth/sign-up", json={"email": email, "password": "password1"}
                ).status_code
                == 200
            )
        user_id = owner.get("/api/auth/me").json()["id"]
        course_id = owner.post("/api/courses", json={"name": "导出测试"}).json()["course_id"]
        store = PostgresStore(database_url)
        token = store.bind_user(user_id)
        try:
            service = DomainService(store, user_id)
            generated = seed_note(store, user_id, course_id)
            asset = generated["asset"]["asset_id"]
            revision = generated["revision"]["revision_id"]
            endpoint = f"/api/notes/{asset}/exports"
            request = {"revision_id": revision, "format": "markdown"}
            assert owner.post(endpoint, json=request).status_code == 409
            preview = service.note_confirm_preview(asset, revision)
            service.confirm_note(asset, revision, preview["confirmation_id"])
            assert other.post(endpoint, json=request).status_code == 404
            info = owner.post(endpoint, json=request).json()
            creation_barrier = Barrier(2)

            def duplicate_request(_):
                connection = PostgresStore(database_url)
                binding = connection.bind_user(user_id)
                try:
                    creation_barrier.wait(timeout=10)
                    return create_export(
                        DomainService(connection, user_id), asset, revision, "markdown"
                    )["export_id"]
                finally:
                    connection.reset_user(binding)
                    connection.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                assert list(pool.map(duplicate_request, range(2))) == [info["export_id"]] * 2
            barrier = Barrier(2)

            def claim():
                worker = PostgresStore(database_url)
                try:
                    barrier.wait(timeout=10)
                    return worker.claim_export_job()
                finally:
                    worker.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = list(pool.map(lambda _: claim(), range(2)))
            assert sum(job is not None for job in jobs) == 1
            stale = next(job for job in jobs if job)
            expired = store.get("export_job", info["export_id"])
            expired["lease_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            store.put("export_job", info["export_id"], expired)
            current = store.claim_export_job()
            assert current["attempts"] == stale["attempts"] + 1
            assert not store.finish_export_job(stale["export_id"], stale["attempts"], result={})
            process_export_job(store, settings, current)
            base = f"/api/exports/{info['export_id']}"
            assert owner.get(base).json()["status"] == "succeeded"
            assert owner.get(base + "/download").status_code == 200
            for suffix in ("", "/download", "/preview"):
                assert other.get(base + suffix).status_code == 404
            assert other.post(base + "/retry").status_code == 404
            printable = create_export(service, asset, revision, "print")
            process_export_job(store, settings, store.claim_export_job())
            assert owner.get(f"/api/exports/{printable['export_id']}/preview").status_code == 200
            deletion = service.deletion_preview(course_id)
            service.delete_course(course_id, deletion["confirmation_id"])
            assert owner.get(base + "/download").status_code == 409
        finally:
            store.reset_user(token)
            store.close()
