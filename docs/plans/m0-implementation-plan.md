# M0 可执行实施计划：领域合同、迁移与安全护栏

> 目标：把 ADR-0001 已确认的规则落实为后端领域合同与可验证的数据库基础；不在 M0
> 交付笔记/试卷编辑器、Agent 新能力、报告 UI、导出格式实现、通用异步 worker 或邮件提醒。
> 为兑现 30 天回收期，M0 只定义并实现可幂等调用的受限清理命令/调度入口；它不承担资料
> 解析、生成或导出等通用后台任务。本计划是实施说明，不代表本次已实现任何业务代码。

## 1. 完成边界与顺序

按以下顺序提交，后一项依赖前一项。每一项应有独立迁移、回滚说明和测试，不把 schema
变更与行为切换混在一次不可诊断的发布中。

1. 固化枚举、资源身份、所有权和审计字段；建立新表，不修改旧 JSON 读路径。
2. 增加软删除、版本、来源快照和确认令牌的 repository/service 合同。
3. 发布只读影响预览与确认执行 API，接入后端授权和状态转换验证。
4. 添加双写/兼容读取并 backfill 可安全识别的旧数据；保留 `fast_quiz_session` legacy 路径。
5. 增加只清理 `purge_after <= now()` 课程的幂等回收入口，记录审计事件；不接入通用队列。
6. 打开新写路径的 feature flag，运行迁移验证、权限回归和恢复演练后再弃用旧写路径。

## 2. 数据库迁移

### 2.1 迁移文件与依赖

在 `db/migrations/` 按时间序号新增以下迁移；每个 PostgreSQL DDL 均在事务内执行，
索引若需要 `CONCURRENTLY` 则拆为无事务迁移并记录原因。

| 迁移 | 新增/变更 | 关键约束与索引 | 回滚/兼容 |
| --- | --- | --- | --- |
| `002_m0_course_lifecycle.sql` | `courses` 增加 `status, archived_at, deleted_at, purge_after, deletion_confirmed_at`；新增 `course_deletion_audit`。 | `status IN (active, archived, deleted)`；`purge_after` 只允许 deleted；`(user_id,status,updated_at)` 索引。 | 新列可空/有默认 active；回滚仅删除索引/列，先确认无新数据依赖。 |
| `003_m0_exams.sql` | `exams`、`exam_blueprint_items`。 | `course_id`、`user_id`、软删除状态；分值非负；蓝图项 `(exam_id, question_type)` 唯一；所有 Exam 查询必须含 owner。 | 不回填，因为旧 `ExamProfile` 是 checkpoint 输入而非可验证的课程考试。 |
| `004_m0_assets_and_revisions.sql` | `learning_assets`、`asset_revisions`、`note_revision_payloads`、`quiz_revision_payloads`、`question_revisions`。 | Asset `(user_id,course_id,type,status)`；revision `(asset_id,revision_no)` 唯一且 append-only；`current_revision_id` 指向同 Asset revision；DB trigger 或 service guard 阻止 confirmed revision 更新。 | 旧 `fast_quiz_session` 不迁入正式 Asset；保留只读 legacy。 |
| `005_m0_material_versions_and_sources.sql` | `material_versions`、`source_locators`、`source_references`、`source_reference_snapshots`。 | Locator 只能属于对应 MaterialVersion；SourceReference 只能指向 AssetRevision；snapshot 只在资料删除例外路径创建；`(material_version_id, locator_kind, ordinal)` 唯一。 | 当前 `documents`/`document_chunks` 保留；将新版本表映射到它们，待 M1 才切换上传管道。 |
| `006_m0_attempt_revision_link.sql` | `attempts` 增加 nullable `quiz_revision_id, legacy_session_id, grading_snapshot, model_version, grading_disclaimer_version`；新 `attempt_answers`、`question_grades`（可空，供 M3 写入）。 | 约束：恰有其一 `quiz_revision_id` / `legacy_session_id`；Attempt 不能引用 draft/archived quiz revision；`(user_id,course_id,quiz_revision_id)` 索引。 | 存量 Attempt 全设 `legacy_session_id=session_id`；不伪造 quiz revision。 |
| `007_m0_confirmation_and_audit.sql` | `confirmation_requests`、`audit_events`。 | confirmation 绑定 `user_id, action, resource_type, resource_id, payload_hash, expires_at, consumed_at`；哈希唯一防重放；audit 是 append-only。 | 过期 token 可清理；审计记录不在常规回滚中删除。 |

### 2.2 表级最小字段合同

