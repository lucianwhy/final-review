from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from test_m2_note_draft import generate, prepare

from final_review.api import create_app
from final_review.domain import DomainConflict, DomainNotFound, DomainService


def setup_note(system):
    prepare(system)
    result = generate(system)
    service = DomainService(system.store, "local-user")
    return service, result.draft["asset_id"], result.draft["revision_id"]


def edit_payload(service, asset_id, revision_id):
    revision = service.note_draft(asset_id, revision_id)["revision"]
    points = deepcopy(revision["points"])
    for point in points:
        point["references"] = [
            {key: ref[key] for key in ("document_id", "chunk_id", "quote")}
            for ref in point["references"]
        ]
    points[0]["content"] += "\n学生补充：考试先写定义。"
    return {"base_revision_id": revision_id, "title": "编辑后的 TCP", "points": points}


def confirm(service, asset_id, revision_id):
    preview = service.note_confirm_preview(asset_id, revision_id)
    return service.confirm_note(asset_id, revision_id, preview["confirmation_id"])


def test_list_edit_confirm_and_immutable_history(system):
    service, asset_id, first = setup_note(system)
    assert service.list_notes("net")["items"][0]["latest_revision_id"] == first
    saved = service.edit_note(asset_id, edit_payload(service, asset_id, first))
    second = saved["revision"]["revision_id"]
    assert saved["revision"]["note_type"] == "key_points"
    assert saved["revision"]["edit_source"] == "student"
    assert saved["references"][0]["locator_id"] != "document"
    original = system.store.get("asset_revision", first)
    assert "学生补充" not in original["markdown"]
    confirmed = confirm(service, asset_id, second)
    assert confirmed["asset"]["current_revision_id"] == second
    assert service.note_draft(asset_id, second)["revision"]["state"] == "confirmed"
    third = service.edit_note(asset_id, edit_payload(service, asset_id, second))["revision"][
        "revision_id"
    ]
    item = service.list_notes("net")["items"][0]
    assert item["status"] == "confirmed" and item["has_pending_changes"]
    assert item["current_revision_id"] == second
    before = system.store.get("asset_revision", second)
    preview = service.note_confirm_preview(asset_id, third)
    assert preview["replaces_confirmed"] and preview["before"]["revision_id"] == second
    assert "changes" in preview
    service.confirm_note(asset_id, third, preview["confirmation_id"])
    history = service.note_draft(asset_id, second)["revision"]
    assert history["state"] == "superseded" and history["markdown"] == before["markdown"]


def test_stale_edit_preview_and_bypass_rejected(system):
    service, asset_id, first = setup_note(system)
    token = service.note_confirm_preview(asset_id, first)["confirmation_id"]
    service.edit_note(asset_id, edit_payload(service, asset_id, first))
    with pytest.raises(DomainConflict):
        service.edit_note(asset_id, edit_payload(service, asset_id, first))
    with pytest.raises(DomainConflict):
        service.confirm_note(asset_id, first, token)
    assert system.store.get("confirmation", token)["consumed_at"] is None
    with pytest.raises(DomainConflict):
        service.confirm_revision(asset_id, first)
    with pytest.raises(DomainConflict):
        service.create_revision(asset_id, {"base_revision_id": first})


@pytest.mark.parametrize("mutation", ["quote", "chunk", "provenance", "duplicate", "foreign"])
def test_invalid_edit_does_not_publish_partial_revision(system, mutation):
    service, asset_id, first = setup_note(system)
    payload = edit_payload(service, asset_id, first)
    point = payload["points"][0]
    if mutation == "quote":
        point["references"][0]["quote"] = "伪造的来源摘录"
    elif mutation == "chunk":
        point["references"][0]["chunk_id"] = "missing-chunk"
    elif mutation == "provenance":
        point["provenance"] = "ai_supplement"
    elif mutation == "duplicate":
        payload["points"].append(deepcopy(point))
    else:
        document = system.store.get("document", point["references"][0]["document_id"])
        document["course_id"] = "other-course"
        system.store.put("document", document["document_id"], document)
    before = deepcopy(system.store.tables)
    with pytest.raises(DomainConflict):
        service.edit_note(asset_id, payload)
    assert system.store.tables == before


def test_failed_write_rolls_back_revision_and_refs(system, monkeypatch):
    service, asset_id, first = setup_note(system)
    payload = edit_payload(service, asset_id, first)
    before = deepcopy(system.store.tables)
    original = system.store.put

    def fail(table, key, value):
        if table == "source_reference":
            raise RuntimeError("simulated write failure")
        return original(table, key, value)

    monkeypatch.setattr(system.store, "put", fail)
    with pytest.raises(RuntimeError):
        service.edit_note(asset_id, payload)
    assert system.store.tables == before


