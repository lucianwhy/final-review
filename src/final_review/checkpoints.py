"""LangGraph synchronous checkpointer backed by SurrealDB.

Stores typed serializer payloads as base64, including pending writes and parent lineage.
The HTTP app uses sync graph invocation; async saver methods are intentionally not exposed.
"""

from base64 import b64decode, b64encode

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)

from .storage import Store, stable_key


class SurrealSaver(BaseCheckpointSaver):
    def __init__(self, store: Store):
        super().__init__()
        self.store = store

    def _dump(self, value):
        kind, data = self.serde.dumps_typed(value)
        return {"kind": kind, "data": b64encode(data).decode("ascii")}

    def _load(self, value):
        return self.serde.loads_typed((value["kind"], b64decode(value["data"])))

    def _filters(self, config):
        cfg = config["configurable"]
        return {"thread_id": cfg["thread_id"], "checkpoint_ns": cfg.get("checkpoint_ns", "")}

    def _config(self, row, checkpoint_id=None):
        return {
            "configurable": {
                "thread_id": row["thread_id"],
                "checkpoint_ns": row["checkpoint_ns"],
                "checkpoint_id": checkpoint_id or row["checkpoint_id"],
            }
        }

    def _tuple(self, row):
        writes = self.store.scan(
            "pending_write", {k: row[k] for k in ("thread_id", "checkpoint_ns", "checkpoint_id")}
        )
        writes.sort(key=lambda w: (w["task_id"], w["idx"]))
        return CheckpointTuple(
            config=self._config(row),
            checkpoint=self._load(row["snapshot"]),
            metadata=self._load(row["metadata"]),
            parent_config=self._config(row, row["parent_id"]) if row["parent_id"] else None,
            pending_writes=[(w["task_id"], w["channel"], self._load(w["value"])) for w in writes],
        )

    def get_tuple(self, config):
        filters = self._filters(config)
        checkpoint_id = get_checkpoint_id(config)
        if checkpoint_id:
            filters["checkpoint_id"] = checkpoint_id
        rows = self.store.scan("checkpoint", filters)
        return self._tuple(max(rows, key=lambda r: r["checkpoint_id"])) if rows else None

    def list(self, config, *, filter=None, before=None, limit=None):
        rows = self.store.scan("checkpoint", self._filters(config) if config else {})
        rows.sort(key=lambda row: row["checkpoint_id"], reverse=True)
        count = 0
        for row in rows:
            if before and row["checkpoint_id"] >= get_checkpoint_id(before):
                continue
            metadata = self._load(row["metadata"])
            if filter and any(metadata.get(k) != v for k, v in filter.items()):
                continue
            if limit is not None and count >= limit:
                break
            count += 1
            yield self._tuple(row)

    def put(self, config, checkpoint, metadata, new_versions):
        row = {
            **self._filters(config),
            "checkpoint_id": checkpoint["id"],
            "parent_id": get_checkpoint_id(config),
            "snapshot": self._dump(checkpoint),
            "metadata": self._dump(get_checkpoint_metadata(config, metadata)),
        }
        key = stable_key(row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"])
        self.store.put("checkpoint", key, row)
        return self._config(row)

    def put_writes(self, config, writes, task_id, task_path=""):
        checkpoint_id = get_checkpoint_id(config)
        for index, (channel, value) in enumerate(writes):
            idx = WRITES_IDX_MAP.get(channel, index)
            row = {
                **self._filters(config),
                "checkpoint_id": checkpoint_id,
                "task_id": task_id,
                "task_path": task_path,
                "idx": idx,
                "channel": channel,
                "value": self._dump(value),
            }
            key = stable_key(
                row["thread_id"], row["checkpoint_ns"], checkpoint_id, task_id, str(idx)
            )
            if idx >= 0 and self.store.get("pending_write", key) is not None:
                continue
            self.store.put("pending_write", key, row)
