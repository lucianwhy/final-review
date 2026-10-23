# M3-02：正式试卷草稿生成

确认出卷配置后，系统创建持久任务，独立 worker 生成题目、校验来源、复核答案，
全部通过后保存 Quiz/Question draft revisions。刷新或离开对话不会停止任务。
生成草稿可在原对话和“模拟测验”列表找回，预览题目、答案、解析、得分点及来源摘录。
编辑、确认、打印及正式 Attempt 属于 M3-03/M3-04，本阶段不开放这些操作。

## 启动与升级

从项目根目录执行迁移，再重启后端并启动试卷 worker：

```powershell
uv run python -m final_review.migrations
uv run uvicorn final_review.api:create_app --factory --host 127.0.0.1 --port 8080 --workers 1
# 另一个终端
uv run python -m final_review.quiz_jobs
```

前端目录运行 `npm run dev -- --host 127.0.0.1`；已有部署需重新构建前端。
Compose 新增 `quiz-worker`，随 `docker compose up --build -d` 启动。
资料上传仍需要资料 worker，出卷不需要笔记 worker。

试卷 worker 使用用户在聊天中选择的模型；独立任务 API 未指定时选配置中的首个可用模型。
`QUIZ_MODEL_TIMEOUT` 默认 120 秒；`QUIZ_MAX_REPAIRS` 默认 2，允许 0～2。
`QUIZ_MAX_OUTPUT_TOKENS` 默认 16,384，显式设置单次模型输出预算；部署到其他模型时须确认
供应商支持该值。`QUIZ_CHOICE_BATCH_SIZE` 默认 5（选择/判断/填空），
`QUIZ_WRITTEN_BATCH_SIZE` 默认 2（简答/计算/证明）。正式试卷最多 100 题。

后台任务在现有 JSONB Job 内保存完整计划、固定编号/分值槽位、待生成批次及已核验题目，
无需新增数据库迁移。已运行的后端和试卷 worker 需要重启，前端需要使用新构建。
失败后点击重试只继续待完成批次；worker 租约重领同样复用已完成进度，旧 attempt 不能覆盖。
资料版本或生成合同变化时拒绝复用，要求重新配置。
模型配置仍由后端环境提供，任务和公开接口不保存 API Key。

## 数据流与代码入口

1. `quiz_config.resolve_quiz_config` 把部分输入解析为 `ResolvedQuizConfig`，
   校验用户、课程、考试和资料范围，保存资料版本、考试更新时间及对话范围上限。
2. `quiz_jobs.enqueue_quiz` 创建 queued Job，可关联原 conversation。
3. `PostgresStore.claim_quiz_job` 使用行锁和 `SKIP LOCKED` 领取任务。
   租约为 15 分钟，worker 每 30 秒续约。过期租约允许重新领取；连续中断超过 3 次后失败。
4. `quiz_generation.quiz_context` 为整卷规划读取最多 60,000 字符、80 个可信片段；
   批次检索池按文件轮流读取最多 600,000 字符、1,000 个片段，在原课程/资料/章节范围内
   根据知识点关键词排名，每批只传入最多 24 个完整片段。记录实际进入规划/批次的
   片段并集及部分覆盖情况，选择整个文件不代表完整读取。
5. `quiz_draft_plan` 生成整卷知识点/题型分配，通过配额校验后，服务器分配固定全局
   编号、顺序和正分值，并将计划与待生成批次持久保存。
6. 每批 `quiz_draft` 只生成本批题目；验证结构、数量、槽位顺序、分值、逐字引用和
   来源标签，并与已完成题目检查重复。每题语义复核包含已完成题目摘要，检查可解性、
   答案一致性、得分点、范围、难度、引用支持关系及跨批语义重复。
7. 计划有最多 2 次修复；每批使用扣除计划已用额度后的剩余修复预算。仅当前批次
   接收具体 issues 修复。模型返回 `finish_reason=length` 时不再用原预算重复同一请求，
   而将未完成批次二分，直到单题；单题仍截断时明确失败并保留已完成批次。
8. 合并后再次运行整卷确定性校验。多批次试卷额外使用摘要进行整卷重点覆盖、排除项和
   语义重复核验，返回短 issues 与受影响题号，避免整卷复核输出长篇逐题解析。
   最多 2 轮整卷修复，只重新生成受影响批次；其他已完成题目保持不变。
9. `publish_quiz` 在同一事务锁定并重新核对课程、考试、资料版本和证据，原子保存
   全部资产/revision/题目/来源/审计/任务结果/对话消息。失败不发布部分试卷。

