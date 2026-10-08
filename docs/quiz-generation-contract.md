# M3-01：正式试卷配置与生成合同

本合同把自然语言需求收敛成完整配置，并定义 M3-02 的计划、候选题目和确定性校验格式。
M3-01 不创建 QuizRevision/QuestionRevision，不确认试卷，也不启动 Attempt。
M3-02 已接入实际生成和持久草稿；当前运行方式、任务 API 及边界见
[正式试卷草稿生成](quiz-draft-generation.md)。以下配置合同仍是共享基础。

## 数据流与入口

`用户输入 → QuizInput → 预填/澄清 → ResolvedQuizConfig → QuizGenerationContext`
`→ QuizDraftPlan → QuizDraftPayload → validate_quiz_draft → M3-02 语义核验与草稿发布`

- `POST /api/courses/{course_id}/quiz-config/resolve`：提交 `quiz_input`，可带
  `conversation_id` 继承该对话的资料/章节限制。返回 `needs_clarification` 或 `ready`。
  这是无状态解析入口，不合并历史配置；客户端提交合并后的输入。
- `POST /agent/invoke`：提交 `intent=quiz` 与 `quiz_input`（空对象也有效），进入正式配置流程。
  明确的试卷/模拟卷请求即使没有 `quiz_input`，也不能用旧考试画像直接启动练习。
- `POST /agent/resume-quiz`：提交 `course_id`、`session_id`、增量 `quiz_input`。
  不完整补充保存到 review_session，不消费原暂停；齐备后再恢复图。
  Agent 重建后，`read/recover` 仍可读到补充后的配置和缺项。
- `POST /api/chat/dispatch`：路由器用 `quiz_mode=draft` 标记正式试卷，`quiz_input`
  只提取用户明确说明的字段。支持多轮自然语言补充，也支持直接提交结构化 `quiz_input`。
  返回 `kind=chat`，未确认时状态为 `needs_input`，确认后创建任务并返回 `quiz_job` 和任务状态，
  通常为 `queued`；完整解析结果在 `quiz_configuration`。
  当前聊天界面收到结构化结果后展示与生成笔记相同主题的配置弹窗。所有模型判断为 quiz 的聊天请求
  均先确认配置，包括练习集；不依赖前端关键词。即使自然语言参数完整，也返回 needs_input 等待确认。
  只有提交结构化 quiz_input 且配置就绪后排队生成。pending_quiz 和确认配置保存在原 conversation。
- 既有 fast_quiz_session 题卡仍可读取，独立快速练习 API 继续兼容；聊天出题改为先确认配置。
  配置就绪不会进入 legacy 作答题卡；任务完成后返回正式 Quiz 草稿入口。
- GET conversation/messages 同时返回 pending_quiz 和 active_quiz，用于刷新恢复配置与任务。
  POST /api/courses/{course_id}/conversations/{conversation_id}/cancel-quiz 清除待确认配置，
  不删除历史题卡或此前确认的配置。资料选择受原对话范围约束，最多 100 份，笔记仍最多 5 份。
- 手动测试步骤见 [quiz-config-dialog-testing.md](quiz-config-dialog-testing.md)。

## 配置字段

| 字段 | 语义 |
| --- | --- |
| exam_id | 可选，未关联时是独立练习配置；必须属于同一用户/课程且未归档 |
| scope_mode | course、chapter、knowledge_points；章节/知识点明确时可推导模式 |
| chapter / knowledge_points | 章节或知识点范围；course 模式不能同时指定这两项 |
| blueprint | 六类题型的数量配额，题型不能重复；总计 1～36 题 |
| duration_mode / duration_minutes | timed 必须有 1～240 分钟；untimed 不带分钟数 |
| difficulty | basic、standard、advanced、mixed |
| emphasis / excluded_topics | 重点与排除项；相同项冲突需要澄清 |
| include_imported_questions | 必须明确；当前只支持 false，true 返回能力冲突，不静默忽略 |
| source_document_ids | None 是尚未决定；[] 明确选择当前允许范围的全部 ready 资料 |
| source_types | 空列表不增加类别限制；非空按真实资料类别筛选 |
| allow_ai_supplement | 必须明确，不根据缺项默认允许或禁止 |
| total_score | 可选；指定时题目分数合计必须一致 |
| output_kind | 固定 quiz_draft；打印形式属于 M3-03 |

