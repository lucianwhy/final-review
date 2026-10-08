"""Exercises real LangChain/OpenAI request shaping with a fake HTTP provider."""

import json

import httpx
import pytest
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from final_review.config import ChatModelConfig, Settings
from final_review.llm import ModelError, ReviewModel, build_fast_quiz_model, build_review_model
from final_review.schemas import GeneratedNote, Route


@pytest.mark.parametrize("base_url", ["https://api.deepseek.com", "https://api.deepseek.com/v1"])
def test_deepseek_structured_flows_disable_incompatible_thinking_mode(base_url):
    config = ChatModelConfig(
        id="deepseek",
        label="DeepSeek",
        model="deepseek-flash",
        base_url=base_url,
        api_key="test-key",
    )
    settings = Settings(_env_file=None)
    for builder in (build_fast_quiz_model, build_review_model):
        assert builder(config, settings).model.extra_body == {"thinking": {"type": "disabled"}}


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


def test_note_generation_retries_missing_tool_call_and_accepts_valid_response():
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            return completion()  # Provider returned text instead of the requested tool call.
        return completion(
            "GeneratedNote",
            {
                "title": "TCP 笔记",
                "points": [
                    {
                        "heading": "三次握手",
                        "content": "同步序列号",
                        "provenance": "source",
                        "citations": [{"chunk_id": "chunk-1", "quote": "同步序列号"}],
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = ReviewModel(
            ChatOpenAI(
                model="test-model",
                api_key="test-key",
                base_url="http://test/v1",
                http_client=client,
            )
        )
        result = model.structured(GeneratedNote, "generate", {"evidence": []})
    assert result["title"] == "TCP 笔记"
    assert len(attempts) == 2


def test_note_generation_accepts_valid_json_text_without_tool_call():
    responses = []

    def handler(request):
        responses.append(json.loads(request.content))
        data = {
            "title": "TCP 摘要",
            "points": [
                {
                    "heading": "三次握手",
                    "content": "同步序列号",
                    "provenance": "source",
                    "citations": [{"chunk_id": "chunk-1", "quote": "同步序列号"}],
                }
            ],
        }
        response = completion()
        body = response.json()
        body["choices"][0]["message"]["content"] = json.dumps(data, ensure_ascii=False)
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = ReviewModel(
            ChatOpenAI(
                model="test-model",
                api_key="test-key",
                base_url="http://test/v1",
                http_client=client,
            )
        )
        result = model.structured(GeneratedNote, "generate", {"evidence": []})
    assert result["title"] == "TCP 摘要"
    assert len(responses) == 1


def test_note_generation_reports_empty_response_after_three_attempts(caplog):
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
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
        with pytest.raises(ModelError, match="连续返回无法解析"):
            model.structured(GeneratedNote, "generate", {"evidence": []})
    assert len(attempts) == 3
    assert "content_length=4" in caplog.text
    assert "done" not in caplog.text


def test_note_extraction_keeps_missing_fields_empty():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["tools"][0]["function"]["name"] == "NoteExtraction"
        return completion(
            "NoteExtraction",
            {
                "note_type": "key_points",
                "scope": "第三章",
                "duration_minutes": None,
                "emphasis": [],
                "audience_level": None,
                "source_types": ["teacher_ppt"],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = ReviewModel(
            ChatOpenAI(
                model="test-model",
                api_key="test-key",
                base_url="http://test/v1",
                http_client=client,
            )
        )
        result = model.note_request({"message": "整理第三章老师 PPT 的考点清单"})
    assert result["note_type"] == "key_points"
    assert result["scope"] == "第三章"
    assert result["duration_minutes"] is None
    assert result["source_types"] == ["teacher_ppt"]


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
