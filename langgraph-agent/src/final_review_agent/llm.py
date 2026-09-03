"""LLM factory: ChatOpenAI when key present, else deterministic mock."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from final_review_agent.config import Settings

logger = logging.getLogger(__name__)


class MockChatModel(BaseChatModel):
    """Deterministic stub so --demo works without OPENAI_API_KEY."""

    model_name: str = "mock-final-review"
    temperature: float = 0.0

    @property
    def _llm_type(self) -> str:
        return "mock-final-review"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        blob = "\n".join(getattr(m, "content", str(m)) for m in messages)
        content = self._stub_response(blob)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    def _task(self, prompt: str) -> str | None:
        """Prefer explicit 任务： markers so skill body keywords do not confuse routing."""
        m = re.search(r"任务：([a-z_]+)", prompt)
        return m.group(1) if m else None

    def _stub_response(self, prompt: str) -> str:
        task = self._task(prompt)

        if task == "clarify_exam" or (
            task is None and ("确认考试" in prompt or "clarify_exam" in prompt.lower())
        ):
            return json.dumps(
                {
                    "exam_formats": ["闭卷笔试"],
                    "question_types": ["选择题", "判断题", "简答题", "计算题"],
                    "needs_clarify": False,
                    "notes": "示例假设：标准闭卷，选择/判断/简答/计算。",
                },
                ensure_ascii=False,
            )

        if task == "clean_markdown":
            m = re.search(r"<<<MARKDOWN>>>(.*?)<<<END>>>", prompt, re.S)
            raw = m.group(1).strip() if m else ""
            if not raw:
                return "# (cleaned empty)\n"
            lines: list[str] = []
            seen: set[str] = set()
            for line in raw.splitlines():
                key = line.strip()
                if not key:
                    continue
                if key in seen and key.startswith("#"):
                    continue
                seen.add(key)
                lines.append(line)
            return "\n".join(lines)[:8000]

        if task == "extract_knowledge":
            return json.dumps(
                [
                    {
                        "name": "线性表：顺序表与链表",
                        "summary": "顺序表随机访问 O(1)；链表插入删除更优（已知结点）。",
                        "source_priority": "老师PPT",
                        "exam_styles": ["选择题", "简答题"],
                        "density": "high",
                    },
                    {
                        "name": "栈与队列",
                        "summary": "栈 LIFO；循环队列判空判满条件。",
                        "source_priority": "历年真题",
                        "exam_styles": ["判断题", "简答题"],
                        "density": "high",
                    },
                    {
                        "name": "二叉树遍历与哈夫曼树",
                        "summary": "先中后序；先序+中序唯一确定；哈夫曼带权路径最短。",
                        "source_priority": "老师PPT",
                        "exam_styles": ["选择题", "计算题"],
                        "density": "medium",
                    },
                    {
                        "name": "排序算法比较",
                        "summary": "快排/归并/堆排复杂度与稳定性。",
                        "source_priority": "平时作业",
                        "exam_styles": ["选择题", "填空题"],
                        "density": "high",
                    },
                ],
                ensure_ascii=False,
                indent=2,
            )

        if task == "generate_questions":
            return json.dumps(
                [
                    {
                        "id": "Q1",
                        "kp": "线性表：顺序表与链表",
                        "qtype": "选择题",
                        "stem": "关于顺序表与单链表，下列说法正确的是？",
                        "options": [
                            "A. 顺序表插入任意位置均为 O(1)",
                            "B. 单链表可按下标 O(1) 随机访问",
                            "C. 已知结点时单链表插入可达 O(1)",
                            "D. 两者空间占用完全相同",
                        ],
                        "source_tag": "题源：老师PPT",
                    },
                    {
                        "id": "Q2",
                        "kp": "栈与队列",
                        "qtype": "判断题",
                        "stem": "栈只能在栈顶进行插入和删除操作。",
                        "source_tag": "题源：历年真题",
                    },
                    {
                        "id": "Q3",
                        "kp": "栈与队列",
                        "qtype": "简答题",
                        "stem": "简述循环队列判空与判满的常用条件，并说明为何引入循环。",
                        "source_tag": "题源：历年真题",
                    },
                    {
                        "id": "Q4",
                        "kp": "二叉树遍历与哈夫曼树",
                        "qtype": "选择题",
                        "stem": "已知二叉树先序与中序遍历序列，能否唯一确定该二叉树？",
                        "options": [
                            "A. 能",
                            "B. 不能",
                            "C. 仅当是完全二叉树时能",
                            "D. 仅当是满二叉树时能",
                        ],
                        "source_tag": "题源：老师PPT",
                    },
                    {
                        "id": "Q5",
                        "kp": "排序算法比较",
                        "qtype": "填空题",
                        "stem": "平均时间复杂度为 O(n log n) 且稳定的常见内部排序是______。",
                        "source_tag": "题源：平时作业",
                    },
                    {
                        "id": "Q6",
                        "kp": "排序算法比较",
                        "qtype": "简答题",
                        "stem": "比较快速排序与归并排序的时间复杂度、空间复杂度与稳定性，并说明适用场景。",
                        "source_tag": "题源：速成课框架改编",
                    },
                ],
                ensure_ascii=False,
                indent=2,
            )

        if task == "generate_explanations":
            return json.dumps(
                [
                    {
                        "id": "Q1",
                        "answer": "C",
                        "is_objective": True,
                        "explanation": (
                            "这题核心知识点：顺序表与链表的时间复杂度差异。"
                            "关键定义：顺序表连续存储可随机访问 O(1)；链表靠指针，"
                            "已知结点时插入/删除 O(1)。易混点：不要把“已知下标”和“已知结点”混为一谈。"
                        ),
                    },
                    {
                        "id": "Q2",
                        "answer": "正确",
                        "is_objective": True,
                        "explanation": (
                            "这题核心知识点：栈的定义——仅允许在栈顶插入删除（LIFO）。"
                            "判定依据：栈顶指针唯一操作端。"
                        ),
                    },
                    {
                        "id": "Q3",
                        "answer": (
                            "判空：front == rear；判满：(rear+1)%MaxSize == front（牺牲一格）。"
                            "引入循环是为了避免“假溢出”。"
                        ),
                        "is_objective": False,
                        "explanation": (
                            "这题答题核心点：循环队列判空/判满公式 + 假溢出原因。"
                            "必须出现：front/rear、取模、牺牲一单元或计数法。"
                            "常见失分点：只写判空不写判满；把假溢出与真溢出混淆。"
                        ),
                    },
                    {
                        "id": "Q4",
                        "answer": "A",
                        "is_objective": True,
                        "explanation": (
                            "这题核心知识点：先序确定根，中序划分左右子树，可递归唯一还原。"
                            "易混点：后序+先序一般不能唯一确定。"
                        ),
                    },
                    {
                        "id": "Q5",
                        "answer": "归并排序",
                        "is_objective": True,
                        "explanation": (
                            "这题核心知识点：排序稳定性与复杂度。"
                            "归并平均/最坏均 O(n log n) 且稳定；快排不稳定；堆排不稳定。"
                        ),
                    },
                    {
                        "id": "Q6",
                        "answer": (
                            "快排：平均 O(n log n)、最坏 O(n^2)、空间 O(log n)、不稳定；"
                            "适合一般内存排序。归并：始终 O(n log n)、空间 O(n)、稳定；"
                            "适合外部排序或要求稳定的场景。"
                        ),
                        "is_objective": False,
                        "explanation": (
                            "这题答题核心点：对照表写清时间/空间/稳定性/场景。"
                            "必须出现：快排最坏退化、归并额外 O(n) 空间、稳定性结论。"
                            "常见失分点：漏写最坏复杂度；把堆排误写成稳定。"
                        ),
                    },
                ],
                ensure_ascii=False,
                indent=2,
            )

        return "OK"


def get_llm(settings: Settings) -> BaseChatModel:
    if settings.use_mock_llm:
        logger.warning("OPENAI_API_KEY not set — using MockChatModel")
        return MockChatModel()

    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": settings.openai_model,
        "api_key": settings.openai_api_key,
        "temperature": 0.2,
    }
    if settings.openai_api_base:
        kwargs["base_url"] = settings.openai_api_base
    return ChatOpenAI(**kwargs)
