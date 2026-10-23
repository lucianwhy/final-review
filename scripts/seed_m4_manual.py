"""Seed a synthetic confirmed quiz in the dedicated M4 manual database only."""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

from final_review.auth import DatabaseAuth
from final_review.config import Settings
from final_review.domain import DomainService
from final_review.migrations import apply_migrations
from final_review.postgres import PostgresStore
from final_review.quiz_config import resolve_quiz_config
from final_review.quiz_jobs import enqueue_quiz, process_quiz_job
from final_review.quiz_review import confirm_quiz_revision, quiz_confirm_preview
from final_review.schemas import ExamCreate, QuizInput

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import ScriptedModel  # noqa: E402

EMAIL = "m4-manual@example.test"
PASSWORD = "M4manual2026!"
COURSE_ID = "m4-manual-course"


def main():
    url = os.environ.get("M4_MANUAL_DATABASE_URL", "")
    details = conninfo_to_dict(url) if url else {}
    if (
        details.get("host") != "127.0.0.1"
        or str(details.get("port")) != "55432"
        or details.get("dbname") != "m4_manual"
    ):
        raise RuntimeError("只允许连接 127.0.0.1:55432/m4_manual 独立测试库")
    apply_migrations(url)
    with psycopg.connect(url) as connection:
        existing = connection.execute(
            "SELECT id FROM app_users WHERE email=%s", (EMAIL,)
        ).fetchone()
    if existing:
        print("测试账号已经存在；保留现有手动验收数据，不重复播种。")
        return
    settings = Settings(
        _env_file=None, database_url=url, auth_mode="database", llm_api_key="", embedding_api_key=""
    )
    auth = DatabaseAuth(settings)
    auth.setup()
    try:
        user, _ = auth.sign_up(EMAIL, PASSWORD)
    finally:
        auth.close()
    store = PostgresStore(url)
    token = store.bind_user(user.id)
    try:
        now = datetime.now(UTC).isoformat()
        store.put(
            "course",
            COURSE_ID,
            {
                "course_id": COURSE_ID,
                "user_id": user.id,
                "name": "M4 错题手动验收",
                "status": "active",
                "created_at": now,
                "updated_at": now,
            },
        )
        exam = DomainService(store, user.id).create_exam(
            COURSE_ID, ExamCreate(name="M4 模拟期末").model_dump(mode="json")
        )
        store.ingest(
            {
                "document_id": "m4-manual-material",
                "course_id": COURSE_ID,
                "user_id": user.id,
                "title": "TCP 合成讲义",
                "file_name": "tcp-synthetic.md",
                "source_type": "teacher_ppt",
                "chapter": "TCP",
                "parse_status": "ready",
                "cleaned_markdown": "TCP 三次握手同步双方初始序列号并确认双向收发能力。",
            },
            [
                {
                    "chunk_id": "m4-manual-chunk",
                    "document_id": "m4-manual-material",
                    "course_id": COURSE_ID,
                    "chapter": "TCP",
                    "title": "TCP 合成讲义",
                    "source_type": "teacher_ppt",
                    "content": "TCP 三次握手同步双方初始序列号并确认双向收发能力。",
                    "embedding": [1.0, 0.0, 0.0],
                }
            ],
        )
        partial = QuizInput(
            exam_id=exam["exam_id"],
            scope_mode="chapter",
            chapter="TCP",
            blueprint=[
                {"question_type": "choice", "question_count": 2},
                {"question_type": "fill_blank", "question_count": 1},
            ],
            duration_mode="untimed",
            difficulty="basic",
            total_score=30,
            source_document_ids=["m4-manual-material"],
            include_imported_questions=False,
            allow_ai_supplement=False,
        )
        resolved = resolve_quiz_config(store, user.id, COURSE_ID, partial)
        if resolved.status != "ready":
            raise RuntimeError("合成试卷配置未通过校验")
        queued = enqueue_quiz(store, resolved.config, model_id="default")
        job = store.get("quiz_job", queued["job_id"])
        job["available_at"] = "2000-01-01T00:00:00+00:00"
        store.put("quiz_job", queued["job_id"], job)
        claimed = store.claim_quiz_job()
        if not claimed:
            raise RuntimeError("合成试卷任务无法领取")
        process_quiz_job(store, ScriptedModel(), settings, claimed)
        finished = store.get("quiz_job", queued["job_id"])
        if finished["status"] != "succeeded":
            raise RuntimeError(f"合成试卷生成失败：{finished.get('error')}")
        card = finished["result"]
        confirmation = quiz_confirm_preview(
            store, user.id, COURSE_ID, card["asset_id"], card["revision_id"]
        )
        confirm_quiz_revision(
            store,
            user.id,
            COURSE_ID,
            card["asset_id"],
            card["revision_id"],
            confirmation["confirmation_id"],
        )
        print("合成正式试卷已就绪；无外部模型调用。")
        print(f"前端登录：{EMAIL} / {PASSWORD}")
        print(f"课程：{COURSE_ID}；考试：{exam['name']}；试卷：{card['title']}")
    finally:
        store.reset_user(token)
        store.close()


if __name__ == "__main__":
    main()
