"""Run export/migration proofs in a new random DB; never migrate the configured course DB."""

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
        raise RuntimeError("需要配置 DATABASE_URL 以创建隔离测试数据库")
    name = f"final_review_m204_{uuid4().hex}"
    isolated_url = make_conninfo(root_url, dbname=name)
    with psycopg.connect(root_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        environment = {**os.environ, "TEST_DATABASE_URL": isolated_url, "PYTHONIOENCODING": "utf-8"}
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_postgres_migrations.py",
                "tests/test_note_review_postgres.py",
                "tests/test_exports_postgres.py",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            timeout=600,
        )
    finally:
        with psycopg.connect(root_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        print("M2-04 隔离测试数据库已删除")
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
