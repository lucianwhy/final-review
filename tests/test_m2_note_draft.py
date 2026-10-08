import pytest
from fastapi.testclient import TestClient

from final_review.api import create_app
from final_review.domain import DomainNotFound, DomainService
from final_review.schemas import AgentRequest, NoteInput, ResumeNoteRequest


def prepare(system, document_id=None):
    system.store.put(
        "course",
        "net",
        {"course_id": "net", "user_id": "local-user", "name": "网络", "status": "active"},
    )
    document = system.store.scan("document", {"course_id": "net"})[0]
    document["user_id"] = "local-user"
    document["parse_status"] = "ready"
    system.store.put("document", document["document_id"], document)
    return document


def generate(system, session="note-draft"):
    document = system.store.scan("document", {"course_id": "net"})[0]
    return system.invoke(
        AgentRequest(
            course_id="net",
            session_id=session,
            message="生成笔记",
            intent="note",
            note_input=NoteInput(
                note_type="key_points",
                scope="TCP",
                duration_minutes=10,
                source_document_ids=[document["document_id"]],
            ),
        ),
        "local-user",
    )


def test_note_draft_has_point_locator_and_readable_card(system):
    document = prepare(system)
    result = generate(system)
    assert result.status == "completed"
    assert result.draft["url"].endswith(result.draft["revision_id"])
    references = system.store.scan("source_reference", {"course_id": "net"})
    assert len(references) == 1
    assert references[0]["point_id"] == "p1"
    assert references[0]["document_id"] == document["document_id"]
    assert references[0]["locator_id"] != "document"
    with TestClient(create_app(system.settings, system)) as client:
        response = client.get(
            f"/api/assets/{result.draft['asset_id']}/revisions/{result.draft['revision_id']}"
        )
        assert response.status_code == 200
        assert response.json()["revision"]["points"][0]["references"][0]["chunk_id"]
    with pytest.raises(DomainNotFound):
        DomainService(system.store, "other-user").note_draft(
            result.draft["asset_id"], result.draft["revision_id"]
        )


def test_invalid_citation_uses_real_source_excerpt(system):
    document = prepare(system)
    system.model.note = lambda data: {
        "title": "错误笔记",
        "points": [
            {
                "heading": "考点",
                "content": "内容",
                "provenance": "source",
                "citations": [{"chunk_id": "invented", "quote": "伪造引文"}],
            }
        ],
    }
    result = generate(system)
    assert result.status == "completed"
    assert "资料原文摘录" in result.answer
    revision = system.store.get("asset_revision", result.draft["revision_id"])
    reference = revision["points"][0]["references"][0]
    assert reference["document_id"] == document["document_id"]
    assert reference["chunk_id"] != "invented"
    original = system.store.list_material_chunks(document["document_id"])[0]["content"]
    assert reference["quote"] in original


def test_synthesis_requires_two_distinct_documents(system):
    prepare(system)
    original = system.model.note

    def note(data):
        result = original(data)
        result["points"][0]["provenance"] = "synthesis"
        return result

    system.model.note = note
    result = generate(system)
    assert result.status == "completed"
    revision = system.store.get("asset_revision", result.draft["revision_id"])
    assert revision["points"][0]["provenance"] == "source"
    assert "资料摘录" in revision["title"]


def test_persistence_error_rolls_back_asset_revision_and_refs(system):
    prepare(system)
    original_put = system.store.put

    def fail_source_reference(table, key, data):
        if table == "source_reference":
            raise RuntimeError("simulated write failure")
        return original_put(table, key, data)

    system.store.put = fail_source_reference
    with pytest.raises(RuntimeError, match="simulated write failure"):
        generate(system)
    for table in ("learning_asset", "asset_revision", "source_reference"):
        assert not system.store.scan(table, {"course_id": "net"})


def test_ai_supplement_is_explicit_and_not_material_reference(system):
    prepare(system)
    original = system.model.note

    def note(data):
        result = original(data)
        result["points"].append(
            {
                "heading": "扩展提醒",
                "content": "AI 补充内容",
                "provenance": "ai_supplement",
                "citations": [],
            }
        )
        return result

    system.model.note = note
    result = generate(system)
    revision = system.store.get("asset_revision", result.draft["revision_id"])
    assert revision["points"][1]["provenance"] == "ai_supplement"
    assert revision["points"][1]["references"] == []
    assert len(system.store.scan("source_reference", {"course_id": "net"})) == 1


def test_retry_and_recovery_do_not_duplicate_draft(system):
    prepare(system)
    result = generate(system)
    recovered = system.recover("net", "note-draft")
    assert recovered.draft == result.draft
    assert len(system.store.scan("learning_asset", {"course_id": "net"})) == 1


def test_missing_boundaries_still_create_no_draft(system):
    document = prepare(system)
    first = system.invoke(
        AgentRequest(course_id="net", session_id="clarify", message="生成笔记", intent="note"),
        "local-user",
    )
    assert first.status == "needs_input"
    assert not system.store.scan("learning_asset", {"course_id": "net"})
    second = system.resume_note(
        ResumeNoteRequest(
            course_id="net",
            session_id="clarify",
            note_input=NoteInput(
                note_type="chapter",
                scope="TCP",
                duration_minutes=10,
                source_document_ids=[document["document_id"]],
            ),
        ),
        "local-user",
    )
    assert second.draft