数量和时长拒绝布尔值等伪整数。每个知识点的总题量不超过 6，题型配额优先满足，
知识点分配由带理由的计划表达。仅指定一个知识点却要求超过 6 题时，需要调整配置。

合并优先级为用户本轮明确字段 > 已收集的字段 > 考试默认值。对话资料/章节限制始终是上限。
考试只预填蓝图、重点、排除、总分及明确的时长/难度偏好，不自动选择资料或授权 AI 补充。
考试画像允许保存不完整或超过本次额度的蓝图；无法用于本次生成的默认值返回澄清。
用户显式选择不限时会清除考试继承的分钟数，但本轮同时指定不限时和分钟数仍是冲突。

完整配置保存 contract_version=1、owner/course、实际资料列表及 material_versions、考试
updated_at 与计算出的 question_count。后续修改考试不会原地改变已保存的配置。
发布前需要通过上下文工厂再次检查考试状态/版本和资料版本，变化时要求重新确认配置。

## 证据、计划与候选内容

`build_quiz_context` 从数据库取回真实片段，检查资料权限、ready 状态、章节和版本。
检索器提供的来源类别和文件名不能覆盖数据库元数据；片段 ID 与内容必须对应本次允许的原文。
上下文包含实际传给模型的 evidence、完整配置、prompt_version=quiz-draft-v1。
选择整份资料不等于读取完整文件。

`ReviewModel.quiz_draft_plan` 返回 allocations：知识点、题型、数量、importance 与
allocation_reason。`ReviewModel.quiz_draft` 根据完整上下文和计划返回候选内容。
这两个方法提供结构化调用合同；M3-01 仅定义合同，M3-02 在独立 worker 中调用，
完成确定性检查、逐题语义复核和有限修复后原子发布。

每题包括 ID、顺序、知识点、题型、题干、分值、参考答案、解析、核心考点、必答点、
常见失分点、得分提示及引用。选择题还需 options 与从零开始的 correct_option；
判断题需 boolean_answer；填空题需 accepted_answers。计算、证明和简答保留文字答案及得分点。

## 确定性校验与来源标签

`validate_quiz_draft` 校验结构、计划和实际题型配额、知识点分配、排除项、题目 ID、
连续顺序、重复题干、题型特有字段、有限正分值、总分，以及逐字引用和引用范围。
失败返回带 code/path/message 的 issues，validated_draft 为空，不能进入发布。

候选题目只能声明 provenance，不能提交 source_label/source_types 等服务端来源字段。

- source：必须引用恰好一份真实资料；标签继承资料类别。
- synthesis：必须引用至少两份资料；标记“综合改编”，保留全部类别和逐条文件/片段/位置引用。
- ai_supplement：必须明确获准且不能带资料引用；只标为“AI补充”。

资料类别依据已存元数据计算，不代表服务器核验上传者的教师身份。合法引用只证明引用真实，
不保证题目、答案、难度或重点在语义上正确。M3-02 使用逐题语义核验并进行有界修复，
通过后才原子保存草稿及来源；不能把本校验器的 valid 当成语义质量证明。

## 验证

- `uv run pytest -q tests/test_m3_quiz_config.py tests/test_m3_quiz_contract.py tests/test_course_chat.py tests/test_agent.py tests/test_llm.py`
- `uv run pytest -q`
- `uv run ruff check src tests examples eval`
- `uv run ruff format --check src tests examples eval`

新增测试覆盖缺项、冲突、考试预填及错误默认值、权限/生命周期、对话范围上限、
重启后的多轮补充、自然语言聊天路由、来源伪造、AI 补充授权、六类题型结构、
分值/数量不符、版本变化，以及 MockTransport 下的真实 LangChain/OpenAI SDK 请求。
测试使用确定性模型和 HTTP fixture，不代表真实模型的出题质量评估。

2026-10-04 最终验证：后端全套 **264 passed、29 skipped**；Ruff lint 与 format 检查通过；
前端 `npm run build` 通过；`git diff --check` 通过。跳过项为未启用的环境依赖测试，
包括独立数据库及 live 模型流程；本次没有执行真实模型的正式试卷生成或新增数据库迁移。
