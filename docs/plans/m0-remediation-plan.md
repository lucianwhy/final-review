# M0 验收阻断项修复计划

> 本文是修复规划，不代表已实施。除本计划外，不改动现有代码、文档或 `002_m0_domain_contracts.sql`。
> `002` 已在临时库执行，默认视为不可改写的历史迁移；所有修复均通过新的
> `003_m0_database_hardening.sql`、迁移执行器和后端改动完成。

## 目标与验收切分

| 任务 | 独立交付目标 | 完成判定 |
| --- | --- | --- |
| M0-R1 | 用 `003` 将 M0 数据关系、不变量和迁移履历落到真实 PostgreSQL。 | 空库、含已执行 `002` 的库均可升级；数据库拒绝孤儿、重复 revision、非法 Attempt 和 confirmed revision 改写。 |
| M0-R2 | 让所有 M0 命令在服务层执行一致的课程可写校验，并让 confirmed 覆盖重新预览、重新确认。 | API 不能借由 Exam/Asset/Revision/Agent/导出路径绕开课程状态、所有权或确认令牌。 |
| M0-R3 | 以完整迁移链运行真实 PostgreSQL 的双账户 API 回归和故障恢复演练。 | 读取、写入、删除、预览、导出及跨用户/token replay 均有非 mock 集成证据。 |

三个任务可以分别验收：R1 不依赖新 API；R2 依赖 R1 的数据库能力；R3 只在 R1、R2 合入后执行。

## 不变量归属

数据库负责“无论调用者是谁都不能写出坏数据”；服务命令负责“合法状态下这次业务操作是否允许”。两层都不得互相替代。

| 不变量 | 数据库保证 | 后端命令保证 |
| --- | --- | --- |
| Course 与所有课程资源同属一个用户、一个课程 | `(user_id, course_id)` 复合 FK；资源关系 FK。 | 从 session 取 user，不信任 body/path 中的 user；无权限统一 404。 |
| revision 属于 Asset、编号不重复 | `asset_id` FK、`unique (user_id, asset_id, revision_no)`。 | 只允许以同 Asset 的可编辑 base 创建下一 draft；处理并发冲突为 409。 |
| confirmed revision 内容不可变 | trigger 拒绝改变 confirmed revision 的内容/来源/hash/确认时间，也拒绝删除 confirmed/superseded 历史行。 | 编辑已确认资产只能 INSERT 新 draft；确认转换前做内容和来源完整性校验。 |
| SourceReference 指向真实、同课程的 revision/material/locator | 三个 FK、复合 owner/course FK，以及 source-reference 的一致性 trigger。 | 仅接受当前用户可读、未失败且同课程的 document/material；返回业务 422/409。 |
| Attempt 恰好关联正式 revision 或 legacy session | `check ((quiz_revision_id is null) <> (legacy_session_id is null))`，并验证为有效约束；正式 revision FK。 | 新正式 Attempt 只允许 confirmed quiz revision；legacy 写入只来自兼容路径。 |
| 确认令牌单次、归属正确且预览未过期 | 原子条件更新 `consumed_at is null and expires_at > now()`；必要唯一索引。 | 重新计算影响 payload/hash，验证 action/resource/owner/mode；冲突返回 409。 |
| Course 是否可写及动作状态机 | 课程状态枚举/check 约束。 | 每个会改变课程资源或产生可持久化结果的命令重新加载 Course 并按统一状态矩阵判定。 |

## M0-R1：003 关系约束、不可变保护与迁移治理

### 1. 新迁移及执行模型

新增 `db/migrations/003_m0_database_hardening.sql`，不得修改 `002`。引入受版本控制的 migration runner：

- 新建 `schema_migrations(version text primary key, checksum text not null, applied_at timestamptz not null, applied_by text not null)`；runner 按数字顺序读取迁移，每个文件在一个数据库事务内执行，成功后在同一事务插入履历。任一步失败则 rollback，不能写成功履历。
- 对已记录版本，runner 校验文件 checksum；不匹配立即失败，禁止静默重跑或篡改历史。
- 对“已由 Docker init 临时执行、但没有履历”的库，runner 先执行只读 baseline preflight：核对 `001`、`002` 所需表/列/约束/索引及预期 SQL checksum。仅全数匹配时，在单一事务中登记 `001`、`002` 为已应用，再运行 `003`；任一不匹配即停止，要求人工恢复到备份或做明确的独立修复，不得盲目登记。
- Compose 的 `/docker-entrypoint-initdb.d` 只会在新数据卷运行，不能作为升级机制。启动/部署脚本必须在应用可接流量前运行 runner，并让 `PostgresStore.setup()` 检查 schema version 至少为 `003`，否则 fail fast。新库同样通过 runner 执行 `001`、`002`、`003`，而不是依赖目录自动初始化。

