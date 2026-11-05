from threading import Event, Thread

from fastapi.testclient import TestClient

from final_review.api import create_app
from final_review.llm import ModelError
from final_review.note_jobs import process_note_job
from final_review.schemas import AgentRequest, NoteInput
from final_review.storage import stable_key


def test_note_job_survives_navigation_and_publishes_in_original_conversation(system):
    store = system.store
    store.put(
        "course",
        "net",
        {"course_id": "net", "user_id": "local-user", "name": "网络", "status": "active"},
    )
    document = store.scan("document", {"course_id": "net"})[0]
    store.put(
        "document",
        document["document_id"],
        {**document, "user_id": "local-user", "parse_status": "ready"},
    )
    with TestClient(create_app(system.settings, system)) as client:
        started = client.post(
            "/agent/invoke?conversation_id=original",
            json={
                "course_id": "net",
                "session_id": "note-one",
                "message": "生成笔记",
                "intent": "note",
            },
        )
        assert started.status_code == 200
        assert started.json()["status"] == "needs_input"
        conversations = client.get("/api/courses/net/conversations").json()["items"]
        assert conversations[0]["title"] == "生成笔记"
        queued = client.post(
            "/agent/queue-note?conversation_id=original&event_id=submit-one",
            json={
                "course_id": "net",
                "session_id": "note-one",
                "note_input": {
                    "note_type": "key_points",
                    "scope": "TCP",
                    "duration_minutes": 10,
                    "source_document_ids": [document["document_id"]],
                },
            },
        )
        assert queued.status_code == 200
        job_id = queued.json()["job_id"]
        repeated = client.post(
            "/agent/queue-note?conversation_id=original&event_id=submit-one",
            json={
                "course_id": "net",
                "session_id": "note-one",
                "note_input": {
                    "note_type": "key_points",
                    "scope": "TCP",
                    "duration_minutes": 10,
                    "source_document_ids": [document["document_id"]],
                },
            },
        )
        assert repeated.json()["job_id"] == job_id
        assert len(store.scan("note_job", {"course_id": "net"})) == 1
        pending = client.get("/api/courses/net/conversations/original/messages").json()
        assert pending["active_note"]["status"] == "queued"
        assert client.get("/api/courses/net/conversations/other/messages").status_code == 404
        job = store.claim_note_job()
        assert job["job_id"] == job_id
        process_note_job(store, system, job)
        history = client.get("/api/courses/net/conversations/original/messages").json()
        assert history["active_note"] is None
        assert history["items"][-1]["draft"]["title"] == "TCP 复习笔记"
        assert store.get("note_job", job_id)["status"] == "succeeded"
        assert len(store.scan("learning_asset", {"course_id": "net"})) == 1
        assert (
            client.patch(
                "/api/courses/net/conversations/original", json={"title": "我的 TCP 笔记"}
            ).status_code
            == 200
        )


def test_new_note_job_rejects_six_files_but_legacy_job_can_resume(system):
    store = system.store
    store.put(
        "course",
        "net",
        {"course_id": "net", "user_id": "local-user", "name": "网络", "status": "active"},
    )
    template = store.scan("document", {"course_id": "net"})[0]
    chunk = next(item for item in store.chunks if item["document_id"] == template["document_id"])
    ids = [f"legacy-file-{index}" for index in range(6)]
    store.chunks = []
    for index, document_id in enumerate(ids):
        store.put(
            "document",
            document_id,
            {
                **template,
                "document_id": document_id,
                "user_id": "local-user",
                "parse_status": "ready",
                "file_name": f"讲义 {index}.pptx",
            },
        )
        store.chunks.append(
            {
                **chunk,
                "document_id": document_id,
                "chunk_id": f"{document_id}-chunk",
                "content": f"第 {index} 份可核对内容",
            }
        )
    with TestClient(create_app(system.settings, system)) as client:
        client.post(
            "/agent/invoke?conversation_id=legacy",
            json={
                "course_id": "net",
                "session_id": "legacy-note",
                "message": "生成笔记",
                "intent": "note",
            },
        )
        body = {
            "course_id": "net",
            "session_id": "legacy-note",
            "note_input": {
                "note_type": "key_points",
                "duration_minutes": 10,
                "source_document_ids": ids,
            },
        }
        rejected = client.post("/agent/queue-note?conversation_id=legacy&event_id=six", json=body)
        assert rejected.status_code == 422
        assert "最多选择 5 份" in rejected.json()["detail"]
        body["note_input"]["source_document_ids"] = ids[:5]
        queued = client.post("/agent/queue-note?conversation_id=legacy&event_id=old", json=body)
        assert queued.status_code == 200
        job_id = queued.json()["job_id"]
        job = store.get("note_job", job_id)
        job["note_input"]["source_document_ids"] = ids
        job.pop("note_policy_version")
        store.put("note_job", job_id, job)
        process_note_job(store, system, store.claim_note_job())
        saved = store.get("note_job", job_id)
        assert saved["status"] == "succeeded"
        assert saved["selection_plan"]["coverage"]["selected_files"] == 6


