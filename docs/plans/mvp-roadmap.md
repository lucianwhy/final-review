# MVP 路线图（以 PRD 为基线）

> 规划日期：2026-09-24。本文只描述建议，不改变产品实现。判定基线为
> `docs/product-requirements.md`；“已完成”表示当前代码已覆盖该需求的完整
> MVP 语义，而不是仅有名称相近的接口或页面。

## 0. 当前基线

当前仓库已经是一个有认证、课程隔离、RAG 入库、带引用问答、快速测验、评分和
基础报告的单进程后端，并有 React 工作台。它不是 PRD 所定义的完整学习资产平台：
没有考试对象、笔记/试卷正式资产、确认工作流、错题对象、计划/提醒、可用的
导出与删除确认链路。因此后续工作应在保留既有 Agent/RAG 能力的前提下，先建立
业务域模型和资产状态机，而不是继续在 `fast_quiz_session` 上叠加页面功能。

## 1. PRD 覆盖矩阵

证据中的路径均为当前实现；“部分完成”只列出已覆盖的子集和缺口。

| PRD | 状态 | 当前依据与缺口 |
| --- | --- | --- |
| A-01 | 部分完成 | `auth.py`、`/api/auth/sign-up`、`sign-in` 提供邮箱密码注册/登录；没有邮箱验证码登录。 |
| A-02 | 已完成 | `api.py` 的 `require_course`、认证中间件和 `PostgresStore` 均按会话 `user_id` 约束资源；`test_auth_and_missing_session` 覆盖认证边界。 |
| A-03 | 部分完成 | 有 `/api/auth/sign-out`；没有修改密码、账户设置页面或接口。 |
| A-04 | 部分完成 | Course、Document、Conversation、Attempt 已持久化到 PostgreSQL；不存在笔记、正式试卷、报告资产的长期保存和用户删除流程。 |
| C-01 | 部分完成 | 有课程创建和列表；没有编辑、归档、删除、影响范围展示或二次确认。 |
| C-02 | 未完成 | 没有 `Exam` 数据模型、迁移、API 或 UI。 |
| C-03 | 部分完成 | `ExamProfile` 仅有题型、重点、排除范围，且附着于 Agent checkpoint；缺日期、分值/满分、备注、出题偏好及持久课程考试关联。 |
| C-04 | 未完成 | `/report` 只汇总题数、平均分和知识点分数，未建分值结构、目标达成度、置信度或预计分数门控。 |
| C-05 | 未完成 | 无 Plan、提醒、邮件投递或临考节奏。 |
| M-01 | 部分完成 | `/knowledge/upload` 接受 md/txt/pdf/pptx/docx；明确拒绝 DOC 和图片，故不满足四类及常见图片格式。 |
| M-02 | 部分完成 | `SourceType` 支持真题/PPT/作业/速成课/AI 补充；没有“其他练习”，UI 也无该选项。 |
| M-03 | 部分完成 | 已有 MarkItDown、清洗、去重、切块、Embedding、索引和 parsing/ready/failed 状态；上传请求同步执行，无 OCR、后台任务/重试和处理中可观察的异步生命周期。 |
| M-04 | 部分完成 | 可列出、按上传时填写章节/来源显示、删除单份资料；不能重命名、筛选/归类，删除无影响分析与二次确认。 |
| M-05 | 部分完成 | 文件上传可承载 PDF/PPTX/DOCX/Markdown；学习通截图（图片）目前被拒绝。 |
| M-06 | 部分完成 | Evidence 有 document/chunk 引用，生成题验证 `source_chunk_ids`，失败文件标 `failed` 且不入库；UI 未展示逐题来源、文件名/类型/片段跳转，且没有真正页码/片段查看器。 |
| AG-01 | 部分完成 | 对话仅文本；资料上传在资料中心，不能从聊天窗口直接上传并进入处理流程。 |
| AG-02 | 部分完成 | Agent 支持资料问答和测验；不支持笔记生成、错题解释、报告分析、计划创建等任务。 |
| AG-03 | 部分完成 | 出题缺 `ExamProfile` 时用 LangGraph interrupt 追问；没有按任务收集范围、数量、时长、难度、输出形式等最少边界。快速测验路径还会直接生成。 |
| AG-04 | 未完成 | 聊天保存文本与 citations，没有笔记预览、草稿编辑、开始测试、打开报告、导出等操作卡片。 |
| AG-05 | 部分完成 | Agent 可进入测验流程；无报告创建，删除资料直接执行、没有影响说明/确认，亦无覆盖正式资产控制。 |
| AG-06 | 部分完成 | Conversation/Message 持久化，Agent checkpoint 保存任务状态；无任务历史/快捷入口与“聊天记录 vs 已确认资产”分层。 |
| N-01 | 未完成 | 没有 Note 模型、笔记生成或四类笔记内容。 |
| N-02 | 未完成 | 无笔记生成配置。 |
| N-03 | 未完成 | 无草稿笔记、编辑、引用编辑或确认归档。 |
| N-04 | 未完成 | `policy.py` 的领域规则可约束题目/解析，但没有笔记资产可验证其内容结构。 |
| N-05 | 未完成 | 仅 `GET /agent/export` 输出 Markdown 测验结果；无正式笔记的 Markdown/Word/PDF/打印导出。 |
| Q-01 | 部分完成 | `QuestionType` 及 Agent 覆盖选择、判断、填空、简答、计算、证明；快速测验 UI 默认仅选择/简答，且没有“基础”难度语义。 |
| Q-02 | 部分完成 | 快速测验可给章节、题型、数量；Agent `ExamProfile` 有题型/重点/排除项。缺考试关联、知识点、时长、难度、是否纳入导入题、完整侧重点配置。 |
| Q-03 | 部分完成 | RAG 有题源优先重排，Agent 有每知识点题量/题型规则；试题未在 UI 显示轻量来源标记，也未以可审计方式保存权重分配依据。 |
| Q-04 | 部分完成 | `Question`/快速试题含题干、类型、知识点、答案、解析和 `source_chunk_ids`；用户作答前不返回答案属正确防泄漏设计，但缺正式试卷持久化、分值和可视来源。 |
| Q-05 | 未完成 | 生成的 fast quiz/Agent session 不是可编辑、可确认的 `Quiz` 草稿，不能编辑题干、答案、解析、分值和顺序。 |
| Q-06 | 未完成 | `render_markdown` 可将 Agent 题目与答案分区，但没有已确认试卷的两种打印版、UI 操作或文件导出。 |
| T-01 | 部分完成 | 前端可生成、作答、提交并查看 Attempt 历史；没有计时器、自动/手动保存作答、只能从已确认试卷开始。 |
| T-02 | 部分完成 | 评分模型处理所有题型，并保存结果；没有明确的、无需模型的客观题自动判分实现。 |
| T-03 | 部分完成 | Grade 有反馈/遗漏点，主观题可文字作答；没有固定展示“仅供学习参考，以教师评分为准”，也未保证参考评分、得分点、缺失点和建议全部出现。 |
| T-04 | 部分完成 | 没有手写/公式图片批改；但“题目图片可作为资料和题干上传”未实现。 |
| T-05 | 部分完成 | AttemptDetail 显示逐题答案、得分、解析、知识点；没有来源展示及针对性追问入口。 |
| W-01 | 未完成 | Attempt 中有 weak_points，但未生成含原题、学生答案、正确答案、解析、错因、知识点的 Mistake。 |
| W-02 | 未完成 | 无错题列表、筛选、重做、掌握状态。 |
| W-03 | 未完成 | 无基于错题状态的间隔提醒。 |
| R-01 | 部分完成 | 报告有累计练习、平均分/正确率近似值、知识点分数；缺学习时长、题型能力、掌握度、目标达成度、置信度、完整建议。 |
| R-02 | 未完成 | 没有考试分值结构和预计分数逻辑，因而不能满足“有结构才估算”的规则。 |
| R-03 | 未完成 | 仅静态报告读取；没有 Agent 解读、一键小测或计划转换。 |

