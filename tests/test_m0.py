from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from final_review.api import create_app
from final_review.domain import DomainConflict, DomainNotFound, DomainService
from final_review.material_jobs import process_material_job


def _course(client: TestClient) -> dict:
    return client.post("/api/courses", json={"name": "M0 course"}).json()


def _document(client: TestClient, course_id: str, system) -> dict:
    result = client.post(
        "/knowledge/upload",
        data={"course_id": course_id, "title": "chapter", "source_type": "teacher_ppt"},
        files={"file": ("chapter.md", "课程资料".encode())},
    )
    assert result.status_code == 202
    job = system.store.claim_material_job()
    process_material_job(system.store, system.kb, job, system.settings.max_upload_mb * 1024 * 1024)
    assert system.store.get_material_job(job["job_id"])["status"] == "succeeded"
    return result.json()


def test_course_lifecycle_exam_and_confirmation(system, tmp_path):
    system.settings.uploads_dir = str(tmp_path / "uploads")
    with TestClient(create_app(system.settings, system)) as client:
        course = _course(client)
        exam = client.post(
            f"/api/courses/{course['course_id']}/exams",
            json={
                "name": "期末",
                "total_score": 100,
                "blueprint": [{"question_type": "choice", "question_count": 10, "score": 100}],
            },
        )
        assert exam.status_code == 200
        assert client.get(f"/api/courses/{course['course_id']}/exams").json()["items"][0]["exam_id"]

        preview = client.post(f"/api/courses/{course['course_id']}/deletion-preview").json()
        assert preview["impact"] == {
            "materials": 0,
            "exams": 1,
            "learning_assets": 0,
            "attempts": 0,
        }
        deleted = client.post(
            f"/api/courses/{course['course_id']}/delete",
            json={"confirmation_id": preview["confirmation_id"]},
        )
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "deleted"
        assert any(
            item["course_id"] == course["course_id"] and item["status"] == "deleted"
            for item in client.get("/api/courses").json()["items"]
        )
        assert (
            client.post(
                f"/api/courses/{course['course_id']}/delete",
                json={"confirmation_id": preview["confirmation_id"]},
            ).status_code
            == 409
        )
        assert (
            client.post(f"/api/courses/{course['course_id']}/restore").json()["status"] == "active"
        )
        system.store.put(
            "course",
            course["course_id"],
            {**system.store.get("course", course["course_id"]), "status": "purged"},
        )
        assert all(
            item["course_id"] != course["course_id"]
            for item in client.get("/api/courses").json()["items"]
        )


def test_immutable_revisions_and_material_snapshot_delete(system, tmp_path):
    system.settings.uploads_dir = str(tmp_path / "uploads")
    with TestClient(create_app(system.settings, system)) as client:
        course = _course(client)
        document = _document(client, course["course_id"], system)
        created = client.post(
            f"/api/courses/{course['course_id']}/assets",
            json={
                "asset_type": "note",
                "title": "第一版",
                "markdown": "# 第一版",
                "source_document_ids": [document["document_id"]],
            },
        ).json()
        asset, first = created["asset"], created["revision"]
        assert (
            client.post(
                f"/api/assets/{asset['asset_id']}/revisions/{first['revision_id']}/confirm"
            ).json()["asset"]["status"]
            == "confirmed"
        )
        second = client.post(
            f"/api/assets/{asset['asset_id']}/revisions",
            json={
                "base_revision_id": first["revision_id"],
                "title": "第二版",
                "markdown": "# 第二版",
                "source_document_ids": [document["document_id"]],
            },
        ).json()
        assert second["revision_no"] == 2
        confirmed = client.post(
            f"/api/assets/{asset['asset_id']}/revisions/{second['revision_id']}/confirm"
        ).json()
        assert confirmed["revision"]["state"] == "confirmed"
        assert system.store.get("asset_revision", first["revision_id"])["state"] == "superseded"

        preview = client.post(
            f"/api/courses/{course['course_id']}/documents/{document['document_id']}/deletion-preview"
        ).json()
        assert preview["blocking_references"] == 2
        assert (
            client.post(
                f"/api/courses/{course['course_id']}/documents/{document['document_id']}/delete",
                json={"confirmation_id": preview["confirmation_id"], "mode": "block"},
            ).status_code
            == 409
        )
        snapshot_preview = client.post(
            f"/api/courses/{course['course_id']}/documents/{document['document_id']}/deletion-preview"
        ).json()
        deleted = client.post(
            f"/api/courses/{course['course_id']}/documents/{document['document_id']}/delete",
            json={
                "confirmation_id": snapshot_preview["confirmation_id"],
                "mode": "retain_source_snapshot",
            },
        )
        assert deleted.json() == {"deleted": True, "retained_source_snapshot": True}
        assert system.store.scan("source_snapshot", {"course_id": course["course_id"]})
        assert not [
            chunk
            for chunk in system.store.chunks
            if chunk["document_id"] == document["document_id"]
        ]


