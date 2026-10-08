from pathlib import Path

from fastapi.testclient import TestClient

from final_review.api import create_app
from final_review.material_jobs import process_material_job


def upload(client, system, course_id, title, chapter="", source_type="homework"):
    response = client.post(
        "/knowledge/upload",
        data={
            "course_id": course_id,
            "title": title,
            "chapter": chapter,
            "source_type": source_type,
        },
        files={"file": (f"{title}.md", f"{title} 网络知识".encode())},
    )
    assert response.status_code == 202, response.text
    job = system.store.claim_material_job()
    assert job
    process_material_job(system.store, system.kb, job, system.settings.max_upload_mb * 1024 * 1024)
    assert system.store.get_material_job(job["job_id"])["status"] == "succeeded"
    return response.json()["document_id"]


def test_edit_filter_and_unreferenced_delete(system, tmp_path):
    system.settings.uploads_dir = str(tmp_path / "uploads")
    with TestClient(create_app(system.settings, system)) as client:
        course = client.post("/api/courses", json={"name": "网络"}).json()["course_id"]
        other = client.post("/api/courses", json={"name": "数学"}).json()["course_id"]
        document_id = upload(client, system, course, "第一章", "旧章节")
        base = f"/api/courses/{course}/documents/{document_id}"
        original = system.store.get("document", document_id)
        first_edit = client.patch(
            base,
            json={
                "title": "初次修改",
                "chapter": "",
                "source_type": "teacher_ppt",
                "expected_updated_at": original["updated_at"],
            },
        )
        assert first_edit.status_code == 200
        updated = client.patch(
            base,
            json={
                "title": "网络讲义",
                "chapter": "第二章",
                "source_type": "teacher_ppt",
                "expected_updated_at": original["updated_at"],
            },
        )
        assert updated.status_code == 409
        wrong_course = client.patch(
            f"/api/courses/{other}/documents/{document_id}",
            json={
                "title": "越权",
                "chapter": "",
                "source_type": "homework",
                "expected_updated_at": original["updated_at"],
            },
        )
        assert wrong_course.status_code == 404
        current = system.store.get("document", document_id)
        saved = client.patch(
            base,
            json={
                "title": "网络讲义",
                "chapter": "第二章",
                "source_type": "teacher_ppt",
                "expected_updated_at": current["updated_at"],
            },
        )
        assert saved.status_code == 200
        assert all(
            chunk["chapter"] == "第二章"
            and chunk["source_type"] == "teacher_ppt"
            and chunk["title"] == "网络讲义"
            for chunk in system.store.chunks
            if chunk["document_id"] == document_id
        )
        assert (
            len(
                client.get(
                    f"/api/courses/{course}/documents",
                    params={"chapter": "第二章", "source_type": "teacher_ppt", "status": "ready"},
                ).json()["items"]
            )
            == 1
        )
        assert (
            client.get(f"/api/courses/{course}/documents", params={"chapter": "旧章节"}).json()[
                "items"
            ]
            == []
        )
        assert system.store.search([1.0, 0.1, 0.0], course, "第二章", 5)
        preview = client.post(base + "/deletion-preview").json()
        assert preview["blocking_references"] == preview["affected_assets"] == 0
        path = Path(current["file_path"])
        assert path.exists()
        deletion = client.post(
            base + "/delete",
            json={
                "confirmation_id": preview["confirmation_id"],
            },
        )
        assert deletion.status_code == 200
        assert not path.exists()
        assert client.get(f"/api/courses/{course}/documents").json()["items"] == []
        assert not system.store.search([1.0, 0.1, 0.0], course, "", 5)
        repeated = client.post(
            base + "/delete",
            json={
                "confirmation_id": preview["confirmation_id"],
            },
        )
        assert repeated.status_code == 409


