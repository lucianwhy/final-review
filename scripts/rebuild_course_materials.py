"""Rebuild an existing course's PPTs with bounded, isolated CLI processes."""

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg

from final_review.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--exclude", default="")
    parser.add_argument("--workers", type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    settings = Settings()
    with psycopg.connect(settings.database_url.get_secret_value()) as connection:
        rows = connection.execute(
            "SELECT document_id,data->>'file_name' FROM documents WHERE course_id=%s "
            "AND data->>'parse_status'='ready' "
            "AND coalesce(data->>'processing_pipeline','')<>'visual-slides-v1'",
            (args.course_id,),
        ).fetchall()
    ids = [
        row[0]
        for row in rows
        if Path(row[1]).suffix.lower() in {".ppt", ".pptx"} and row[0] != args.exclude
    ]
    logs = Path("logs/material-rebuild")
    logs.mkdir(parents=True, exist_ok=True)

    def run(document_id):
        print("Starting", document_id, flush=True)
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        with (logs / f"{document_id}.log").open("w", encoding="utf-8") as output:
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/rebuild_materials.py",
                    "--document-id",
                    document_id,
                    "--publish",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                check=False,
            )
        return result.returncode

    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = {pool.submit(run, document_id): document_id for document_id in ids}
        for task in as_completed(tasks):
            document_id = tasks[task]
            code = task.result()
            print("Completed", document_id, "exit", code, flush=True)
            if code:
                failures.append(document_id)
    if failures:
        raise RuntimeError(f"Failed rebuilds (previous versions preserved): {failures}")
    print("All course PPT rebuilds completed", len(ids), flush=True)


if __name__ == "__main__":
    main()