def test_source_change_blocks_confirm_and_deleted_snapshot_is_readable(system):
    service, asset_id, first = setup_note(system)
    token = service.note_confirm_preview(asset_id, first)["confirmation_id"]
    document_id = service.note_draft(asset_id, first)["revision"]["points"][0]["references"][0][
        "document_id"
    ]
    document = system.store.get("document", document_id)
    document["parse_status"] = "failed"
    system.store.put("document", document_id, document)
    with pytest.raises(DomainConflict):
        service.confirm_note(asset_id, first, token)
    assert system.store.get("confirmation", token)["consumed_at"] is None
    document["parse_status"] = "ready"
    system.store.put("document", document_id, document)
    confirm(service, asset_id, first)
    preview = service.material_deletion_preview(document_id, "net")
    service.delete_material(document_id, preview["confirmation_id"], "retain_source_snapshot")
    ref = service.note_draft(asset_id, first)["revision"]["points"][0]["references"][0]
    assert ref["available"] is False and ref["snapshot"]["excerpt"] == ref["quote"]


def test_review_apis_authorization_and_token_replay(system):
    service, asset_id, first = setup_note(system)
    with pytest.raises(DomainNotFound):
        DomainService(system.store, "other-user").list_notes("net")
    with pytest.raises(DomainNotFound):
        DomainService(system.store, "other-user").note_draft(asset_id, first)
    with pytest.raises(DomainNotFound):
        DomainService(system.store, "other-user").edit_note(
            asset_id, edit_payload(service, asset_id, first)
        )
    with TestClient(create_app(system.settings, system)) as client:
        assert client.get("/api/courses/net/notes").json()["items"][0]["asset_id"] == asset_id
        saved = client.post(
            f"/api/notes/{asset_id}/revisions", json=edit_payload(service, asset_id, first)
        )
        assert saved.status_code == 200
        second = saved.json()["revision"]["revision_id"]
        preview = client.post(
            f"/api/notes/{asset_id}/confirm-preview", json={"revision_id": second}
        ).json()
        payload = {"revision_id": second, "confirmation_id": preview["confirmation_id"]}
        assert client.post(f"/api/notes/{asset_id}/confirm", json=payload).status_code == 200
        assert client.post(f"/api/notes/{asset_id}/confirm", json=payload).status_code == 409
        assert client.get(f"/api/assets/{asset_id}/revisions/{second}").status_code == 200


def test_expired_confirmation_and_changed_chunk_are_rejected(system):
    service, asset_id, first = setup_note(system)
    token = service.note_confirm_preview(asset_id, first)["confirmation_id"]
    record = system.store.get("confirmation", token)
    record["expires_at"] = "2000-01-01T00:00:00+00:00"
    system.store.put("confirmation", token, record)
    with pytest.raises(DomainConflict, match="过期"):
        service.confirm_note(asset_id, first, token)
    token = service.note_confirm_preview(asset_id, first)["confirmation_id"]
    system.store.chunks[0]["content"] = "这份资料内容已经变化"
    with pytest.raises(DomainConflict, match="片段已变化"):
        service.confirm_note(asset_id, first, token)
    assert system.store.get("learning_asset", asset_id)["status"] == "draft"
    assert system.store.get("confirmation", token)["consumed_at"] is None


def test_unchanged_historical_reference_survives_material_rebuild(system):
    service, asset_id, first = setup_note(system)
    payload = edit_payload(service, asset_id, first)
    ref = payload["points"][0]["references"][0]
    document = system.store.get("document", ref["document_id"])
    old = system.store.list_material_chunks(ref["document_id"])
    document["historical_chunks"] = old
    system.store.put("document", ref["document_id"], document)
    system.store.chunks = [
        row for row in system.store.chunks if row["document_id"] != ref["document_id"]
    ]
    original_version = service.note_draft(asset_id, first)["references"][0]["material_version_id"]
    saved = service.edit_note(asset_id, payload)
    assert saved["references"][0]["material_version_id"] == original_version
    confirm(service, asset_id, saved["revision"]["revision_id"])


def test_edit_accepts_references_merged_across_generation_batches(system):
    service, asset_id, first = setup_note(system)
    payload = edit_payload(service, asset_id, first)
    ref = payload["points"][0]["references"][0]
    chunk = system.store.list_material_chunks(ref["document_id"])[0]
    payload["points"][0]["references"] = [
        {**ref, "quote": chunk["content"][offset : offset + 10]} for offset in range(9)
    ]
    payload["points"][0]["content"] = "较长的合并考点。" * 1500
    with TestClient(create_app(system.settings, system)) as client:
        response = client.post(f"/api/notes/{asset_id}/revisions", json=payload)
        assert response.status_code == 200
        assert len(response.json()["revision"]["points"][0]["references"]) == 9
        revision_id = response.json()["revision"]["revision_id"]
        confirm(service, asset_id, revision_id)
        preview = service.material_deletion_preview(ref["document_id"], "net")
        service.delete_material(
            ref["document_id"], preview["confirmation_id"], "retain_source_snapshot"
        )
        references = service.note_draft(asset_id, revision_id)["revision"]["points"][0][
            "references"
        ]
        assert all(row["snapshot"]["excerpt"] == row["quote"] for row in references)
