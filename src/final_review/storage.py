"""Small SurrealDB HTTP adapter. Every query checks statement-level errors."""

from hashlib import sha256
from typing import Protocol

import httpx

from .config import Settings


class Store(Protocol):
    def put(self, table: str, key: str, data: dict) -> None: ...
    def get(self, table: str, key: str) -> dict | None: ...
    def scan(self, table: str, filters: dict) -> list[dict]: ...
    def delete(self, table: str, key: str) -> None: ...
    def delete_document(self, key: str) -> None: ...
    def deindex_document(self, key: str) -> None: ...
    def update_material_metadata(self, key: str, changes: dict) -> dict | None: ...
    def ingest(self, document: dict, chunks: list[dict]) -> None: ...
    def search(
        self,
        vector: list[float],
        course: str,
        chapter: str,
        limit: int,
        document_ids: list[str] | None = None,
    ) -> list[dict]: ...
    def list_material_chunks(self, document_id: str) -> list[dict]: ...
    def ensure_material_source(self, document: dict) -> dict: ...


def stable_key(*parts: str) -> str:
    return sha256("\x00".join(parts).encode()).hexdigest()


class StorageError(RuntimeError):
    pass


class SurrealStore:
    TABLES = {
        "course",
        "export_job",
        "document",
        "chunk",
        "conversation",
        "message",
        "review_session",
        "knowledge_point",
        "checkpoint",
        "pending_write",
        "attempt",
        "learning_event",
        "fast_quiz_session",
        "exam",
        "learning_asset",
        "asset_revision",
        "quiz_revision_payload",
        "question_revision",
        "material_version",
        "source_reference",
        "source_locator",
        "source_snapshot",
        "confirmation",
        "audit_event",
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
        allowed = {
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "course_id",
            "conversation_id",
            "document_id",
        }
        if not filters.keys() <= allowed:
            raise ValueError("不支持的查询字段")
        where = " AND ".join(f"{k} = ${k}" for k in filters) or "true"
        order = " ORDER BY created_at ASC" if table == "message" else ""
        return self.query(f"SELECT * OMIT id FROM {table} WHERE {where}{order};", filters)[0]

    def delete(self, table, key):
        self._table(table)
        self.query("DELETE type::thing($table, $key);", {"table": table, "key": key})

    def delete_document(self, key):
        self.query(
            "BEGIN TRANSACTION; DELETE chunk WHERE document_id = $key; "
            "DELETE type::thing('document', $key); COMMIT TRANSACTION;",
            {"key": key},
        )

    def deindex_document(self, key):
        """Remove retrieval chunks while retaining the document provenance record."""
        self.query("DELETE chunk WHERE document_id = $key;", {"key": key})

    def list_material_chunks(self, document_id):
        chunks = self.scan("chunk", {"document_id": document_id})
        return sorted(
            chunks, key=lambda chunk: (chunk.get("chunk_ordinal", 2**31), chunk["chunk_id"])
        )

    def ensure_material_source(self, document):
        from .source_locators import chunk_locator, material_version

        version = material_version(document)
        if not self.get("material_version", version["material_version_id"]):
            self.put("material_version", version["material_version_id"], version)
        for ordinal, chunk in enumerate(self.list_material_chunks(document["document_id"])):
            locator = chunk_locator(version, chunk, ordinal)
            self.put("source_locator", locator["locator_id"], locator)
        return version

    def update_material_metadata(self, key, changes):
        from datetime import UTC, datetime

        document = self.get("document", key)
        if (
            document is None
            or document.get("parse_status") not in {"ready", "failed"}
            or document.get("updated_at") != changes["expected_updated_at"]
        ):
            return None
        for field in ("title", "chapter", "source_type"):
            document[field] = changes[field]
        document["updated_at"] = datetime.now(UTC).isoformat()
        self.query(
            "BEGIN TRANSACTION; "
            "UPSERT type::thing('document', $key) CONTENT $document; "
            "UPDATE chunk SET title=$title, chapter=$chapter, source_type=$source_type "
            "WHERE document_id=$key; COMMIT TRANSACTION;",
            {
                "key": key,
                "document": document,
                "title": document["title"],
                "chapter": document["chapter"],
                "source_type": document["source_type"],
            },
        )
        return document

    def ingest(self, document, chunks):
        # Single transaction prevents incomplete documents from entering retrieval.
        # Data payload is sent in a JSON-bound variable, not interpolated as SQL.
        if "user_id" in document:
            from .source_locators import material_version

            document = {**document, "parse_status": document.get("parse_status", "ready")}
            document["material_version_id"] = material_version(document)["material_version_id"]
        self.query(
            "BEGIN TRANSACTION;"
            "UPSERT type::thing('document', $key) CONTENT $document;"
            "DELETE chunk WHERE document_id = $key;"
            "INSERT INTO chunk $chunks RETURN NONE;"
            "COMMIT TRANSACTION;",
            {"key": document["document_id"], "document": document, "chunks": chunks},
        )
        if "user_id" in document:
            self.ensure_material_source(document)

    def search(self, vector, course, chapter, limit, document_ids=None):
        # Exact cosine search over the filtered course, suitable for a small course corpus.
        # No global TopK-before-filter bug; HNSW is a future scaling choice, not a claim here.
        rows = self.query(
            "SELECT chunk_id, document_id, title, course_id, chapter, source_type, content, "
            "vector::similarity::cosine(embedding, $vector) AS similarity "
            "FROM chunk WHERE course_id = $course AND ($chapter = '' OR chapter = $chapter) "
            "AND ($all_documents OR document_id IN $document_ids) "
            "ORDER BY similarity DESC LIMIT $limit;",
            {
                "vector": vector,
                "course": course,
                "chapter": chapter,
                "limit": limit,
                "all_documents": document_ids is None,
                "document_ids": document_ids or [],
            },
        )[0]
        return rows