前端任务 `progress` 显示已核验题数/总题数，整卷复核退回批次时进度可能下降。
模型故障、资料不足和质量校验仍可能失败；分批解决输出长度及恢复问题，不放宽来源要求。
worker 将日志写入 `logs/quiz-worker.log`，自动轮转；公开接口不返回 checkpoint 或原始候选。

公开阶段为 queued/retrieving/planning/generating/validating/reviewing/repairing/publishing。
任务状态为 queued/running/failed/succeeded。模型请求故障进入 failed，可手动重试；
worker 中断后的租约重领属于恢复，不会无限自动重试模型质量失败。

## 数据关系

- `learning_asset(asset_type=quiz)`：资产标题、draft 状态、latest revision；current revision 为空。
- `asset_revision`：统一资产状态机、生成来源、版本号、规范化内容及内容 hash。
- `quiz_revision_payload`：完整配置、分配计划、总分、时长、覆盖情况、模型 ID、提示词版本及复核记录。
  `questions` 为题目 revision 引用、顺序和分值的列表，读取接口将其与题目内容组合。
- `question_revision`：独立的题目身份/版本 ID、题型、题干、答案、解析、得分提示与来源。
  题目版本内容禁止原地修改；后续编辑需要创建新版本。
- `source_reference`：既绑定所属资产 revision，也绑定 question revision、MaterialVersion 和定位器。
  题目内保留文件名、原文摘录、版本和位置，资料删除后仍可展示，原文不可用时不提供跳转。

来源由服务器计算：单资料继承实际类别；多资料标为“综合改编”；无引用题目必须明确获准并标为
“AI补充”，仍须复核。模型不能自报老师/真题标签。每题每个 chunk_id 最多引用一次；
同一片段支持多项事实时，应引用覆盖这些事实的一段连续原文。

迁移 `009_quiz_drafts.sql` 增加任务表、试卷/题目/来源关联约束与内容不可变约束。
既有数据库约束继续拒绝从 draft 创建正式 Attempt。通用资产编辑/确认接口拒绝新生成试卷，
防止绕过后续试卷复核流程。旧通用 Quiz 资产与 legacy 快速练习保持原兼容行为。

## API

| 接口 | 输入与结果 |
| --- | --- |
| `POST /api/courses/{course_id}/quiz-jobs` | `{quiz_input, conversation_id?, model_id?}`，返回 `202` 和任务摘要 |
| `GET .../quiz-jobs/{job_id}` | 读取阶段、状态、失败原因、issues 或结果资产/版本 ID |
| `POST .../quiz-jobs/{job_id}/retry` | 仅失败任务可重试，返回 `202`；版本变化要求新配置 |
| `GET /api/courses/{course_id}/quizzes` | 找回该用户该课程的试卷资产 |
| `GET .../quizzes/{asset_id}/revisions/{revision_id}` | 完整草稿、答案、解析、来源与复核记录 |

创建任务和聊天确认支持 `Idempotency-Key`。相同 key/相同请求返回原 Job；相同 key/不同配置返回 409。
并发相同请求通过事务级锁只创建一个 Job。重试保留递增 attempt，旧 worker 无法覆盖新任务。
同一个 conversation 同时只允许一个 queued/running 试卷任务；可继续普通聊天。

聊天确认配置后返回 `quiz_job` 和任务状态，通常为 queued；相同幂等请求重放可能返回 running、
failed 或 succeeded。conversation/messages 返回 `active_quiz`，完成消息携带 `quiz_draft`。
客户端轮询恢复进度，预览不调用模型。“取消配置”只清除未提交配置，不取消已开始的生成任务。

`/agent/invoke`、`/agent/resume-quiz` 仍提供配置澄清和就绪配置；直接集成的客户端需要将完整
配置对应的 `QuizInput` 提交到 quiz-jobs。它们不会把正式草稿误接到 legacy 等待作答节点。

## 验证与边界

```powershell
uv run pytest -q
uv run python scripts/check_quiz_postgres.py
uv run python scripts/check_quiz_live.py --model-id default --output backups/m3-02-live.json
uv run ruff check src tests examples eval
uv run ruff format --check src tests examples eval
```

数据库验证脚本创建随机隔离库并清理；不迁移用户课程库。live 脚本调用实际配置的模型，
使用两道 TCP 题和合成资料进行小样本验收，会产生实际模型调用费用。
测试模型只验证工程流程；live 小样本也不代表整门课程、大卷、公式/证明题的质量保证。
学生最终确认前仍需审阅，来源类别来自上传元数据，不证明上传者的教师身份。

前端目录执行 `npm run build` 与 `npm run test:e2e`。
验收证据见 [M3-02 验收记录](m3-02-acceptance-2026-10-04.md)。
