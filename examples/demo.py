"""Live HTTP demo; requires configured backend. No fabricated model responses."""

import json
import os
from pathlib import Path
from uuid import uuid4

import httpx

base = os.getenv("AGENT_URL", "http://127.0.0.1:8080")
headers = {"Authorization": f"Bearer {os.environ['API_TOKEN']}"} if os.getenv("API_TOKEN") else {}


def show(response):
    response.raise_for_status()
    data = response.json()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return data


with httpx.Client(base_url=base, headers=headers, timeout=300) as client:
    show(
        client.post(
            "/knowledge/ingest",
            json={
                "course_id": "network",
                "title": "TCP 教学示例",
                "source_type": "ai_supplement",
                "chapter": "TCP",
                "markdown": Path(__file__).with_name("course.md").read_text("utf-8"),
            },
        )
    )
    session = "demo-" + uuid4().hex[:8]
    show(
        client.post(
            "/agent/invoke",
            json={
                "course_id": "network",
                "session_id": session,
                "intent": "ask",
                "chapter": "TCP",
                "message": "为什么 TCP 需要三次握手？考试如何作答？",
            },
        )
    )
    result = show(
        client.post(
            "/agent/invoke",
            json={
                "course_id": "network",
                "session_id": session,
                "intent": "quiz",
                "chapter": "TCP",
                "message": "围绕 TCP 三次握手给我出题",
                "exam_profile": {"question_types": ["short_answer"], "emphasis": ["三次握手"]},
            },
        )
    )
    if result["status"] == "awaiting_answers":
        answers = {q["id"]: input(q["stem"] + "\n你的答案：") for q in result["questions"]}
        show(
            client.post(
                "/assessment/evaluate",
                json={
                    "course_id": "network",
                    "session_id": session,
                    "answers": answers,
                },
            )
        )