### 横向验收标准核对

PRD 第 9 节 10 条验收中，当前仅第 2 条的“已解析资料可作为 Agent/生成依据”
和第 10 条的数据隔离接近完成；第 1、3--9 条均缺少至少一个关键环节。尤其是
“导出”“确认归档”“错题筛选”“六类指标”“来源可查看”“破坏性操作确认”不能
由已有 API 名称或静态原型推定完成。

## 2. 冲突、待决策与技术风险

### 需求/现状冲突

1. PRD 规定“先确认后归档”，但当前快速测验生成后立即作为可作答 session，且
   没有草稿/确认状态；Agent 测验同样直接进入等待作答。需要以资产状态机取代
   临时 session 的产品语义。
2. PRD 要求已确认资产可继续编辑、覆盖时提示差异并确认；同时又要求正式资产
   可审计。必须定义“就地修订 + 版本”还是“新版本替换”，否则无法同时保证复用
   稳定性和历史 Attempt 可重现。
3. PRD 要求资料/题目图片可上传，但 MVP 非目标排除了手写批改和复杂公式图片批改。
   两者并不矛盾，但要明确图片 OCR 只用于可检索文字与题干展示，不能承诺答案识别。
4. AG-03 要先澄清关键边界，当前快速测验的“默认 5 题”绕过了时长、难度、范围等
   信息。需规定快速入口的默认值是否已构成用户确认，以及哪些字段可留空。
