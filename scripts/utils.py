#!/usr/bin/env python3
"""
Data storage helpers for the final-review skill.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _fix_encoding() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
        except Exception:
            pass


def get_skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def get_storage_dir() -> Path:
    storage = get_skill_dir() / "storage"
    storage.mkdir(exist_ok=True)
    return storage


def sanitize_name(name: str) -> str:
    filtered = []
    for char in name.strip():
        if char.isalnum() or char in {" ", "-", "_", "·"} or "\u4e00" <= char <= "\u9fff":
            filtered.append(char)
    safe = "".join(filtered).strip().replace(" ", "_")
    return safe or "untitled"


def get_subject_dir(subject_name: str) -> Path:
    subject_dir = get_storage_dir() / sanitize_name(subject_name)
    subject_dir.mkdir(exist_ok=True)
    (subject_dir / "knowledge_base").mkdir(exist_ok=True)
    (subject_dir / "markdown格式").mkdir(exist_ok=True)
    return subject_dir


def get_markdown_dir(subject_name: str) -> Path:
    return get_subject_dir(subject_name) / "markdown格式"


def _json_path(subject_name: str, filename: str) -> Path:
    return get_subject_dir(subject_name) / filename


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path: Path, payload: Any) -> str:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return str(path)


def save_parsed_content(subject_name: str, content: str) -> str:
    path = _json_path(subject_name, "parsed_content.md")
    path.write_text(content, encoding="utf-8")
    return str(path)


def load_parsed_content(subject_name: str) -> str | None:
    path = _json_path(subject_name, "parsed_content.md")
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def save_markdown_copy(subject_name: str, source_name: str, markdown_content: str) -> str:
    stem = Path(source_name).stem
    filename = sanitize_name(stem) + ".md"
    path = get_markdown_dir(subject_name) / filename
    path.write_text(markdown_content, encoding="utf-8")
    return str(path)


def list_markdown_copies(subject_name: str) -> list[str]:
    markdown_dir = get_markdown_dir(subject_name)
    return sorted(file.name for file in markdown_dir.glob("*.md"))


def save_quiz(subject_name: str, quiz_list: list[dict[str, Any]]) -> str:
    return _save_json(_json_path(subject_name, "quiz.json"), quiz_list)


def load_quiz(subject_name: str) -> list[dict[str, Any]]:
    return _load_json(_json_path(subject_name, "quiz.json"), [])


def save_wrong_answers(subject_name: str, wrong_list: list[dict[str, Any]]) -> str:
    return _save_json(_json_path(subject_name, "wrong_answers.json"), wrong_list)


def load_wrong_answers(subject_name: str) -> list[dict[str, Any]]:
    return _load_json(_json_path(subject_name, "wrong_answers.json"), [])


def add_wrong_answer(subject_name: str, question: dict[str, Any], user_answer: str = "") -> int:
    wrong = load_wrong_answers(subject_name)
    wrong.append(
        {
            "timestamp": datetime.now().isoformat(),
            "question": question,
            "user_answer": user_answer,
        }
    )
    save_wrong_answers(subject_name, wrong)
    return len(wrong)


def save_progress(subject_name: str, progress: dict[str, Any]) -> str:
    return _save_json(_json_path(subject_name, "progress.json"), progress)


def load_progress(subject_name: str) -> dict[str, Any]:
    return _load_json(
        _json_path(subject_name, "progress.json"),
        {"last_index": 0, "total_answered": 0, "total_correct": 0},
    )


def save_knowledge_base(subject_name: str, markdown_content: str) -> str:
    path = get_subject_dir(subject_name) / "knowledge_base" / "index.md"
    path.write_text(markdown_content, encoding="utf-8")
    return str(path)


def load_knowledge_base(subject_name: str) -> str | None:
    path = get_subject_dir(subject_name) / "knowledge_base" / "index.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def append_to_knowledge_base(subject_name: str, section_title: str, content: str) -> str:
    path = get_subject_dir(subject_name) / "knowledge_base" / "index.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    appendix = f"\n\n## 【错题补充】{section_title}\n\n{content}\n"
    path.write_text(existing + appendix, encoding="utf-8")
    return str(path)


def save_exam_profile(subject_name: str, payload: dict[str, Any]) -> str:
    return _save_json(_json_path(subject_name, "exam_profile.json"), payload)


def load_exam_profile(subject_name: str) -> dict[str, Any]:
    return _load_json(
        _json_path(subject_name, "exam_profile.json"),
        {
            "exam_reminder": "",
            "question_blueprint": {},
            "programming_focus": [],
        },
    )


def save_preferences(subject_name: str, payload: dict[str, Any]) -> str:
    return _save_json(_json_path(subject_name, "preferences.json"), payload)


def load_preferences(subject_name: str) -> dict[str, Any]:
    return _load_json(
        _json_path(subject_name, "preferences.json"),
        {
            "preferred_question_types": [],
            "explanation_style": "standard",
            "tutor_mode": "先讲后练",
            "html_hint_enabled": True,
        },
    )


def list_subjects() -> list[str]:
    return sorted(directory.name for directory in get_storage_dir().iterdir() if directory.is_dir())


def _topic_key(question: dict[str, Any]) -> str:
    return question.get("topic") or question.get("knowledge_point") or "未分类"


def get_subject_info(subject_name: str) -> dict[str, Any]:
    subject_dir = get_subject_dir(subject_name)
    quiz = load_quiz(subject_name)
    wrong = load_wrong_answers(subject_name)
    progress = load_progress(subject_name)
    knowledge_base = load_knowledge_base(subject_name)

    topic_stats: dict[str, int] = {}
    for entry in wrong:
        topic = _topic_key(entry.get("question", {}))
        topic_stats[topic] = topic_stats.get(topic, 0) + 1

    weak_points = [
        {"topic": topic, "wrong_count": count}
        for topic, count in sorted(topic_stats.items(), key=lambda item: item[1], reverse=True)
    ]

    return {
        "name": subject_name,
        "path": str(subject_dir),
        "has_content": (subject_dir / "parsed_content.md").exists(),
        "markdown_files": list_markdown_copies(subject_name),
        "quiz_count": len(quiz),
        "wrong_count": len(wrong),
        "has_exam_profile": _json_path(subject_name, "exam_profile.json").exists(),
        "has_preferences": _json_path(subject_name, "preferences.json").exists(),
        "has_knowledge_base": knowledge_base is not None,
        "progress": progress,
        "weak_points": weak_points,
    }


def print_subject_info(subject_name: str) -> None:
    info = get_subject_info(subject_name)
    print(f"科目: {info['name']}")
    print(f"  路径: {info['path']}")
    print(f"  资料: {'已上传' if info['has_content'] else '未上传'}")
    print(f"  Markdown 副本: {len(info['markdown_files'])} 个")
    print(f"  题库: {info['quiz_count']} 题")
    print(f"  错题: {info['wrong_count']} 题")
    print(f"  考试蓝图: {'已配置' if info['has_exam_profile'] else '未配置'}")
    print(f"  偏好: {'已配置' if info['has_preferences'] else '未配置'}")
    print(f"  知识库: {'已生成' if info['has_knowledge_base'] else '未生成'}")
    if info["weak_points"]:
        print("  薄弱知识点:")
        for weak_point in info["weak_points"][:5]:
            print(f"    - {weak_point['topic']}: 错 {weak_point['wrong_count']} 次")


def main() -> None:
    _fix_encoding()
    if len(sys.argv) < 2:
        print("用法:")
        print("  python utils.py list")
        print("  python utils.py info <科目名>")
        print("  python utils.py path <科目名>")
        sys.exit(1)

    command = sys.argv[1]
    if command == "list":
        subjects = list_subjects()
        if subjects:
            print("已创建的科目:")
            for subject in subjects:
                print(f"  - {subject}")
        else:
            print("暂无科目")
    elif command == "info" and len(sys.argv) >= 3:
        print_subject_info(sys.argv[2])
    elif command == "path" and len(sys.argv) >= 3:
        print(get_subject_dir(sys.argv[2]))
    else:
        print("未知命令")
        sys.exit(1)


if __name__ == "__main__":
    main()
