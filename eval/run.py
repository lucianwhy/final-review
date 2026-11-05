"""Live evaluation: uses actual backend/model; writes measured results, never guesses metrics."""

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--output", default="storage/eval.json")
    args = parser.parse_args()
    data = json.loads(Path(__file__).with_name("cases.json").read_text("utf-8"))
    course = "eval-" + uuid4().hex[:12]
    headers = (
        {"Authorization": f"Bearer {os.environ['API_TOKEN']}"} if os.getenv("API_TOKEN") else {}
    )
    rows = []
    with httpx.Client(base_url=args.url, headers=headers, timeout=300) as client:
        documents = {}
        for doc in data["documents"]:
            response = client.post(
                "/knowledge/ingest",
                json={
                    "course_id": course,
                    "title": doc["name"],
                    "chapter": doc["chapter"],
                    "source_type": "ai_supplement",
                    "markdown": doc["markdown"],
                },
            )
            response.raise_for_status()
            documents[doc["name"]] = response.json()["document_id"]
        for case in data["cases"]:
            response = client.post(
                "/agent/invoke",
                json={
                    "course_id": course + "-empty" if case.get("empty_course") else course,
                    "session_id": case["id"],
                    "message": case["query"],
                    "intent": "ask",
                    "chapter": case.get("chapter", ""),
                },
            )
            row = {"id": case["id"], "http_status": response.status_code}
            if response.is_success:
                result = response.json()
                row["result"] = result
                if "documents" in case:
                    expected = {documents[name] for name in case["documents"]}
                    returned = {e["document_id"] for e in result["citations"]}
                    row["citation_source_hit"] = bool(expected & returned)
                    row["keyword_coverage"] = sum(
                        term.casefold() in result["answer"].casefold() for term in case["terms"]
                    ) / len(case["terms"])
                else:
                    row["correct_abstention"] = result["status"] == "insufficient_evidence"
            rows.append(row)
            print(f"{case['id']}: HTTP {response.status_code}")
    positives = [r for r in rows if "citation_source_hit" in r]
    negatives = [r for r in rows if "correct_abstention" in r]
    report = {
        "course_id": course,
        "cases": rows,
        "http_success_rate": sum(r["http_status"] == 200 for r in rows) / len(rows),
        "citation_source_hit_rate_on_success": (
            sum(r["citation_source_hit"] for r in positives) / len(positives) if positives else None
        ),
        "abstention_accuracy_on_success": (
            sum(r["correct_abstention"] for r in negatives) / len(negatives) if negatives else None
        ),
        "note": "合成样本；引用来源命中与关键词覆盖不等于语义正确率。HTTP 错误单独计数。",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {output.resolve()}")


if __name__ == "__main__":
    main()
