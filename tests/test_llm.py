"""Exercises real LangChain/OpenAI request shaping with a fake HTTP provider."""

import json

import httpx
import pytest
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from final_review.llm import ModelError, ReviewModel
from final_review.schemas import Route


def completion(name=None, arguments=None):
    message = {"role": "assistant", "content": "done" if name is None else None}
    if name:
        message["tool_calls"] = [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }
        ]
    return httpx.Response(
        200,
        json={
            "id": "chat-test",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {"index": 0, "message": message, "finish_reason": "tool_calls" if name else "stop"}
            ],
        },
    )


def test_real_langchain_tool_roundtrip(system):
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            assert payload["tool_choice"] == "required"
            return completion("search_course_material", {"query": "TCP"})
        messages = payload["messages"]
        assert messages[-1]["role"] == "tool"
        assert "chunk_id" in messages[-1]["content"]
        assert "同步双方初始序列号" in messages[-1]["content"]
        return completion()

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = ReviewModel(
            ChatOpenAI(
                model="test-model",
                api_key="test-key",
                base_url="http://test/v1",
                http_client=client,
            )
        )
        evidence = model.retrieve({"course_id": "net", "message": "为什么三次握手"}, system.kb)
    assert len(requests) == 2
    assert evidence[0]["course_id"] == "net"


def test_structured_output_retries_schema_error():
    attempts = []

    def handler(request):
        payload = json.loads(request.content)
        attempts.append(payload)
        assert payload["tools"][0]["function"]["name"] == "Route"
        return completion("Route", {"intent": "bogus" if len(attempts) == 1 else "ask"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = ReviewModel(
            ChatOpenAI(
                model="test-model",
                api_key="test-key",
                base_url="http://test/v1",
                http_client=client,
            )
        )
        assert model.structured(Route, "route", {"message": "解释 TCP"}) == {"intent": "ask"}
    assert len(attempts) == 2


def test_unknown_tool_is_rejected(system):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: completion("run_shell", {"command": "invalid"}),
        )
    ) as client:
        model = ReviewModel(
            ChatOpenAI(
                model="test-model",
                api_key="test-key",
                base_url="http://test/v1",
                http_client=client,
            )
        )
        with pytest.raises(ModelError, match="未开放"):
            model.retrieve({"course_id": "net", "message": "TCP"}, system.kb)


def test_embedding_request_dimensions():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["dimensions"] == 3
        assert payload["input"] == ["TCP"]
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "object": "embedding", "embedding": [1.0, 0.0, 0.0]}],
                "model": "test-embedding",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        embeddings = OpenAIEmbeddings(
            model="test-embedding",
            api_key="test-key",
            base_url="http://test/v1",
            dimensions=3,
            http_client=client,
            check_embedding_ctx_length=False,
        )
        assert embeddings.embed_query("TCP") == [1.0, 0.0, 0.0]
