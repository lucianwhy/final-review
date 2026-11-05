import { test, expect } from "@playwright/test";

test("graded result reaches filtered mistakes, manual mastery and persisted practice", async ({ page, request }) => {
  const base = "http://127.0.0.1:8081";
  await request.post(`${base}/test/reset`);
  const course = await (await request.post(`${base}/api/courses`, { data: { name: "M4 错题验收" } })).json();
  const courseId = course.course_id;
  const exam = await (await request.post(`${base}/api/courses/${courseId}/exams`, { data: { name: "期末考试" } })).json();
  const material = await (await request.post(`${base}/knowledge/ingest`, { data: {
    course_id: courseId, title: "TCP 讲义", chapter: "TCP", source_type: "teacher_ppt",
    markdown: "TCP 三次握手同步双方初始序列号并确认双方收发能力。",
  } })).json();
  const queued = await request.post(`${base}/api/courses/${courseId}/quiz-jobs`, { data: { quiz_input: {
    exam_id: exam.exam_id, scope_mode: "chapter", chapter: "TCP", blueprint: [
      { question_type: "choice", question_count: 1 }, { question_type: "short_answer", question_count: 1 },
    ], duration_mode: "untimed", difficulty: "basic", total_score: 20,
    include_imported_questions: false, source_document_ids: [material.document_id], allow_ai_supplement: false,
  } } });
  expect(queued.status()).toBe(202);
  const jobId = (await queued.json()).job_id;
  await expect.poll(async () => (await (await request.get(`${base}/api/courses/${courseId}/quiz-jobs/${jobId}`)).json()).status, { timeout: 20000 }).toBe("succeeded");
  const job = await (await request.get(`${base}/api/courses/${courseId}/quiz-jobs/${jobId}`)).json();
  const card = job.result;
  const revisionRoot = `${base}/api/courses/${courseId}/quizzes/${card.asset_id}/revisions/${card.revision_id}`;
  const preview = await (await request.post(`${revisionRoot}/confirm-preview`)).json();
  expect((await request.post(`${revisionRoot}/confirm`, { data: { confirmation_id: preview.confirmation_id } })).ok()).toBeTruthy();
  const started = await (await request.post(`${base}/api/courses/${courseId}/quizzes/${card.asset_id}/attempts`)).json();
  const attemptId = started.attempt.attempt_id;
  const choice = started.questions.find((item: { question_type: string }) => item.question_type === "choice");
  const attemptRoot = `${base}/api/courses/${courseId}/formal-attempts/${attemptId}`;
  expect((await request.put(`${attemptRoot}/answers`, { data: { question_revision_id: choice.question_revision_id, answer: 1, expected_version: 0 } })).ok()).toBeTruthy();
  const submitted = await request.post(`${attemptRoot}/submit`);
  expect(submitted.ok()).toBeTruthy();
  expect((await submitted.json()).attempt.grading_snapshot.items.filter((item: { correct: boolean }) => !item.correct)).toHaveLength(2);

  await page.goto("/#quiz");
  await expect(page.getByRole("heading", { name: "我的作答" })).toBeVisible();
  await page.getByRole("button", { name: /查看已交卷记录/ }).click();
  const result = page.getByRole("dialog", { name: "TCP 模拟试卷" });
  await expect(result.getByRole("button", { name: "查看我的错题" })).toBeVisible();
  await result.getByRole("button", { name: "查看我的错题" }).click();
  await expect(page).toHaveURL(/#mistakes$/);
  await expect(page.getByRole("heading", { name: "我的错题" })).toBeVisible();
  await expect(page.locator(".mistake-card")).toHaveCount(2);
  await page.getByLabel("考试筛选").selectOption(exam.exam_id);
  await page.getByLabel("章节筛选").selectOption("TCP");
  await page.getByLabel("题型筛选").selectOption("choice");
  await page.getByLabel("掌握状态筛选").selectOption("unmastered");
  await expect(page.locator(".mistake-card")).toHaveCount(1);
  const cardNode = page.locator(".mistake-card").first();
  await cardNode.getByRole("button", { name: "标记已掌握" }).click();
  await expect(page.locator(".mistake-card")).toHaveCount(0);
  await page.getByLabel("掌握状态筛选").selectOption("mastered");
  await expect(page.locator(".mistake-card")).toHaveCount(1);
  await page.locator(".mistake-card").getByRole("button", { name: "重新练习" }).click();
  const practice = page.getByRole("dialog", { name: "错题重练" });
  await expect(practice).toContainText("练习记录：mistake-practice-");
  await practice.locator('input[type="radio"]').first().check();
  await practice.getByRole("button", { name: "提交重练答案" }).click();
  await expect(practice).toContainText("重练正确");
  await practice.locator(".ant-modal-close").click();
  await page.reload();
  await expect(page.locator(".mistake-card")).toHaveCount(2);
  await page.getByLabel("题型筛选").selectOption("choice");
  await expect(page.locator(".mistake-card")).toHaveCount(1);
  await page.locator(".mistake-card").getByRole("button", { name: "练习记录" }).click();
  await page.locator(".mistake-history button").first().click();
  await expect(page.getByRole("dialog", { name: "错题重练" })).toContainText("重练正确");
  await page.getByRole("dialog", { name: "错题重练" }).locator(".ant-modal-close").click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("掌握状态筛选").selectOption("mastered");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await expect(page.locator(".mistake-card")).toHaveCount(1);
  await page.locator(".mistake-card").getByRole("button", { name: "标记未掌握" }).click();
  await expect(page.locator(".mistake-card")).toHaveCount(0);
  await page.getByLabel("掌握状态筛选").selectOption("unmastered");
  await expect(page.locator(".mistake-card")).toHaveCount(1);
});