def test_failed_note_job_can_be_retried(system, monkeypatch):
    store = system.store
    store.put(
        "course",
        "net",
        {"course_id": "net", "user_id": "local-user", "name": "网络", "status": "active"},
    )
    document = store.scan("document", {"course_id": "net"})[0]
    store.put(
        "document",
        document["document_id"],
        {**document, "user_id": "local-user", "parse_status": "ready"},
    )
    with TestClient(create_app(system.settings, system)) as client:
        client.post(
            "/agent/invoke?conversation_id=original",
            json={
                "course_id": "net",
                "session_id": "note-two",
                "message": "生成笔记",
                "intent": "note",
            },
        )
        queued = client.post(
            "/agent/queue-note?conversation_id=original&event_id=submit-two",
            json={
                "course_id": "net",
                "session_id": "note-two",
                "note_input": {
                    "note_type": "key_points",
                    "scope": "TCP",
                    "duration_minutes": 10,
                    "source_document_ids": [document["document_id"]],
                },
            },
        )
        job_id = queued.json()["job_id"]
        original = system.resume_note
        monkeypatch.setattr(system, "resume_note", lambda *_: (_ for _ in ()).throw(RuntimeError()))
        process_note_job(store, system, store.claim_note_job())
        failed = client.get("/api/courses/net/conversations/original/messages").json()
        assert failed["active_note"]["status"] == "failed"
        monkeypatch.setattr(system, "resume_note", original)
        retry = client.post(
            f"/agent/retry-note?course_id=net&conversation_id=original&job_id={job_id}"
        )
        assert retry.status_code == 200
        process_note_job(store, system, store.claim_note_job())
        assert store.get("note_job", job_id)["status"] == "succeeded"


def test_timeout_reports_stage_and_retry_reuses_verified_batches(system, monkeypatch):
    store = system.store
    store.put(
        "course",
        "net",
        {"course_id": "net", "user_id": "local-user", "name": "网络", "status": "active"},
    )
    document = store.scan("document", {"course_id": "net"})[0]
    store.put(
        "document",
        document["document_id"],
        {**document, "user_id": "local-user", "parse_status": "ready"},
    )
    template = next(
        chunk for chunk in store.chunks if chunk["document_id"] == document["document_id"]
    )
    store.chunks = [
        {
            **template,
            "chunk_id": f"chunk-{index}",
            "chunk_ordinal": index,
            "content": f"TCP 可核对内容 {index}",
        }
        for index in range(25)
    ]
    original_note = system.model.note
    calls = []

    def fail_second_batch(data):
        calls.append(data["batch_index"])
        if data["batch_index"] == 2:
            raise TimeoutError("model request timed out")
        return original_note(data)

    monkeypatch.setattr(system.model, "note", fail_second_batch)
    with TestClient(create_app(system.settings, system)) as client:
        client.post(
            "/agent/invoke?conversation_id=original",
            json={
                "course_id": "net",
                "session_id": "note-timeout",
                "message": "生成笔记",
                "intent": "note",
            },
        )
        queued = client.post(
            "/agent/queue-note?conversation_id=original&event_id=submit-timeout",
            json={
                "course_id": "net",
                "session_id": "note-timeout",
                "note_input": {
                    "note_type": "key_points",
                    "scope": "TCP",
                    "duration_minutes": 10,
                    "source_document_ids": [document["document_id"]],
                },
            },
        )
        job_id = queued.json()["job_id"]
        process_note_job(store, system, store.claim_note_job())
        failed = store.get("note_job", job_id)
        assert failed["status"] == "failed"
        assert "模型请求超时" in failed["error"]
        assert set(failed["partial_batches"]) == {"0"}
        original_ids = [item["chunk_id"] for item in failed["selection_plan"]["evidence"]]
        assert failed["selection_plan"]["coverage"]["read_chunks"] == 25
        assert calls == [1, 2]
        active = client.get("/api/courses/net/conversations/original/messages").json()[
            "active_note"
        ]
        assert active["error"] == failed["error"]
        assert active["coverage"]["read_chunks"] == 25

        monkeypatch.setattr(
            system.model,
            "note",
            lambda data: calls.append(data["batch_index"]) or original_note(data),
        )
        assert (
            client.post(
                f"/agent/retry-note?course_id=net&conversation_id=original&job_id={job_id}"
            ).status_code
            == 200
        )
        store.chunks.append(
            {
                **template,
                "chunk_id": "new-earlier-chunk",
                "chunk_ordinal": -1,
                "content": "TCP 新增重点",
            }
        )
        process_note_job(store, system, store.claim_note_job())
        assert store.get("note_job", job_id)["status"] == "succeeded"
        assert [
            item["chunk_id"] for item in store.get("note_job", job_id)["selection_plan"]["evidence"]
        ] == original_ids
        assert calls == [1, 2, 2, 3]
        assets = store.scan("learning_asset", {"course_id": "net"})
        assert len(assets) == 1
        revision = store.get(
            "asset_revision", "revision-" + stable_key(assets[0]["asset_id"], "1")[:32]
        )
        assert len(revision["points"]) <= 20
        assert revision["coverage"]["files"][0]["read_chunks"] == 25
        assert all(
            ref["chunk_id"] in {chunk["chunk_id"] for chunk in store.chunks}
            for point in revision["points"]
            for ref in point["references"]
        )


