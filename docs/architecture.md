# 架构与取舍

将规则型 Skill 工程化为可运行、可测试的单 Agent 后端。输入为课程资料、考试信息和复习需求；结果为有证据的回答、模拟题和得分反馈。当前目标规模：单进程、可信环境、小型课程语料。

## Module 与 Interface

`FinalReviewAgent` 是外部主要 module，interface 为 `invoke / resume_profile / evaluate / recover / read`。调用者处理课程、会话和公开响应，不操作图节点或检查点。

内部 seam 有真实替代需求：Store（SurrealDB / 测试内存 adapter）、模型与 Embeddings（实际提供商 / 测试 fixture）。测试从 Agent 和 HTTP interface 验证行为。

LangChain 负责模型、PromptTemplate、Tool、Retriever、结构化结果；LangGraph 负责控制流与持久状态。文件转换和数据库写入是确定性函数。检索阶段由模型决定检索词及是否继续检索，后续生成与评分使用固定结构化调用。

## 数据流

入库：MarkItDown → 原始 Markdown → 清洗控制字符/空白 → 字符分块与重复块去重 → 分批 Embedding → SurrealDB 事务写入。

文档键由课程、标题、章节、题源、清洗内容哈希构成。相同资料重复上传不再支付 Embedding 成本。内容变更产生新文档，当前不自动撤下旧版本。

检索：查询 Embedding → 数据库先过滤课程/章节 → 精确余弦排序召回 → 最低相关性阈值 → 题源 bonus → TopK。

```text
rank_score = similarity + 0.04 × priority
priority: past_exam=4, teacher_ppt=3, homework=2, crash_course=1, ai_supplement=0
```

先排除不相关片段，再加权。权重是启发式，不是强制题源排序：高度相关 PPT 仍可排在弱相关真题之前。来源由上传者填写，后端不认证其真实性。

当前使用精确向量计算，无 HNSW。小规模语料下易于验证元数据过滤；扩容时可替换 Store.search，先测带过滤的 ANN 召回率，再引入索引。

## SurrealDB 表

| 表 | 内容 |
| --- | --- |
| document | 课程、来源、章节、原始/清洗 Markdown、哈希、分块数 |
| chunk | 文档 ID、文本、Embedding、课程/章节/题源元数据 |
| knowledge_point | 会话最近已完成测评的知识点计划 |
| review_session | 最近公开结果、薄弱点；保留一个 Embedding 配置记录 |
| checkpoint | thread/namespace/checkpoint ID、父 ID、序列化图状态和元数据 |
| pending_write | task ID、channel、索引与序列化值 |

checkpoint 是恢复的事实来源；review_session 是业务快照，两者职责不同。检查点使用 LangGraph typed serializer，base64 后存储；包含 pending writes 和父子关系。只实现同步 Checkpointer interface。

## 状态与失败处理

- course_id + session_id 哈希作为 thread_id。每会话一个活动任务，64 个条带锁保护进程内并发。
- needs_input 等待考试信息；awaiting_answers 等待完整作答。中断节点内不做外部写入。
- 每轮题目 ID 含独立 run_id；评分必须完整匹配。重复提交同一轮相同答案返回已有评分。
- 知识点均分低于 60 加入薄弱点，达到 60 则移除；用于排序，不是经过校准的掌握度模型。
- 确定性验证引用 ID、原文、题型、题量，再让模型核对语义。最多修复 MAX_REPAIRS 次，失败返回资料不足。
- 故障保留检查点，recover 从未完成节点继续。该节点内模型调用可能重跑并产生费用。
- 等待作答时不返回参考答案、评分规则或证据正文。评分完成后才返回完整解析。

## Skill 映射

| 规则 | 代码 |
| --- | --- |
| 真题 > PPT > 作业 > 速成课 > AI | policy.SOURCE_PRIORITY + KnowledgeBase.search |
| 转 Markdown → 清洗 → 知识点 → 出题 | upload/ingest + generate 中 plan → quiz |
| 先确认考试信息 | exam_profile + interrupt |
| 每知识点 1～6 题，遵循考试题型 | Schema + verify 确定性校验 |
| 解析服务拿分 | Question Schema + DOMAIN_POLICY + grade Prompt |
| 正文题答分离 | rendering.render_markdown |
| 跨轮沿用考试信息 | 已确认 exam_profile 存入 checkpoint |

SKILL.md 是规则源文档，运行时稳定规则显式映射到代码和测试。修改 Skill 时应同步映射。HTML 交互规范尚未实现为后端渲染器。

## 扩展前提

API_TOKEN 是整体访问控制，无逐用户授权。多 worker/副本需数据库锁或队列；当前不可直接横向扩容。检查点历史、旧文档版本无自动清理。大规模精确扫描与检查点历史扫描可能变慢。

模型协议测试和数据库测试不等于模型质量评测。证明/计算题还需要学科校验，不把模型复核当成数学保证。后续扩展优先由真实失败样本决定。
