import { expect, test } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
});

test("one course holds two exams and can be deleted and restored", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("高等数学");
  await page.getByRole("button", { name: "保存课程" }).click();
  await expect(page.getByRole("heading", { name: "高等数学" })).toBeVisible();

  for (const name of ["期中考试", "期末考试"]) {
    await page.getByRole("button", { name: "＋ 添加考试" }).click();
    await page.getByRole("dialog", { name: "添加考试" }).getByRole("textbox", { name: "考试名称" }).fill(name);
    await page.getByRole("button", { name: "保存考试" }).click();
    await expect(page.getByRole("heading", { name })).toBeVisible();
  }
  await expect(page.getByRole("heading", { name: "关联考试 2 场" })).toBeVisible();

  await page.getByRole("button", { name: "编辑课程" }).click();
  await page.getByRole("dialog", { name: "编辑课程" }).getByRole("textbox", { name: "学科" }).fill("数学");
  await page.getByRole("button", { name: "保存课程" }).click();
  await expect(page.getByRole("region", { name: "课程详情" }).getByText("数学", { exact: true })).toBeVisible();

  const finalExam = page.locator("article.wb-exam").filter({ has: page.getByRole("heading", { name: "期末考试" }) });
  await finalExam.getByRole("button", { name: "编辑" }).click();
  await page.getByRole("dialog", { name: "编辑考试" }).getByRole("textbox", { name: "老师重点 每行一条" }).fill("级数收敛");
  await page.getByRole("button", { name: "保存考试" }).click();
  await expect(finalExam).toContainText("级数收敛");
  await finalExam.getByRole("button", { name: "归档" }).click();
  await expect(finalExam).toContainText("已归档");

  await page.getByRole("button", { name: "归档课程" }).click();
  await expect(page.getByRole("button", { name: "恢复课程" })).toBeVisible();
  await page.getByRole("button", { name: "恢复课程" }).click();

  await page.getByRole("button", { name: "删除课程" }).click();
  const confirmation = page.getByRole("dialog", { name: "删除“高等数学”？" });
  await expect(confirmation).toContainText("考试");
  await expect(confirmation.locator(".wb-impact")).toContainText("2");
  await confirmation.getByRole("button", { name: "确认删除课程" }).click();
  await expect(page.getByRole("button", { name: "恢复课程" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("button", { name: "恢复课程" })).toBeVisible();
  await page.getByRole("button", { name: "恢复课程" }).click();
  await expect(page.getByRole("heading", { name: "关联考试 2 场" })).toBeVisible();
});
