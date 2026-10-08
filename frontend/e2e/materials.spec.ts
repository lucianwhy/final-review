import { expect, test } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
});

test("course loading preserves a navigation event still in flight", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "导航恢复" } });
  let finishLoading!: () => void;
  const loading = new Promise<void>(resolve => { finishLoading = resolve; });
  await page.route("**/api/courses", async route => {
    await loading;
    await route.continue();
  });
  await page.goto("/");
  // Model the hash changing before its queued event is delivered, while courses load.
  await page.evaluate(() => { window.history.pushState(null, "", "#materials"); });
  finishLoading();
  await expect(page.getByRole("button", { name: /当前课程 导航恢复/ })).toBeVisible();
  await page.evaluate(() => { window.dispatchEvent(new HashChangeEvent("hashchange")); });
  await expect(page.getByLabel("文件", { exact: true })).toBeVisible();
});

test("themed material controls support keyboard selection and narrow screens", async ({ page, request }, testInfo) => {
  await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "Web服务端技术原理及应用" } });
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.goto("/");
  await page.getByRole("button", { name: "我的资料" }).first().click();
  const source = page.getByRole("combobox", { name: "来源类型", exact: true });
  await source.click();
  await expect(page.getByRole("option", { name: "平时作业", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".material-select-menu:visible")).not.toHaveClass(/-enter/);
  await page.screenshot({ path: testInfo.outputPath("material-controls-desktop.png") });
  await source.press("ArrowDown");
  await source.press("Enter");
  await source.click();
  await expect(page.getByRole("option", { name: "其他练习", exact: true })).toHaveAttribute("aria-selected", "true");
  await source.press("Escape");
  await expect(page.getByRole("option", { name: "其他练习", exact: true })).toBeHidden();
  await page.setViewportSize({ width: 390, height: 844 });
  await source.click();
  await expect(page.getByRole("option", { name: "其他练习", exact: true })).toBeVisible();
  await expect(page.locator(".material-select-menu:visible")).not.toHaveClass(/-enter/);
  await page.screenshot({ path: testInfo.outputPath("material-controls-mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("batch uploads five files with shared metadata and rejects six", async ({ page, request }) => {
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "批量资料" } })).json();
  await page.goto("/");
  await page.getByRole("button", { name: "我的资料" }).first().click();
  const files = Array.from({ length: 6 }, (_, index) => ({
    name: `batch-${index + 1}.md`, mimeType: "text/markdown", buffer: Buffer.from(`第 ${index + 1} 份测试资料`),
  }));
  await page.getByLabel("文件", { exact: true }).setInputFiles(files);
  await expect(page.getByText("每次最多上传 5 个文件，请重新选择。")).toBeVisible();
  await expect(page.getByRole("button", { name: "上传并处理" })).toBeDisabled();
  expect((await (await request.get(`http://127.0.0.1:8081/api/courses/${course.course_id}/documents`)).json()).items).toHaveLength(0);
  await page.getByLabel("标题", { exact: true }).fill("单份标题");
  await page.getByLabel("章节", { exact: true }).fill("第一章");
  await page.getByRole("combobox", { name: "来源类型", exact: true }).click();
  await page.getByRole("option", { name: "老师 PPT", exact: true }).click();
  await page.getByLabel("文件", { exact: true }).setInputFiles(files.slice(0, 5));
  await expect(page.getByText("已选择 5 / 5 个文件")).toBeVisible();
  await expect(page.getByLabel("标题", { exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.getByText("已接收 5 个文件，正在排队处理。")).toBeVisible();
  await expect(page.locator(".materials-upload-results .accepted")).toHaveCount(5);
  await expect(page.locator(".material-status.ready")).toHaveCount(5);
  const documents = (await (await request.get(`http://127.0.0.1:8081/api/courses/${course.course_id}/documents`)).json()).items;
  expect(documents).toHaveLength(5);
  expect(documents.map((item: { title: string }) => item.title).sort()).toEqual(files.slice(0, 5).map(item => item.name));
  expect(documents.every((item: { source_type: string; chapter: string }) => item.source_type === "teacher_ppt" && item.chapter === "第一章")).toBe(true);
});

