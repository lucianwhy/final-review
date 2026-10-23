# M3 本地启动、关闭与业务验证手册

本手册适用于 `E:\idea\final-review` 的当前实现，工程证据和质量边界见 [M3 完整验收记录](m3-acceptance-2026-10-12.md)。请优先在测试课程和隔离数据库中走完整链路，不要把测试库地址指向真实课程库。

## 启动

在项目根目录的 PowerShell 中：

```powershell
uv sync --frozen
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d postgres
uv run python -m final_review.migrations
uv run uvicorn final_review.api:create_app --factory --host 127.0.0.1 --port 8080 --workers 1
```

先在 `.env` 中配置与本地 PostgreSQL 一致的 `DATABASE_URL`、模型与 Embedding 参数。迁移会应用到该 URL 指向的数据库；升级已有数据前先备份。新的 `010_formal_attempt_history.sql` 要与后端代码一起部署。保持后端单 worker，以符合现有会话锁设计。

另开终端启动正式试卷 worker：

```powershell
uv run python -m final_review.quiz_jobs
```

若本次要上传新资料，再另开终端运行 `uv run python -m final_review.material_jobs`。M3 的浏览器打印不需要笔记导出 worker。前端另开终端：

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

也可使用 `docker compose up --build -d` 启动 Compose 服务，Agent 启动时自动执行版本化迁移。启动后检查 `http://127.0.0.1:8080/health` 与前端终端显示的地址。

## 关闭

- 分别在前端、后端、试卷 worker、资料 worker 的终端按 `Ctrl+C`。
- 只运行本地 PostgreSQL 容器时，执行 `docker compose stop postgres`。
- 使用完整 Compose 时，执行 `docker compose down`；命名数据库卷会保留。不要用 `down -v` 作为日常关闭命令。

## PDF 上传后显示“处理失败”

先查看资料 worker 终端的最后一段异常。若出现 `unsupported Unicode escape sequence` 和 `\\u0000 cannot be converted to text`，说明 PDF 提取结果带有 NUL 控制字符；当前资料清洗会在保存原始 Markdown 与检索片段前移除它。更新代码后在资料 worker 窗口按 `Ctrl+C`，重新运行 `uv run python -m final_review.material_jobs`，再在“我的资料”对原记录点击“重试”。不需要删除课程或重新上传文件。`Could not get FontBBox` 是 PDF 字体解析提示，本次入库失败的直接原因是 NUL 字符。

## 从生成到追问的手工验证

1. 在测试账号下创建一个测试课程，上传一份合成 TCP 讲义，等待资料状态为 `ready`。
2. 在该课程 AI 对话中提出明确出题要求：章节 TCP、1 道选择题加 1 道简答题、20 分、限时或不限时、指定测试资料，并明确不允许 AI 补充。确认配置后观察 Job 从排队到成功；离开或刷新后仍能在原对话和“模拟测验”找回草稿。
3. 打开试卷草稿，检查题干、选项、答案、解析、知识点和来源摘录。编辑一道题的题干或答案并保存，确认生成了新的 draft revision，旧 revision 内容未变；改动题显示“来源待复核”。
4. 执行“复核编辑内容”。若模型指出问题，修正后再次保存/复核；通过后查看确认信息并确认。未复核或总分与出题配置不一致时，确认应被阻断。
5. 分别打开“仅题目版”和“题目与答案解析版”打印预览：两版题目区顺序一致；仅题目版没有答案；完整版的答案解析统一在题目区后面。
6. 从当前已确认试卷开始在线测试。保存至少一道题的答案并刷新页面，从“我的作答”继续，检查已保存答案恢复、未保存修改不会被当成已提交。限时测试以服务端截止时间为准。
7. 交卷后查看得分。选择/判断/填空按固定规则评分；文字主观题显示参考答案、逐得分点判断、缺失点、改进建议和“仅供学习参考，以教师评分为准”。若模型失败，作答保持已提交，可重试评分。
8. 打开逐题来源卡片及可用原文链接，对一题发起追问。追问引用应仅来自该题；旧资料不可用时仍展示保存的摘录，但不提供原文跳转。
9. 再次编辑并确认试卷，回看第一次 Attempt：题干、答案、评分与来源仍应来自首次开始作答时固定的 revision。草稿与 legacy 快速练习不能通过正式入口创建 Attempt。

## 自动检查

```powershell
uv run --no-cache pytest -q -p no:cacheprovider tests/test_m3_full_chain.py tests/test_m3_formal_attempt.py tests/test_m3_formal_grading.py tests/test_m3_formal_result.py tests/test_m3_quiz_review.py
uv run ruff check src tests examples eval
uv run ruff format --check src tests examples eval
uv run python scripts/check_m3_postgres.py
uv run python scripts/check_m3_live.py --output backups/m3-live-synthetic.json
cd frontend
npm run build
npm run test:e2e -- e2e/quiz-drafts.spec.ts
```

`check_m3_postgres.py` 使用 `.env` 的数据库连接作为建库入口，创建随机命名的隔离数据库，测试后删除该隔离库。需要建库权限。若改为直接运行 PostgreSQL 测试，`TEST_DATABASE_URL` **必须**指向可丢弃的专用数据库：测试 fixture 会删除其 `public` schema。`check_m3_live.py` 只用合成 TCP 题，但会产生模型调用费用；小样本通过不证明整门课程的学科内容质量。Python Playwright 若未安装自带 Chromium，可在本地将 `EXPORT_CHROMIUM_EXECUTABLE` 配置为已安装 Chrome 的绝对路径；真实 PPTX OCR 测试需 Tesseract。