`003` 本身可重复安全：每个 DDL 用可判定的存在性保护；数据回填按确定性键和 `WHERE ... IS NULL` 进行；约束/触发器以 catalog 检查后创建。但“文件已成功应用”的正常重复执行由 `schema_migrations` 跳过，而不是依靠 `IF NOT EXISTS` 掩盖不同版本内容。

### 2. `003` 的数据库改动

先从 JSON `data` 回填下列物理列，再添加 `NOT NULL`、FK 和 `VALIDATE CONSTRAINT`。对无法回填的存量脏数据，迁移应失败并输出键/计数，不能通过 NULL 或伪造关联继续。

1. **Course 基准键与课程 FK**：为 `courses` 添加/验证 `unique (user_id, course_id)`；所有有 `course_id` 的 M0 表添加 `foreign key (user_id, course_id) references courses(user_id, course_id)`。适用表至少包括 `exams`、`learning_assets`、`asset_revisions`、`quiz_revision_payloads`、`question_revisions`、`material_versions`、`source_references`、`source_reference_snapshots`、`confirmation_requests`、`audit_events` 以及 `attempts`。保留已有 `app_users` FK。
2. **资源关系列和 FK**：将 JSON 中的 `asset_id`、`revision_no`、`state`、`material_version_id`、`revision_id`、`document_id` 等投影为受约束列。`learning_assets` 增加可被引用的 `unique (user_id, record_key)`；`asset_revisions.asset_id` 以 `(user_id, asset_id)` FK 指向该键；revision 的课程也以复合 FK 与 Asset 的课程一致。`material_versions` 以 `(user_id, document_id)` FK 指向 documents；若一个 document 可有多个版本，使用 `(user_id, record_key)` 作 SourceReference 的版本键，不把 document_id 误当版本主键。
3. **SourceReference 关系**：`source_references` 必须有 `asset_revision_id`、`material_version_id`、可空 `locator_id`；分别 FK 到 revision、material version、同 material version 的 locator。新增 `material_locators`（或在既有版本表内使用稳定 locator key）并以 `(material_version_id, locator_kind, ordinal)` 唯一。触发器拒绝 SourceReference 的 user/course 与其 revision、material version、locator 任一不一致；快照同样 FK 到其引用 revision/version。
4. **revision 与状态保护**：建立 `unique (user_id, asset_id, revision_no)`；`revision_no > 0`，state/asset status 使用显式枚举 check。建立 `BEFORE UPDATE OR DELETE` trigger：一旦 OLD.state 属于 `confirmed` 或 `superseded`，禁止改变正文、标题、content_hash、来源集合、asset_id、revision_no、confirmed_at 或删除；仅允许一次受控的 state 转换（confirmed 到 superseded）且内容字段完全相同。Asset 的 `current_revision_id` 使用复合 FK，并由 trigger 校验它属于同一 Asset 且为 confirmed。服务代码不得再使用泛化 upsert 来覆盖这些行。
5. **Attempt XOR**：替换 `002` 中仅禁止“两者同时存在”的 `attempts_revision_xor_legacy`，先删除该命名约束，再添加 `NOT VALID check ((quiz_revision_id is null) <> (legacy_session_id is null))`，回填所有历史 Attempt 的 legacy session 后 `VALIDATE CONSTRAINT`。`quiz_revision_id` FK 指向 quiz revision payload 的真实 revision key；必要时增加 trigger，限制正式引用为 confirmed 且同 course/user。不得把 legacy Attempt 伪造为正式 revision。
6. **确认与审计写入**：confirmation 表增加适合原子消费的列/索引（owner、action、resource、payload_hash、expires_at、consumed_at）；审计表通过 append-only trigger 禁止 UPDATE/DELETE。令牌消费不以“先读后写”实现。

