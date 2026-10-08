import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from final_review.api import create_app
from final_review.config import ChatModelConfig
from final_review.course_chat import CourseMaterials, cited_evidence, parse_chat_decision
from final_review.schemas import MaterialInput


@pytest.mark.parametrize(
    "content", ['{"intent":"ask"}', '```json\n{"intent":"ask"}\n```', '```\n{"intent":"ask"}\n```']
)
def test_router_accepts_json_and_provider_code_fences(content):
    assert parse_chat_decision(content).intent == "ask"


@pytest.fixture
def chat_setup(system, monkeypatch):
    system.settings.chat_models = [
        ChatModelConfig(
            id="selected",
            label="所选模型",
            model="test-model",
            base_url="https://example.invalid/v1",
            api_key="test-key",
        )
    ]
    decisions, calls, answers = {}, [], []

    class Provider:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            calls.append(kwargs)
            messages = kwargs["messages"]
            if messages[0]["content"].startswith("判断用户当前消息的意图"):
                message = json.loads(messages[-1]["content"])["message"]
                content = json.dumps(
                    decisions.get(
                        message,
                        {
                            "intent": "ask",
                            "action": "search",
                            "query": "三次握手",
                        },
                    ),
                    ensure_ascii=False,
                )
            else:
                content = answers.pop(0) if answers else "【课程资料依据】同步序列号。[资料1]"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    monkeypatch.setattr("final_review.api.OpenAI", Provider)
    with TestClient(create_app(system.settings, system)) as client:
        course = client.post("/api/courses", json={"name": "Web服务端技术原理及应用"}).json()
        course_id = course["course_id"]
        client.patch(
            f"/api/courses/{course_id}",
            json={
                "subject": "计算机",
                "expected_updated_at": course["updated_at"],
            },
        )
        document = system.kb.ingest(
            MaterialInput(
                document_id="web-notes",
                course_id=course_id,
                title="老师课件.pptx",
                chapter="TCP",
                source_type="teacher_ppt",
                markdown="TCP 三次握手同步双方初始序列号。",
            ),
            user_id="local-user",
        )
        yield SimpleNamespace(
            system=system,
            client=client,
            course_id=course_id,
            document_id=document["document_id"],
            decisions=decisions,
            calls=calls,
            answers=answers,
        )


def test_formal_quiz_chat_clarifies_without_fast_generation(chat_setup, monkeypatch):
    setup = chat_setup
    setup.decisions["出一套模拟卷"] = {
        "intent": "quiz",
        "quiz_mode": "draft",
        "question_count": 20,
        "chapter": "TCP",
        "source_document_ids": [setup.document_id],
        "quiz_input": {"chapter": "TCP"},
    }
    setup.decisions["基础难度"] = {
        "intent": "quiz",
        "quiz_mode": "draft",
        "quiz_input": {"difficulty": "basic"},
    }
    setup.decisions["一题简答，不限时，不纳入导入题，不允许AI补充"] = {
        "intent": "quiz",
        "quiz_mode": "draft",
        "quiz_input": {
            "blueprint": [{"question_type": "short_answer", "question_count": 1}],
            "duration_mode": "untimed",
            "include_imported_questions": False,
            "allow_ai_supplement": False,
        },
    }

    def forbidden(*args, **kwargs):
        raise AssertionError("正式试卷配置不能调用快速练习模型")

    monkeypatch.setattr("final_review.api.build_fast_quiz_model", forbidden)
    first = send(setup, "出一套模拟卷")
    assert first.status_code == 200
    assert first.json()["status"] == "needs_input"
    assert "difficulty" in first.json()["quiz_configuration"]["missing"]
    second = send(setup, "基础难度")
    assert second.status_code == 200
    assert "difficulty" not in second.json()["quiz_configuration"]["missing"]
    third = send(setup, "一题简答，不限时，不纳入导入题，不允许AI补充")
    assert third.status_code == 200
    assert third.json()["status"] == "needs_input"
    assert third.json()["quiz_configuration"]["status"] == "ready"
    confirmed = setup.client.post(
        "/api/chat/dispatch",
        json={
            "course_id": setup.course_id,
            "conversation_id": "one",
            "model_id": "selected",
            "message": "确认试题配置",
            "quiz_input": third.json()["quiz_configuration"]["quiz_input"],
        },
    )
    assert confirmed.json()["status"] == "queued"
    assert third.json()["quiz_configuration"]["config"]["source_document_ids"] == [
        setup.document_id
    ]
    assert "pending_quiz" in setup.calls[-1]["messages"][0]["content"]