- 所有新业务表：`id UUID/ULID`、`user_id`、`course_id`（适用时）、`created_at`、`updated_at`、
  `created_by`；FK 均指向真实 owner 资源。**应用层必须同时按 `id + user_id` 读取，不能只依赖**
  **前端传来的 course ID。**
- `exams`：`name, exam_at, total_score nullable, emphasis jsonb, exclusions jsonb, notes,
  generation_preferences jsonb, status`。完整分值结构的判定为：`total_score IS NOT NULL`、至少
  一个 blueprint item，且各 item 分值/题数完整、分值求和等于 `total_score`。
- `learning_assets`：`type(note|quiz)`、`status(draft|confirmed|archived|deleted)`、
  `current_revision_id nullable`；**草稿可有 revision，确认后必须有 current revision**。
- `asset_revisions`：`revision_no`、`state(draft|confirmed|superseded|archived)`、
  `markdown_content`、`content_hash`、`editor_type(student|agent|system)`、`based_on_revision_id`、
  `confirmed_at`。**确认后不可 UPDATE/DELETE；编辑从新 revision INSERT 开始。**
- `source_reference_snapshots`：`display_file_name, source_type, locator_json, excerpt,
  material_content_hash, captured_at`；**其内容足以在原资料不可访问后向用户解释引用，但不重复**
  **保存整份受版权约束的文件。**
- `confirmation_requests`：以 `action` 区分 `course.delete`、`material.delete`、
  `asset.overwrite`；payload hash 必须包括资源版本与影响计数，防止预览后资源变化仍执行旧确认。

### 2.3 迁移安全检查

1. 在副本数据库先执行 `pg_dump --schema-only`、迁移、`pg_dump --schema-only` 比对。
2. 对存量 `courses/documents/attempts` 跑只读计数：总数、每 user/course 分布、无 owner 行、
   无 document chunk 行；迁移后计数必须一致。
3. 用一份包含 active/archived/deleted course、draft/confirmed revision、legacy/new Attempt 的
   fixture 执行升级与降级演练。
4. 新外键和 check constraint 先以 `NOT VALID` 添加、清理/验证存量后 `VALIDATE CONSTRAINT`，
   避免生产长锁；所有例外在迁移说明中记录。

## 3. API 契约

所有写命令都要求有效 session，服务端从 session 获取 `user_id`。响应不泄露其它账户资源存在性：
读取/写入无权资源统一 `404`；无效状态/过期确认令牌为 `409`；字段错误为 `422`。

### 3.1 课程与考试

| API | 请求/响应要点 | 状态与授权 |
| --- | --- | --- |
| `PATCH /api/courses/{course_id}` | 名称、学科、`expected_updated_at`；返回 Course。 | 仅 active/archived owner；乐观并发失败 `409`。 |
| `POST /api/courses/{course_id}/archive` / `restore` | 可选 reason；返回新 status。 | owner；deleted 不可直接 archive。 |
| `POST /api/courses/{course_id}/deletion-preview` | 无 body；返回 `{impact, confirmation_id, expires_at}`。 | owner；只读，不改变 Course。 |
| `POST /api/courses/{course_id}/delete` | `{confirmation_id}`。 | token 与 owner/action/resource/payload hash 一致且未过期/未消费；写 deleted + `purge_after=+30d` + audit。 |
| `POST /api/courses/{course_id}/restore` | 无 body。 | owner；仅 deleted 且未到 purge_after。 |
| `GET/POST /api/courses/{course_id}/exams` | POST 完整 Exam 输入；GET 默认排除 deleted。 | course owner。 |
| `GET/PATCH /api/exams/{exam_id}`、`POST /archive` | PATCH 使用 `expected_updated_at`；blueprint 整体替换。 | Exam owner 通过 course 反查；不允许跨 course 绑定。 |

`impact` 固定为 `{materials, exams, learning_assets, attempts}` 四个整数和 `calculated_at`；
不返回资源标题，避免将大规模枚举混进破坏操作。实际 delete 再计算一次计数，若 hash 改变返回
`409 deletion_preview_stale`，要求重新预览。

内部受限命令 `purge_expired_courses(now, batch_size)` 只处理 `status=deleted AND purge_after <= now`，
必须可重复运行、逐课程事务化并写入 `course.purged` 审计事件。它不得接受来自浏览器的 course
ID 或 user ID；调度身份和批大小通过部署配置控制。

### 3.2 资产、版本、资料删除预览

