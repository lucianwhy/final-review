# M3 完整验收记录（2026-10-12）

**结论：M3-01～M3-06 工程验收完成，用户手动验收反馈全部通过，阶段可归档并转入源码学习。** M3-01/02 保留既有工程证据，M3-03～06 的工程证据如下。功能和关联修复已在 `jzj-dev` 本地提交为 `a20ecae`、`187b5cd`，文档另行归档提交，未推送。本记录不等于生产部署或大规模学科质量保证；跳过的测试不计为通过。

## 用户手动验收反馈

2026-10-12，用户明确反馈已在浏览器亲自测试 M3 全部业务功能，结果均通过。本项按 **用户手动验收反馈：通过** 归档，不补写未记录的点击顺序、题目答案、截图或测试日志。此前保存/恢复、有答案评分和限时操作的独立证据缺口继续保留，详见 [M3 浏览器手动验收记录](m3-manual-acceptance-2026-10-12.md)。本次收尾仅核对源码、文档和 Git 状态，没有重跑全量测试或调用模型。

## 已取得的证据

| 范围 | 执行结果 | 证明范围 |
| --- | --- | --- |
| M3 专项及现有渲染测试 | `147 passed, 10 skipped` | 配置、生成、编辑、复核、确认、打印、正式 Attempt、评分、结果与追问的内存测试；10 项 PostgreSQL 专项因未配置隔离测试库跳过 |
| 后端全量 | `350 passed, 40 skipped` | 使用全新工作区 Pytest 临时目录及本机 Chrome；1 项真实 PPTX OCR 因未安装 Tesseract 明确跳过，其他跳过项含独立 PostgreSQL/SurrealDB 等外部环境测试 |
| 内存 HTTP 全链路 | `tests/test_m3_full_chain.py` 通过 | 从创建 Quiz Job、worker 发布草稿，到编辑、复核、确认、保存作答、评分、结果和追问的接口接缝；模型为确定性测试替身 |
| 随机隔离 PostgreSQL | `uv run --no-cache python scripts/check_m3_postgres.py`：`12 passed`，隔离库已删除 | 迁移空库/旧基线、M3-02 发布与任务、M3-03 不可变新草稿/确认、旧卷被替换后既有 Attempt 的答案保存、评分记录和追问写入 |
| 前端构建及浏览器 | `npm run build` 通过；`npm run test:e2e`：`43 passed` | M3 从生成到追问的页面链路、刷新恢复、手机宽度无横向溢出，以及既有聊天/资料/笔记回归；笔记 PDF 用例使用本机 Chrome |
| 真实模型合成小样本 | `scripts/check_m3_live.py` 通过，报告在 `backups/m3-live-synthetic-2026-10-12.json` | 一道合成 TCP 题的编辑复核、主观得分点判断、限定片段追问均返回有效结构；未使用用户课程资料 |
| Ruff | `ruff check src tests examples eval`、`ruff format --check src tests examples eval` 均通过 | Python 静态规则与格式 |
| TypeScript | `npx tsc -b --pretty false` 通过 | 前端类型检查，包括编辑、作答、结果组件 |
| Git | `git diff --check` 通过 | 已跟踪文件无空白错误；新增文件仍须由正常提交流程纳入 |

专项命令：

```powershell
uv run --no-cache pytest -q -p no:cacheprovider tests/test_m3_quiz_config.py tests/test_m3_quiz_contract.py tests/test_m3_quiz_generation.py tests/test_m3_quiz_edit_contract.py tests/test_m3_quiz_edit.py tests/test_m3_quiz_review.py tests/test_m3_formal_attempt.py tests/test_m3_formal_grading.py tests/test_m3_formal_result.py tests/test_m3_full_chain.py tests/test_rendering.py tests/test_m3_quiz_postgres.py tests/test_m3_quiz_edit_postgres.py tests/test_m3_formal_attempt_postgres.py
```

## 按任务卡核对

- **M3-01 / M3-02**：保留既有 [M3-02 验收记录](m3-02-acceptance-2026-10-04.md)。该记录包含当时的隔离 PostgreSQL 和一次真实 Qwen 小样本；不等于本次新增链路的验收。
- **M3-03**：单题/整卷编辑合同、不可变新草稿、来源复核、改动题目语义复核、确认令牌、正式 Markdown 与两种打印版通过单元、HTTP、隔离 PostgreSQL 和浏览器全链路；待复核与总分问题阻断确认，旧版本保持可读。
- **M3-04**：仅从 current confirmed revision 开始 Attempt；答案保存、乐观版本冲突、服务端超时、恢复、交卷和旧版本固定引用已有测试。迁移 `010_formal_attempt_history.sql` 在隔离库通过旧卷替换后的 Attempt 更新检查。
- **M3-05**：客观题纯函数规则及无模型评分、主观题得分点结构化判断、模型/反馈快照、免责声明、重复评分幂等通过测试；隔离库验证 `attempt_answers`、`question_grades` 写入及评分行不可变；真实模型在合成单题中返回有效得分点判断。
- **M3-06**：只在交卷且评分后读取结果；逐题解析、知识点、来源与题目范围内的追问，伪造片段和跨用户读取被拒绝；隔离库验证历史 Attempt 追问写入，浏览器全链路与合成真实模型追问通过。

