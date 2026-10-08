"""Exercise configured chat models against an isolated in-memory course corpus."""

import argparse
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import MemoryStore, ScriptedModel, TestEmbeddings  # noqa: E402

from final_review.agent import FinalReviewAgent  # noqa: E402
from final_review.api import create_app  # noqa: E402
from final_review.config import Settings  # noqa: E402
from final_review.rag import KnowledgeBase  # noqa: E402
from final_review.schemas import MaterialInput  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="deepseek")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings()
    settings.api_token = SecretStr("")
    settings.embedding_dimensions = 3
    store = MemoryStore()
    kb = KnowledgeBase(store, TestEmbeddings(), settings)
    agent = FinalReviewAgent(store, kb, ScriptedModel(), settings)
    samples = []
    with TestClient(create_app(settings, agent)) as client:
        course = client.post("/api/courses", json={"name": "Web服务端技术原理及应用"}).json()
        course_id = course["course_id"]
        kb.ingest(MaterialInput(
            document_id="web-http", course_id=course_id, title="HTTP课件.md",
            source_type="teacher_ppt", chapter="HTTP", markdown=(
                "# HTTP\nHTTP是应用层协议。HTTP是无状态协议，每次请求独立处理。\n"
                "GET用于读取资源，POST用于提交数据。状态码200表示成功，404表示资源未找到，"
                "500表示服务端内部错误。Cookie保存在客户端，Session状态通常保存在服务端。\n"
                "HTTPS使用TLS保护传输过程。HTTP请求包含方法、路径、请求头和可选的请求体。"
            ),
        ), user_id="local-user")
        kb.ingest(MaterialInput(
            document_id="web-tcp", course_id=course_id, title="TCP讲义.md",
            source_type="homework", chapter="TCP", markdown=(
                "# TCP\nTCP通过三次握手同步双方初始序列号，并确认收发能力。"
                "TCP通过确认和重传实现可靠传输，通过滑动窗口进行流量控制。"
            ),
        ), user_id="local-user")
        cases = [
            ("course", "你知道当前是什么课程吗？"),
            ("statistics", "现在你的知识库里面有多少文本？"),
            ("analysis", "我总是记混Cookie和Session，你觉得我应该怎么理解，帮我分析一下。"),
            ("file_scope", "只根据HTTP课件.md解释HTTP为什么说是无状态协议。"),
            ("strict", "仍然只根据这份资料，告诉我本校老师今年考试的确切日期。"),
            ("quiz", "根据这份资料随机出五道简答题。"),
        ]
        for name, message in cases:
            response = client.post("/api/chat/dispatch", json={
                "course_id": course_id, "conversation_id": "live-check",
                "message": message, "model_id": args.model,
            })
            result = response.json()
            sample = {"case": name, "message": message, "status": response.status_code,
                      "result": result}
            samples.append(sample)
            print(json.dumps(sample, ensure_ascii=False), flush=True)
            if response.status_code != 200:
                raise RuntimeError(f"Live check failed: {name}, status={response.status_code}")
            if name == "course":
                assert "Web服务端" in result["reply"]
            elif name == "statistics":
                assert "2" in result["reply"] and "片段" in result["reply"]
            elif name == "analysis":
                assert result["intent"] == "ask"
            elif name == "file_scope":
                assert result["citations"]
                assert all(ref["document_id"] == "web-http" for ref in result["citations"])
            elif name == "strict":
                assert result["intent"] == "ask"
                assert any(word in result["reply"] for word in ("没有", "未提供", "不足", "无法"))
            elif name == "quiz":
                assert result["kind"] == "quiz" and result["quiz"]["question_count"] == 5
                questions = client.get(
                    f"/api/courses/{course_id}/chat-quizzes/{result['quiz']['session_id']}"
                ).json()["questions"]
                assert len(questions) == 5
                sample["questions"] = questions
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({"model": args.model, "samples": samples},
                                             ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
