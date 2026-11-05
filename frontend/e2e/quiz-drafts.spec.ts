import { test, expect } from "@playwright/test";

test("formal generation survives reload and shows answers, sources and saved drafts", async ({ page, request }) => {
  const base = "http://127.0.0.1:8081";
  await request.post(`${base}/test/reset`);
  const course = await (await request.post(`${base}/api/courses`, { data: { name: "M3-02 网络" } })).json();
  const material = await (await request.post(`${base}/knowledge/ingest`, { data: {
    course_id: course.course_id, title: "TCP 讲义", chapter: "TCP", source_type: "teacher_ppt",
    markdown: "TCP 三次握手同步双方初始序列号并确认双方收发能力。",
  } })).json();
  const conversationId = "formal-generation";
  const input = { scope_mode: "chapter", chapter: "TCP", blueprint: [
    { question_type: "choice", question_count: 4 }, { question_type: "short_answer", question_count: 2 },
  ], duration_mode: "untimed", difficulty: "standard", include_imported_questions: false,
    source_document_ids: [material.document_id], total_score: 20 };
  const started = await request.post(`${base}/api/chat/dispatch`, { data: {
    course_id: course.course_id, conversation_id: conversationId, model_id: "review",
    message: "生成TCP模拟卷", quiz_input: input,
  } });
  expect(started.ok()).toBeTruthy();
  await page.goto(`/#chat/${course.course_id}/${conversationId}`);
  const dialog = page.getByRole("dialog", { name: "补充试题要求" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "AI 补充题", exact: true }).click();
  await dialog.getByRole("option", { name: "不允许", exact: true }).click();
  await dialog.getByRole("button", { name: "确认试题配置" }).click();
  await expect(dialog).toHaveCount(0);
  await page.reload();
  const card = page.getByRole("button", { name: /TCP 模拟试卷.*查看试卷草稿/ });
  await expect(card).toBeVisible({ timeout: 15000 });
  await card.click();
  const preview = page.getByRole("dialog", { name: "试卷草稿预览" });
  await expect(preview).toContainText("6 题 · 共 20 分 · 草稿");
  await expect(preview.getByText("老师PPT", { exact: true })).toHaveCount(6);
  await expect(preview.getByRole("button", { name: /开始测试|确认试卷/ })).toHaveCount(0);
  await preview.locator("summary").filter({ hasText: "查看答案、解析与得分点" }).first().click();
  await expect(preview.getByRole("heading", { name: "参考答案" }).first()).toBeVisible();
  await preview.locator("summary").filter({ hasText: "来源：TCP 讲义" }).first().click();
  await expect(preview.locator("blockquote").first()).toContainText("同步双方初始序列号");
  await page.screenshot({ path: "test-results/quiz-draft-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: "test-results/quiz-draft-mobile.png" });
  await preview.getByRole("link", { name: "查看原文 →" }).first().click();
  await expect(page).toHaveURL(/#materials\//);
  await page.getByRole("button", { name: "关闭", exact: true }).last().click();
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "模拟测验", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "试卷与在线测试", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "TCP 模拟试卷 查看草稿 →" }).click();
  const latest = page.getByRole("dialog", { name: "试卷草稿预览" });
  await expect(latest).toContainText("6 题 · 共 20 分");
  await latest.getByRole("button", { name: "编辑试卷" }).click();
  await latest.getByLabel("题干").first().fill("编辑后的 TCP 三次握手题干是什么？");
  await latest.getByRole("button", { name: "保存新草稿" }).click();
  await expect(latest).toContainText("1 道编辑后的题目待语义复核");
  await latest.getByRole("button", { name: "复核编辑内容" }).click();
  await expect(latest.getByRole("button", { name: "查看确认信息" })).toBeEnabled();
  await latest.getByRole("button", { name: "查看确认信息" }).click();
  await latest.getByRole("button", { name: "确认这份试卷" }).click();
  const formal = page.getByRole("dialog", { name: "试卷预览" });
  await expect(formal).toContainText("正式版本");
  await formal.getByRole("button", { name: "打印仅题目版" }).click();
  const print = page.getByRole("dialog", { name: "仅题目版" });
  await expect(print).toBeVisible();
  await expect(print.locator("iframe")).toBeVisible();
  await print.locator(".ant-modal-close").click();
  await formal.getByRole("button", { name: "开始测试" }).click();
  const attempt = page.getByRole("dialog", { name: "TCP 模拟试卷" });
  await expect(attempt).toContainText("作答中");
  await attempt.locator(".formal-options input[type=radio]").first().check();
  await attempt.getByRole("button", { name: "保存本题答案" }).first().click();
  await expect(attempt).toContainText("已保存");
  await page.reload();
  await expect(page.getByRole("heading", { name: "我的作答" })).toBeVisible();
  await page.getByRole("button", { name: /继续作答/ }).click();
  const resumed = page.getByRole("dialog", { name: "TCP 模拟试卷" });
  await expect(resumed.locator(".formal-options input[type=radio]").first()).toBeChecked();
  await resumed.getByRole("button", { name: "交卷" }).click();
  await expect(resumed).toContainText("已交卷");
  await expect(resumed).toContainText("本次得分");
  await expect(resumed).toContainText("来源：老师PPT");
  await resumed.getByLabel("继续追问").first().fill("这道题为什么要同步序列号？");
  await resumed.getByRole("button", { name: "发送追问" }).first().click();
  await expect(resumed).toContainText("请对照参考答案和解析");
  await page.screenshot({ path: "test-results/m3-result-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: "test-results/m3-result-mobile.png" });
});