def test_practice_request_opens_configuration_instead_of_fast_generation(chat_setup):
    setup = chat_setup
    setup.decisions["来20道练习题"] = {"intent": "quiz", "question_count": 20}
    result = send(setup, "来20道练习题")
    assert result.status_code == 200
    assert result.json()["kind"] == "chat"
    assert result.json()["status"] == "needs_input"
    assert "blueprint" in result.json()["quiz_configuration"]["missing"]
    assert "quiz" not in result.json()


def send(setup, message, conversation="one"):
    return setup.client.post(
        "/api/chat/dispatch",
        json={
            "course_id": setup.course_id,
            "conversation_id": conversation,
            "model_id": "selected",
            "message": message,
        },
    )


def test_course_identity_statistics_and_latest_materials(chat_setup):
    setup = chat_setup
    setup.decisions["当前课程？"] = {"action": "overview", "overview_kind": "course"}
    setup.decisions["知识库多少文本？"] = {"action": "overview", "overview_kind": "statistics"}
    assert "Web服务端" in send(setup, "当前课程？").json()["reply"]
    assert "计算机" in send(setup, "当前课程？", "new").json()["reply"]
    reply = send(setup, "知识库多少文本？").json()["reply"]
    assert "1 份资料" in reply and "1 份可检索" in reply
    ready = setup.system.store.get("document", setup.document_id)
    assert str(len("".join(ready["cleaned_markdown"].split()))) in reply
    setup.system.store.put(
        "document",
        "pending-web",
        {
            "document_id": "pending-web",
            "course_id": setup.course_id,
            "user_id": "local-user",
            "parse_status": "queued",
            "title": "待处理.txt",
        },
    )
    assert "排队中 1 份" in send(setup, "知识库多少文本？").json()["reply"]
    ready["parse_status"] = "deleted"
    setup.system.store.put("document", setup.document_id, ready)
    reply = send(setup, "知识库多少文本？").json()["reply"]
    assert "0 份可检索" in reply
    assert len(setup.calls) == 5  # metadata answers require no second model call


def test_scoped_retrieval_citations_and_independent_history(chat_setup):
    setup = chat_setup
    setup.system.kb.ingest(
        MaterialInput(
            document_id="unselected",
            course_id=setup.course_id,
            title="另一份.txt",
            source_type="homework",
            markdown="TCP 三次握手确认收发能力。",
        ),
        user_id="local-user",
    )
    setup.decisions["只看老师课件解释握手"] = {
        "source_document_ids": [setup.document_id],
        "query": "三次握手",
        "materials_only": True,
    }
    result = send(setup, "只看老师课件解释握手")
    assert result.status_code == 200, result.text
    assert {ref["document_id"] for ref in result.json()["citations"]} == {setup.document_id}
    assert result.json()["citations"][0]["file_name"] == "老师课件.pptx"
    context = setup.calls[-1]["messages"]
    # Only read chunks are evidence; the catalog may still list other files.
    assert all("另一份.txt" not in item["content"] for item in context[1:])
    history = setup.client.get(f"/api/courses/{setup.course_id}/conversations/one/messages").json()[
        "items"
    ]
    assert history[-1]["citations"] == result.json()["citations"]
    assert history[-1]["model"] == "所选模型"
    send(setup, "继续解释")
    assert '"materials_only": true' in setup.calls[-1]["messages"][0]["content"]
    send(setup, "另一个问题", "new")
    assert '"materials_only": false' in setup.calls[-1]["messages"][0]["content"]
    assert not any(
        item["content"] == "只看老师课件解释握手" for item in setup.calls[-1]["messages"]
    )