| API | 请求/响应要点 | 状态与授权 |
| --- | --- | --- |
| `POST /api/courses/{course_id}/assets` | `{type, title, initial_markdown, source_references}`；返回 draft Asset + revision。 | owner；source references 必须是同 course 可用资料版本。 |
| `POST /api/assets/{asset_id}/revisions` | `{base_revision_id, title, markdown, source_references}`；返回新 draft revision。 | owner；base 必须为 current confirmed 或本人可写 draft；不原地 PATCH confirmed revision。 |
| `POST /api/assets/{asset_id}/revisions/{revision_id}/confirm` | `{expected_asset_updated_at}`；返回 confirmed revision/current revision。 | owner；引用完整性、Markdown hash、状态转换均通过才确认；失败 `409/422`。 |
| `POST /api/assets/{asset_id}/archive` | `{expected_asset_updated_at}`。 | owner；confirmed → archived；已有 Attempt 可继续只读。 |
| `POST /api/materials/{material_id}/deletion-preview` | 无 body；返回 `{blocking_references, retain_snapshot_allowed, confirmation_id}`。 | owner；统计 confirmed revision 引用。 |
| `POST /api/materials/{material_id}/delete` | `{confirmation_id, mode: "block" | "retain_source_snapshot"}`。 | `block` 在有引用时 `409`; `retain_source_snapshot` 必须有预览同意的 token，服务端事务创建 snapshots 后软删除。 |

M0 只提供合同和服务层；笔记/试卷编辑 UI、生成入口与下载端点由后续里程碑接入。

### 3.3 报告、评分与导出预留契约

- `GET /api/courses/{course_id}/report?exam_id=&from=&to=` 的返回模型先定义
  `scope, metrics, confidence, projected_score: null | {value, basis}`。当 Exam 无完整蓝图时，
  必须返回 `projected_score: null` 和 `projection_unavailable_reason: incomplete_score_blueprint`，
  不返回 `0` 或估算文字。
- `QuestionGrade` 预留 `grading_basis, model_provider, model_name, model_version,
  prompt_version, feedback_snapshot, disclaimer_version, graded_at`；M3 写入。M0 的 API schema
  和历史 Attempt 读取可返回 null，不制造不存在的评分证据。
- 未来导出端点只接受 `revision_id`，并先校验 revision 为 confirmed；响应应包含
  `content_hash` 与 `revision_no`。禁止以 Asset 当前版本替代请求的历史 revision。

## 4. 状态机与不变量

### 4.1 Course

```text
active ──archive──> archived ──restore──> active
  │                      │
  └──confirmed delete────┴──────────────> deleted ──restore (<30 days)──> active/archived
                                               └──purge job (>=30 days)──> purged
```

- `deletion-preview` 不改变状态；`delete` 必须消费一个未过期 token。
- `deleted/purged` 课程不接受资料、考试、资产、Agent、Attempt 或导出写入；purged 不可恢复。
- 课程删除是级联逻辑删除，不能物理级联删除有审计/历史 Attempt 的记录。

### 4.2 Asset 与 revision

```text
Asset: draft ──confirm(revision)──> confirmed ──archive──> archived ──restore──> confirmed
Revision: draft ──confirm──> confirmed ──new edit──> superseded (old) + draft (new)
```

- confirmed revision 的内容、来源、hash 和确认时间不可变；“编辑正式资产”是以它为
  `based_on_revision_id` 的新 draft。
- 若确认新 revision，旧 current confirmed revision 变 `superseded` 但保留可读；Attempt 永远
  指向它启动时的 revision，不自动重绑。
- confirmed/archived Asset 的删除或覆盖必须走确认令牌；draft 可丢弃但仍记 audit。

### 4.3 Material 与来源引用

```text
ready material + no confirmed references ──confirmed delete──> deleted
ready material + confirmed references ──default──> blocked
ready material + confirmed references ──retain_source_snapshot + confirmation──> deleted
```

- “资料被引用”只以 confirmed/archived asset revision 为准；draft 引用可随草稿丢弃，不阻塞。
- snapshot 创建与 Material 删除必须同一数据库事务；任一步失败则二者都不生效。
- 删除后不再作为 RAG 可信题源，也不可打开原文件；正式 asset 显示 frozen snapshot 和
  “原资料已按用户请求删除”的说明。

## 5. 权限与状态转换测试

在现有 pytest 之外新增 fixture：两个用户、两个课程、一个 confirmed Asset、两个 revisions、
一份被引用/未引用资料、legacy/new Attempt、有效/过期/已消费 confirmation token。最低测试集：