## 验收边界

1. **外部依赖**：这台机器没有 Tesseract，真实 PPTX OCR 用例明确跳过；其余跳过项涉及独立服务或环境。随机隔离库 M3 专项和两项迁移升级检查已另外运行，但不能替代所有旧 PostgreSQL 测试。
2. **模型质量**：工程验收中的新增接口真实模型证据是一道合成 TCP 题，M3-02 另有既有生成小样本；随后浏览器手动测试取得真实出卷及追问的成功记录和用户整体通过反馈。这些样本都不代表整门课程、大卷、复杂证明或追问内容的准确率保证。
3. **部署**：工程自动化验收使用随机隔离库。随后经用户明确授权，已备份本机 `final_review` 开发库并应用迁移 010，校验和及新增表核对通过；归档备份位于 Git 忽略的 `storage/backups/final_review_pre_010_20261012_104942.dump`，`pg_restore -l` 校验通过，但未进行完整恢复演练。服务曾用于手动验收，现已按用户要求关闭。未进行生产部署或 Git 推送；其他环境仍需备份、迁移和部署验证。
4. **环境问题处理**：此前自动审批因账户用量限制未执行提权测试；额度恢复后已重跑并通过。默认 Pytest 临时目录访问受限，最终全量使用新的工作区 `--basetemp`。Python Playwright 默认 Chromium 未安装，测试显式指向本机 Chrome；真实 OCR 依赖 Tesseract。

## 复验命令

在具有依赖工具的开发环境运行；Windows 上如 Python Playwright 未安装自带 Chromium，可将 `EXPORT_CHROMIUM_EXECUTABLE` 指向已安装 Chrome：

```powershell
uv run python scripts/check_m3_postgres.py
uv run --no-cache pytest -q -p no:cacheprovider --basetemp .m3-pytest-clean
uv run python scripts/check_m3_live.py --output backups/m3-live-synthetic.json
uv run ruff check src tests examples eval
uv run ruff format --check src tests examples eval
cd frontend
npm run build
npm run test:e2e
```

隔离库脚本在结束时删除随机测试库；直接运行 `tests/test_postgres_migrations.py` 的 `TEST_DATABASE_URL` 数据库会清空其 `public` schema。真实模型脚本会产生调用费用，仅使用合成题。所有 M3-03～M3-06 卡片已按当前工程验收范围标为完成。

## 本地手工试用后补充（2026-10-12）

用户上传的 4 页 PDF 在文字提取后含 5 个 NUL 控制字符，原有检索文本清洗虽会去除它们，保存在资料记录中的原始 Markdown 仍带 NUL，PostgreSQL JSON 写入因此报 `UntranslatableCharacter`。现已在资料入库准备阶段清除待保存 Markdown 中的控制字符。该 PDF 只读重跑后得到 4 个片段，待保存 Markdown 与片段均无 NUL；资料处理相关测试 `45 passed`，随机隔离 PostgreSQL 的普通文本和带 NUL 文本入库测试 `2 passed`，隔离库已删除。用户原失败记录需重启资料 worker 后在页面点击“重试”，尚未把这次用户数据重试记为通过。

## 手动出卷入口故障修复（2026-10-12）

用户在浏览器两次发送明确的模拟试卷请求，`/api/chat/dispatch` 均返回 502：每次模型首次回复中的若干列表字段不符合 `ChatDecision/QuizInput` 合同，第二次回复不是有效 JSON，任务尚未进入试卷 worker。当前 SiliconFlow 模型接入原先没有启用其支持的 JSON 模式。修复后，该地址请求 JSON 对象；模型对出卷无关的非法检索动作回退为默认动作，未指定列表字段的 `null` 视为省略，非空错误类型及普通问答的非法动作仍拒绝。聊天、试卷配置、全链路和 API 回归为 `61 passed, 1 skipped`，Ruff lint/format 通过；后端已重启且 `/health` 返回 200。真实浏览器重发后聊天分派为 200、试卷任务成功；剩余手动验证状态详见 [M3 浏览器手动验收记录](m3-manual-acceptance-2026-10-12.md)。