def test_general_knowledge_strict_scope_and_deleted_sources(chat_setup):
    setup = chat_setup
    setup.decisions["聊聊学习方法"] = {"action": "general"}
    setup.answers.append("【通用知识补充】先理解概念再练习。")
    assert "【通用知识补充】先理解概念再练习。" == send(setup, "聊聊学习方法").json()["reply"]
    setup.decisions["只依据不存在章节"] = {"chapter": "不存在", "materials_only": True}
    result = send(setup, "只依据不存在章节")
    assert "没有找到足够" in result.json()["reply"] and not result.json()["citations"]
    setup.decisions["指定其他课程"] = {"source_document_ids": ["foreign"]}
    result = send(setup, "指定其他课程", "outside")
    assert result.status_code == 409
    assert "不属于当前课程" in result.json()["detail"]
    setup.decisions["只看课件"] = {"source_document_ids": [setup.document_id], "action": "read"}
    send(setup, "只看课件", "delete-test")
    doc = setup.system.store.get("document", setup.document_id)
    doc["parse_status"] = "deleted"
    setup.system.store.put("document", setup.document_id, doc)
    assert send(setup, "继续", "delete-test").status_code == 409


def test_targeted_clarification_is_remembered_and_resolved(chat_setup):
    setup = chat_setup
    setup.decisions["整理一下这章"] = {
        "intent": "clarify",
        "clarification": "你想解释这章重点，还是生成复习笔记？",
    }
    setup.decisions["先解释"] = {"action": "search", "query": "三次握手"}
    first = send(setup, "整理一下这章")
    assert first.json()["reply"] == "你想解释这章重点，还是生成复习笔记？"
    result = send(setup, "先解释")
    assert result.json()["intent"] == "ask"
    planner_messages = setup.calls[-2]["messages"]
    assert "pending_clarification" in planner_messages[0]["content"]
    planner_history = json.loads(planner_messages[-1]["content"])["history"]
    assert any(item["content"] == first.json()["reply"] for item in planner_history)


def test_invalid_citation_is_repaired_and_never_persisted(chat_setup):
    setup = chat_setup
    setup.answers.extend(["伪引用[资料99]", "正确依据[资料1]"])
    result = send(setup, "解释握手")
    assert result.status_code == 200
    assert "资料99" not in result.json()["reply"]
    setup.answers.extend(["伪引用[资料99]", "还是伪引用[资料99]"])
    assert send(setup, "解释握手", "bad").status_code == 502
    history = setup.client.get(f"/api/courses/{setup.course_id}/conversations/bad/messages").json()[
        "items"
    ]
    assert [row["role"] for row in history] == ["user"]


def test_quiz_dialog_restores_and_cancels_without_generating(chat_setup, monkeypatch):
    setup = chat_setup
    setup.decisions["给我测试一下掌握情况"] = {
        "intent": "quiz",
        "quiz_input": {"difficulty": "basic"},
    }

    def forbidden(*args, **kwargs):
        raise AssertionError("配置确认前不能调用出题模型")

    monkeypatch.setattr("final_review.api.build_fast_quiz_model", forbidden)
    result = send(setup, "给我测试一下掌握情况")
    assert result.json()["status"] == "needs_input"
    path = f"/api/courses/{setup.course_id}/conversations/one"
    pending = setup.client.get(path + "/messages").json()["pending_quiz"]
    assert pending["quiz_input"]["difficulty"] == "basic"
    assert setup.client.post(path + "/cancel-quiz").json()["cancelled"]
    assert setup.client.get(path + "/messages").json()["pending_quiz"] is None
    assert setup.system.store.scan("fast_quiz_session", {}) == []
    assert send(setup, "给我测试一下掌握情况").json()["status"] == "needs_input"


def test_discussing_quizzes_does_not_open_configuration(chat_setup):
    result = send(chat_setup, "模拟试卷一般如何设计？")
    assert result.json()["intent"] == "ask"
    assert "quiz_configuration" not in result.json()


