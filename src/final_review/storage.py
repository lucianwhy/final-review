"""Small SurrealDB HTTP adapter. Every query checks statement-level errors."""

from hashlib import sha256
from typing import Protocol

import httpx

from .config import Settings


class Store(Protocol):
    def put(self, table: str, key: str, data: dict) -> None: ...
    def get(self, table: str, key: str) -> dict | None: ...
    def scan(self, table: str, filters: dict) -> list[dict]: ...
    def ingest(self, document: dict, chunks: list[dict]) -> None: ...
    def search(self, vector: list[float], course: str, chapter: str, limit: int) -> list[dict]: ...


def stable_key(*parts: str) -> str:
    return sha256("\x00".join(parts).encode()).hexdigest()


class StorageError(RuntimeError):
    pass


class SurrealStore:
    TABLES = {
        "document",
        "chunk",
        "review_session",
        "knowledge_point",
        "checkpoint",
        "pending_write",
    }

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=settings.surreal_url.rstrip("/"),
            auth=(settings.surreal_username, settings.surreal_password.get_secret_value()),
            headers={
                "Accept": "application/json",
                "Surreal-NS": settings.surreal_namespace,
                "Surreal-DB": settings.surreal_database,
            },
            timeout=30,
        )

    def close(self):
        self.client.close()

    def query(self, sql: str, variables: dict | None = None) -> list:
        # RPC binds JSON values in the request body; large documents never enter URLs or SQL.
        try:
            response = self.client.post(
                "/rpc",
                json={
                    "id": "query",
                    "method": "query",
                    "params": [sql, variables or {}],
                },
            )
            response.raise_for_status()
            payload = response.json()
            if "error" in payload:
                raise StorageError(f"SurrealDB RPC 失败: {payload['error']}")
            statements = payload["result"]
        except (httpx.HTTPError, ValueError) as exc:
            raise StorageError("SurrealDB 连接或响应失败") from exc
        if not isinstance(statements, list):
            raise StorageError("SurrealDB 响应格式错误")
        for statement in statements:
            if statement.get("status") != "OK":
                raise StorageError(f"SurrealDB 查询失败: {statement.get('result')}")
        return [s.get("result") for s in statements]

    def setup(self):
        for table in sorted(self.TABLES):
            self.query(f"DEFINE TABLE IF NOT EXISTS {table} SCHEMALESS;")
        self.query("DEFINE INDEX IF NOT EXISTS chunk_course ON chunk FIELDS course_id;")
        self.query("DEFINE INDEX IF NOT EXISTS chunk_document ON chunk FIELDS document_id;")
        self.query("DEFINE INDEX IF NOT EXISTS checkpoint_thread ON checkpoint FIELDS thread_id;")
        self.query("DEFINE INDEX IF NOT EXISTS writes_thread ON pending_write FIELDS thread_id;")
        # Keep one embedding space per database; dimension alone cannot detect model changes.
        fingerprint = {
            "model": self.settings.embedding_model,
            "dimensions": self.settings.embedding_dimensions,
            "base_url": self.settings.embedding_base_url,
        }
        if self.get("review_session", "embedding_config") is None:
            self.put("review_session", "embedding_config", fingerprint)
        if self.get("review_session", "embedding_config") != fingerprint:
            raise StorageError("Embedding 配置与已有数据库不一致；请使用新数据库重新入库")

    def _table(self, table):
        if table not in self.TABLES:
            raise ValueError("未知数据表")
        return table

    def put(self, table, key, data):
        self._table(table)
        self.query(
            "UPSERT type::thing($table, $key) CONTENT $data;",
            {
                "table": table,
                "key": key,
                "data": data,
            },
        )

    def get(self, table, key):
        self._table(table)
        rows = self.query(
            "SELECT * OMIT id FROM type::thing($table, $key);",
            {
                "table": table,
                "key": key,
            },
        )[0]
        return rows[0] if rows else None

    def scan(self, table, filters):
        table = self._table(table)
        allowed = {"thread_id", "checkpoint_ns", "checkpoint_id", "course_id"}
        if not filters.keys() <= allowed:
            raise ValueError("不支持的查询字段")
        where = " AND ".join(f"{k} = ${k}" for k in filters) or "true"
        return self.query(f"SELECT * OMIT id FROM {table} WHERE {where};", filters)[0]

    def ingest(self, document, chunks):
        # Single transaction prevents incomplete documents from entering retrieval.
        # Data payload is sent in a JSON-bound variable, not interpolated as SQL.
        self.query(
            "BEGIN TRANSACTION;"
            "UPSERT type::thing('document', $key) CONTENT $document;"
            "DELETE chunk WHERE document_id = $key;"
            "INSERT INTO chunk $chunks RETURN NONE;"
            "COMMIT TRANSACTION;",
            {"key": document["document_id"], "document": document, "chunks": chunks},
        )

    def search(self, vector, course, chapter, limit):
        # Exact cosine search over the filtered course, suitable for a small course corpus.
        # No global TopK-before-filter bug; HNSW is a future scaling choice, not a claim here.
        rows = self.query(
            "SELECT chunk_id, document_id, title, course_id, chapter, source_type, content, "
            "vector::similarity::cosine(embedding, $vector) AS similarity "
            "FROM chunk WHERE course_id = $course AND ($chapter = '' OR chapter = $chapter) "
            "ORDER BY similarity DESC LIMIT $limit;",
            {"vector": vector, "course": course, "chapter": chapter, "limit": limit},
        )[0]
        return rows
