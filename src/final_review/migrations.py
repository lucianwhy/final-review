"""Versioned PostgreSQL migration runner.

Docker's init directory only runs for a new volume.  This runner is the single
upgrade path for both new and existing databases and records the exact SQL
digest that was applied.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path

import psycopg

from .config import Settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"
BASELINE_TABLES = {
    "app_users",
    "courses",
    "documents",
    "attempts",
    "exams",
    "learning_assets",
    "asset_revisions",
    "material_versions",
    "source_references",
}
BASELINE_COLUMNS = {
    ("courses", "status"),
    ("attempts", "legacy_session_id"),
    ("attempts", "quiz_revision_id"),
    ("asset_revisions", "record_key"),
    ("material_versions", "document_id"),
    ("source_references", "record_key"),
}


class MigrationError(RuntimeError):
    pass


def migrations(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _baseline_is_present(cursor: psycopg.Cursor) -> bool:
    cursor.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)",
        (list(BASELINE_TABLES),),
    )
    if {row[0] for row in cursor.fetchall()} != BASELINE_TABLES:
        return False
    cursor.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND (table_name, column_name) IN "
        "(('courses','status'),('attempts','legacy_session_id'),('attempts','quiz_revision_id'),"
        "('asset_revisions','record_key'),('material_versions','document_id'),"
        "('source_references','record_key'))"
    )
    return set(cursor.fetchall()) == BASELINE_COLUMNS


def apply_migrations(database_url: str, *, applied_by: str = "final-review") -> list[str]:
    """Apply pending migrations atomically and return their version filenames."""
    files = migrations()
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version text PRIMARY KEY, checksum text NOT NULL, "
                "applied_at timestamptz NOT NULL DEFAULT now(), "
                "applied_by text NOT NULL)"
            )
            cursor.execute("SELECT version, checksum FROM schema_migrations")
            recorded = dict(cursor.fetchall())
            if not recorded and _baseline_is_present(cursor):
                # 001/002 may have run through docker-entrypoint-initdb.d before
                # this runner existed.  Record only a recognizable complete baseline.
                for path in files[:2]:
                    cursor.execute(
                        "INSERT INTO schema_migrations(version, checksum, applied_by) "
                        "VALUES (%s,%s,%s)",
                        (path.name, _digest(path), "baseline-preflight"),
                    )
                recorded = {path.name: _digest(path) for path in files[:2]}
            for path in files:
                checksum = _digest(path)
                if path.name in recorded:
                    if recorded[path.name] != checksum:
                        raise MigrationError(f"迁移文件校验和不匹配: {path.name}")
                    continue
                cursor.execute(path.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO schema_migrations(version, checksum, applied_by) "
                    "VALUES (%s,%s,%s)",
                    (path.name, checksum, applied_by),
                )
        connection.commit()
    return [path.name for path in files if path.name not in recorded]


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Final Review PostgreSQL migrations")
    parser.add_argument("database_url", nargs="?", help="省略时从 .env 的 DATABASE_URL 读取")
    arguments = parser.parse_args()
    database_url = arguments.database_url or Settings().database_url.get_secret_value()
    if not database_url:
        parser.error("需要 DATABASE_URL 参数或 .env 中的 DATABASE_URL")
    for version in apply_migrations(database_url):
        print(f"applied {version}")


if __name__ == "__main__":
    main()