约束采用“添加 `NOT VALID` → 回填/审计 → `VALIDATE`”顺序以控制锁时间；`CREATE INDEX CONCURRENTLY` 如确有大表需要，单列为受控非事务阶段并在 runner manifest 标注，不能与事务迁移伪装为同一步。

### 3. R1 验收与回滚边界

- 在临时真实 PostgreSQL 中直接 SQL 验证：插入跨 course/user 的 Exam、孤儿 revision、重复 revision_no、错配 SourceReference、同时为空/同时非空的 Attempt、更新 confirmed revision 均失败；合法 legacy/new Attempt 均成功。
- 空库：runner 从零执行 001→002→003，检查 `schema_migrations` 三条记录、所有 FK/check/trigger 存在，且应用启动通过版本检查。
- 存量库：以已执行 `002` 的副本运行 baseline preflight、登记旧版本、执行 003；逐表比较迁移前后行数及每 user/course 的 records，旧 documents/fast_quiz/attempt 可读。
- 重复执行：第二次 runner 不执行 SQL，checksum 相同且 schema/数据不变；模拟中断 SQL 后事务内无半成品约束、无版本记录，再次运行可完成。
- 失败恢复：先做可恢复备份和 schema dump；任何 preflight/backfill/validate 失败停止部署，保留失败日志和备份，不跳版本。003 为 expand/约束迁移，不提供生产“自动 down”；若发布后需撤回应用，停在兼容读代码并保留 schema。只有在确认没有 R2 新写数据后，才用单独、经演练的 rollback 脚本移除 003 新对象；绝不回滚或编辑 002。

## M0-R2：命令门控、覆盖确认与 API 契约

### 1. 统一课程可写校验

提炼 `require_owned_course_for(action)`/`DomainService.course(..., writable=True)` 为单一政策，且每个命令在**写入前、同一事务内**重新读取 Course，而非相信前置路由检查。状态矩阵须固化：`deleted`、`purged` 对所有资源读取以外的命令一律拒绝；`archived` 是否允许每项已有业务动作必须显式列出，不能靠遗漏默认放行。资源修改至少包括创建/更新/归档 Exam、创建 Asset、创建/确认 Revision、归档/删除 Asset、资料删除、生成/提交 Attempt、Agent 的持久化命令和正式 export。

重点修复目前只经 `exam()`/`_owned()` 的路径：`update_exam`、`archive_exam`、`confirm_revision`、`archive_asset`；同时审计 API 中直接调用 `store.put` 的 Agent/Attempt 入口以及 `/agent/export`。每个路径还要在资源解析后确认 resource.course_id 与重新读取 Course 一致。

### 2. confirmed 资产覆盖的预览和确认

将“确认 revision 导致已确认 Asset 的 current revision 改变”定义为 `asset.overwrite` 破坏性操作：

1. 新增 `POST /api/assets/{asset_id}/overwrite-preview`，仅当 Asset 已 confirmed/archived 且目标 draft 属于该 Asset 时返回影响摘要、`confirmation_id`、`expires_at`、当前 revision id/content hash 和目标 revision id/content hash；不改状态。
2. `POST /api/assets/{asset_id}/revisions/{revision_id}/confirm` 对首次确认 draft 保持普通确认；若会替换 confirmed current revision，则请求体必须含 `confirmation_id`。服务端锁定 Asset/目标 revision，重新计算预览 payload（包括 current/target revision 与受影响 Attempt/导出可见性计数），原子消费 token，再将旧版本 supersede、新版本 confirmed、更新 current revision、写审计。
3. token 绑定 user、action、asset、目标 revision、payload hash 和过期时间。不同用户、不同 Asset/revision、过期、已消费，以及预览后 current revision/影响范围变化都返回 `409`；无权资源返回 `404`。不允许前端通过省略 token、复用 course-delete token 或直接 PATCH 避开此流程。

### 3. API 错误与读写契约

- 认证身份是唯一的 user source；跨用户 course/exam/asset/revision/material/attempt/preview/delete/export 一律 404，避免资源枚举。
- schema/字段不合法为 422；状态、乐观锁、stale preview、token replay/过期为 409；迁移未就绪/存储不可用为 503。
- 所有会修改资源的 API 接受并验证必要的 `expected_updated_at` 或同等版本条件；数据库唯一/FK 冲突转换为稳定的 409/422 API 错误，不能泄露驱动细节。
- `/agent/export` 只能读取调用者拥有且课程可读的会话；正式资产导出只接受 confirmed revision id，不得用“当前 Asset”代替历史 revision。若目前尚未交付正式导出端点，必须明确返回未实现，而不是将 legacy Agent export 误称为正式 revision export。