def test_single_source_first_run_succeeds_with_grounded_fallback(system, monkeypatch):
    store = system.store
    store.put(
        "course",
        "net",
        {"course_id": "net", "user_id": "local-user", "name": "网络", "status": "active"},
    )
    document = store.scan("document", {"course_id": "net"})[0]
    store.put(
        "document",
        document["document_id"],
        {**document, "user_id": "local-user", "parse_status": "ready"},
    )

    def invalid_response(_data):
        raise ModelError("模型连续返回无法解析的结构化内容")

    monkeypatch.setattr(system.model, "note", invalid_response)
    result = system.invoke(
        AgentRequest(
            course_id="net",
            session_id="single-fallback",
            message="生成笔记",
            intent="note",
            note_input=NoteInput(
                note_type="qa_cards",
                duration_minutes=10,
                source_document_ids=[document["document_id"]],
            ),
        ),
        "local-user",
    )
    assert result.status == "completed"
    assert "资料原文摘录" in result.answer
    revision = store.get("asset_revision", result.draft["revision_id"])
    assert "资料摘录" in revision["title"]
    assert revision["points"][0]["references"][0]["quote"] in store.chunks[0]["content"]


def test_three_sources_first_worker_attempt_survives_first_batch_verifier_rejection(
    system,
    monkeypatch,
):
    store = system.store
    store.put(
        "course",
        "net",
        {"course_id": "net", "user_id": "local-user", "name": "网络", "status": "active"},
    )
    template = store.scan("document", {"course_id": "net"})[0]
    base_chunk = next(
        chunk for chunk in store.chunks if chunk["document_id"] == template["document_id"]
    )
    documents = []
    store.chunks = []
    for file_index in range(3):
        document_id = f"source-{file_index}"
        documents.append(document_id)
        store.put(
            "document",
            document_id,
            {
                **template,
                "document_id": document_id,
                "user_id": "local-user",
                "parse_status": "ready",
                "file_name": f"source-{file_index}.pptx",
            },
        )
        store.chunks.extend(
            {
                **base_chunk,
                "document_id": document_id,
                "chunk_id": f"{document_id}-chunk-{index}",
                "chunk_ordinal": index,
                "content": f"来源 {file_index} 第 {index} 处可核对的知识。",
            }
            for index in range(15)
        )
    verification_calls = 0

    def reject_first_batch(_data):
        nonlocal verification_calls
        verification_calls += 1
        return {
            "supported": verification_calls > 2,
            "issues": ["内容未由摘录支持"] if verification_calls <= 2 else [],
        }

    monkeypatch.setattr(system.model, "verify", reject_first_batch)
    with TestClient(create_app(system.settings, system)) as client:
        client.post(
            "/agent/invoke?conversation_id=first-run",
            json={
                "course_id": "net",
                "session_id": "three-sources",
                "message": "生成笔记",
                "intent": "note",
            },
        )
        queued = client.post(
            "/agent/queue-note?conversation_id=first-run&event_id=once",
            json={
                "course_id": "net",
                "session_id": "three-sources",
                "note_input": {
                    "note_type": "qa_cards",
                    "duration_minutes": 10,
                    "source_document_ids": documents,
                },
            },
        )
        job = store.claim_note_job()
        assert job["job_id"] == queued.json()["job_id"]
        process_note_job(store, system, job)
        saved = store.get("note_job", job["job_id"])
        assert saved["status"] == "succeeded"
        assert saved["attempts"] == 1
        assert sorted(saved["partial_batches"]) == ["0", "1", "2", "3", "4"]
        assert saved["partial_batches"]["0"]["fallback_reason"] == "生成内容未通过来源核对"
        assert saved["result"]["status"] == "completed"
        assert len(store.scan("learning_asset", {"course_id": "net"})) == 1