test("batch continues after upload failure and retries only failed files", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "重试资料" } });
  await page.goto("/");
  await page.getByRole("button", { name: "我的资料" }).first().click();
  const uploads: { name: string; title: string; key: string | undefined }[] = [];
  await page.route("**/knowledge/upload", async route => {
    const payload = route.request().postDataBuffer()!.toString();
    const name = /filename="([^"]+)"/.exec(payload)![1];
    const title = /name="title"\r\n\r\n([^\r]+)/.exec(payload)![1];
    uploads.push({ name, title, key: route.request().headers()["idempotency-key"] });
    if (name === "retry.md" && uploads.filter(item => item.name === name).length === 1) {
      await route.fulfill({ status: 503, json: { detail: "临时上传失败" } });
    } else await route.continue();
  });
  await page.getByLabel("标题", { exact: true }).fill("不能用于批量");
  await page.getByLabel("文件", { exact: true }).setInputFiles(["retry.md", "success.md"].map(name => ({
    name, mimeType: "text/markdown", buffer: Buffer.from(name),
  })));
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.getByText("已接收 1 个文件，1 个上传失败。点击“重试失败文件”可重试。")).toBeVisible();
  await expect(page.locator(".materials-upload-results .failed")).toContainText("临时上传失败");
  await expect(page.getByText("已选择 1 / 5 个文件")).toBeVisible();
  await page.getByRole("button", { name: "重试失败文件" }).click();
  await expect(page.getByText("资料已接收，正在排队处理。")).toBeVisible();
  await expect(page.locator(".material-status.ready")).toHaveCount(2);
  expect(uploads.map(item => item.name)).toEqual(["retry.md", "success.md", "retry.md"]);
  expect(uploads[2].title).toBe("retry.md");
  expect(uploads[2].key).toBe(uploads[0].key);
});

test("student uploads course material and sees a readable failure", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("计算机网络");
  await page.getByRole("button", { name: "保存课程" }).click();
  await page.getByRole("button", { name: "我的资料" }).first().click();

  await expect(page.getByRole("heading", { name: "添加资料" })).toBeVisible();
  await page.getByRole("combobox", { name: "来源类型", exact: true }).click();
  await page.getByRole("option", { name: "其他练习", exact: true }).click();
  await page.getByLabel("文件").setInputFiles({ name: "review.md", mimeType: "text/markdown", buffer: Buffer.from("网络三次握手") });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.getByText("资料已接收，正在排队处理。")).toBeVisible();
  await expect(page.getByText("review.md", { exact: false }).first()).toBeVisible();
  await expect(page.locator(".material-status.ready")).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "我的资料" }).first().click();
  await expect(page.locator(".material-status.ready")).toBeVisible();

  await page.getByLabel("文件").setInputFiles({ name: "bad.png", mimeType: "image/png", buffer: Buffer.from("invalid image") });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.failed")).toBeVisible();
  await expect(page.getByText("图片损坏或格式与扩展名不符")).toBeVisible();
  await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
});

test("student edits, filters and deletes an unreferenced material", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("计算机网络");
  await page.getByRole("button", { name: "保存课程" }).click();
  await page.getByRole("button", { name: "我的资料" }).first().click();
  await page.getByLabel("章节", { exact: true }).fill("第一章");
  await page.getByLabel("文件").setInputFiles({ name: "lecture.md", mimeType: "text/markdown", buffer: Buffer.from("网络三次握手") });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.ready")).toBeVisible();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "编辑资料信息" });
  await dialog.getByLabel("标题").fill("网络讲义");
  await dialog.getByLabel("章节").fill("第二章");
  await dialog.getByRole("combobox", { name: "来源类型", exact: true }).click();
  await page.getByRole("option", { name: "老师 PPT", exact: true }).click();
  await dialog.getByRole("button", { name: "保存资料信息" }).click();
  await expect(page.getByText("网络讲义")).toBeVisible();
  await page.getByRole("combobox", { name: "筛选章节", exact: true }).click();
  await page.getByRole("option", { name: "第二章", exact: true }).click();
  await page.getByRole("combobox", { name: "筛选来源", exact: true }).click();
  await page.getByRole("option", { name: "平时作业", exact: true }).click();
  await expect(page.getByText("没有符合筛选条件的资料。")).toBeVisible();
  await page.getByRole("combobox", { name: "筛选来源", exact: true }).click();
  await page.getByRole("option", { name: "老师 PPT", exact: true }).click();
  await expect(page.getByText("网络讲义")).toBeVisible();
  await page.getByRole("button", { name: "删除", exact: true }).click();
  const deleteDialog = page.getByRole("dialog", { name: "删除“网络讲义”？" });
  await expect(deleteDialog.getByText("正式资产")).toBeVisible();
  await deleteDialog.getByRole("button", { name: "确认删除资料" }).click();
  await expect(page.getByText("资料已删除。")).toBeVisible();
  await expect(page.getByText("暂无资料。选择文件开始上传。")).toBeVisible();
});

