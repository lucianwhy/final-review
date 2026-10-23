# M3 最终开发报告（2026-10-12）

**状态：M3-01～M3-06 开发与工程验收完成，用户手动验收反馈全部通过，可以正式转入源码学习。** 既有后端全量 350 通过、随机隔离 PostgreSQL 12 通过、浏览器全量 43 通过、前端构建通过；三项新增模型接口在合成 TCP 小样本中通过，后续手动测试取得真实链路成功记录。M3 功能与关联修复已在 `jzj-dev` 本地提交为 `a20ecae`、`187b5cd`，归档文档随本次文档提交保存，未推送远端。本机开发库已获授权备份并应用迁移 010，项目服务已关闭。工程证据与用户反馈的边界见 [M3 完整验收记录](m3-acceptance-2026-10-12.md)。

## 业务链路

```text
学生提出出卷要求 → QuizInput 澄清并冻结资料版本 → 持久 Quiz Job
→ worker 读取资料、计划、生成、规则校验、逐题语义复核 → Quiz/Question draft revisions
→ 学生编辑整卷 → 新 draft revision → 改动题目复核 → 一次性令牌确认
→ current confirmed revision / 两种 Markdown 打印版
→ Attempt 固定引用该 revision → 保存答案与服务端计时 → 交卷
→ 客观题规则评分 + 主观题参考评分快照
→ 逐题结果、来源卡片与题目范围内的 AI 追问
```

### 主要调用点

| 阶段 | 入口与核心实现 | 数据结果 |
| --- | --- | --- |
| 配置 | `api.py` 的 `/api/chat/dispatch`、`quiz_config.resolve_quiz_config`、`schemas.QuizInput/ResolvedQuizConfig` | 具用户、课程、题型、资料版本的完整配置；缺项继续澄清 |
| 生成 | `quiz_jobs.enqueue_quiz/process_quiz_job` → `quiz_generation.generate_quiz/publish_quiz` → `quiz_contract.validate_quiz_plan/validate_quiz_draft` | 持久任务、来源/语义复核审计、不可变草稿试卷与题目版本 |
| 编辑确认 | `quiz_edit_contract` → `quiz_edit.save_quiz_revision` → `quiz_review.review_quiz_revision/confirm_quiz_revision` | 新草稿、待复核题号、确认令牌；确认后更新 current 指针，保留旧版本 |
| 打印 | `quiz_print.build_quiz_sections/print_quiz_revision` | 确认时保存单一 Markdown 正文；仅题目版取其题目区前缀，完整版附答案解析区 |
| 作答 | `formal_attempt.start_formal_attempt/save_formal_answer/read_formal_attempt/submit_formal_attempt` | Attempt 固定关联 QuizRevision；逐题保存、版本冲突保护、服务端超时与历史恢复 |
| 评分 | `formal_grading.objective_correct/grade_formal_attempt`、`ReviewModel.grade_formal_subjective` | 客观题确定性得分；主观题由模型判断得分点、服务器计算参考分，固化 rubric/反馈/模型版本 |
| 结果追问 | `formal_result.read_formal_result/ask_quiz_followup`、`ReviewModel.follow_up_quiz_question` | 交卷后逐题结果、来源可用性和限定于该题来源的追问记录 |

前端从 `ChatHome` 的配置弹窗进入生成；`QuizDrafts`、`QuizDraftPreview`、`QuizDraftActions` 完成编辑、复核、确认和打印；`FormalAttemptView` 支持作答、刷新恢复、结果及追问。

## 关键设计与状态边界

