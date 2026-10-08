"""Durable quiz jobs with lease fencing and a single publication transaction."""

import logging
import time
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from uuid import uuid4

from .config import Settings
from .domain import DomainConflict, DomainNotFound, DomainService
from .llm import ModelError, build_review_model
from .postgres import PostgresStore
from .quiz_generation import QuizGenerationError, generate_quiz, locked, paper_hash, publish_quiz
from .schemas import ResolvedQuizConfig
from .storage import stable_key

logger = logging.getLogger(__name__)


def now():
    return datetime.now(UTC).isoformat()


def public_quiz_job(job):
    return {
        key: job.get(key)
        for key in (
            "job_id",
            "status",
            "stage",
            "error",
            "issues",
            "result",
            "attempts",
            "progress",
            "created_at",
            "updated_at",
        )
    }


def owned_job(store, user_id, course_id, job_id, *, for_update=False):
    job = locked(store, "quiz_job", job_id) if for_update else store.get("quiz_job", job_id)
    if not job or job["user_id"] != user_id or job["course_id"] != course_id:
        raise DomainNotFound(job_id)
    return job


def enqueue_quiz(store, config, *, model_id, conversation_id=None, idempotency_key=None):
    request_hash = paper_hash(
        {
            "config": config.model_dump(mode="json"),
            "model_id": model_id,
            "conversation_id": conversation_id,
        }
    )
    job_id = (
        "quiz-job-"
        + stable_key(config.user_id, config.course_id, idempotency_key or uuid4().hex)[:32]
    )
    with store.transaction():
        if hasattr(store, "lock_quiz_request"):
            store.lock_quiz_request(job_id)
        existing = store.get("quiz_job", job_id)
        if existing:
            if existing["request_hash"] != request_hash:
                raise DomainConflict("相同 Idempotency-Key 不能用于不同的出卷请求")
            return public_quiz_job(existing)
        DomainService(store, config.user_id).course(config.course_id, writable=True)
        conversation = None
        if conversation_id:
            key = stable_key(config.course_id, conversation_id)
            conversation = locked(store, "conversation", key)
            if not conversation or conversation["user_id"] != config.user_id:
                raise DomainNotFound(conversation_id)
            active_id = (conversation.get("active_quiz") or {}).get("job_id")
            active = store.get("quiz_job", active_id) if active_id else None
            if active and active["status"] in {"queued", "running"}:
                raise DomainConflict("当前对话已有试卷正在生成，请等待完成")
        job = {
            "job_id": job_id,
            "user_id": config.user_id,
            "course_id": config.course_id,
            "conversation_id": conversation_id,
            "config": config.model_dump(mode="json"),
            "model_id": model_id,
            "request_hash": request_hash,
            "status": "queued",
            "stage": "queued",
            "attempts": 0,
            "max_attempts": 3,
            "lease_until": None,
            "error": None,
            "issues": [],
            "result": None,
            "available_at": now(),
            "created_at": now(),
            "updated_at": now(),
        }
        store.put("quiz_job", job_id, job)
        if conversation:
            store.put(
                "conversation",
                key,
                {
                    **conversation,
                    "pending_quiz": None,
                    "active_quiz": {"job_id": job_id},
                    "updated_at": now(),
                },
            )
        return public_quiz_job(job)


def retry_quiz(store, user_id, course_id, job_id):
    with store.transaction():
        job = owned_job(store, user_id, course_id, job_id, for_update=True)
        DomainService(store, user_id).course(course_id, writable=True)
        if job["status"] != "failed":
            raise DomainConflict("仅失败的试卷任务可以重试")
        # Revalidate the frozen configuration. Changed versions require a new request.
        from .quiz_generation import quiz_context

        quiz_context(store, ResolvedQuizConfig.model_validate(job["config"]))
        if job.get("conversation_id"):
            key = stable_key(course_id, job["conversation_id"])
            conversation = locked(store, "conversation", key)
            if not conversation or conversation["user_id"] != user_id:
                raise DomainNotFound(job["conversation_id"])
            other_id = (conversation.get("active_quiz") or {}).get("job_id")
            other = store.get("quiz_job", other_id) if other_id and other_id != job_id else None
            if other and other["status"] in {"queued", "running"}:
                raise DomainConflict("当前对话已有试卷正在生成")
            store.put("conversation", key, {**conversation, "active_quiz": {"job_id": job_id}})
        # Keep the attempt counter monotonic so a previous worker can never regain its lease.
        job.update(
            status="queued",
            stage="queued",
            error=None,
            issues=[],
            lease_until=None,
            available_at=now(),
            updated_at=now(),
            max_attempts=job["attempts"] + 3,
        )
        store.put("quiz_job", job_id, job)
        return public_quiz_job(job)


def leased(job, attempt):
    return bool(
        job
        and job["status"] == "running"
        and job["attempts"] == attempt
        and job.get("lease_until")
        and datetime.fromisoformat(job["lease_until"]) > datetime.now(UTC)
    )


