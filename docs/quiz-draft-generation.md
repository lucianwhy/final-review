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
模型配置仍由后端环境提供，任务和公开接口不保存 API Key。

## 数据流与代码入口

1. `quiz_config.resolve_quiz_config` 把部分输入解析为 `ResolvedQuizConfig`，
   校验用户、课程、考试和资料范围，保存资料版本、考试更新时间及对话范围上限。
2. `quiz_jobs.enqueue_quiz` 创建 queued Job，可关联原 conversation。
3. `PostgresStore.claim_quiz_job` 使用行锁和 `SKIP LOCKED` 领取任务。
   租约为 15 分钟，worker 每 30 秒续约。过期租约允许重新领取；连续中断超过 3 次后失败。
4. `quiz_generation.quiz_context` 按文件轮流读取可信原文，最多 60,000 字符、80 个片段。
   `build_quiz_context` 将证据绑定到数据库原文和资料版本。
   记录实际读取数量、资料 ID 和是否部分覆盖；选择整个文件不代表完整读取。
5. `quiz_draft_plan` 生成知识点/题型分配；`validate_quiz_plan` 在生成题目前检查配额和范围。
6. `quiz_draft` 生成六类题型的候选内容。`validate_quiz_draft` 检查结构、数量、顺序、
   分值、逐字引用和服务端来源标记。
7. `review_quiz_draft` 对每个题目 ID 恰好复核一次，检查可解性、答案一致性、得分点、
   范围、难度和引用支持关系。合法引用不等于语义正确。
8. 失败时 `repair_quiz_draft` 接收具体 issues，返回完整候选；随后重新校验和复核。
   计划和候选共用最多 2 次修复预算。用完后失败，不发布部分试卷。
9. `publish_quiz` 在事务中锁定课程、考试和资料，再次核对状态、版本和证据。
   同一事务保存资产、revision、题目、来源、审计、任务成功结果和原对话结果消息。

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