test("referenced material requires an explicit source snapshot choice", async ({ page, request }) => {
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "数据库" } })).json();
  await page.goto("/#materials");
  await page.getByLabel("文件").setInputFiles({ name: "source.md", mimeType: "text/markdown", buffer: Buffer.from("事务与并发控制") });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.ready")).toBeVisible();
  const documents = await (await request.get(`http://127.0.0.1:8081/api/courses/${course.course_id}/documents`)).json();
  const material = documents.items[0];
  const created = await (await request.post(`http://127.0.0.1:8081/api/courses/${course.course_id}/assets`, {
    data: { asset_type: "note", title: "重点笔记", markdown: "# 重点", source_document_ids: [material.document_id] },
  })).json();
  await request.post(`http://127.0.0.1:8081/api/assets/${created.asset.asset_id}/revisions/${created.revision.revision_id}/confirm`);
  await page.getByRole("button", { name: "删除", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: /删除“/ });
  const confirmButton = dialog.getByRole("button", { name: "确认删除资料" });
  await expect(confirmButton).toBeDisabled();
  await expect(confirmButton).toHaveCSS("cursor", "not-allowed");
  const buttonBox = await confirmButton.boundingBox();
  expect(buttonBox).not.toBeNull();
  await page.mouse.click(buttonBox!.x + buttonBox!.width / 2, buttonBox!.y + buttonBox!.height / 2);
  await expect(dialog).toBeVisible();
  const beforeChoice = await (await request.get(`http://127.0.0.1:8081/api/courses/${course.course_id}/documents`)).json();
  expect(beforeChoice.items.some((item: { document_id: string }) => item.document_id === material.document_id)).toBe(true);
  await dialog.getByRole("checkbox", { name: "我了解影响，保留来源快照后删除" }).check();
  await dialog.getByRole("button", { name: "确认删除资料" }).click();
  await expect(page.getByText("资料已删除，正式内容的来源快照已保留。")).toBeVisible();
});

test("student opens a material excerpt and returns to its link", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("人工智能");
  await page.getByRole("button", { name: "保存课程" }).click();
  await page.getByRole("button", { name: "我的资料" }).first().click();
  await page.getByLabel("文件").setInputFiles({
    name: "lecture.md", mimeType: "text/markdown", buffer: Buffer.from("Source excerpt for review"),
  });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.ready")).toBeVisible();
  await page.getByRole("button", { name: "预览" }).click();
  const dialog = page.getByRole("dialog", { name: "整理后的资料" });
  await expect(dialog.getByText("lecture.md", { exact: false })).toBeVisible();
  await expect(dialog.locator(".material-reading-text")).toHaveText("Source excerpt for review");
  const chunkId = await dialog.locator("[data-chunk-id]").first().getAttribute("data-chunk-id");
  const documents = await page.evaluate(async () => {
    const courses = await (await fetch("/api/courses")).json();
    const courseId = courses.items[0].course_id;
    const documents = await (await fetch(`/api/courses/${courseId}/documents`)).json();
    return { courseId, documentId: documents.items[0].document_id };
  });
  await page.evaluate(hash => { window.location.hash = hash; }, `#materials/${documents.courseId}/${documents.documentId}/${chunkId}`);
  await expect(dialog.locator(".material-reading-section.cited")).toHaveCount(1);
  await page.reload();
  await expect(page.getByRole("dialog", { name: "整理后的资料" }).locator(".material-reading-text")).toHaveText("Source excerpt for review");
});

