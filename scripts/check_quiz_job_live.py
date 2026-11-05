"""Read-only generation proof against an existing job; never retry or publish that job."""

import argparse
import json
import time
from pathlib import Path

from final_review.config import Settings
from final_review.llm import build_review_model
from final_review.postgres import PostgresStore
from final_review.quiz_generation import generate_quiz
from final_review.schemas import ResolvedQuizConfig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    store = PostgresStore(settings.database_url.get_secret_value())
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_file = output.with_suffix(".checkpoint.json")
    row = store.connection.execute(
        "SELECT data FROM quiz_jobs WHERE record_key=%s", (args.job_id,)
    ).fetchone()
    if not row:
        raise ValueError("Job not found")
    job = row["data"]
    store.bind_user(job["user_id"])
    config = ResolvedQuizConfig.model_validate(job["config"])
    model = build_review_model(
        settings.get_chat_model(job["model_id"]),
        settings.model_copy(update={"model_timeout": settings.quiz_model_timeout}),
        quiz_generation=True,
    )
    checkpoint = (
        json.loads(checkpoint_file.read_text(encoding="utf-8"))
        if args.resume and checkpoint_file.exists()
        else None
    )

    def save(state):
        checkpoint_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        count = sum(len(b["questions"]) for b in state["completed"])
        print(f"Verified {count}/{config.question_count}", flush=True)

    report = {"job_id": args.job_id, "model_id": job["model_id"], "database_writes": False}
    started = time.monotonic()
    try:
        generated = generate_quiz(
            store,
            model,
            config,
            checkpoint=checkpoint,
            save_checkpoint=save,
            max_repairs=settings.quiz_max_repairs,
            choice_batch_size=settings.quiz_choice_batch_size,
            written_batch_size=settings.quiz_written_batch_size,
            progress=lambda stage: print(f"Stage: {stage}", flush=True),
        )
        report.update(
            status="validated",
            draft=generated["draft"],
            audit=generated["audit"],
            coverage=generated["coverage"],
        )
    except Exception as exc:
        report.update(
            status="failed",
            exception_type=type(exc).__name__,
            issues=getattr(exc, "issues", []),
            diagnostics=getattr(exc, "diagnostics", {}),
        )
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        store.close()
    print(report["status"], flush=True)
    return 0 if report["status"] == "validated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
