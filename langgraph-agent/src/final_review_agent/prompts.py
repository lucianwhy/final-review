"""Prompt templates; skill text is injected at call time (not hardcoded)."""

from __future__ import annotations

SYSTEM_PREFIX = """你是期末复习助手 Agent。必须严格遵守以下远程 skill / agent 规则。

===== SKILL.md =====
{skill_md}

===== AGENT.md =====
{agent_md}
===== END RULES =====
"""

CLARIFY_PROMPT = """任务：clarify_exam
根据用户材料与说明，确认考试题型。若材料已足够推断，直接给出合理假设，不要无意义追问。

用户说明：
{user_notes}

材料摘要（前 2000 字）：
{markdown_preview}

请只输出 JSON：
{{
  "exam_formats": ["闭卷笔试", ...],
  "question_types": ["选择题", "判断题", ...],
  "needs_clarify": false,
  "notes": "一句话说明假设依据"
}}
"""

CLEAN_PROMPT = """任务：clean_markdown
清洗下列 Markdown：去乱码、去重复、去低价值废话；保留定义、性质、定理、标准方法、常见考法、易错点。
目标：清晰、可背、可考。

<<<MARKDOWN>>>
{raw_markdown}
<<<END>>>

直接输出清洗后的 Markdown 正文，不要包裹代码块。
"""

EXTRACT_PROMPT = """任务：extract_knowledge
从清洗后的复习材料提取知识点。题源优先级必须遵守：
历年真题 > 老师PPT > 平时作业 > 速成课 > AI补充

考试题型：{question_types}

<<<CLEANED>>>
{cleaned_markdown}
<<<END>>>

只输出 JSON 数组，每项：
{{
  "name": "...",
  "summary": "...",
  "source_priority": "历年真题|老师PPT|平时作业|速成课|AI补充",
  "exam_styles": ["选择题", ...],
  "density": "high|medium|low"
}}
"""

GENERATE_QUESTIONS_PROMPT = """任务：generate_questions
按真实考试题型出题。每个知识点通常 1~6 题；越密越高频题越多。
为每题加轻量题源标记（类别级）。

考试题型：{question_types}

知识点 JSON：
{knowledge_points}

清洗材料（可参考）：
{cleaned_markdown}

只输出 JSON 数组，每项：
{{
  "id": "Q1",
  "kp": "知识点名",
  "qtype": "选择题|判断题|填空题|简答题|计算题|证明题",
  "stem": "题干",
  "options": ["A. ..."] ,
  "source_tag": "题源：历年真题"
}}
options 仅客观选择题需要。
"""

EXPLAIN_PROMPT = """任务：generate_explanations
为每道题生成得分导向解析。
- 客观题必须含「这题核心知识点」
- 主观题必须含「这题答题核心点」「必须出现」「常见失分点」

题目 JSON：
{questions}

只输出 JSON 数组，每项：
{{
  "id": "Q1",
  "answer": "...",
  "is_objective": true,
  "explanation": "..."
}}
"""