5. `AGENT.md` 的“每题可追溯”要求比 Skill 的轻量标记更严格，与 PRD 一致；现有
   数据仅保存 chunk ID，页面无法还原可见的来源链路，不能将内部 ID 视为用户可追溯。

### 已确认的产品决策

下列决策已于 2026-09-24 确认，权威记录见
[`docs/decisions/0001-mvp-defaults.md`](../decisions/0001-mvp-defaults.md)。M0 不再重新选择，
只负责把这些决策固化为迁移、API 契约、状态机和测试。

| 决策 | 已确认规则 | 理由 |
| --- | --- | --- |
| 课程删除 | 软删除 30 天；删除前展示资料、考试、学习资产、作答记录数量；二次确认后进入回收期。 | 可控删除，且给用户纠错窗口。 |
| 正式资产版本 | 已确认笔记/试卷的编辑创建不可变新版本；Attempt 固定关联当时试卷版本。 | 支持审计和历史结果复现。 |
| 被引用资料删除 | 默认阻止；用户显式选择“保留来源快照后删除”才可继续。 | 正式资产的来源链路不会被悄然破坏。 |
| 报告与预计分数 | 按课程、可选考试、日期范围生成；没有完整分值结构时不显示预计分数。 | 使报告范围可解释，避免伪精确。 |
| 主观题评分 | 仅供学习参考；保存评分依据、模型版本和反馈快照。 | 可追溯且不将 AI 评分冒充正式成绩。 |
| 导出 | Markdown 为统一内容源；Word、PDF、打印版均从已确认版本派生。 | 防止不同格式的内容发生漂移。 |
| 提醒 | MVP 只做站内提醒；邮件提醒为后续独立里程碑，PRD 已同步。 | 邮件投递、频控和偏好管理是独立可靠性/合规范围。 |

### 技术风险

- **同步长任务**：上传转换/Embedding 和生成/导出目前在请求中同步执行；大文件会超时，
  “处理中”无法真正持续可见。需 job queue、幂等键、状态机、错误原因和重试上限。
- **文件与来源保真**：当前只存 Markdown、文件路径和 chunk；页码/幻灯片/图片区域没有稳定
  anchor。导入管道应输出 `SourceLocator`，否则“跳转原片段”只能是空 UI。
- **资料删除竞态**：`delete_document` 先删文件再删索引，且没有确认/引用检查；异步化后要
  用事务/outbox 与对象存储延迟清理避免半删除。
- **多租户授权扩面**：现有 store 有用户绑定，新增每个资源、下载、导出、后台 job 都必须
  后端校验 owner；不能依赖前端 course_id。
- **评分正确性与成本**：客观题使用模型评分会不稳定且付费；应先确定性评分，主观题才调用
  模型并缓存输入/版本。数学证明仍应标为参考性。
