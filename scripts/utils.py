#!/usr/bin/env python3
"""
数据存储工具 - 管理题库、错题本、进度、知识库
"""
import sys
import io
import json
from pathlib import Path
from datetime import datetime


def _fix_encoding():
    """修复 Windows 终端 UTF-8 编码"""
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
        except Exception:
            pass


def get_skill_dir():
    """获取 skill 根目录"""
    return Path(__file__).parent.parent


def get_storage_dir():
    """获取存储目录"""
    storage = get_skill_dir() / "storage"
    storage.mkdir(exist_ok=True)
    return storage


def sanitize_name(name):
    """清理名称，用于文件夹名"""
    safe = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_", "·", "\u4e00-\u9fff")).strip()
    safe = safe.replace(" ", "_")
    return safe or "untitled"


def get_subject_dir(subject_name):
    """获取科目存储目录，自动创建子目录"""
    storage = get_storage_dir()
    subject_dir = storage / sanitize_name(subject_name)
    subject_dir.mkdir(exist_ok=True)
    (subject_dir / "knowledge_base").mkdir(exist_ok=True)
    return subject_dir


# ---------- 原文存储 ----------

def save_parsed_content(subject_name, content):
    """保存解析后的原文"""
    path = get_subject_dir(subject_name) / "parsed_content.md"
    path.write_text(content, encoding="utf-8")
    return str(path)


def load_parsed_content(subject_name):
    """加载解析后的原文"""
    path = get_subject_dir(subject_name) / "parsed_content.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


# ---------- 题库存储 ----------

def save_quiz(subject_name, quiz_list):
    """保存题库 (quiz_list 是 list[dict])"""
    path = get_subject_dir(subject_name) / "quiz.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(quiz_list, f, ensure_ascii=False, indent=2)
    return str(path)


def load_quiz(subject_name):
    """加载题库"""
    path = get_subject_dir(subject_name) / "quiz.json"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------- 错题本 ----------

def save_wrong_answers(subject_name, wrong_list):
    """保存错题本"""
    path = get_subject_dir(subject_name) / "wrong_answers.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(wrong_list, f, ensure_ascii=False, indent=2)
    return str(path)


def load_wrong_answers(subject_name):
    """加载错题本"""
    path = get_subject_dir(subject_name) / "wrong_answers.json"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def add_wrong_answer(subject_name, question, user_answer=""):
    """添加一道错题"""
    wrong = load_wrong_answers(subject_name)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "question": question,
        "user_answer": user_answer,
    }
    wrong.append(entry)
    save_wrong_answers(subject_name, wrong)
    return len(wrong)


# ---------- 进度存储 ----------

def save_progress(subject_name, progress):
    """保存刷题进度"""
    path = get_subject_dir(subject_name) / "progress.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    return str(path)


def load_progress(subject_name):
    """加载刷题进度"""
    path = get_subject_dir(subject_name) / "progress.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------- 知识库 ----------

def save_knowledge_base(subject_name, markdown_content):
    """保存知识库 Markdown"""
    path = get_subject_dir(subject_name) / "knowledge_base" / "index.md"
    path.write_text(markdown_content, encoding="utf-8")
    return str(path)


def load_knowledge_base(subject_name):
    """加载知识库"""
    path = get_subject_dir(subject_name) / "knowledge_base" / "index.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def append_to_knowledge_base(subject_name, section_title, content):
    """向知识库追加内容（用于补充错题相关知识点）"""
    path = get_subject_dir(subject_name) / "knowledge_base" / "index.md"
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")

    appendix = f"\n\n## 【错题补充】{section_title}\n\n{content}\n"
    path.write_text(existing + appendix, encoding="utf-8")
    return str(path)


# ---------- 统计与查询 ----------

def list_subjects():
    """列出所有科目"""
    storage = get_storage_dir()
    return [d.name for d in storage.iterdir() if d.is_dir()]


def get_subject_info(subject_name):
    """获取科目完整信息"""
    subject_dir = get_subject_dir(subject_name)
    quiz = load_quiz(subject_name)
    wrong = load_wrong_answers(subject_name)
    progress = load_progress(subject_name)
    kb = load_knowledge_base(subject_name)

    # 统计薄弱知识点
    from collections import Counter
    topic_counter = Counter()
    for w in wrong:
        q = w.get("question", {})
        topic = q.get("topic", q.get("knowledge_point", "未分类"))
        topic_counter[topic] += 1

    weak_points = [
        {"topic": t, "wrong_count": c}
        for t, c in topic_counter.most_common()
    ]

    return {
        "name": subject_name,
        "has_content": (subject_dir / "parsed_content.md").exists(),
        "quiz_count": len(quiz),
        "wrong_count": len(wrong),
        "has_knowledge_base": kb is not None,
        "progress": progress,
        "weak_points": weak_points,
    }


def print_subject_info(subject_name):
    """打印科目信息（命令行用）"""
    info = get_subject_info(subject_name)
    print(f"科目: {info['name']}")
    print(f"  资料: {'已上传' if info['has_content'] else '未上传'}")
    print(f"  题库: {info['quiz_count']} 题")
    print(f"  错题: {info['wrong_count']} 题")
    print(f"  知识库: {'已生成' if info['has_knowledge_base'] else '未生成'}")
    if info["weak_points"]:
        print("  薄弱知识点:")
        for wp in info["weak_points"][:5]:
            print(f"    - {wp['topic']}: 错 {wp['wrong_count']} 次")


# ---------- 命令行入口 ----------

def main():
    _fix_encoding()
    if len(sys.argv) < 2:
        print("用法:")
        print("  python utils.py list              列出所有科目")
        print("  python utils.py info <科目名>      查看科目详情")
        print("  python utils.py path <科目名>      查看科目存储路径")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "list":
        subjects = list_subjects()
        if subjects:
            print("已创建的科目:")
            for s in subjects:
                print(f"  - {s}")
        else:
            print("暂无科目")
    elif cmd == "info" and len(sys.argv) >= 3:
        print_subject_info(sys.argv[2])
    elif cmd == "path" and len(sys.argv) >= 3:
        print(get_subject_dir(sys.argv[2]))
    else:
        print("未知命令")
        sys.exit(1)


if __name__ == "__main__":
    main()