- **版本不漂移**：编辑产生新的 `asset_revision`、`quiz_revision_payload` 和 `question_revision`，不原地修改旧题。新草稿存在时，已确认的 current revision 仍可用于正式测试；新草稿确认后，旧正式版本变为 superseded，既有 Attempt 继续引用旧题。
- **分层校验**：Pydantic 校验单题结构；整卷比较锁定题目身份、题型、顺序与总分；服务器重新解析当前证据、逐字摘录及资料版本；题意与来源支持关系交给模型复核。合法引用不自动证明答案正确。
- **确认门槛**：改动题目复核未通过或总分与配置不一致时不能确认。确认预览签发绑定内容与来源哈希的短时令牌，消费和版本切换在事务内进行；通用资产确认接口仍拒绝正式试卷，以免绕过该流程。
- **作答权限**：仅 current confirmed 版本可新建正式 Attempt；draft 与 legacy 快速练习不能进入正式入口。试卷答案在作答 API 中隐藏，结果接口要求已交卷且评分完成。
- **评分审计**：选择/判断/填空按结构化答案评分；填空只进行固定的 Unicode、大小写和空白归一化。文字主观题逐得分点得到模型判断，服务器按得分点比例计算参考分；保留评分依据、模型标识/版本、答案哈希、反馈与“仅供学习参考，以教师评分为准”。重复评分返回原快照。
- **来源与追问**：结果展示保存在题目版本中的来源摘录；原资料仍可用才提供跳转。追问请求不接受用户自选资料 ID，模型只收到该题、作答、反馈及该题保存的来源，返回引用 ID 必须属于这道题。

迁移 `010_formal_attempt_history.sql` 增加 `attempt_answers`、`question_grades` 及版本关系约束，并允许既有 Attempt 在原试卷被新版本替换后继续更新；新 Attempt 仍只能引用 confirmed revision。实库测试揭示 UPSERT 会先触发 INSERT 检查，迁移因此明确识别同一既有 Attempt 的不可变父版本，隔离库回归通过。旧 legacy Attempt 保持原关系，不合成正式试卷版本。

## 保留边界与后续工作

- 本地手工上传 PDF 后发现提取文本的 NUL 控制字符会使 PostgreSQL 拒绝保存原始 Markdown；资料入库清洗已修复，并通过只读实文件检查及隔离库回归。历史记录未独立证明原失败文件的页面重试结果，不从本次整体通过反馈推断该文件的具体操作；证据见验收记录。
- 浏览器手动出卷入口两次遇到模型意图判断格式错误；已针对当前 SiliconFlow 接入启用 JSON 模式并有限修复未指定字段解析，假模型回归通过且后端已重启。真实浏览器重发后的出卷任务成功；作答保存与限时等手动边界见 [M3 浏览器手动验收记录](m3-manual-acceptance-2026-10-12.md)。
- 本地工程链路和用户报告的手动功能验收已完成，不能据此推断所有真实课程、大卷和复杂证明题的语义质量。模型生成和评分仍需课程级内容评测。
- 作答答案由学生逐题显式保存；页面在交卷前提示未保存修改不会提交。限时到达由服务端在下一次读/写时落库，打开中的页面会立即请求超时交卷；没有独立的到期扫描任务。交卷后若模型暂不可用，作答仍保持已提交，可重试评分。
- 主观参考评分不覆盖手写图片和复杂公式图片；出题配置中的独立导入题开关仍明确拒绝未实现的纳入能力。资料来源标签来自保存的来源类别元数据，不证明上传者身份。
- 本机开发库已经用户授权备份并迁移到 010，服务在手动验收后已关闭；M3 已本地提交，未推送或部署到生产环境。其他环境启用时仍需备份、迁移和更新前后端及 worker；真实 PPTX OCR 在先前工程验收中因缺少 Tesseract 跳过。
- 尚无已记录且未解决的 M3 严重阻断。后续需跟踪：单进程会话锁要求 API 保持 `--workers 1`；启动检查尚未显式验证 010，部署须额外核对迁移；作答未保存不会随交卷提交；超时没有独立后台扫描任务；模型请求可能重试，成本与响应时长仍需观察。本次为收尾核对，未执行新的全面代码审计。
- PDF 内嵌图片尚不做 OCR，独立导入题未接入出卷，手写/复杂公式图片不在评分范围。学习报告导航页仍为静态占位，错题、真实报告与学习计划属于 M4；M3 完成不代表整个 PRD/MVP 已全部实现。

## Git 收尾与提交记录