- **历史兼容性**：现有 `fast_quiz_session`、checkpoint 和 Attempt JSON 无正式 quiz revision。
  迁移必须为旧记录保留 legacy 读取路径或一次性 backfill。
- **可观测性/测试缺口**：现有 pytest 覆盖 Agent、API、RAG 与 PostgreSQL seam，但没有前端
  自动化测试、异步 job 测试、导出视觉回归、跨用户下载测试或模型质量集成门禁。

## 3. 推荐目标架构与数据模型

### 架构边界

保留 `FinalReviewAgent` 作为“课程上下文任务编排器”，但将持久业务对象从 Agent
checkpoint 中拆出。建议按下图分层：

```text
React Web / mobile browser
  └─ BFF/API：认证、资源授权、命令、查询、SSE/轮询 job 状态
       ├─ Course domain：Course、Exam、Plan、Reminder
       ├─ Material pipeline：Upload → Parse/OCR → Clean/Chunk/Index → SourceLocator
       ├─ Learning assets：Note/Quiz 草稿 → Review/Edit → Confirm → Revision/Export
       ├─ Assessment：Attempt → deterministic objective grade + AI subjective feedback
       │                  → Mistake / Mastery event → Report projection
       ├─ Agent task facade：Task、澄清、结果卡片链接（调用上述 domain command）
       └─ PostgreSQL + pgvector / object storage / worker queue
```

关键原则：

- Agent 只能发起经过权限和状态转换验证的 domain command，不能直接覆盖正式资产或删除资料。
- 资料解析、Embedding、生成、导出和提醒由 worker 执行；API 立即返回 Job/Task，UI 展示状态。
- 资料文本、每个知识点、题目和关键结论都用 `SourceReference` 指向不可变的资料版本和
  `SourceLocator`；AI 补充是显式来源类型，不得借用资料引用。
- Attempt 永远引用 `quiz_revision_id` 与每题 revision snapshot，确保后来编辑试卷也不改变
  历史成绩与反馈。

### 模型差异

| PRD 对象 | 当前状态 | 目标模型（关键字段） |
| --- | --- | --- |
| User | `app_users` + session 已有 | 增加 `reminder_preferences`、密码更新/验证码挑战审计。 |
| Course | 仅 name、时间和 user | `status(active/archived/deleted)`、subject、deleted_at；关联所有下列对象。 |
| Exam | 不存在 | `id, course_id, name, exam_at, question_blueprint, total_score, emphasis, exclusions, notes, generation_preferences, status`。 |
| Material | Document JSON + chunk | `Material`（文件元数据、source_type、chapter、version、parse_status、error）+ `MaterialVersion` + `SourceLocator(page/slide/region/text_range)`；支持 other_practice 与图片。 |
| SourceReference | Evidence 临时输出/题目 chunk IDs | 独立多对多表：`asset_revision_id, material_version_id, locator_id, claim_kind, confidence, attribution(ai_supplement/composite)`。 |
| Note | 不存在 | `LearningAsset` 基表 + `NoteRevision(type, content, citations, editor, generated_by_task)`，草稿/确认/归档状态。 |
| Quiz/Question | Agent state 或 `fast_quiz_session` JSON | `QuizRevision(exam_id, config, duration, status)`、`QuestionRevision(type, stem, options, answer, rubric, score, order, knowledge_point)`、来源集合。 |
| Attempt | JSON Attempt 已有 | `Attempt(quiz_revision_id, status, started_at, submitted_at, elapsed)` + `AttemptAnswer` + `QuestionGrade`；保留 legacy 映射。 |
| Mistake/Mastery | weak_points 字符串 | `Mistake(attempt_answer_id, reason, mastery_status, next_review_at)` + `MasteryObservation`，可筛选、重做。 |
| Report | 实时聚合 API | 可重建 `ReportSnapshot`（window、exam、metrics、confidence、method_version、suggestions），不存伪精确分数。 |
| AgentSession/Task | Conversation + checkpoint | `AgentSession`、`AgentMessage`、`AgentTask(intent, input, clarification, status, result_links, job_id)`；与正式资产只用链接关联。 |

