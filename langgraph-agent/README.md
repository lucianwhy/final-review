# LangGraph Agent（final-review）

把本仓库根目录的 Cursor Skill（`SKILL.md` / `AGENT.md`）封装成可运行的 **LangChain / LangGraph** Agent。

默认**优先读取仓库根目录本地文件**；本地缺失时再回退到 GitHub raw URL。也可通过环境变量 / CLI 强制使用远端 skill。

## 功能概览

LangGraph 节点流水线：

1. `load_skill` — 本地优先加载 skill；可选 httpx 拉取并缓存远端
2. `convert_markdown` — markitdown 适配；CLI 缺失时接受已有 Markdown 或给出清晰占位说明
3. `clarify_exam` — 推断/确认考试题型
4. `clean_markdown` — 去噪去重，保留定义/定理/方法/易错点
5. `extract_knowledge` — 按题源优先级：历年真题 > 老师PPT > 平时作业 > 速成课 > AI补充
6. `generate_questions` — 每知识点 1–6 题，轻量题源标记
7. `generate_explanations` — 得分导向解析（客观题「这题核心知识点」；主观题「答题核心点/必须出现/常见失分点」）
8. `format_output` — 文本：题目在前、答案在后；HTML：答案默认折叠

## 环境要求

- Python 3.11+
- 可选：`markitdown` CLI（转换 PPT/PDF 等）；没有时请直接喂 `.md`

## 安装

在仓库根目录下进入本目录：

```bash
cd langgraph-agent
python -m venv .venv
source .venv/bin/activate
pip install -e .
# 或：pip install -r requirements.txt 并把 src 加入 PYTHONPATH
```

复制环境变量模板：

```bash
cp .env.example .env
# 编辑 .env：填入 OPENAI_API_KEY（可选）；不填则自动进入 mock 模式
```

## Skill 加载策略（本地优先）

| 优先级 | 条件 | 行为 |
|--------|------|------|
| 1 | 设置了 `SKILL_URL` / `AGENT_URL`，或 CLI `--skill-url` / `--agent-url` | 强制拉取远端（「远端 skill」） |
| 2 | 仓库根目录存在 `SKILL.md` / `AGENT.md` | 直接读本地文件 |
| 3 | 本地不存在 | 回退到默认 GitHub raw URL，结果缓存在 `.cache/skills/` |

从 Python 包目录 `src/final_review_agent/` 看，本地路径对应仓库根的 `SKILL.md` / `AGENT.md`（即相对包目录向上三级）。

## 配置

| 变量 | 说明 | 默认 |
|------|------|------|
| `OPENAI_API_KEY` | OpenAI 兼容密钥；为空则 mock | _(空)_ |
| `OPENAI_API_BASE` | 可选自定义 base URL | _(空)_ |
| `OPENAI_MODEL` | 模型名 | `gpt-4o-mini` |
| `SKILL_URL` | **可选**，强制远端 SKILL.md | _(未设置=本地优先)_ |
| `AGENT_URL` | **可选**，强制远端 AGENT.md | _(未设置=本地优先)_ |
| `OUTPUT_FORMAT` | `text` 或 `html` | `text` |

### 强制使用远端 skill

```bash
export SKILL_URL=https://raw.githubusercontent.com/lucianwhy/final-review/master/SKILL.md
export AGENT_URL=https://raw.githubusercontent.com/lucianwhy/final-review/master/AGENT.md
# 或 CLI：
python -m final_review_agent --demo --skill-url https://example.com/my-SKILL.md
```

远端加载流程：`skill_loader.fetch_text` → URL 的 SHA256 前 16 位 → `.cache/skills/<hash>.md`；命中缓存则不再请求。

生成/解析节点会把加载到的 `skill_md` / `agent_md` 注入 system prompt。

## 运行

### Mock 演示（无需 API Key）

```bash
cd langgraph-agent
export OPENAI_API_KEY=
python -m final_review_agent --demo --format text

# 或
bash demos/run_mock.sh
```

### 真实 LLM

```bash
export OPENAI_API_KEY=sk-...
# 可选：export OPENAI_API_BASE=https://api.openai.com/v1
python -m final_review_agent --input demos/sample_notes.md --format text
python -m final_review_agent --input notes.md --format html --out review.html
```

### CLI 参数

- `--input / -i`：输入文件（`.md` 直接读；其它格式尝试 `markitdown`）
- `--demo`：使用 `demos/sample_notes.md`
- `--format text|html`
- `--skill-url` / `--agent-url`：强制远端 skill
- `--out`：写入文件
- `-v`：详细日志

## 目录结构

```
langgraph-agent/
  README.md
  pyproject.toml
  requirements.txt
  .env.example
  .gitignore
  demos/sample_notes.md
  demos/run_mock.sh
  src/final_review_agent/
    __main__.py
    config.py
    skill_loader.py
    state.py
    llm.py
    prompts.py
    graph.py
    nodes/...
```

仓库根目录（本 Agent 的 skill 源）：

- `../SKILL.md`
- `../AGENT.md`

## 与 Skill 文档的关系

复习规则正文仍以仓库根目录的 [`SKILL.md`](../SKILL.md) / [`AGENT.md`](../AGENT.md) 为准；本目录只提供可运行的 LangGraph 封装，**不会把 skill 正文硬编码进 Python**。
