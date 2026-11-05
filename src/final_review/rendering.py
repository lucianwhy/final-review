from .policy import SOURCE_LABELS
from .schemas import AgentResponse


def render_markdown(response: AgentResponse) -> str:
    lines = ["# 期末复习", ""]
    if response.status == "needs_input":
        return "请先确认考试题型、重点与不考范围。\n"
    questions = response.questions
    if response.assessment:
        questions = response.assessment["reference_questions"]
    if questions:
        lines += ["## 题目", ""]
        for index, question in enumerate(questions, 1):
            lines += [
                f"### {index}. {question['stem']}",
                "",
                f"题源：{SOURCE_LABELS[question['source_type']]}",
                "",
            ]
            lines += question.get("options", []) + [""]
    if response.answer:
        lines += [response.answer, ""]
    if response.assessment:
        lines += ["## 答案与解析", ""]
        grades = {g["question_id"]: g for g in response.assessment["items"]}
        for index, question in enumerate(questions, 1):
            core = (
                "这题核心知识点"
                if question["question_type"] in {"choice", "fill_blank", "true_false"}
                else "这题答题核心点"
            )
            lines += [
                f"### 第 {index} 题",
                "",
                f"参考答案：{question['reference_answer']}",
                "",
                f"{core}：{question['core_point']}",
                "",
                "必须出现：" + "；".join(question["must_include"]),
                "",
                "常见失分点：" + "；".join(question["common_mistakes"]),
                "",
                f"得分步骤：{question['scoring_tips']}",
                "",
                f"本次评分：{grades[question['id']]['score']}/100",
                "",
                f"反馈：{grades[question['id']]['feedback']}",
                "",
            ]
    if response.suggestions:
        lines += ["## 后续复习", ""] + [f"- {s}" for s in response.suggestions] + [""]
    if response.citations:
        lines += ["## 资料来源", ""]
        lines += [
            f"- {e.title} / {e.chapter}（{SOURCE_LABELS[e.source_type]}；{e.chunk_id}）"
            for e in response.citations
        ]
    return "\n".join(lines) + "\n"