## 4. 依赖排序的开发里程碑

### M0：领域合同、迁移和安全护栏

**范围**：确定第 2 节的产品决策；为 Course 生命周期、Exam、MaterialVersion、
SourceReference、LearningAsset/Revision、QuizRevision 设计迁移和后端授权策略；定义
状态转换、幂等命令、审计事件、旧 `fast_quiz_session` 兼容/回填方案；补齐删除影响分析 API
契约。

**非范围**：不交付笔记/试卷编辑器、报告 UI、邮件发送或新的模型能力。

**验收标准**：所有对象有 owner/course 外键和迁移；草稿/确认/归档/删除转换有合法性测试；
跨用户读取/导出/删除被拒绝；每种不可逆操作都先返回影响摘要和 confirmation token。

**测试命令**：`uv run pytest -q`、`uv run ruff check src tests`、`uv run ruff format --check src tests`。

**手动验收**：用两个账户创建同名课程；确认第二个账户无法读取第一个账户的资料、考试、
草稿和确认 token；尝试删除被引用资料，确认只能看到影响说明、不能直接删除。

### M1：课程、考试画像与资料管道产品化

**依赖**：M0。**范围**：课程编辑/归档/删除；多考试 CRUD 和完整画像；资料上传支持
DOC/常见图片、来源“其他练习”、重命名/章节来源筛选；异步 Parse/OCR/Index Job、失败原因、
安全重试；持久 `SourceLocator` 和资料预览/片段链接。

**非范围**：不生成正式笔记/试卷，不做手写答案批改，不做提醒投递。

**验收标准**：一个课程可管理两场考试；PDF/PPTX/DOCX/图片至少各一种进入可检索或明确失败；
失败文件不能进入检索；资料页面显示 job 状态、来源、章节、片段；删除前展示引用影响。

**测试命令**：`uv run pytest -q tests/test_api.py tests/test_rag.py tests/test_postgres.py`；
`npm run build`（在 `frontend`）。

**手动验收**：创建“期中/期末”两个考试，上传 PPT 与作业截图，刷新页面直到完成；从一个
检索结果打开对应文件/页或文本片段；取消和确认一次资料删除，检查两条路径都正确。

### M2：来源可追溯的笔记草稿到确认闭环

**依赖**：M1。**范围**：四类笔记生成配置与 Agent 澄清；Note 草稿、编辑、引用编辑、
确认归档、revision/audit；来源卡片与 AI 补充/综合改编标记；Markdown、DOCX、PDF、打印
导出。

**非范围**：不做完整试卷编辑和在线测验；不做错题提醒。

**验收标准**：缺范围/时长/来源等必要条件时 Agent 追问；笔记必须是草稿，编辑后确认才出现在
正式资产；每个考点可见文件名、来源类型与可用片段；导出的 Markdown/Word/PDF 内容与已确认
revision 一致，AI 补充不能显示为教师资料。

**测试命令**：`uv run pytest -q`；`npm run build`；导出服务的专用单测（生成后用
`python -m pytest tests/test_exports.py -q`，实现后加入）。

**手动验收**：在已解析 PPT 上生成“第三章 10 分钟考点清单”，编辑一个标题和来源引用，确认；
分别下载三种格式并打印预览；点击两个来源确认跳至正确资料位置。

### M3：可确认的试卷、导出与在线测试

**依赖**：M1、M2 的资产/revision 框架。**范围**：完整试卷配置（考试、章节/知识点、题型、
数量、时长、难度、重点、导入题开关）；题目/分值/顺序编辑与确认；题目来源/轻量标记；两种
打印版；从确认 revision 启动带计时、保存、交卷的 Attempt；客观题确定性评分，主观题参考性
AI 评分和固定免责声明。

**非范围**：不做图片答案批改、复杂公式 OCR 批改、错题计划或预测分数。

**验收标准**：草稿试卷不可开始正式测试，确认后可；历史 Attempt 引用的题目不会因后续编辑
改变；客观题离线可重复判同分；主观题逐题显示参考答案、得分点、缺失点、建议和免责声明；
“仅题目/含答案解析”导出均题目在前。

