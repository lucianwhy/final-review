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
  await expect(page.getByRole("heading", { name: "试卷草稿", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "TCP 模拟试卷 查看草稿 →" }).click();
  await expect(page.getByRole("dialog", { name: "试卷草稿预览" })).toContainText("6 题 · 共 20 分");
});