### 4. R2 验收

- 对每个上述写命令，先将 Course 切为 deleted/purged，再调用端点；断言 409、无资源/审计/token 消费副作用。对允许 archived 的显式动作另测允许条件。
- 首次 confirm 不需要 overwrite token；第二次 confirm 无 token 为 409；预览后任一 current/target/影响变化使 token stale；成功覆盖后旧 confirmed revision 内容未变、状态为 superseded，新 revision confirmed，token 仅可消费一次。
- 直接 repository/upsert 尝试改 confirmed revision 由 R1 trigger 拒绝，证明该 API 防线不是唯一防线。

## M0-R3：真实 PostgreSQL 双账户 API 回归与发布演练

### 1. 集成测试环境

新增独立 PostgreSQL integration fixture（测试容器或每次创建的临时数据库），不使用 `InMemory`、`PostgresStore.__new__` fake cursor 或 mock 代替本任务断言。fixture 必须：启动带 pgvector 的 PostgreSQL、通过 migration runner 运行完整链、创建两名用户和各自 cookie/session、用生产 `create_app(agent=None)` + 真正 `PostgresStore` 请求 API；外部模型调用用测试替身仅限模型本身，不能替换认证、HTTP、数据库或事务。

### 2. 必测 API 矩阵

| 场景 | 账户 A 操作 | 账户 B / 重放断言 |
| --- | --- | --- |
| 读取 | 创建 Course、Exam、Asset、Revision、Document、Attempt；读取列表、详情、report、legacy export。 | B 对每个已知 ID 的 GET 均 404。 |
| 修改 | A 修改/归档 Exam、创建/确认 Revision、提交 Attempt。 | B 的 PATCH/POST 均 404，数据库行和 audit 不变。 |
| 删除与 preview | A 取得 course/material/overwrite preview 并完成一次合法操作。 | B 消费 A token 为 404；A 重放已消费 token 为 409；过期及 payload 变化为 409。 |
| 导出 | A 对自己的 legacy session 成功；正式 export 仅 confirmed revision 成功，draft 为 409/422。 | B 枚举 A session/revision/export 404。 |
| 关系约束 | API 建立同 course 的来源与 revision。 | 跨 course/user 的 reference、base revision、Attempt revision 由 API 拒绝，且 SQL FK/check 复核失败。 |

测试还需覆盖两个并发请求同时 consume 同一 token、同时确认同一 revision 的结果恰有一个成功；验证事务失败时没有 snapshot 而 document 已删除、或 token 已消费而 Asset 未改变的半状态。

### 3. 发布验证顺序

1. **空库演练**：runner、启动检查、双账户 API 全量测试、schema dump 检查。
2. **存量副本演练**：从与生产同版本备份恢复到隔离库，执行 baseline + 003；统计和抽样验证 legacy Attempt/read/export，跑双账户回归。
3. **生产发布**：备份并记录 restore 演练结果；先运行 runner/preflight，再启动应用健康检查与 schema-version 检查，最后放量。部署失败立即停止流量切换并按 R1 失败恢复策略处理。
4. **重复与恢复演练**：runner 连跑两次；注入 003 中途失败和应用确认事务中途失败；确认前者可重跑、后者原子回滚，且恢复的数据库仍能通过 R3 全量回归。

## 禁止的“修复”

- 不删除或放宽 FK、check、trigger 来迁就旧数据；旧数据必须被明确 backfill、隔离或使迁移失败。
- 不编辑 `002`、不靠 Docker init 目录假装完成版本管理，也不只在 README 写“手工执行 002”。
- 不把 XOR 改成“最多一个”，不允许 null/null Attempt，也不把 legacy 伪装为 confirmed revision。
- 不用 mock/内存测试替代 PostgreSQL 双账户 API 回归；mock 测试可保留为快速单测，但不能作为验收证据。
- 不通过跳过预览、复用 token、降低 404/409 契约或移除不可变保护来通过验收。
