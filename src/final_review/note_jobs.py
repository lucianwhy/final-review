"""Durable note generation worker, independent of the browser request."""

import logging
import time
from datetime import UTC, datetime
from threading import Event, Thread

from openai import APITimeoutError

from .agent import (
    FinalReviewAgent,
    NoteProgress,
    SessionConflict,
    bind_note_progress,
    reset_note_progress,
)
from .config import Settings
from .llm import build_models
from .postgres import PostgresStore
from .rag import KnowledgeBase
from .schemas import ResumeNoteRequest
from .storage import stable_key

logger = logging.getLogger(__name__)


def process_note_job(store, agent: FinalReviewAgent, job: dict) -> None:
    token = store.bind_user(job["user_id"])
    stop_heartbeat = Event()
    lost_lease = Event()
    stage = "正在整理资料"

    def heartbeat() -> None:
        # A separate connection avoids using the worker's connection from two threads.
        try:
            lease_store = PostgresStore(store.database_url)
            lease_token = lease_store.bind_user(job["user_id"])
            try:
                while not stop_heartbeat.wait(30):
                    if not lease_store.renew_note_job(job["job_id"], job["attempts"]):
                        lost_lease.set()
                        return
            finally:
                lease_store.reset_user(lease_token)
                lease_store.close()
        except Exception:
            logger.exception("笔记任务续约失败: %s", job["job_id"])
            lost_lease.set()

    lease_thread = None
    if isinstance(store, PostgresStore):
        lease_thread = Thread(target=heartbeat, name="note-job-heartbeat", daemon=True)
        lease_thread.start()

    def update_progress(
        current_stage: str, *, batch_index=None, batch_result=None, selection_plan=None
    ) -> bool:
        nonlocal stage
        if lost_lease.is_set():
            return False
        stage = current_stage
        return store.update_note_job_progress(
            job["job_id"],
            job["attempts"],
            current_stage,
            batch_index=batch_index,
            batch_result=batch_result,
            selection_plan=selection_plan,
        )

    progress_token = bind_note_progress(
        NoteProgress(
            completed=job.get("partial_batches", {}).copy(),
            update=update_progress,
            begin_publish=lambda: store.begin_note_publish(job["job_id"], job["attempts"]),
            selection_plan=job.get("selection_plan"),
            policy_version=job.get("note_policy_version", 1),
        )
    )
    try:
        request = ResumeNoteRequest(
            course_id=job["course_id"],
            session_id=job["session_id"],
            note_input=job["note_input"],
        )
        try:
            result = agent.resume_note(request, job["user_id"])
        except SessionConflict as exc:
            if "租约已失效" in str(exc) or "笔记任务已取消" in str(exc):
                raise
            # A prior worker may have passed the interrupt before losing its lease.
            result = agent.recover(job["course_id"], job["session_id"])
        if lost_lease.is_set():
            raise SessionConflict("笔记任务租约已失效")
        result_data = result.model_dump(mode="json")
        with store.transaction():
            if not store.finish_note_job(job["job_id"], job["attempts"], result=result_data):
                return
            conversation_key = stable_key(job["course_id"], job["conversation_id"])
            conversation = store.get("conversation", conversation_key)
            if not conversation:
                return
            message_key = stable_key(
                job["course_id"], job["conversation_id"], "note", f"{job['event_id']}-assistant"
            )
            if not store.get("message", message_key):
                content = (
                    (result.prompt or {}).get("message")
                    if result.status == "needs_input"
                    else result.answer
                )
                message = {
                    "conversation_id": job["conversation_id"],
                    "course_id": job["course_id"],
                    "user_id": job["user_id"],
                    "role": "assistant",
                    "content": content or "笔记任务已完成",
                    "created_at": datetime.now(UTC).isoformat(),
                }
                if result.draft:
                    message["draft"] = result.draft
                store.put("message", message_key, message)
            active = (
                dict(
                    session_id=job["session_id"],
                    status="needs_input",
                    prompt=result.prompt,
                    note_input=job["note_input"],
                )
                if result.status == "needs_input"
                else None
            )
            store.put(
                "conversation",
                conversation_key,
                {
                    **conversation,
                    "active_note": active,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
    except Exception as exc:
        if (store.get("note_job", job["job_id"]) or {}).get("status") == "cancelled":
            return
        logger.exception("笔记任务处理失败: %s", job["job_id"])
        timed_out = isinstance(exc, (APITimeoutError, TimeoutError)) or any(
            cls.__name__ == "OpenAITimeoutError" for cls in type(exc).__mro__
        )
        error = (
            f"模型请求超时（{stage}），请缩小范围后重试"
            if timed_out
            else f"笔记生成失败（{stage}），请重试"
        )
        store.finish_note_job(job["job_id"], job["attempts"], error=error)
    finally:
        stop_heartbeat.set()
        if lease_thread:
            lease_thread.join(timeout=5)
        reset_note_progress(progress_token)
        store.reset_user(token)


def main() -> None:
    settings = Settings()
    database_url = settings.database_url.get_secret_value()
    if not database_url:
        raise RuntimeError("笔记 worker 需要 DATABASE_URL")
    store = PostgresStore(database_url)
    store.setup()
    model, embeddings = build_models(settings, note_generation=True)
    agent = FinalReviewAgent(store, KnowledgeBase(store, embeddings, settings), model, settings)
    try:
        while True:
            job = store.claim_note_job()
            if job is None:
                time.sleep(1)
                continue
            process_note_job(store, agent, job)
    finally:
        store.close()


if __name__ == "__main__":
    main()
