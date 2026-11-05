# M4 第一轮开发与测试报告（2026-10-21）

## 本轮交付

| 卡片 | 结果 | 可核对的实现 |
| --- | --- | --- |
| M4-01 | 开发与自动验收完成，待用户手动验收 | 正式评分事务中写入 Mistake 和初始 MasteryObservation；唯一键为 Attempt＋QuestionRevision；错题保留原题、作答、评分、答案、解析、错因、知识点、来源和旧 QuizRevision。 |
| M4-02 | 开发与自动验收完成，待用户手动验收 | 六维组合筛选、手动掌握事件、独立 MistakePractice 创建/提交/找回；练习记录追溯原错题和 Attempt。 |
| M4-05 | **仅错题集页面部分完成** | “我的错题”页面、空态、筛选、原始证据、掌握按钮、重练弹窗与记录；报告指标、计划和提醒界面未开发。 |

M4-03、M4-04、M4-06 本轮未开发。重练客观题沿用 M3 确定性判分；文字题提交后对照参考答案自查，不产生新的 AI 分数。重练结果不自动改动掌握状态，须由用户点击标记；间隔提醒和下次复习时间属于后续 M4-03。

本轮代码包含迁移 `011_mistake_practice.sql`，新增 `mistakes`、`mastery_observations`、`mistake_practices`。数据库外键、唯一约束和触发器校验错题属于已评分的正式 Attempt 及其题目版本，并禁止改变已保存的来源快照。应用层所有读写先校验登录用户及课程；PostgreSQL Store 再按会话 `user_id` 隔离。

## 数据安全边界

- 工作区原有 6 个未提交文件原样保留；旧迁移未修改，新增 011。
- 原本的 `final-review_postgres-1` 容器和 `final-review_postgres-data` 卷未启动或升级。新建的 `final-review-m4-postgres` 使用独立卷 `final-review-m4-manual-data`，仅监听 `127.0.0.1:55432`。011 只应用于其中的 `m4_manual` 和随机隔离测试库。
- PostgreSQL 集成脚本从 `m4_manual` 创建随机命名的临时数据库，结束时删除该临时库；它不会删除 `m4_manual`。手动验收账号、课程和试卷位于 `m4_manual`，留待用户操作。
- 合成试卷由固定测试模型在本地生成；运行中的后端已将 LLM 和 Embedding Key 置空。本轮没有外部模型调用或费用，也没有迁移现有用户课程库。
- 既有 M3 已评分作答不会在页面读取时自动回填错题。再次调用其评分接口可幂等投影，但在对现有用户库安排批量回填前，需要另行确认备份和影响范围。

## 自动验收证据

| 验证 | 命令或范围 | 结果 |
| --- | --- | --- |
| M4 单元与内存 HTTP API | `python -m pytest -q tests/test_m4_mistakes.py` | 4 通过：唯一生成、来源快照、六维组合、状态事件、重练、跨课程拒绝。 |
| 隔离 PostgreSQL | `scripts/check_m3_postgres.py tests/test_postgres_migrations.py::test_empty_database_runner_is_versioned_and_repeatable tests/test_m4_mistakes_postgres.py`，`DATABASE_URL` 指向独立 `m4_manual` | 2 通过：完整迁移含 011、旧版本追溯、重复处理、来源不可变、Attempt/Grade 不变、双用户真实 Cookie HTTP 401/404 隔离。 |
| 后端全量 | `python -m pytest -q -p no:cacheprovider --basetemp=<workspace-temp>`，配置本机 `EXPORT_CHROMIUM_EXECUTABLE` | **358 通过、42 跳过**。初跑仅既有 PDF 导出测试因未配置 Chrome 失败；设置已安装 Chrome 路径后全量通过。 |
| 前端构建与静态检查 | `npm run build`、`npx tsc -b`、`ruff check src tests` | 均通过。Vite 仅提示现有主包体积超过 500 kB。 |
| 浏览器全量 | `npm run test:e2e`，配置本机 `EXPORT_CHROMIUM_EXECUTABLE` | **44 通过**。新增错题 E2E 从正式提交评分进入错题页，验证筛选、掌握和重练；随后单独重跑该用例 **1 通过**，补验刷新后找回练习记录及 390px 宽度下的掌握按钮。 |

本机普通 `%TEMP%` 的 pytest 目录不可读，后端全量使用工作区专用 `--basetemp` 并在验证后清理；该环境问题未通过修改应用代码规避。相关测试使用的 Python 与 Node 均为本项目既有环境。

## 正在运行的手动验收环境