test("duplicate uploads report ready and failed material states truthfully", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "重复资料" } });
  await page.goto("/#materials");
  const ready = { name: "ready.md", mimeType: "text/markdown", buffer: Buffer.from("测试文字") };
  const broken = { name: "broken.png", mimeType: "image/png", buffer: Buffer.from("broken image") };
  await page.getByLabel("文件", { exact: true }).setInputFiles(ready);
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.ready")).toHaveCount(1);
  await page.getByLabel("文件", { exact: true }).setInputFiles(ready);
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".materials-upload-results .reused")).toContainText("已存在，资料可检索");
  await expect(page.getByRole("status")).not.toContainText("正在排队处理");
  await page.getByLabel("文件", { exact: true }).setInputFiles(broken);
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.failed")).toHaveCount(1);
  await page.getByLabel("文件", { exact: true }).setInputFiles(broken);
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".materials-upload-results .processing_failed")).toContainText("已有资料处理失败");
  await expect(page.getByRole("status")).toContainText("请在下方资料列表点击“重试”");
  await expect(page.getByRole("button", { name: "上传并处理" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "重试", exact: true })).toBeEnabled();
  await expect(page.locator(".materials-list .material-detail")).toHaveCount(2);
});

test("material polling continues when job and document snapshots differ", async ({ page, request }) => {
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "状态刷新" } })).json();
  let reads = 0;
  await page.route(`**/api/courses/${course.course_id}/documents`, route => route.fulfill({ json: {
    items: [{ document_id: "polling-document", title: "状态测试", source_type: "homework",
      parse_status: ++reads === 1 ? "running" : "ready" }],
  } }));
  await page.route(`**/api/courses/${course.course_id}/material-jobs`, route => route.fulfill({ json: {
    items: [{ job_id: "polling-job", document_id: "polling-document", status: "succeeded", attempts: 1 }],
  } }));
  await page.goto("/");
  await expect(page.getByRole("button", { name: /当前课程 状态刷新/ })).toBeVisible();
  await page.getByRole("button", { name: "我的资料" }).first().click();
  await expect(page.locator(".material-status.ready")).toHaveCount(1);
  expect(reads).toBeGreaterThan(1);
});

test("plain knowledge preview offers original slides and excluded page warnings", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "智能资料预览" } });
  await page.goto("/");
  await page.getByRole("button", { name: "我的资料" }).first().click();
  await page.getByLabel("文件", { exact: true }).setInputFiles({
    name: "plain.md", mimeType: "text/markdown", buffer: Buffer.from("# 请求流程\n\n**Servlet** 调用 `service()` 方法。"),
  });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.ready")).toBeVisible();
  await page.route("**/documents/*/chunks?include_content=true", async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.processing_pipeline = "visual-slides-v1";
    body.pages = [
      { position: 1, title: "请求流程", kind: "knowledge", quality: "verified", issues: [] },
      { position: 3, title: "目录", kind: "navigation", quality: "verified", issues: [] },
      { position: 2, title: "图示不清", kind: "knowledge", quality: "review_needed", issues: ["箭头不清楚"] },
    ];
    body.items[0].position = 1;
    body.items[0].position_kind = "slide";
    await route.fulfill({ json: body });
  });
  await page.route("**/documents/*/chunks/*", async route => {
    const response = await route.fetch();
    await route.fulfill({ json: { ...await response.json(), position: 1, position_kind: "slide" } });
  });
  await page.route("**/documents/*/pages/*", route => route.fulfill({ contentType: "image/png", body: Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5F8AAAAASUVORK5CYII=", "base64",
  ) }));
  await page.getByRole("button", { name: "预览", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "整理后的资料" });
  await expect(dialog.getByRole("heading", { name: "请求流程" })).toBeVisible();
  await expect(dialog.locator(".material-reading-text")).toHaveText("Servlet 调用 service() 方法。");
  await expect(dialog.getByText("箭头不清楚")).toHaveCount(0);
  await dialog.getByRole("button", { name: "查看原页 ↗" }).click();
  await expect(dialog.getByRole("img", { name: "第 1 页课件" })).toBeVisible();
  await dialog.getByRole("button", { name: "← 返回整理内容" }).click();
  await expect(dialog.locator(".material-reading-text")).toBeVisible();
  await dialog.getByRole("button", { name: "待审核 1" }).click();
  await expect(dialog.getByText("箭头不清楚")).toBeVisible();
  await expect(dialog.getByText("目录", { exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "查看原页 ↗" }).click();
  await expect(dialog.getByRole("img", { name: "第 2 页课件" })).toBeVisible();
  await dialog.getByRole("button", { name: "← 返回待审核" }).click();
  await expect(dialog.getByText("箭头不清楚")).toBeVisible();
  await dialog.getByRole("button", { name: "不参与检索 1" }).click();
  await expect(dialog.getByRole("heading", { name: "目录" })).toBeVisible();
  await expect(dialog.getByText("箭头不清楚")).toHaveCount(0);
  await dialog.getByRole("button", { name: "整理内容", exact: true }).click();
  await expect(dialog.locator(".material-reading-text")).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("reading-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(dialog.getByRole("button", { name: "整理内容", exact: true })).toBeVisible();
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("reading-mobile.png") });
});