def test_domain_service_rejects_another_users_course(system):
    system.store.put(
        "course",
        "private",
        {"course_id": "private", "user_id": "owner", "name": "private", "created_at": "now"},
    )
    try:
        DomainService(system.store, "other").course("private")
    except DomainNotFound:
        pass
    else:
        raise AssertionError("another user must not read a course")


def test_m0_lifecycle_confirmation_exam_and_source_boundaries(system, tmp_path):
    system.settings.uploads_dir = str(tmp_path / "uploads")
    with TestClient(create_app(system.settings, system)) as client:
        course = _course(client)
        course_id = course["course_id"]
        assert (
            client.patch(
                f"/api/courses/{course_id}",
                json={"name": "stale", "expected_updated_at": "not-current"},
            ).status_code
            == 409
        )
        assert client.post(f"/api/courses/{course_id}/archive").status_code == 200
        assert client.post(f"/api/courses/{course_id}/archive").status_code == 409
        assert client.post(f"/api/courses/{course_id}/restore").json()["status"] == "active"

        client.post(f"/api/courses/{course_id}/exams", json={"name": "open blueprint"})
        second_exam = client.post(
            f"/api/courses/{course_id}/exams", json={"name": "second exam"}
        ).json()
        assert len(client.get(f"/api/courses/{course_id}/exams").json()["items"]) == 2
        assert client.post(f"/api/exams/{second_exam['exam_id']}/archive").status_code == 200
        assert (
            client.patch(
                f"/api/exams/{second_exam['exam_id']}",
                json={"name": "locked", "expected_updated_at": second_exam["updated_at"]},
            ).status_code
            == 409
        )

        stale_preview = client.post(f"/api/courses/{course_id}/deletion-preview").json()
        client.post(f"/api/courses/{course_id}/exams", json={"name": "changes impact"})
        assert (
            client.post(
                f"/api/courses/{course_id}/delete",
                json={"confirmation_id": stale_preview["confirmation_id"]},
            ).status_code
            == 409
        )
        preview = client.post(f"/api/courses/{course_id}/deletion-preview").json()
        assert (
            client.post(
                f"/api/courses/{course_id}/delete",
                json={"confirmation_id": preview["confirmation_id"]},
            ).json()["purge_after"]
            is not None
        )
        assert (
            client.post(f"/api/courses/{course_id}/exams", json={"name": "blocked"}).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/courses/{course_id}/assets",
                json={"asset_type": "note", "title": "blocked", "markdown": "# blocked"},
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/agent/invoke",
                json={"course_id": course_id, "session_id": "blocked", "message": "test"},
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/assessment/evaluate",
                json={"course_id": course_id, "session_id": "blocked", "answers": {"q": "a"}},
            ).status_code
            == 409
        )

        restored = client.post(f"/api/courses/{course_id}/restore").json()
        assert restored["status"] == "active"
        domain = DomainService(system.store, restored["user_id"])
        expired = {**restored, "status": "deleted", "purge_after": "2020-01-01T00:00:00+00:00"}
        system.store.put("course", course_id, expired)
        with pytest.raises(DomainConflict):
            domain.restore_course(course_id)
        assert domain.purge_expired_courses(datetime.now(UTC)) == [course_id]
        assert domain.purge_expired_courses(datetime.now(UTC)) == []

        source_course = _course(client)
        other_course = _course(client)
        other_document = _document(client, other_course["course_id"], system)
        document = _document(client, source_course["course_id"], system)
        failed = system.store.get("document", document["document_id"])
        failed["parse_status"] = "failed"
        system.store.put("document", document["document_id"], failed)
        assert (
            client.post(
                f"/api/courses/{source_course['course_id']}/assets",
                json={
                    "asset_type": "note",
                    "title": "bad source",
                    "markdown": "# bad",
                    "source_document_ids": [document["document_id"]],
                },
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/courses/{source_course['course_id']}/assets",
                json={
                    "asset_type": "note",
                    "title": "cross course source",
                    "markdown": "# bad",
                    "source_document_ids": [other_document["document_id"]],
                },
            ).status_code
            == 409
        )
