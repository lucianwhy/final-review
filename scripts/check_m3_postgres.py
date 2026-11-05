"""Run M3 persistence proofs in a new disposable PostgreSQL database."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from final_review.config import Settings


def main():
    root_url = Settings().database_url.get_secret_value()
    if not root_url:
        raise RuntimeError("需要配置可创建临时数据库的 DATABASE_URL")
    name = f"final_review_m3_{uuid4().hex}"
    isolated = make_conninfo(root_url, dbname=name)
    with psycopg.connect(root_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        selected_tests = sys.argv[1:] or [
            "tests/test_postgres_migrations.py::test_empty_database_runner_is_versioned_and_repeatable",
            "tests/test_postgres_migrations.py::test_existing_001_002_with_legacy_attempt_upgrades",
            "tests/test_m3_quiz_postgres.py",
            "tests/test_m3_quiz_edit_postgres.py",
            "tests/test_m3_formal_attempt_postgres.py",
        ]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                *selected_tests,
            ],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "TEST_DATABASE_URL": isolated, "PYTHONIOENCODING": "utf-8"},
            timeout=900,
            check=False,
        )
        return result.returncode
    finally:
        with psycopg.connect(root_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        print("M3 隔离测试数据库已删除")


if __name__ == "__main__":
    raise SystemExit(main())