2026-10-12 提交前快照：分支 `jzj-dev`，28 个已跟踪文件修改、28 个未跟踪文件，暂存区为空。M3-01/02 已有历史提交；下表记录用户确认的本次提交范围，文件状态为提交前状态。路径均相对仓库根目录，新增源文件、迁移和测试均纳入相应提交。

| 分组 | 已修改文件 | 新增、未跟踪文件 |
| --- | --- | --- |
| M3 后端与迁移 | `src/final_review/api.py`、`llm.py`、`postgres.py`、`quiz_generation.py`、`schemas.py` | `src/final_review/quiz_edit_contract.py`、`quiz_edit.py`、`quiz_review.py`、`quiz_print.py`、`formal_attempt.py`、`formal_grading.py`、`formal_result.py`；`db/migrations/010_formal_attempt_history.sql` |
| M3 前端 | `frontend/src/QuizConfigDialog.tsx`、`QuizDraftPreview.tsx`、`QuizDrafts.tsx`、`main.tsx`、`quiz-drafts.css` | `frontend/src/QuizDraftActions.tsx`、`FormalAttemptView.tsx`、`formal-attempt.css` |
| M3 测试与测试支撑 | `frontend/e2e/quiz-config.spec.ts`、`quiz-drafts.spec.ts`；`tests/conftest.py`、`test_m3_quiz_config.py`、`test_m3_quiz_postgres.py`、`test_postgres_migrations.py` | `tests/test_m3_quiz_edit_contract.py`、`test_m3_quiz_edit.py`、`test_m3_quiz_edit_postgres.py`、`test_m3_quiz_review.py`、`test_m3_formal_attempt.py`、`test_m3_formal_attempt_postgres.py`、`test_m3_formal_grading.py`、`test_m3_formal_result.py`、`test_m3_full_chain.py` |
| 验证脚本与合成证据 | — | `scripts/check_m3_live.py`、`scripts/check_m3_postgres.py`；`backups/m3-live-synthetic-2026-10-12.json`（合成样本，按需要纳入） |
| 文档 | `README.md`、`docs/backlog.md`、`docs/quiz-draft-generation.md` | `docs/m3-development-report-2026-10-12.md`、`m3-acceptance-2026-10-12.md`、`m3-manual-acceptance-2026-10-12.md`、`m3-local-runbook.md`、`m3-source-learning-route.md` |
| 手动验收中修复的共用模块 | `src/final_review/course_chat.py`、`rag.py`；`tests/test_course_chat.py`、`test_rag.py` | — |

`api.py` 同时含 M3 接口和意图判断修复，`test_postgres_migrations.py` 同时含迁移与 NUL 回归；已按差异块分别纳入功能和修复提交。

另有应保留、单独审查的改动：`src/final_review/slide_understanding.py`、`tests/test_slide_understanding.py` 是此前资料视觉处理修改；`tests/test_api.py` 是 OCR 环境跳过与失败信息；`tests/quiz_fixture.py` 是格式调整；`.env.example` 修改了数据库连接及密码配置，应检查是否含本地凭据，不建议直接纳入 M3 功能提交。本次不回退这些文件。

本地 `.env`、`uploads/`、`storage/`（含数据库备份）、`logs/`、`exports/` 必须保留；它们由 Git 忽略，不应作为阶段源码提交内容。

用户已确认提交，按以下三组保存本地历史：

- `a20ecae` — `feat(m3): 完成试卷编辑确认、正式作答评分与追问`，39 个文件，含功能、迁移、前端、测试、验证脚本和合成模型报告。
- `187b5cd` — `fix(materials,chat): 修复资料控制字符入库与出卷意图解析`，6 个文件，含关联缺陷和回归测试。
- `docs(m3): 归档工程验收和用户手动验收反馈`，归档本文及相关文档，提交编号见 Git 历史。

本轮没有新增业务代码或重新运行全量测试；暂存差异、Python 语法和本地配置凭据检查通过。`.env.example`、`src/final_review/slide_understanding.py`、`tests/test_slide_understanding.py`、`tests/test_api.py`、`tests/quiz_fixture.py` 五项独立改动保留未提交。未推送远端。
