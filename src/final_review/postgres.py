"""PostgreSQL implementation of the existing Store seam.

It deliberately retains the Store API, allowing the RAG and LangGraph workflow
to move off SurrealDB without changing their business rules.  A ContextVar
binds every request to the authenticated user before any read or write.
"""

import json
from contextlib import contextmanager
from contextvars import ContextVar, Token
from hashlib import md5, sha256
from pathlib import Path
from threading import Lock, local
from weakref import WeakSet

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .source_locators import chunk_locator, material_version
from .storage import StorageError, stable_key

_user_id: ContextVar[str | None] = ContextVar("final_review_user_id", default=None)


class PostgresStore:
    TABLES = {
        "course": "courses",
        "document": "documents",
        "chunk": "document_chunks",
        "conversation": "conversations",
        "message": "messages",
        "note_job": "note_jobs",
        "quiz_job": "quiz_jobs",
        "export_job": "export_jobs",
        "review_session": "review_sessions",
        "knowledge_point": "knowledge_points",
        "checkpoint": "checkpoints",
        "pending_write": "pending_writes",
        "attempt": "attempts",
        "learning_event": "learning_events",
        "fast_quiz_session": "fast_quiz_sessions",
        "exam": "exams",
        "learning_asset": "learning_assets",
        "asset_revision": "asset_revisions",
        "quiz_revision_payload": "quiz_revision_payloads",
        "question_revision": "question_revisions",
        "material_version": "material_versions",
        "source_reference": "source_references",
        "source_snapshot": "source_reference_snapshots",
        "confirmation": "confirmation_requests",
        "audit_event": "audit_events",
    }

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._local = local()
        self._connections: WeakSet[psycopg.Connection] = WeakSet()
        self._connections_lock = Lock()
        self._closed = False

    @property
    def connection(self) -> psycopg.Connection:
        """One physical session per thread; LangGraph checkpointers never share it."""
        connection = getattr(self._local, "connection", None)
        if connection is None or getattr(connection, "closed", False):
            with self._connections_lock:
                if self._closed:
                    raise StorageError("数据库连接已关闭")
                connection = psycopg.connect(
                    self.database_url,
                    row_factory=dict_row,
                    autocommit=True,
                )
                self._connections.add(connection)
            self._local.connection = connection
        return connection

    @property
    def _transaction_depth(self) -> int:
        return getattr(self._local, "transaction_depth", 0)

    @contextmanager
    def transaction(self):
        """Commit or roll back the current thread's atomic operation on exit."""
        connection = self.connection
        self._local.transaction_depth = self._transaction_depth + 1
        try:
            with connection.transaction():
                yield
        finally:
            self._local.transaction_depth -= 1

    def close(self):
        with self._connections_lock:
            self._closed = True
            for connection in list(self._connections):
                connection.close()
            self._connections.clear()

    def setup(self):
        """Fail fast when the self-managed PostgreSQL migration is absent."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                if cursor.fetchone() is None:
                    raise StorageError(
                        "缺少 pgvector；请先执行 db/migrations/001_database_auth.sql"
                    )
                cursor.execute("SELECT to_regclass('public.document_chunks') AS table_name")
                if cursor.fetchone()["table_name"] is None:
                    raise StorageError("缺少第二阶段数据表；请先执行数据库迁移")
                cursor.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = %s",
                    ("003_m0_database_hardening.sql",),
                )
                if cursor.fetchone() is None:
                    raise StorageError(
                        "数据库迁移未完成；请先运行 python -m final_review.migrations"
                    )
                cursor.execute("SELECT to_regclass('public.material_jobs') AS table_name")
                if cursor.fetchone()["table_name"] is None:
                    raise StorageError("缺少资料 Job 表；请先运行数据库迁移")
                cursor.execute("SELECT to_regclass('public.note_jobs') AS table_name")
                if cursor.fetchone()["table_name"] is None:
                    raise StorageError("缺少笔记 Job 表；请先运行数据库迁移")
                cursor.execute("SELECT to_regclass('public.export_jobs') AS table_name")
                if cursor.fetchone()["table_name"] is None:
                    raise StorageError("缺少导出 Job 表；请先运行数据库迁移")
                cursor.execute("SELECT to_regclass('public.quiz_jobs') AS table_name")
                if cursor.fetchone()["table_name"] is None:
                    raise StorageError("缺少试卷 Job 表；请先运行数据库迁移")
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 初始化检查失败") from exc

    def bind_user(self, user_id: str) -> Token:
        return _user_id.set(user_id)

    def reset_user(self, token: Token):
        _user_id.reset(token)

    def _user(self) -> str:
        user = _user_id.get()
        if not user:
            raise StorageError("数据库操作缺少已认证用户上下文")
        return user

    def session_key(self, course_id: str, session_id: str) -> str:
        """Prevent equal client session names from sharing LangGraph checkpoints."""
        return stable_key(self._user(), course_id, session_id)

    def _table(self, table: str) -> str:
        try:
            return self.TABLES[table]
        except KeyError as exc:
            raise ValueError("未知数据表") from exc

    def _columns(self, table: str, data: dict) -> dict:
        names = [
            "course_id",
            "conversation_id",
            "document_id",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "created_at",
        ]
        if table == "source_reference":
            names.remove("document_id")
        if table == "course":
            names.extend(
                ["status", "archived_at", "deleted_at", "purge_after", "deletion_confirmed_at"]
            )
        if table == "attempt":
            names.extend(["legacy_session_id", "quiz_revision_id"])
        if table == "learning_asset":
            names.extend(["asset_id", "current_revision_id"])
        if table == "asset_revision":
            names.extend(
                ["revision_id", "asset_id", "revision_no", "state", "confirmed_at", "content_hash"]
            )
        if table == "material_version":
            names.append("material_version_id")
        if table == "source_reference":
            names.extend(["asset_revision_id", "material_version_id", "locator_id"])
        if table == "fast_quiz_session":
            names.append("session_id")
        if table == "quiz_revision_payload":
            names.extend(["quiz_revision_id", "state", "asset_revision_id"])
        if table == "question_revision":
            names.extend(["question_revision_id", "quiz_revision_id"])
        if table == "source_reference":
            names.append("question_revision_id")
        return {key: data[key] for key in names if key in data}

    def put(self, table: str, key: str, data: dict):
        if table == "asset_revision":
            data = {**data, "content_hash": self._revision_hash(data)}
        if table == "source_reference" and "asset_revision_id" not in data:
            data = {**data, "asset_revision_id": data.get("revision_id"), "locator_id": "document"}
        physical = self._table(table)
        user_id = self._user()
        columns = self._columns(table, data)
        names = ["record_key", "user_id", *columns, "data"]
        values = [key, user_id, *columns.values(), Jsonb(data)]
        update = [
            f"{name} = EXCLUDED.{name}" for name in names if name not in {"record_key", "user_id"}
        ]
        placeholders = ", ".join(["%s"] * len(names))
        sql = (
            f"INSERT INTO {physical} ({', '.join(names)}) VALUES ({placeholders}) "
            f"ON CONFLICT (record_key) DO UPDATE SET {', '.join(update)}"
        )
        try:
            with self.transaction():
                with self.connection.cursor() as cursor:
                    cursor.execute(sql, values)
                    if table == "material_version":
                        cursor.execute(
                            "INSERT INTO material_locators("
                            "user_id,course_id,material_version_id,locator_id,locator_kind,ordinal"
                            ") VALUES (%s,%s,%s,'document','document',0) ON CONFLICT DO NOTHING",
                            (user_id, data["course_id"], data["material_version_id"]),
                        )
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 写入失败") from exc

    @staticmethod
    def _revision_hash(data: dict) -> str:
        source_ids = data.get("source_document_ids", [])
        return md5(
            (
                data.get("title", "")
                + "\n"
                + data.get("markdown", "")
                + "\n"
                + json.dumps(source_ids, ensure_ascii=False, separators=(",", ":"))
            ).encode()
        ).hexdigest()

    def get(self, table: str, key: str) -> dict | None:
        physical = self._table(table)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT data FROM {physical} WHERE record_key = %s AND user_id = %s",
                    (key, self._user()),
                )
                row = cursor.fetchone()
            return row["data"] if row else None
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 查询失败") from exc

    def get_for_update(self, table: str, key: str) -> dict | None:
        """Read an owned record while holding a row lock inside ``transaction``."""
        if not self._transaction_depth:
            raise StorageError("行锁读取必须在事务中执行")
        physical = self._table(table)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT data FROM {physical} WHERE record_key = %s "
                    "AND user_id = %s FOR UPDATE",
                    (key, self._user()),
                )
                row = cursor.fetchone()
            return row["data"] if row else None
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 行锁查询失败") from exc

    def scan(self, table: str, filters: dict) -> list[dict]:
        physical = self._table(table)
        allowed = {
            "user_id",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "course_id",
            "conversation_id",
            "document_id",
        }
        if not filters.keys() <= allowed:
            raise ValueError("不支持的查询字段")
        clauses, values = ["user_id = %s"], [self._user()]
        for key, value in filters.items():
            if key == "user_id":
                if value != self._user():
                    return []
                continue
            clauses.append(f"{key} = %s")
            values.append(value)
        order = " ORDER BY created_at ASC" if table == "message" else ""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT data FROM {physical} WHERE {' AND '.join(clauses)}{order}", values
                )
                return [row["data"] for row in cursor.fetchall()]
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 查询失败") from exc

    def delete(self, table: str, key: str):
        physical = self._table(table)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM {physical} WHERE record_key = %s AND user_id = %s",
                    (key, self._user()),
                )
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 删除失败") from exc

    def delete_document(self, key: str):
        user_id = self._user()
        try:
            with self.transaction():
                with self.connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM document_chunks WHERE document_id = %s AND user_id = %s",
                        (key, user_id),
                    )
                    cursor.execute(
                        "DELETE FROM documents WHERE record_key = %s AND user_id = %s",
                        (key, user_id),
                    )
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 删除资料失败") from exc

    def deindex_document(self, key: str):
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM document_chunks WHERE document_id = %s AND user_id = %s",
                    (key, self._user()),
                )
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 移除资料检索索引失败") from exc

    def update_material_metadata(self, key: str, changes: dict) -> dict | None:
        """Update display metadata and indexed chunk metadata under one row lock."""
        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT data FROM documents WHERE record_key=%s AND user_id=%s FOR UPDATE",
                    (key, self._user()),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                document = row["data"]
                if (
                    document.get("parse_status") not in {"ready", "failed"}
                    or document.get("updated_at") != changes["expected_updated_at"]
                ):
                    return None
                from datetime import UTC, datetime

                document.update(
                    {field: changes[field] for field in ("title", "chapter", "source_type")}
                )
                document.pop("material_version_id", None)
                document["updated_at"] = datetime.now(UTC).isoformat()
                cursor.execute(
                    "UPDATE documents SET data=%s WHERE record_key=%s AND user_id=%s",
                    (Jsonb(document), key, self._user()),
                )
                cursor.execute(
                    "UPDATE document_chunks SET data = data || %s::jsonb "
                    "WHERE document_id=%s AND user_id=%s",
                    (
                        Jsonb(
                            {
                                field: document[field]
                                for field in ("title", "chapter", "source_type")
                            }
                        ),
                        key,
                        self._user(),
                    ),
                )
        return document

    def ingest(self, document: dict, chunks: list[dict]):
        # One transaction ensures failed parsing never exposes a half-indexed document.
        user_id = self._user()
        document = {
            **document,
            "user_id": user_id,
            "parse_status": document.get("parse_status", "ready"),
        }
        document["material_version_id"] = material_version(document)["material_version_id"]
        try:
            with self.transaction():
                with self.connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO documents(record_key,user_id,course_id,document_id,data) "
                        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(record_key) DO UPDATE SET "
                        "course_id=EXCLUDED.course_id,data=EXCLUDED.data",
                        (
                            document["document_id"],
                            user_id,
                            document["course_id"],
                            document["document_id"],
                            Jsonb(document),
                        ),
                    )
                    cursor.execute(
                        "DELETE FROM document_chunks WHERE document_id = %s AND user_id = %s",
                        (document["document_id"], user_id),
                    )
                    for chunk in chunks:
                        cursor.execute(
                            "INSERT INTO document_chunks("
                            "record_key,user_id,course_id,document_id,data,content,embedding"
                            ") VALUES (%s,%s,%s,%s,%s,%s,%s::vector)",
                            (
                                chunk["chunk_id"],
                                user_id,
                                chunk["course_id"],
                                chunk["document_id"],
                                Jsonb(chunk),
                                chunk["content"],
                                self._vector(chunk["embedding"]),
                            ),
                        )
                    self._persist_material_source(cursor, document, chunks)
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 资料入库失败") from exc

    def create_material_job(self, document: dict, job: dict) -> dict:
        """Create placeholder and job together; a repeated key returns its first job."""
        user_id = self._user()
        try:
            with self.transaction():
                with self.connection.cursor() as cursor:
                    existing = None
                    if job["idempotency_key"]:
                        cursor.execute(
                            "SELECT * FROM material_jobs WHERE user_id=%s AND course_id=%s "
                            "AND idempotency_key=%s FOR UPDATE",
                            (user_id, job["course_id"], job["idempotency_key"]),
                        )
                        existing = cursor.fetchone()
                    if existing is None:
                        cursor.execute(
                            "INSERT INTO documents(record_key,user_id,course_id,document_id,data) "
                            "VALUES (%s,%s,%s,%s,%s)",
                            (
                                document["document_id"],
                                user_id,
                                document["course_id"],
                                document["document_id"],
                                Jsonb(document),
                            ),
                        )
                        cursor.execute(
                            "INSERT INTO material_jobs(job_id,user_id,course_id,document_id,"
                            "idempotency_key,fingerprint,status,stage) "
                            "VALUES (%s,%s,%s,%s,%s,%s,'queued',NULL)",
                            (
                                job["job_id"],
                                user_id,
                                job["course_id"],
                                job["document_id"],
                                job["idempotency_key"],
                                job["fingerprint"],
                            ),
                        )
            return self._job(existing) if existing else self.get_material_job(job["job_id"])
        except psycopg.errors.UniqueViolation as exc:
            if job["idempotency_key"]:
                existing = self.find_material_job(job["course_id"], job["idempotency_key"])
                if existing:
                    return existing
            duplicate = self.find_duplicate_material(
                job["course_id"],
                document["file_name"],
                document["content_sha256"],
            )
            if duplicate and duplicate["job"]:
                return duplicate["job"]
            raise StorageError("资料任务创建冲突") from exc

    def find_duplicate_material(
        self, course_id: str, file_name: str, content_sha256: str
    ) -> dict | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT data FROM documents WHERE user_id=%s AND course_id=%s "
                "AND data->>'file_name'=%s AND data->>'content_sha256'=%s "
                "AND data->>'parse_status'<>'deleted' LIMIT 1",
                (self._user(), course_id, file_name, content_sha256),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "SELECT data FROM documents WHERE user_id=%s AND course_id=%s "
                    "AND data->>'file_name'=%s AND NOT data ? 'content_sha256' "
                    "AND data->>'parse_status'<>'deleted'",
                    (self._user(), course_id, file_name),
                )
                for legacy in cursor.fetchall():
                    path_value = legacy["data"].get("file_path")
                    if not path_value:
                        continue
                    path = Path(path_value)
                    if not path.is_absolute():
                        path = Path(__file__).resolve().parents[2] / path
                    if path.is_file() and path.stat().st_size <= 50 * 1024 * 1024:
                        if sha256(path.read_bytes()).hexdigest() == content_sha256:
                            row = legacy
                            break
        if not row:
            return None
        document = row["data"]
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM material_jobs WHERE user_id=%s AND "
                "document_id=%s ORDER BY created_at DESC LIMIT 1",
                (self._user(), document["document_id"]),
            )
            job_row = cursor.fetchone()
        return {"document": document, "job": self._job(job_row) if job_row else None}

    @staticmethod
    def _job(row: dict) -> dict:
        return {
            key: value.isoformat() if hasattr(value, "isoformat") else value
            for key, value in row.items()
            if key != "user_id"
        }

    def find_material_job(self, course_id: str, key: str) -> dict | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM material_jobs WHERE user_id=%s AND course_id=%s "
                "AND idempotency_key=%s",
                (self._user(), course_id, key),
            )
            row = cursor.fetchone()
        return self._job(row) if row else None

    def get_material_job(self, job_id: str) -> dict | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM material_jobs WHERE job_id=%s AND user_id=%s", (job_id, self._user())
            )
            row = cursor.fetchone()
        return self._job(row) if row else None

    def list_material_jobs(self, course_id: str) -> list[dict]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM material_jobs WHERE user_id=%s AND course_id=%s "
                "ORDER BY created_at DESC",
                (self._user(), course_id),
            )
            return [self._job(row) for row in cursor.fetchall()]

    def claim_export_job(self) -> dict | None:
        """Claim across owners; every subsequent operation binds the returned owner."""
        from datetime import UTC, datetime, timedelta

        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT record_key,user_id,data FROM export_jobs WHERE "
                    "data->>'status'='queued' OR (data->>'status'='running' AND "
                    "(data->>'lease_until')::timestamptz<now()) "
                    "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                job = row["data"]
                attempts = job.get("attempts", 0) + 1
                job = {
                    **job,
                    "attempts": attempts,
                    "error": None,
                    "status": "running",
                    "lease_until": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                }
                if attempts > job.get("max_attempts", 3):
                    job.update(status="failed", lease_until=None, error="导出多次中断，请重试")
                cursor.execute(
                    "UPDATE export_jobs SET data=%s WHERE record_key=%s",
                    (Jsonb(job), row["record_key"]),
                )
        return {**job, "user_id": str(row["user_id"])} if job["status"] == "running" else None

    def finish_export_job(
        self, job_id: str, attempt: int, *, result: dict | None = None, error: str | None = None
    ) -> bool:
        with self.transaction():
            job = self.get_for_update("export_job", job_id)
            if not job or job["status"] != "running" or job["attempts"] != attempt:
                return False
            job.update(
                status="failed" if error else "succeeded",
                result=result,
                error=error,
                lease_until=None,
            )
            self.put("export_job", job_id, job)
        return True

    def lock_quiz_request(self, key: str):
        if not self._transaction_depth:
            raise StorageError("任务锁必须在事务中执行")
        self.connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (key,))

    def claim_quiz_job(self) -> dict | None:
        from datetime import UTC, datetime, timedelta

        with self.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT record_key,user_id,data FROM quiz_jobs WHERE "
                "(data->>'status'='queued' AND (data->>'available_at')::timestamptz<=now()) "
                "OR (data->>'status'='running' AND (data->>'lease_until')::timestamptz<now()) "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            )
            row = cursor.fetchone()
            if row is None:
                return None
            job = row["data"]
            attempt = job["attempts"] + 1
            if attempt > job["max_attempts"]:
                job.update(status="failed", lease_until=None, error="生成多次中断，请重试")
            else:
                job.update(
                    status="running",
                    attempts=attempt,
                    error=None,
                    lease_until=(datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
                )
            cursor.execute(
                "UPDATE quiz_jobs SET data=%s WHERE record_key=%s", (Jsonb(job), row["record_key"])
            )
        return {**job, "user_id": str(row["user_id"])} if job["status"] == "running" else None

    def claim_note_job(self) -> dict | None:
        """Claim a queued note or an expired lease, across worker processes."""
        # Each poll gets a fresh transaction timestamp, including empty polls.
        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT record_key,user_id,data FROM note_jobs WHERE "
                    "(data->>'status'='queued' AND (data->>'available_at')::timestamptz<=now()) "
                    "OR (data->>'status'='running' AND "
                    "(data->>'lease_until')::timestamptz<now()) "
                    "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                job = row["data"]
                attempts = job.get("attempts", 0) + 1
                if attempts > job.get("max_attempts", 3):
                    job = {
                        **job,
                        "status": "failed",
                        "error": "笔记生成多次中断，请重试",
                        "lease_until": None,
                    }
                else:
                    from datetime import UTC, datetime, timedelta

                    job = {
                        **job,
                        "status": "running",
                        "attempts": attempts,
                        "lease_until": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
                        "error": None,
                        "publish_started": False,
                    }
                cursor.execute(
                    "UPDATE note_jobs SET data=%s WHERE record_key=%s",
                    (Jsonb(job), row["record_key"]),
                )
        return {**job, "user_id": str(row["user_id"])} if job["status"] == "running" else None

    def finish_note_job(
        self, job_id: str, attempt: int, *, result: dict | None = None, error: str | None = None
    ) -> bool:
        """Only the current lease holder may publish a terminal result."""
        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT data FROM note_jobs WHERE record_key=%s AND user_id=%s FOR UPDATE",
                    (job_id, self._user()),
                )
                row = cursor.fetchone()
                if (
                    not row
                    or row["data"]["status"] != "running"
                    or row["data"]["attempts"] != attempt
                ):
                    return False
                job = {
                    **row["data"],
                    "status": "failed" if error else "succeeded",
                    "result": result,
                    "error": error,
                    "lease_until": None,
                    "publish_started": (
                        False if error else row["data"].get("publish_started", False)
                    ),
                }
                cursor.execute(
                    "UPDATE note_jobs SET data=%s WHERE record_key=%s", (Jsonb(job), job_id)
                )
        return True

    def cancel_note_job(self, job_id: str) -> str:
        """Cancel a queued/running job unless final draft publication has begun."""
        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT data FROM note_jobs WHERE record_key=%s AND user_id=%s FOR UPDATE",
                    (job_id, self._user()),
                )
                row = cursor.fetchone()
                if row is None:
                    return "missing"
                job = row["data"]
                if job["status"] == "cancelled":
                    return "cancelled"
                if job["status"] not in {"queued", "running", "failed"}:
                    return "completed"
                if job.get("publish_started"):
                    return "publishing"
                job = {
                    **job,
                    "status": "cancelled",
                    "lease_until": None,
                    "error": None,
                    "partial_batches": {},
                }
                cursor.execute(
                    "UPDATE note_jobs SET data=%s WHERE record_key=%s", (Jsonb(job), job_id)
                )
        return "cancelled"

    def begin_note_publish(self, job_id: str, attempt: int) -> bool:
        """Fence cancellation before the first draft write."""
        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT data FROM note_jobs WHERE record_key=%s AND user_id=%s FOR UPDATE",
                    (job_id, self._user()),
                )
                row = cursor.fetchone()
                if (
                    not row
                    or row["data"]["status"] != "running"
                    or row["data"]["attempts"] != attempt
                ):
                    return False
                job = {**row["data"], "publish_started": True}
                cursor.execute(
                    "UPDATE note_jobs SET data=%s WHERE record_key=%s", (Jsonb(job), job_id)
                )
        return True

    def update_note_job_progress(
        self,
        job_id: str,
        attempt: int,
        stage: str,
        *,
        batch_index: int | None = None,
        batch_result: dict | None = None,
        selection_plan: dict | None = None,
    ) -> bool:
        """Save completed batches before the graph node commits its checkpoint."""
        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT data FROM note_jobs WHERE record_key=%s AND user_id=%s FOR UPDATE",
                    (job_id, self._user()),
                )
                row = cursor.fetchone()
                if (
                    not row
                    or row["data"]["status"] != "running"
                    or row["data"]["attempts"] != attempt
                ):
                    return False
                job = {**row["data"], "stage": stage}
                if selection_plan is not None:
                    job["selection_plan"] = selection_plan
                if batch_index is not None and batch_result is not None:
                    job["partial_batches"] = {
                        **job.get("partial_batches", {}),
                        str(batch_index): batch_result,
                    }
                cursor.execute(
                    "UPDATE note_jobs SET data=%s WHERE record_key=%s", (Jsonb(job), job_id)
                )
        return True

    def renew_note_job(self, job_id: str, attempt: int) -> bool:
        """Keep a live long-running generation from being claimed twice."""
        from datetime import UTC, datetime, timedelta

        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT data FROM note_jobs WHERE record_key=%s AND user_id=%s FOR UPDATE",
                    (job_id, self._user()),
                )
                row = cursor.fetchone()
                if (
                    not row
                    or row["data"]["status"] != "running"
                    or row["data"]["attempts"] != attempt
                ):
                    return False
                job = {
                    **row["data"],
                    "lease_until": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
                }
                cursor.execute(
                    "UPDATE note_jobs SET data=%s WHERE record_key=%s", (Jsonb(job), job_id)
                )
        return True

    def claim_material_job(self) -> dict | None:
        """One worker claims one due job; expired leases can be claimed again."""
        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE material_jobs AS j SET status='failed',"
                    "error_code='source_removed',error_message='资料已删除',"
                    "lease_until=NULL,finished_at=now() FROM documents AS d "
                    "WHERE j.document_id=d.document_id AND j.user_id=d.user_id "
                    "AND d.data->>'parse_status'='deleted' "
                    "AND j.status IN ('queued','running')"
                )
                cursor.execute(
                    "UPDATE material_jobs SET status='failed',error_code='lease_expired',"
                    "error_message='资料处理多次中断，请手动重试',finished_at=now(),"
                    "lease_until=NULL WHERE status='running' AND lease_until<now() "
                    "AND attempts>=max_attempts RETURNING user_id,document_id,error_message"
                )
                for expired in cursor.fetchall():
                    cursor.execute(
                        "UPDATE documents SET data=jsonb_set(jsonb_set(data,"
                        "'{parse_status}',to_jsonb('failed'::text)),'{parse_error}',"
                        "to_jsonb(%s::text)) WHERE record_key=%s AND user_id=%s "
                        "AND data->>'parse_status' <> 'deleted'",
                        (expired["error_message"], expired["document_id"], expired["user_id"]),
                    )
                cursor.execute(
                    "SELECT * FROM material_jobs WHERE "
                    "(status='queued' AND available_at<=now()) OR "
                    "(status='running' AND lease_until<now() AND attempts<max_attempts) "
                    "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                )
                row = cursor.fetchone()
                claimed = None
                if row:
                    cursor.execute(
                        "UPDATE material_jobs SET status='running', attempts=attempts+1, "
                        "stage='parse', started_at=now(), "
                        "lease_until=now()+interval '15 minutes', "
                        "error_code=NULL,error_message=NULL WHERE job_id=%s RETURNING *",
                        (row["job_id"],),
                    )
                    claimed = {**self._job(cursor.fetchone()), "user_id": str(row["user_id"])}
                    cursor.execute(
                        "UPDATE documents SET data=(data - 'parse_error') || "
                        "jsonb_build_object('parse_status','running') "
                        "WHERE record_key=%s AND user_id=%s",
                        (row["document_id"], row["user_id"]),
                    )
        return claimed

    def update_material_job(self, job_id: str, attempt: int, *, stage: str):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE material_jobs SET stage=%s, lease_until=now()+interval '15 minutes' "
                "WHERE job_id=%s AND user_id=%s AND status='running' AND attempts=%s",
                (stage, job_id, self._user(), attempt),
            )

    def fail_material_job(
        self, job_id: str, attempt: int, *, code: str, message: str, retry: bool
    ) -> bool:
        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT document_id FROM material_jobs WHERE job_id=%s "
                    "AND user_id=%s AND status='running' AND attempts=%s FOR UPDATE",
                    (job_id, self._user(), attempt),
                )
                row = cursor.fetchone()
                if row:
                    status = "queued" if retry else "failed"
                    cursor.execute(
                        "UPDATE material_jobs SET status=%s,error_code=%s,"
                        "error_message=%s,lease_until=NULL,"
                        "available_at=CASE WHEN %s THEN now()+interval '5 seconds' "
                        "ELSE available_at END,"
                        "finished_at=CASE WHEN %s THEN NULL ELSE now() END "
                        "WHERE job_id=%s",
                        (status, code, message, retry, retry, job_id),
                    )
                    cursor.execute(
                        "UPDATE documents SET data=jsonb_set(jsonb_set(data,"
                        "'{parse_status}',to_jsonb(%s::text)),'{parse_error}',"
                        "to_jsonb(%s::text)) WHERE record_key=%s AND user_id=%s "
                        "AND data->>'parse_status' <> 'deleted'",
                        (status, message, row["document_id"], self._user()),
                    )
        return row is not None

    def publish_material_job(
        self, job_id: str, attempt: int, document: dict, chunks: list[dict]
    ) -> bool:
        """Publish chunks, ready document, and Job success in one transaction."""
        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status,attempts FROM material_jobs WHERE job_id=%s "
                    "AND user_id=%s FOR UPDATE",
                    (job_id, self._user()),
                )
                row = cursor.fetchone()
                valid = bool(row and row["status"] == "running" and row["attempts"] == attempt)
                if valid:
                    cursor.execute(
                        "SELECT data->>'parse_status' AS status FROM documents "
                        "WHERE record_key=%s AND user_id=%s FOR UPDATE",
                        (document["document_id"], self._user()),
                    )
                    source = cursor.fetchone()
                    if source is None or source["status"] == "deleted":
                        cursor.execute(
                            "UPDATE material_jobs SET status='failed',"
                            "error_code='source_removed',error_message='资料已删除',"
                            "lease_until=NULL,finished_at=now() WHERE job_id=%s",
                            (job_id,),
                        )
                        valid = False
                if valid:
                    cursor.execute(
                        "UPDATE documents SET data=%s WHERE record_key=%s AND user_id=%s",
                        (Jsonb(document), document["document_id"], self._user()),
                    )
                    cursor.execute(
                        "DELETE FROM document_chunks WHERE document_id=%s AND user_id=%s",
                        (document["document_id"], self._user()),
                    )
                    for chunk in chunks:
                        cursor.execute(
                            "INSERT INTO document_chunks(record_key,user_id,course_id,"
                            "document_id,data,content,embedding) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s::vector)",
                            (
                                chunk["chunk_id"],
                                self._user(),
                                chunk["course_id"],
                                chunk["document_id"],
                                Jsonb(chunk),
                                chunk["content"],
                                self._vector(chunk["embedding"]),
                            ),
                        )
                    self._persist_material_source(cursor, document, chunks)
                    cursor.execute(
                        "UPDATE material_jobs SET status='succeeded',stage='index',"
                        "lease_until=NULL,finished_at=now() WHERE job_id=%s",
                        (job_id,),
                    )
        return valid

    def _persist_material_source(self, cursor, document: dict, chunks: list[dict]) -> None:
        version = material_version(document)
        cursor.execute(
            "INSERT INTO material_versions(record_key,user_id,course_id,document_id,"
            "material_version_id,data) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (record_key) DO NOTHING",
            (
                version["material_version_id"],
                self._user(),
                version["course_id"],
                version["document_id"],
                version["material_version_id"],
                Jsonb(version),
            ),
        )
        cursor.execute(
            "INSERT INTO material_locators(user_id,course_id,material_version_id,"
            "locator_id,locator_kind,ordinal) VALUES (%s,%s,%s,'document','document',0) "
            "ON CONFLICT DO NOTHING",
            (self._user(), version["course_id"], version["material_version_id"]),
        )
        for ordinal, chunk in enumerate(chunks):
            locator = chunk_locator(version, chunk, ordinal)
            cursor.execute(
                "INSERT INTO material_locators(user_id,course_id,material_version_id,"
                "locator_id,locator_kind,ordinal,data) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (user_id,material_version_id,locator_id) "
                "DO UPDATE SET data=EXCLUDED.data",
                (
                    self._user(),
                    locator["course_id"],
                    locator["material_version_id"],
                    locator["locator_id"],
                    locator["locator_kind"],
                    locator["ordinal"],
                    Jsonb(locator["data"]),
                ),
            )

    def list_material_chunks(self, document_id: str) -> list[dict]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT data FROM document_chunks WHERE user_id=%s AND document_id=%s",
                (self._user(), document_id),
            )
            rows = [row["data"] for row in cursor.fetchall()]
        return sorted(rows, key=lambda row: (row.get("chunk_ordinal", 2**31), row["chunk_id"]))

    def ensure_material_source(self, document: dict) -> dict:
        """Backfill locator rows for ready material indexed before M1-05."""
        version = material_version(document)
        chunks = self.list_material_chunks(document["document_id"])
        with self.transaction():
            with self.connection.cursor() as cursor:
                self._persist_material_source(cursor, document, chunks)
        return version

    def retry_material_job(self, job_id: str) -> dict | None:
        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM material_jobs WHERE job_id=%s AND user_id=%s FOR UPDATE",
                    (job_id, self._user()),
                )
                row = cursor.fetchone()
                result = None
                document = None
                if row:
                    cursor.execute(
                        "SELECT data->>'parse_status' AS status FROM documents "
                        "WHERE record_key=%s AND user_id=%s FOR UPDATE",
                        (row["document_id"], self._user()),
                    )
                    document = cursor.fetchone()
                if (
                    row
                    and row["status"] == "failed"
                    and document
                    and document["status"] != "deleted"
                ):
                    cursor.execute(
                        "UPDATE material_jobs SET status='queued',stage=NULL,attempts=0,"
                        "error_code=NULL,error_message=NULL,available_at=now(),"
                        "started_at=NULL,finished_at=NULL WHERE job_id=%s RETURNING *",
                        (job_id,),
                    )
                    result = self._job(cursor.fetchone())
                    cursor.execute(
                        "UPDATE documents SET data=(data - 'parse_error') || "
                        "jsonb_build_object('parse_status','queued') "
                        "WHERE record_key=%s AND user_id=%s",
                        (row["document_id"], self._user()),
                    )
        return result

    @staticmethod
    def _vector(vector: list[float]) -> str:
        return "[" + ",".join(str(value) for value in vector) + "]"

    def search(
        self,
        vector: list[float],
        course: str,
        chapter: str,
        limit: int,
        document_ids: list[str] | None = None,
    ) -> list[dict]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """SELECT document_chunks.data,
                       1 - (document_chunks.embedding <=> %s::vector) AS similarity
                    FROM document_chunks
                    JOIN documents ON documents.record_key = document_chunks.document_id
                      AND documents.user_id = document_chunks.user_id
                    WHERE document_chunks.user_id = %s AND document_chunks.course_id = %s
                      AND COALESCE(documents.data->>'parse_status','ready') = 'ready'
                      AND (%s = '' OR document_chunks.data->>'chapter' = %s)
                      AND (%s OR document_chunks.document_id = ANY(%s))
                    ORDER BY document_chunks.embedding <=> %s::vector LIMIT %s""",
                    (
                        self._vector(vector),
                        self._user(),
                        course,
                        chapter,
                        chapter,
                        document_ids is None,
                        document_ids or [],
                        self._vector(vector),
                        limit,
                    ),
                )
                # Chunks retain their embedding in JSON for the stored source record as
                # well as in pgvector.  It is not part of the retrieval contract:
                # passing it on causes Evidence's strict schema validation to fail.
                return [
                    {
                        **{key: value for key, value in row["data"].items() if key != "embedding"},
                        "similarity": float(row["similarity"]),
                    }
                    for row in cursor.fetchall()
                ]
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 向量检索失败") from exc