def test_confirmed_reference_requires_snapshot_and_exact_preview(system, tmp_path):
    system.settings.uploads_dir = str(tmp_path / "uploads")
    with TestClient(create_app(system.settings, system)) as client:
        course = client.post("/api/courses", json={"name": "网络"}).json()["course_id"]
        document_id = upload(client, system, course, "资料")
        base = f"/api/courses/{course}/documents/{document_id}"
        early = client.post(base + "/deletion-preview").json()
        created = client.post(
            f"/api/courses/{course}/assets",
            json={
                "asset_type": "note",
                "title": "复习笔记",
                "markdown": "# 知识点",
                "source_document_ids": [document_id],
            },
        ).json()
        asset, revision = created["asset"], created["revision"]
        client.post(f"/api/assets/{asset['asset_id']}/revisions/{revision['revision_id']}/confirm")
        stale = client.post(base + "/delete", json={"confirmation_id": early["confirmation_id"]})
        assert stale.status_code == 409
        preview = client.post(base + "/deletion-preview").json()
        assert preview["blocking_references"] == preview["affected_assets"] == 1
        assert (
            client.post(
                base + "/delete",
                json={"confirmation_id": preview["confirmation_id"], "mode": "block"},
            ).status_code
            == 409
        )
        preview = client.post(base + "/deletion-preview").json()
        result = client.post(
            base + "/delete",
            json={"confirmation_id": preview["confirmation_id"], "mode": "retain_source_snapshot"},
        )
        assert result.status_code == 200, result.text
        snapshot = system.store.scan("source_snapshot", {"course_id": course})[0]
        assert snapshot["locator_id"] == "document"
        assert snapshot["display_file_name"] == "资料.md"
        assert snapshot["excerpt"]
        assert system.store.search([1.0, 0.1, 0.0], course, "", 5) == []


def test_processing_material_cannot_be_edited_or_republished_after_delete(system, tmp_path):
    system.settings.uploads_dir = str(tmp_path / "uploads")
    with TestClient(create_app(system.settings, system)) as client:
        course = client.post("/api/courses", json={"name": "网络"}).json()["course_id"]
        queued = client.post(
            "/knowledge/upload",
            data={"course_id": course, "title": "排队资料", "source_type": "homework"},
            files={"file": ("queue.md", b"network basics")},
        ).json()
        document_id = queued["document_id"]
        base = f"/api/courses/{course}/documents/{document_id}"
        edit = {
            "title": "改名",
            "chapter": "第一章",
            "source_type": "teacher_ppt",
            "expected_updated_at": system.store.get("document", document_id)["updated_at"],
        }
        assert client.patch(base, json=edit).status_code == 409
        claimed = system.store.claim_material_job()
        assert client.patch(base, json=edit).status_code == 409
        preview = client.post(base + "/deletion-preview").json()
        assert (
            client.post(
                base + "/delete",
                json={
                    "confirmation_id": preview["confirmation_id"],
                },
            ).status_code
            == 200
        )
        process_material_job(
            system.store, system.kb, claimed, system.settings.max_upload_mb * 1024 * 1024
        )
        assert system.store.get("document", document_id)["parse_status"] == "deleted"
        assert not any(chunk["document_id"] == document_id for chunk in system.store.chunks)


def test_draft_cannot_be_confirmed_after_its_source_is_deleted(system, tmp_path):
    system.settings.uploads_dir = str(tmp_path / "uploads")
    with TestClient(create_app(system.settings, system)) as client:
        course = client.post("/api/courses", json={"name": "网络"}).json()["course_id"]
        document_id = upload(client, system, course, "草稿来源")
        created = client.post(
            f"/api/courses/{course}/assets",
            json={
                "asset_type": "note",
                "title": "草稿笔记",
                "markdown": "# 草稿",
                "source_document_ids": [document_id],
            },
        ).json()
        base = f"/api/courses/{course}/documents/{document_id}"
        preview = client.post(base + "/deletion-preview").json()
        assert preview["blocking_references"] == 0
        assert (
            client.post(
                base + "/delete",
                json={
                    "confirmation_id": preview["confirmation_id"],
                },
            ).status_code
            == 200
        )
        asset, revision = created["asset"], created["revision"]
        confirm = client.post(
            f"/api/assets/{asset['asset_id']}/revisions/{revision['revision_id']}/confirm"
        )
        assert confirm.status_code == 409
