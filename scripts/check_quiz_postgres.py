"""M3-02 database verification in a random database, never the user's course DB."""

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
    name = f"final_review_m302_{uuid4().hex}"
    isolated = make_conninfo(root_url, dbname=name)
    with psycopg.connect(root_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_postgres_migrations.py",
                "tests/test_m3_quiz_postgres.py",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "TEST_DATABASE_URL": isolated, "PYTHONIOENCODING": "utf-8"},
            timeout=600,
        )
        return result.returncode
    finally:
        with psycopg.connect(root_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        print("M3-02 隔离测试数据库已删除")


if __name__ == "__main__":
    raise SystemExit(main())