- 前端入口：[http://127.0.0.1:5173/](http://127.0.0.1:5173/)；正式试卷页路由 `/#quiz`，错题页路由 `/#mistakes`。
- 后端健康检查：[http://127.0.0.1:8080/health](http://127.0.0.1:8080/health)；API 文档：[http://127.0.0.1:8080/docs](http://127.0.0.1:8080/docs)。
- 登录账号 `m4-manual@example.test`，密码 `M4manual2026!`。账号仅在独立测试库中使用。
- 已准备课程“**M4 错题手动验收**”、考试“**M4 模拟期末**”、资料“**TCP 合成讲义**”及已确认试卷“**TCP 模拟试卷**”。试卷共 3 道客观题：2 道选择题、1 道填空题，每题 10 分；正确选择项都是 A，填空参考答案为“同步初始序列号”。初始错题数为 0。生成此试卷没有外部费用。
- 启动后实测前后端 HTTP 均返回 200；测试账号登录成功，接口读到 1 门课程、1 份正式试卷、0 道初始错题。
- 后端日志：`logs/m4-backend.stdout.log`、`logs/m4-backend.stderr.log`；前端日志：`logs/m4-frontend.stdout.log`、`logs/m4-frontend.stderr.log`；数据库日志：`docker logs final-review-m4-postgres`。页面请求失败还应查看浏览器开发者工具的 Console 和 Network。

若服务已停止，可先启动独立数据库容器，再使用 `M4_MANUAL_DATABASE_URL=postgresql://postgres:<本地测试密码>@127.0.0.1:55432/m4_manual` 运行 `scripts/seed_m4_manual.py`；脚本只接受该主机、端口和库名，已播种时不会覆盖手动数据。运行后端时将 `DATABASE_URL` 指向同一库、`AUTH_MODE=database`、LLM/Embedding Key 置空；运行前端时令 Vite API 代理指向 `127.0.0.1:8080`。不要对项目原有 `.env` 指向的数据库直接运行迁移。

## 用户逐步手动测试清单

下面每一步的异常先看浏览器 Console/Network；“后端日志”指上面的两个 `m4-backend` 文件，“前端日志”指两个 `m4-frontend` 文件。数据库连接或迁移错误再看 `docker logs final-review-m4-postgres`。

| 步骤 | 操作与输入 | 预期结果 | 失败时日志位置 |
| --- | --- | --- | --- |
| 1. 登录 | 打开前端首页，在“课程管理”登录框输入上述账号和密码，点“登录”。 | 能看到“M4 错题手动验收”课程；页面不再提示未登录。 | 浏览器 Network `/api/auth/sign-in`、后端日志。 |
| 2. 打开正式试卷 | 左侧点“模拟测验”，或访问 `/#quiz`；点“TCP 模拟试卷”，在预览里点“开始测试”。 | 弹出“TCP 模拟试卷”，显示作答中、3 道题、总分 30；不预先显示参考答案。 | 浏览器 Network `/quizzes/.../attempts`、后端日志。 |
| 3. 制造两道错题 | 第 1 道选择题选 **B** 并点“保存本题答案”；第 2 道选择题选 **A** 并保存；填空题填“错误答案”并保存。 | 各题显示“已保存”；刷新再从“我的作答”继续时答案仍在。 | 浏览器 Network `/formal-attempts/.../answers`、后端日志。 |
| 4. 交卷与评分 | 点“交卷”。 | 总分 **10/30**；第 1 道选择、填空错误，第 2 道选择正确；逐题可见原作答、参考答案和来源。 | 浏览器 Network `/submit`、`/grade`、`/result`，后端日志。 |
| 5. 进入错题集 | 在结果弹窗点“查看我的错题”，或左侧点“我的错题”。 | 路由为 `/#mistakes`，显示 **2 道错题**；无已答对的第 2 道选择题。 | 浏览器 Network `/api/mistakes`、后端日志、前端日志。 |
| 6. 核对来源 | 展开任一错题的“查看原作答、答案与解析”。 | 可见刚才的错误答案、参考答案、错因、知识点、来源摘录及原 Attempt ID、QuestionRevision ID。 | 浏览器 Network `/api/mistakes`、后端日志。 |
| 7. 六维组合筛选 | 依次选课程“M4 错题手动验收”、考试“M4 模拟期末”、章节“TCP”、知识点“TCP考点1”、题型“选择题”、掌握状态“未掌握”。 | 剩 **1 道**第 1 题；改题型为“填空题”则显示另一道；点击“清除筛选”恢复 2 道。 | 浏览器 Network `/api/mistakes?...`、后端日志。 |
| 8. 标记掌握 | 再筛到选择题，点“标记已掌握”；将掌握状态筛为“已掌握”。 | 该题显示“已掌握”；筛“未掌握”时不显示；回“模拟测验”查看原结果仍为 **10/30**。 | 浏览器 Network `/mastery`、`/api/mistakes`，后端日志。 |
| 9. 重练与找回 | 在该错题点“重新练习”，选择 **A**，点“提交重练答案”；关闭弹窗后点“练习记录”并打开刚才的记录。 | 弹窗显示真实 `mistake-practice-...` 记录 ID、“重练正确”、原题版本；记录重开仍能看到答案与结果，掌握标记不被自动覆盖。 | 浏览器 Network `/practices`、`/submit`，后端日志。 |
| 10. 手机宽度 | 浏览器开发者工具切到约 390px 宽，重新打开 `/#mistakes`，操作筛选和标记。 | 页面不横向溢出；筛选控件、错题卡和按钮可读可点。 | 浏览器 Console、前端日志。 |

自动测试已检查重复评分只保留 1 条相同 Attempt/QuestionRevision 的错题、手动状态不写回评分、旧 QuizRevision 回放、跨账号不可读写。本清单等待用户本人操作；在收到手动验收结果前，不进入 M4 第二轮。