def update_progress(store, job, stage):
    with store.transaction():
        current = locked(store, "quiz_job", job["job_id"])
        if not leased(current, job["attempts"]):
            raise DomainConflict("试卷任务租约已失效")
        current.update(
            stage=stage,
            updated_at=now(),
            lease_until=(datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        )
        store.put("quiz_job", job["job_id"], current)


def process_quiz_job(store, model, settings, job):
    token = store.bind_user(job["user_id"])
    stop, lost = Event(), Event()

    def heartbeat():
        lease_token = store.bind_user(job["user_id"])
        try:
            while not stop.wait(30):
                with store.transaction():
                    current = locked(store, "quiz_job", job["job_id"])
                    if not leased(current, job["attempts"]):
                        lost.set()
                        return
                    current["lease_until"] = (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
                    store.put("quiz_job", job["job_id"], current)
        except Exception:
            logger.exception("试卷任务续约失败")
            lost.set()
        finally:
            store.reset_user(lease_token)

    thread = Thread(target=heartbeat, daemon=True) if isinstance(store, PostgresStore) else None
    if thread:
        thread.start()

    def progress(stage):
        if lost.is_set():
            raise DomainConflict("试卷任务租约已失效")
        update_progress(store, job, stage)

    def save_checkpoint(state):
        with store.transaction():
            current = locked(store, "quiz_job", job["job_id"])
            if lost.is_set() or not leased(current, job["attempts"]):
                raise DomainConflict("试卷任务租约已失效")
            current.update(
                checkpoint=state,
                progress={
                    "completed_questions": sum(len(b["questions"]) for b in state["completed"]),
                    "total_questions": job["config"]["question_count"],
                    "completed_batches": len(state["completed"]),
                    "remaining_batches": len(state["pending"]),
                },
                updated_at=now(),
            )
            store.put("quiz_job", job["job_id"], current)

    try:
        generated = generate_quiz(
            store,
            model,
            ResolvedQuizConfig.model_validate(job["config"]),
            max_repairs=settings.quiz_max_repairs,
            progress=progress,
            checkpoint=job.get("checkpoint"),
            save_checkpoint=save_checkpoint,
            choice_batch_size=settings.quiz_choice_batch_size,
            written_batch_size=settings.quiz_written_batch_size,
        )
        progress("publishing")
        with store.transaction():
            current = locked(store, "quiz_job", job["job_id"])
            if lost.is_set() or not leased(current, job["attempts"]):
                return
            current["stage"] = "publishing"
            result = publish_quiz(store, generated, job["job_id"], job["model_id"])
            current.update(
                status="succeeded",
                stage="succeeded",
                result=result,
                lease_until=None,
                error=None,
                issues=[],
                updated_at=now(),
            )
            store.put("quiz_job", job["job_id"], current)
            if job.get("conversation_id"):
                key = stable_key(job["course_id"], job["conversation_id"])
                conversation = locked(store, "conversation", key)
                if conversation and conversation["user_id"] == job["user_id"]:
                    message_id = stable_key(job["user_id"], job["job_id"], "quiz-result")
                    store.put(
                        "message",
                        message_id,
                        {
                            "user_id": job["user_id"],
                            "course_id": job["course_id"],
                            "conversation_id": job["conversation_id"],
                            "role": "assistant",
                            "content": (
                                f"试卷草稿已生成：{result['question_count']} 题，"
                                f"共 {result['total_score']:g} 分。请查看题目、答案和来源。"
                            ),
                            "quiz_draft": result,
                            "created_at": now(),
                        },
                    )
                    if (conversation.get("active_quiz") or {}).get("job_id") == job["job_id"]:
                        conversation.update(active_quiz=None, updated_at=now())
                        store.put("conversation", key, conversation)
    except Exception as exc:
        logger.exception("试卷生成失败: %s", job["job_id"])
        with store.transaction():
            current = locked(store, "quiz_job", job["job_id"])
            if leased(current, job["attempts"]):
                # Never store provider exceptions that may contain headers or credentials.
                safe = isinstance(exc, (QuizGenerationError, DomainConflict, ModelError))
                current.update(
                    status="failed",
                    lease_until=None,
                    updated_at=now(),
                    error=str(exc) if safe else "模型或数据库请求失败，请稍后重试",
                    issues=exc.issues if isinstance(exc, QuizGenerationError) else [],
                    diagnostics=(
                        exc.diagnostics
                        if isinstance(exc, QuizGenerationError)
                        else {"exception_type": type(exc).__name__, "stage": current["stage"]}
                    ),
                )
                store.put("quiz_job", job["job_id"], current)
    finally:
        stop.set()
        if thread:
            thread.join(timeout=5)
        store.reset_user(token)


def main():
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                "logs/quiz-worker.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
            ),
        ],
    )
    settings = Settings()
    store = PostgresStore(settings.database_url.get_secret_value())
    store.setup()
    models = {}
    try:
        while True:
            job = store.claim_quiz_job()
            if job is None:
                time.sleep(1)
                continue
            if job["model_id"] not in models:
                try:
                    config = settings.get_chat_model(job["model_id"])
                    worker_settings = settings.model_copy(
                        update={"model_timeout": settings.quiz_model_timeout}
                    )
                    models[job["model_id"]] = build_review_model(
                        config, worker_settings, quiz_generation=True
                    )
                except ValueError:
                    # Process with a failing adapter so the durable job records a safe error.
                    class UnavailableModel:
                        def quiz_draft_plan(self, *args, **kwargs):
                            raise QuizGenerationError("本次选择的模型已不可用，请重新配置试卷")

                    process_quiz_job(store, UnavailableModel(), settings, job)
                    continue
            process_quiz_job(store, models[job["model_id"]], settings, job)
    finally:
        store.close()


if __name__ == "__main__":
    main()