def test_complete_model_input_still_waits_for_confirmation_and_cancel_preserves_saved_config(
    chat_setup,
):
    setup = chat_setup
    explicit = {
        "scope_mode": "course",
        "blueprint": [{"question_type": "choice", "question_count": 8}],
        "difficulty": "basic",
        "duration_mode": "untimed",
        "source_document_ids": [],
        "allow_ai_supplement": False,
        "include_imported_questions": False,
    }
    setup.decisions["要求已说完整"] = {"intent": "quiz", "quiz_input": explicit}
    result = send(setup, "要求已说完整").json()
    assert result["status"] == "needs_input"
    assert result["quiz_configuration"]["status"] == "ready"
    confirmed = setup.client.post(
        "/api/chat/dispatch",
        json={
            "course_id": setup.course_id,
            "conversation_id": "one",
            "model_id": "selected",
            "message": "确认试题配置",
            "quiz_input": explicit,
        },
    )
    assert confirmed.json()["status"] == "queued"
    assert send(setup, "要求已说完整").json()["status"] == "needs_input"
    setup.client.post(f"/api/courses/{setup.course_id}/conversations/one/cancel-quiz")
    from final_review.storage import stable_key

    saved = setup.system.store.get("conversation", stable_key(setup.course_id, "one"))
    assert saved["pending_quiz"] is None
    assert saved["quiz_config"]["question_count"] == 8


def test_sampling_covers_files_and_bounded_read_reports_partial(chat_setup):
    setup = chat_setup
    setup.system.kb.ingest(
        MaterialInput(
            document_id="other-web",
            course_id=setup.course_id,
            title="作业.txt",
            source_type="homework",
            markdown="三次握手还确认双方收发能力。",
        ),
        user_id="local-user",
    )
    materials = CourseMaterials(setup.system.store, setup.course_id, "local-user")
    documents = materials.select()
    assert {row.document_id for row in materials.sample(documents, 5)} == {
        setup.document_id,
        "other-web",
    }
    evidence, coverage = materials.read(documents, budget=30)
    assert coverage["partial"]
    assert sum(len(row.content) for row in evidence) <= 30
    with pytest.raises(ValueError):
        cited_evidence("不存在[资料99]", evidence)


def test_chapter_headings_limit_read_search_and_quiz_even_without_metadata(chat_setup):
    setup = chat_setup
    setup.system.kb.ingest(
        MaterialInput(
            document_id="whole-book",
            course_id=setup.course_id,
            title="整本教材.md",
            source_type="teacher_ppt",
            markdown=(
                "# 第一章\nHTTP是无状态协议。\n# 第三章\nTCP三次握手同步序列号。\n"
                "# 第四章\nDNS把域名转换成IP地址。"
            ),
        ),
        user_id="local-user",
    )
    materials = CourseMaterials(setup.system.store, setup.course_id, "local-user")
    selected = materials.select(["whole-book"], "第 3 章")
    rows, coverage = materials.read(selected)
    assert not coverage["partial"]
    assert rows and all("DNS" not in item.content and "HTTP" not in item.content for item in rows)
    evidence = materials.search(setup.system.kb, "三次握手", selected, "第3章")
    assert evidence and all("DNS" not in item.content for item in evidence)
    assert all("DNS" not in item.content for item in materials.sample(selected, 5))


def test_other_owner_materials_are_excluded_and_conflicts_are_given_both_sources(chat_setup):
    setup = chat_setup
    setup.system.store.put(
        "document",
        "other-owner",
        {
            "document_id": "other-owner",
            "course_id": setup.course_id,
            "user_id": "other-user",
            "title": "不能读取",
            "parse_status": "ready",
            "cleaned_markdown": "秘密文本",
        },
    )
    materials = CourseMaterials(setup.system.store, setup.course_id, "local-user")
    assert "other-owner" not in materials.by_id
    setup.system.kb.ingest(
        MaterialInput(
            document_id="conflicting",
            course_id=setup.course_id,
            title="另一份观点.md",
            source_type="homework",
            markdown="TCP握手不需要同步序列号。（与课件冲突）",
        ),
        user_id="local-user",
    )
    setup.decisions["比较两份说法"] = {"action": "read"}
    setup.answers.append("两份说法有冲突：课件需要同步序列号[资料1]，另一份说法否认[资料2]。")
    result = send(setup, "比较两份说法")
    assert len(result.json()["citations"]) == 2
    messages = setup.calls[-1]["messages"]
    assert "资料存在冲突时指出各自来源" in messages[0]["content"]
    assert not any("秘密文本" in item["content"] for item in messages)