test("partial page retains verified knowledge and lists only failed blocks for review", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "逐块核验预览" } });
  await page.goto("/");
  await page.getByRole("button", { name: "我的资料" }).first().click();
  await page.getByLabel("文件", { exact: true }).setInputFiles({
    name: "partial.md", mimeType: "text/markdown", buffer: Buffer.from("# 正确代码\n\nservice()"),
  });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.ready")).toBeVisible();
  await page.route("**/documents/*/chunks?include_content=true", async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.processing_pipeline = "visual-slides-v1";
    body.quality_status = "partial";
    body.pages = [{ position: 1, title: "混合知识页", kind: "knowledge", quality: "partial",
      issues: ["有一处无来源结论"], blocks: [
        { block_id: "b1", title: "正确代码", quality: "verified", issues: [] },
        { block_id: "b2", title: "无来源结论", quality: "review_needed", issues: ["图片未包含此事实"] },
      ] }];
    body.items[0].position = 1;
    body.items[0].position_kind = "slide";
    await route.fulfill({ json: body });
  });
  await page.getByRole("button", { name: "预览", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "整理后的资料" });
  await expect(dialog.locator(".material-reading-text")).toHaveText("service()");
  await expect(dialog.getByText("图片未包含此事实")).toHaveCount(0);
  await dialog.getByRole("button", { name: "待审核 1" }).click();
  await expect(dialog.getByText("正确代码 · 已通过", { exact: true })).toBeVisible();
  await expect(dialog.getByText("无来源结论 · 待核对", { exact: true })).toBeVisible();
  await expect(dialog.getByText("图片未包含此事实")).toBeVisible();
  await expect(dialog.getByText("部分通过 · 已核验知识保留，待核对知识不参与检索")).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("partial-blocks-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("partial-blocks-mobile.png") });
  await dialog.getByRole("button", { name: "整理内容", exact: true }).click();
  await expect(dialog.locator(".material-reading-text")).toHaveText("service()");
});

test("continuous reading scrolls while tabs and actions stay visible", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("长资料");
  await page.getByRole("button", { name: "保存课程" }).click();
  await page.getByRole("button", { name: "我的资料" }).first().click();
  const longText = Array.from({ length: 80 }, (_, index) => `第 ${index + 1} 段：${"资料片段内容。".repeat(30)}`).join("\n\n");
  await page.getByLabel("文件").setInputFiles({
    name: "long.md", mimeType: "text/markdown", buffer: Buffer.from(longText),
  });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await expect(page.locator(".material-status.ready")).toBeVisible();
  await page.getByRole("button", { name: "预览" }).click();
  const dialog = page.getByRole("dialog", { name: "整理后的资料" });
  const reading = dialog.locator(".material-preview-content");
  await expect(reading.locator(".material-reading-section").first()).toBeVisible();
  expect(await reading.locator(".material-reading-section").count()).toBeGreaterThan(1);
  const footer = dialog.locator(".wb-dialog-actions");
  const before = await footer.boundingBox();
  expect(before).not.toBeNull();
  expect(await reading.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  await reading.evaluate(element => { element.scrollTop = element.scrollHeight; });
  expect(await reading.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  await expect(dialog.getByRole("button", { name: "待审核 0" })).toBeVisible();
  await expect(footer.getByRole("link", { name: "下载原文件" })).toBeVisible();
  await expect(footer.getByRole("button", { name: "关闭" })).toBeVisible();
  expect((await footer.boundingBox())?.y).toBe(before!.y);
});
