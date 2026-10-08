from fastapi.testclient import TestClient

from final_review.api import create_app
from final_review.material_jobs import process_material_job


def _upload(client, content=b"TCP three way handshake", headers=None):
    return client.post(
        "/knowledge/upload",
        headers=headers or {},
        data={"course_id": "net", "title": "讲义", "source_type": "homework"},
        files={"file": ("lecture.md", content)},
    )


def _work(system):
    claimed = system.store.claim_material_job()
    assert claimed
    process_material_job(
        system.store, system.kb, claimed, system.settings.max_upload_mb * 1024 * 1024
    )
    return claimed


def test_upload_returns_durable_queued_job_and_publishes_after_worker(system, tmp_path):
    system.settings.uploads_dir = str(tmp_path / "uploads")
    application = create_app(system.settings, system)
    with TestClient(application) as client:
        response = _upload(client)
        assert response.status_code == 202
        payload = response.json()
        assert payload["status"] == "queued"
        assert client.get(payload["status_url"]).json()["status"] == "queued"
        assert system.store.get("document", payload["document_id"])["parse_status"] == "queued"
        assert not any(
            chunk["document_id"] == payload["document_id"] for chunk in system.store.chunks
        )
    with TestClient(application) as client:
        assert client.get(payload["status_url"]).json()["status"] == "queued"
        _work(system)
        assert client.get(payload["status_url"]).json()["status"] == "succeeded"
        assert system.store.get("document", payload["document_id"])["parse_status"] == "ready"
        assert (
            len(
                [
                    chunk
                    for chunk in system.store.chunks
                    if chunk["document_id"] == payload["document_id"]
                ]
            )
            == 1
        )


def test_idempotency_and_failed_job_manual_retry(system, tmp_path):
    system.settings.uploads_dir = str(tmp_path / "uploads")
    with TestClient(create_app(system.settings, system)) as client:
        headers = {"Idempotency-Key": "same-upload"}
        first = _upload(client, headers=headers)
        second = _upload(client, headers=headers)
        assert first.status_code == second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]
        assert _upload(client, b"different", headers=headers).status_code == 409
        assert len(client.get("/api/courses/net/material-jobs").json()["items"]) == 1
        claimed = _work(system)
        assert system.store.get_material_job(claimed["job_id"])["status"] == "succeeded"
        assert client.post(first.json()["status_url"] + "/retry").status_code == 409


def test_permanent_failure_stays_unsearchable_and_retry_reuses_document(system, tmp_path):
    system.settings.uploads_dir = str(tmp_path / "uploads")
    with TestClient(create_app(system.settings, system)) as client:
        response = client.post(
            "/knowledge/upload",
            data={"course_id": "net", "title": "损坏图片", "source_type": "homework"},
            files={"file": ("broken.png", b"not an image")},
        )
        assert response.status_code == 202
        payload = response.json()
        _work(system)
        job = client.get(payload["status_url"]).json()
        assert job["status"] == "failed"
        assert job["error_code"] == "invalid_material"
        assert not any(
            chunk["document_id"] == payload["document_id"] for chunk in system.store.chunks
        )
        retried = client.post(payload["status_url"] + "/retry")
        assert retried.status_code == 202
        assert retried.json()["document_id"] == payload["document_id"]
        assert retried.json()["status"] == "queued"