def test_queued_note_can_be_cancelled_and_conversation_reused(system):
    store = system.store
    store.put(
        "course",
        "net",
        {"course_id": "net", "user_id": "local-user", "name": "网络", "status": "active"},
    )
    document = store.scan("document", {"course_id": "net"})[0]
    store.put(
        "document",
        document["document_id"],
        {**document, "user_id": "local-user", "parse_status": "ready"},
    )
    with TestClient(create_app(system.settings, system)) as client:
        client.post(
            "/agent/invoke?conversation_id=cancel-chat",
            json={
                "course_id": "net",
                "session_id": "queued-note",
                "message": "生成笔记",
                "intent": "note",
            },
        )
        queued = client.post(
            "/agent/queue-note?conversation_id=cancel-chat&event_id=one",
            json={
                "course_id": "net",
                "session_id": "queued-note",
                "note_input": {
                    "note_type": "chapter",
                    "duration_minutes": 10,
                    "source_document_ids": [document["document_id"]],
                },
            },
        )
        job_id = queued.json()["job_id"]
        cancelled = client.post(
            "/agent/cancel-note?conversation_id=cancel-chat",
            json={"course_id": "net", "session_id": "queued-note"},
        )
        assert cancelled.status_code == 200
        assert store.get("note_job", job_id)["status"] == "cancelled"
        assert store.claim_note_job() is None
        assert (
            client.get("/api/courses/net/conversations/cancel-chat/messages").json()["active_note"]
            is None
        )
        assert (
            client.post(
                "/agent/invoke?conversation_id=cancel-chat",
                json={
                    "course_id": "net",
                    "session_id": "new-note",
                    "message": "再生成笔记",
                    "intent": "note",
                },
            ).status_code
            == 200
        )


def test_running_cancel_discards_inflight_model_result(system, monkeypatch):
    store = system.store
    store.put(
        "course",
        "net",
        {"course_id": "net", "user_id": "local-user", "name": "网络", "status": "active"},
    )
    document = store.scan("document", {"course_id": "net"})[0]
    store.put(
        "document",
        document["document_id"],
        {**document, "user_id": "local-user", "parse_status": "ready"},
    )
    entered = Event()
    release = Event()
    original_note = system.model.note

    def slow_note(data):
        entered.set()
        assert release.wait(10)
        return original_note(data)

    monkeypatch.setattr(system.model, "note", slow_note)
    with TestClient(create_app(system.settings, system)) as client:
        client.post(
            "/agent/invoke?conversation_id=running-chat",
            json={
                "course_id": "net",
                "session_id": "running-note",
                "message": "生成笔记",
                "intent": "note",
            },
        )
        queued = client.post(
            "/agent/queue-note?conversation_id=running-chat&event_id=two",
            json={
                "course_id": "net",
                "session_id": "running-note",
                "note_input": {
                    "note_type": "chapter",
                    "duration_minutes": 10,
                    "source_document_ids": [document["document_id"]],
                },
            },
        )
        job = store.claim_note_job()
        assert job["job_id"] == queued.json()["job_id"]
        worker = Thread(target=process_note_job, args=(store, system, job))
        worker.start()
        try:
            assert entered.wait(10)
            cancelled = client.post(
                "/agent/cancel-note?conversation_id=running-chat",
                json={"course_id": "net", "session_id": "running-note"},
            )
            assert cancelled.status_code == 200
        finally:
            release.set()
            worker.join(timeout=10)
        assert not worker.is_alive()
        assert store.get("note_job", job["job_id"])["status"] == "cancelled"
        assert store.scan("learning_asset", {"course_id": "net"}) == []


def test_cancel_rejected_after_draft_publication_begins(system):
    store = system.store
    store.put(
        "course",
        "net",
        {"course_id": "net", "user_id": "local-user", "name": "网络", "status": "active"},
    )
    document = store.scan("document", {"course_id": "net"})[0]
    store.put(
        "document",
        document["document_id"],
        {**document, "user_id": "local-user", "parse_status": "ready"},
    )
    with TestClient(create_app(system.settings, system)) as client:
        client.post(
            "/agent/invoke?conversation_id=publish-chat",
            json={
                "course_id": "net",
                "session_id": "publish-note",
                "message": "生成笔记",
                "intent": "note",
            },
        )
        client.post(
            "/agent/queue-note?conversation_id=publish-chat&event_id=three",
            json={
                "course_id": "net",
                "session_id": "publish-note",
                "note_input": {
                    "note_type": "chapter",
                    "duration_minutes": 10,
                    "source_document_ids": [document["document_id"]],
                },
            },
        )
        job = store.claim_note_job()
        assert store.begin_note_publish(job["job_id"], job["attempts"])
        response = client.post(
            "/agent/cancel-note?conversation_id=publish-chat",
            json={"course_id": "net", "session_id": "publish-note"},
        )
        assert response.status_code == 409
        assert store.get("note_job", job["job_id"])["status"] == "running"
