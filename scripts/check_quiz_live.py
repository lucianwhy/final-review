"""Small real-model acceptance run; only writes to a random disposable database."""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from final_review.config import Settings
from final_review.llm import build_review_model
from final_review.migrations import apply_migrations
from final_review.postgres import PostgresStore
from final_review.quiz_config import resolve_quiz_config
from final_review.quiz_generation import read_quiz
from final_review.quiz_jobs import enqueue_quiz, process_quiz_job
from final_review.schemas import QuizInput


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--output", default="backups/m3-02-live.json")
    args = parser.parse_args()
    settings = Settings()
    model_config = settings.get_chat_model(args.model_id)
    root_url = settings.database_url.get_secret_value()
    name = f"final_review_m302_live_{uuid4().hex}"
    isolated = make_conninfo(root_url, dbname=name)
    with psycopg.connect(root_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    store = None
    started = time.monotonic()
    try:
        apply_migrations(isolated)
        owner = str(uuid4())
        with psycopg.connect(isolated) as connection:
            connection.execute(
                "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,%s)",
                (owner, "quiz-live@example.test", "isolated-test-only"),
            )
            connection.commit()
        store = PostgresStore(isolated)
        store.bind_user(owner)
        now = datetime.now(UTC).isoformat()
        store.put(
            "course",
            "live-course",
            {
                "course_id": "live-course",
                "user_id": owner,
                "name": "M3-02 合成验收课程",
                "status": "active",
                "created_at": now,
            },
        )
        text = (
            "TCP三次握手用于建立连接、同步双方初始序列号，并使双方确认对端收发能力。"
            "客户端发送SYN，序列号为x；服务器回复SYN和ACK，序列号为y，确认号为x+1；"
            "客户端回复ACK，确认号为y+1。SYN占用一个序列号。"
            "第三次握手使服务器确认客户端收到了服务器的响应。"
        )
        store.ingest(
            {
                "document_id": "live-material",
                "course_id": "live-course",
                "user_id": owner,
                "title": "TCP 合成测试样本",
                "file_name": "synthetic-tcp.md",
                "source_type": "other_practice",
                "chapter": "TCP",
                "parse_status": "ready",
                "cleaned_markdown": text,
            },
            [
                {
                    "chunk_id": "live-chunk",
                    "document_id": "live-material",
                    "course_id": "live-course",
                    "title": "TCP 合成测试样本",
                    "chapter": "TCP",
                    "source_type": "other_practice",
                    "content": text,
                    "embedding": [1.0, 0.0, 0.0],
                }
            ],
        )
        config = resolve_quiz_config(
            store,
            owner,
            "live-course",
            QuizInput(
                scope_mode="knowledge_points",
                chapter="TCP",
                knowledge_points=["三次握手"],
                blueprint=[
                    {"question_type": "choice", "question_count": 1},
                    {"question_type": "short_answer", "question_count": 1},
                ],
                total_score=20,
                duration_mode="untimed",
                difficulty="standard",
                include_imported_questions=False,
                allow_ai_supplement=False,
                source_document_ids=["live-material"],
            ),
        ).config
        queued = enqueue_quiz(store, config, model_id=model_config.id)
        model = build_review_model(
            model_config, settings.model_copy(update={"model_timeout": settings.quiz_model_timeout})
        )
        print(f"开始真实模型验收：{model_config.id}；2题，20分", flush=True)
        process_quiz_job(store, model, settings, store.claim_quiz_job())
        job = store.get("quiz_job", queued["job_id"])
        result = {
            "model_id": model_config.id,
            "model": model_config.model,
            "status": job["status"],
            "error": job["error"],
            "issues": job["issues"],
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "source_kind": "synthetic_acceptance_sample",
            "created_at": now,
        }
        if job["result"]:
            card = job["result"]
            result["paper"] = read_quiz(
                store, owner, "live-course", card["asset_id"], card["revision_id"]
            )["revision"]
        elif job.get("diagnostics"):
            result["diagnostics"] = job["diagnostics"]
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {k: v for k, v in result.items() if k not in {"paper", "diagnostics"}},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0 if job["status"] == "succeeded" else 1
    finally:
        if store:
            store.close()
        with psycopg.connect(root_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        print("真实模型验收数据库已删除；用户课程库未迁移。", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
