"""Two quiz print views derived from one immutable confirmed Markdown body."""

import re

from .domain import DomainConflict, DomainNotFound, DomainService

_TYPE_LABELS = {
    "choice": "选择题",
    "true_false": "判断题",
    "fill_blank": "填空题",
    "short_answer": "简答题",
    "calculation": "计算题",
    "proof": "证明题",
}
_OPTION_PREFIX = re.compile(r"^[A-H][.．、:：]\s*", re.IGNORECASE)


def build_quiz_sections(title: str, total_score: float, questions: list[dict]) -> tuple[str, str]:
    """Return the question-only prefix and the full canonical Markdown body."""
    ordered = sorted(questions, key=lambda item: item["order"])
    lines = [f"# {title}", "", f"共 {len(ordered)} 题 · 满分 {total_score:g} 分", "", "## 题目", ""]
    for question in ordered:
        lines.extend(
            [
                f"### 第 {question['order']} 题 · {_TYPE_LABELS[question['question_type']]}"
                f" · {question['score']:g} 分",
                "",
                question["stem"],
                "",
            ]
        )
        for index, option in enumerate(question.get("options", [])):
            lines.append(f"- {chr(65 + index)}. {_OPTION_PREFIX.sub('', option).strip()}")
        if question.get("options"):
            lines.append("")
    questions_only = "\n".join(lines).rstrip() + "\n"
    lines = ["## 答案与解析", ""]
    for question in ordered:
        lines.extend(
            [
                f"### 第 {question['order']} 题",
                "",
                f"**答案：** {question['reference_answer']}",
                "",
                f"**解析：** {question['explanation']}",
                "",
            ]
        )
        if question.get("must_include"):
            lines.extend(["**得分点：**", ""])
            lines.extend(f"- {point}" for point in question["must_include"])
            lines.append("")
    return questions_only, questions_only + "\n" + "\n".join(lines).rstrip() + "\n"


def build_quiz_markdown(title: str, total_score: float, questions: list[dict]) -> str:
    return build_quiz_sections(title, total_score, questions)[1]


def print_quiz_revision(store, user_id, course_id, asset_id, revision_id, variant):
    """Read a confirmed snapshot; never render draft answers as a formal print view."""
    if variant not in {"questions", "answers"}:
        raise ValueError("打印版式无效")
    domain = DomainService(store, user_id)
    domain.course(course_id)
    asset = domain._owned("learning_asset", asset_id)
    revision = domain._owned("asset_revision", revision_id)
    payload = domain._owned("quiz_revision_payload", revision_id)
    if (
        asset.get("course_id") != course_id
        or asset.get("asset_type") != "quiz"
        or revision.get("asset_id") != asset_id
        or revision.get("course_id") != course_id
        or payload.get("asset_id") != asset_id
        or payload.get("course_id") != course_id
    ):
        raise DomainNotFound(revision_id)
    if revision.get("state") not in {"confirmed", "superseded", "archived"} or payload.get(
        "state"
    ) != revision.get("state"):
        raise DomainConflict("请先确认试卷，再打印正式版本")
    markdown = revision["markdown"]
    question_length = payload.get("print_question_length")
    if not isinstance(question_length, int) or not 0 < question_length < len(markdown):
        raise DomainConflict("此试卷版本缺少正式打印正文")
    if variant == "questions":
        markdown = markdown[:question_length]
    return {
        "revision_id": revision_id,
        "variant": variant,
        "title": payload["title"],
        "markdown": markdown,
    }
