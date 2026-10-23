# M3 集中源码学习路线

目标：能够从一次学生操作出发，沿真实调用链解释“生成 → 编辑确认 → 作答 → 评分 → 结果与追问”，并区分校验、模型判断、事务持久化及页面展示。M3 开发与工程验收已完成，用户手动验收反馈全部通过，可以开始学习；阅读基线为 `jzj-dev` 的本地 M3 功能与修复提交（`a20ecae`、`187b5cd`）。工程与用户反馈的证据边界见 [验收记录](m3-acceptance-2026-10-12.md)，不将小样本外推为所有真实课程的内容质量保证。

建议每次只学一个单元：先说出业务输入和预期结果，再跟随入口、服务、存储与测试。不要逐文件通读。你已熟悉 `QuizQuestionEdit` 的字段与单题结构校验，单元三直接从保存与复核接续。

## 单元一：出题请求如何变成可执行配置

- **场景**：学生说“按 TCP 章节出选择和简答题”，但配置可能缺时长、资料或 AI 补充选择。
- **源码链**：`frontend/src/ChatHome.tsx`、`QuizConfigDialog.tsx` → `api.py` 的 `/api/chat/dispatch`、`classify_chat_intent` → `course_chat.parse_chat_decision` → `quiz_config.merge_quiz_input/resolve_quiz_config` → `schemas.QuizInput/ResolvedQuizConfig` → `quiz_jobs.enqueue_quiz`。
- **重点**：明确输入优先于考试预填；对话范围是上限；资料与考试版本被固定；未就绪时返回澄清，ready 才排任务。
- **手动缺陷案例**：结合 `tests/test_course_chat.py` 理解 JSON 模式与 Schema 校验的区别，以及模型返回的未指定列表 `null` 为什么转换为字段省略，以免覆盖上一轮配置。
- **只读验证**：对照 `tests/test_m3_quiz_config.py` 找一个缺项、一个越权资料、一个 ready 用例，追踪各自的返回或异常。
- **检查问题**：① 缺少 `allow_ai_supplement` 时为什么不自动取默认值？② `source_document_ids=[]` 与 `None` 含义有何不同？③ 同一幂等键但配置变化会发生什么？

## 单元二：后台任务如何生成并发布可编辑草稿

- **场景**：用户关闭页面后，试卷仍继续生成；模型候选可能出现错误引用或答案不受资料支持。
- **源码链**：`quiz_jobs.process_quiz_job` → `quiz_generation.quiz_context/generate_quiz` → `quiz_contract.validate_quiz_plan/validate_quiz_draft` → `ReviewModel.review_quiz_draft` → `quiz_generation.publish_quiz/read_quiz`。
- **重点**：租约与续约、分批检查点、确定性规则与语义复核的不同职责、有限修复预算、发布事务和来源快照。
- **只读验证**：读 `tests/test_m3_quiz_generation.py` 中失败后修复、租约失效及发布回滚用例；观察何时会产生 asset revision。
- **检查问题**：① 为什么引用摘录存在仍需语义复核？② worker 丢失租约后为什么不能继续发布？③ 生成期间资料版本变化会在哪一步被阻断？

## 单元三：编辑为何产生新版本，如何安全确认

- **场景**：学生把已有题目的题干或答案改了，再确认成正式试卷并打印。
- **源码链**：`QuizDraftActions.tsx` → `quiz_edit_contract.validate_quiz_edit` → `quiz_edit.save_quiz_revision` → `quiz_review.review_quiz_revision/quiz_confirm_preview/confirm_quiz_revision` → `quiz_print.build_quiz_sections/print_quiz_revision`。
- **重点**：旧 revision 不可变；提交的 `base_revision_id` 与 latest 指针校验；改动题的来源先标待复核；模型复核与一次性确认令牌；两种打印版来自同一 Markdown 正文。
- **只读验证**：先读 `tests/test_m3_quiz_edit.py` 的旧版不变断言，再读 `tests/test_m3_quiz_review.py` 的未复核阻断与打印顺序断言。
- **检查问题**：① 只改分值为何通常不进入语义复核列表？② 为什么确认前再次核对资料版本？③ 新草稿存在时旧 confirmed revision 和它的 Attempt 会怎样？

## 单元四：正式 Attempt 怎样锁定试卷并恢复作答

- **场景**：学生开始限时测试，保存一题后刷新，再继续并交卷；期间试卷可能被编辑。
- **源码链**：`FormalAttemptView.tsx` → `formal_attempt.start_formal_attempt/save_formal_answer/read_formal_attempt/submit_formal_attempt` → `attempt` / `attempt_answer` 存储与迁移 010。
- **重点**：只从 current confirmed 创建；题目列表在作答 API 中不含答案；服务端截止时间；乐观版本号阻止旧请求覆盖；Attempt 永远引用开始时的 QuizRevision。
- **只读验证**：跟读 `tests/test_m3_formal_attempt.py` 的保存恢复、超时、旧卷绑定；查看 `db/migrations/010_formal_attempt_history.sql` 对 superseded 历史的处理。
- **检查问题**：① 客户端倒计时和服务端截止时间谁是权威？② `expected_version` 不匹配时为什么拒绝保存？③ 为什么不能把旧 Attempt 的 `quiz_revision_id` 改成新卷？

## 单元五：评分怎样做到可重现与可审计

- **场景**：客观题立即判分，文字简答获得参考反馈；模型失败后重试不得悄悄改写已有成绩。
- **源码链**：`api.py` 交卷/评分入口 → `formal_grading.objective_correct/grade_formal_attempt` → `ReviewModel.grade_formal_subjective` → Attempt 评分快照与 `question_grade` 记录。
- **重点**：客观题纯函数；填空归一化的边界；模型只判断得分点，服务器计算参考分；rubric、模型版本、答案哈希、反馈与免责声明；重复评分返回旧快照。
- **只读验证**：读 `tests/test_m3_formal_grading.py` 的类型严格性、无模型客观评分、伪造得分点和幂等用例。
- **检查问题**：① 为什么不能让模型直接决定最终分数？② 评分时如何确认模型没有遗漏某个得分点？③ 为什么主观反馈需要保存模型版本与当时的答案哈希？

## 单元六：结果与追问如何守住题目及来源范围

- **场景**：学生交卷后查看某题错因、打开来源，并追问“为什么”。
- **源码链**：`FormalAttemptView.tsx` → `formal_result.read_formal_result/ask_quiz_followup` → `quiz_generation.read_quiz` 的来源可用性 → `ReviewModel.follow_up_quiz_question`。
- **重点**：交卷且评分后才公开正确答案；旧卷题目与来源快照不漂移；删除资料后只保留摘录、不提供原文链接；追问只接收该题上下文，引用 ID 必须属于该题。
- **只读验证**：读 `tests/test_m3_formal_result.py` 的交卷门槛、跨用户拒绝、伪造来源拒绝及追问持久化用例。
- **检查问题**：① 为什么追问请求不能直接传任意 `document_id`？② 来源摘录可显示但原文不可打开时是什么状态？③ 后端如何防止交卷前访问参考答案？

每学完一个单元，先独立回答三个问题，再对照测试与源码纠偏；之后用“业务输入 → 核心调用 → 持久对象 → 失败边界”四行整理语雀复盘。完成六个单元后，再画一次完整调用链，并标出已验证的工程边界与仍需课程级质量评测的部分。