| 类别 | 必测案例 |
| --- | --- |
| 多租户 | 用户 B 对用户 A 的 course/exam/asset/revision/material/attempt/preview/delete/export 返回 404；伪造 body 的 `user_id` 无效。 |
| 课程删除 | preview 四类计数准确；首次 delete 成功、同 token 重放 409；预览后新增资料使 token 失效；30 天内可 restore，清理后不可 restore。 |
| 版本不可变 | confirmed revision PATCH/DELETE 被拒绝；新编辑产生递增 revision；Attempt 仍读取创建时 quiz revision；不能确认跨 Asset revision。 |
| 资料来源 | 未引用资料经确认可删除；被 confirmed asset 引用的默认 delete 409；snapshot 模式保留显示所需字段、移除检索资格、且 audit 完整。 |
| 考试/报告门控 | Exam 不能跨课程；不完整蓝图报告无预计分并带原因；完整蓝图才允许 projection 字段非空。 |
| 评分审计 | subjective Grade schema 缺免责声明/模型版本/反馈快照时验证失败；历史评分读取仍返回当时 snapshot。 |
| 并发与幂等 | 两次 confirm/delete 请求只有一次成功；`expected_updated_at` 过期为 409；同一 idempotency key 返回同一成功结果。 |
| 迁移兼容 | 升级前 documents/attempts 可读；旧 Attempt 被标 legacy 而非关联伪造 revision；新旧报告聚合不重复计数。 |

建议命令（实现后）：

```powershell
uv run pytest -q tests/test_api.py tests/test_postgres.py tests/test_m0_lifecycle.py tests/test_m0_permissions.py
uv run ruff check src tests
uv run ruff format --check src tests
```

数据库集成测试必须使用临时 PostgreSQL 数据库并应用完整 migration 链，而不是只使用内存
adapter；确认令牌消费、FK、事务原子性和所有权谓词都依赖真实数据库行为。

## 6. 旧数据兼容与发布策略

### 6.1 分类与映射

| 现有数据 | M0 处理 | 禁止事项 |
| --- | --- | --- |
| `courses` JSON 记录 | 加 lifecycle 列；存量设 `active`。 | 不按名称合并课程。 |
| `documents` / `document_chunks` | 保持原表及检索；为 ready Document 创建可选 MaterialVersion 映射或 lazy-on-read 映射。 | 不重新 Embedding、不修改 chunk ID。 |
| `fast_quiz_sessions` | 作为 legacy session 只读；保留原题 snapshot。 | 不将其伪装为 confirmed Quiz。 |
| `attempts` | 设置 `legacy_session_id`，`quiz_revision_id = NULL`；报告可纳入正确率但不能声称版本化来源。 | 不合成试卷 revision 或替换历史答案/反馈。 |
| Agent checkpoint / conversation / message | 原样保留；新 `AgentTask` 从启用后写入。 | 不解析旧 checkpoint 反推正式资产。 |

### 6.2 三阶段发布

1. **Expand**：应用新 schema、读兼容、审计与 preview API；旧写路径不变，feature flag
   `m0_domain_contracts=false`。
2. **Migrate/verify**：运行幂等 backfill；比较每 course 的文档/Attempt 计数，抽样校验
   legacy session 与 Attempt；完成备份和恢复演练。
3. **Contract**：启用新 Course/Exam/Asset 写路径；保留 legacy 读至少一个 MVP 发布周期。
   删除旧列/端点必须在无 legacy 数据或有导出归档方案后另立迁移，不能作为 M0 一部分。

### 6.3 失败处理与回滚

- 迁移失败：停止在当前事务，恢复迁移前备份；不手工修补后跳过版本号。
- backfill 失败：记录资源 ID 和原因，重跑同一幂等 batch；不标记半迁移的 asset 为 confirmed。
- 发布后发现权限错误：立即关闭 feature flag 到旧读路径，保留 audit/confirmation 记录供排查；
  任何已经软删除的 Course 不因回滚自动恢复，必须显式恢复并记审计。

## 7. M0 Definition of Done

M0 只有在以下全部满足时完成：

1. ADR-0001 的七项决策都有 schema、服务层不变量和 API 契约对应项；
2. 完整 migration 链可在空库和含当前数据的副本上升级，且兼容测试通过；
3. Course 删除影响预览/二次确认、资料引用阻断/来源快照、revision 不可变性都由后端验证；
4. 跨账户资源、下载预留接口和确认令牌的自动化权限测试通过；
5. legacy document、fast quiz、Attempt、checkpoint 仍可读取，且不会被错误标成正式版本化资产；
6. 30 天后回收入口只处理到期软删除课程、可重复执行并记录审计，且不暴露给终端用户；
7. API 契约、迁移顺序、状态机和运维回滚说明已评审并写入实现文档；
8. 不包含任何 M1--M4 的用户可见业务功能承诺，尤其不包含邮件提醒。