**测试命令**：`uv run pytest -q tests/test_agent.py tests/test_api.py tests/test_rendering.py`；
`npm run build`；增加 Playwright/Cypress 后执行其 e2e 命令。

**手动验收**：生成 5 题混合试卷，调整一题分值和顺序，确认，开始 5 分钟测试，中途刷新恢复
作答，交卷；确认解析和来源可见；重新编辑试卷后回看第一次 Attempt，确认内容未漂移。

### M4：错题、报告、计划与 Agent 操作卡

**依赖**：M3。**范围**：Mistake/Mastery、筛选/重做/掌握标记、间隔复习计划；六类报告指标、
置信度、条件式预计分数；报告转小测/计划；Agent Task 及可操作结果卡片；站内提醒。

**非范围**：不做原生 App、微信/短信、教师/班级协作、手写批改。

**验收标准**：错题自动入集且可按 PRD 六种维度筛选；重做/掌握状态改变下次复习时间；报告无
分值结构时不显示预计分，有完整结构时显示估算依据与置信度；一键小测/计划创建可回到相应资产；
删除/覆盖操作在 Agent 卡片中仍需确认。

**测试命令**：`uv run pytest -q`、`npm run build`、前端 e2e 全量命令。邮件提醒不属于本里程碑；
后续邮件里程碑应使用 sandbox provider 的集成测试，禁止向真实地址发送测试邮件。

**手动验收**：完成一场混合试卷并故意答错两题，进入错题集按章节和题型筛选，标记/重做一题；
打开报告检查六类指标和置信度；填写考试分值结构后复查预计分数；从报告创建小测和次日计划。

### M5：上线质量门槛与迁移演练

**依赖**：M0--M4。**范围**：历史数据迁移、备份/恢复、任务可观测性、错误告警、成本指标、
权限与下载安全审计、移动浏览器关键流程、真实课程样本质量评测。

**非范围**：新的终端、协作功能和 V1.1 OCR/手写批改承诺。

**验收标准**：从当前数据库迁移可回滚/可验证；失败 job 可安全重试且不生成半正式资产；关键
MVP 验收路径在桌面与移动浏览器完成；跨账户、下载授权、删除确认和来源标注有自动回归；
真实样本评测不以模型单测代替。

**测试命令**：`uv run pytest -q`、`uv run ruff check src tests examples eval`、
`uv run ruff format --check src tests examples eval`、`npm run build`、前端 e2e 全量、迁移
smoke test、`uv run python eval/run.py`（仅使用批准的脱敏评测集）。

**手动验收**：按 PRD 第 9 节的 10 条逐项演示并留存结果；模拟解析失败、模型失败、job 重试、
会话过期和无权限下载；在手机浏览器完成上传、对话、基础作答。

## 5. 建议先做的第一个端到端用户切片

选择 **“已登录学生：创建课程和考试 → 上传一份老师 PPT → 生成并确认一份带来源的章节
考点清单 → 导出打印”**。

它优先于“直接做测验”，因为它一次验证 MVP 的资料依据、来源追溯、考试画像、Agent 澄清、
草稿确认、正式资产、导出和删除保护等不可替代的产品骨架；这些也是后续试卷、错题和报告共享
的数据合同。具体完成定义：

1. 学生创建课程与一场含日期、范围/重点的考试，上传 PPT，并看到可用的解析状态与来源定位；
2. 对话请求“生成第三章、10 分钟、偏定义与易错点的考点清单”，缺失字段时得到最少追问；
3. 生成内容以草稿卡片打开，学生可改标题/正文/来源，确认后成为正式 Note revision；
4. 每个考点能看到文件名、`teacher_ppt` 和对应片段；无证据内容显式标为 AI 补充；
5. 可下载 Markdown、DOCX、PDF 和打印版；删除该 PPT 时系统展示正式笔记受影响并要求二次确认。

完成这个切片后再实施 M3，可将“试卷”复用相同的 revision、来源、确认、导出和权限机制，
避免重复建设两套不兼容的资产流程。
