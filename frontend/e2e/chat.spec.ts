import { expect, test } from "@playwright/test";

test("ordinary chat retains source links after reload", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
    data: { name: "Web服务端" },
  })).json();
  const citation = { course_id: course.course_id, document_id: "http-doc", chunk_id: "http-chunk",
    title: "HTTP", file_name: "HTTP.pptx", position_kind: "slide", position: 3, citation_number: 1 };
  let sent = false;
  await page.route("**/api/chat/dispatch", route => {
    sent = true;
    return route.fulfill({ json: { kind: "chat", intent: "ask", model: "Review",
      reply: "HTTP 是无状态协议。[资料1]", citations: [citation] } });
  });
  await page.route("**/api/courses/*/conversations/*/messages", route => route.fulfill({ json: {
    items: sent ? [{ role: "user", content: "解释 HTTP" },
      { role: "assistant", content: "HTTP 是无状态协议。[资料1]", citations: [citation] }] : [],
  } }));
  await page.route("**/api/courses/*/documents/http-doc/chunks?include_content=true", route => route.fulfill({ json: {
    file_name: "HTTP.pptx", document_id: "http-doc", material_version_id: "v1",
    source_type: "teacher_ppt", items: [{ ...citation, locator_id: "http-chunk", excerpt: "HTTP 是无状态协议。" }],
  } }));
  await page.route("**/api/courses/*/documents/http-doc/chunks/http-chunk", route => route.fulfill({ json: {
    ...citation, content: "HTTP 是无状态协议。",
  } }));
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("解释 HTTP");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("link", { name: /HTTP.pptx · 第 3 张幻灯片/ })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("link", { name: /HTTP.pptx · 第 3 张幻灯片/ })).toBeVisible();
  await page.screenshot({ path: "test-results/course-chat-sources.png" });
  await page.getByRole("link", { name: /HTTP.pptx · 第 3 张幻灯片/ }).click();
  await expect(page.getByRole("dialog", { name: "整理后的资料" })).toContainText("HTTP 是无状态协议。");
});

test("chat quiz loads actual questions, reveals answers and survives reload", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
    data: { name: "计算机网络" },
  })).json();
  const quiz = { session_id: "chat-practice", question_count: 5 };
  let sent = false;
  await page.route("**/api/chat/dispatch", route => {
    sent = true;
    return route.fulfill({ json: { kind: "quiz", intent: "quiz", model: "Review",
      reply: "已生成五道练习题。", quiz } });
  });
  await page.route("**/api/courses/*/conversations/*/messages", route => route.fulfill({ json: {
    items: sent ? [{ role: "user", content: "随机出五道题" },
      { role: "assistant", content: "已生成五道练习题。", quiz }] : [],
  } }));
  await page.route("**/api/courses/*/chat-quizzes/chat-practice*", route => {
    const answers = route.request().url().includes("include_answers=true");
    return route.fulfill({ json: { questions: Array.from({ length: 5 }, (_, index) => ({
      id: `q${index}`, stem: `问题 ${index + 1}`, options: [], knowledge_point: "TCP", citations: [],
      ...(answers ? { reference_answer: `参考答案 ${index + 1}`, explanation: "答题解析", must_include: ["序列号"] } : {}),
    })) } });
  });
  await page.goto(`/#chat/${course.course_id}`);
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("随机出五道题");
  await page.getByRole("button", { name: "发送消息" }).click();
  const card = page.getByRole("region", { name: "课程练习题" });
  await expect(card.getByRole("heading", { name: /问题/ })).toHaveCount(5);
  expect((await card.boundingBox())!.width).toBeGreaterThan(450);
  await expect(page.getByText("参考答案 1", { exact: true })).toHaveCount(0);
  await card.getByRole("button", { name: "查看答案与解析" }).click();
  await expect(card.getByText("参考答案 1", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/course-chat-quiz.png" });
  await page.reload();
  await expect(card.getByRole("heading", { name: /问题/ })).toHaveCount(5);
  await expect(card.getByRole("button", { name: "查看答案与解析" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect((await card.boundingBox())!.width).toBeGreaterThan(200);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});

test("chat switches models and sends conversation history", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const calls: Record<string, unknown>[] = [];
  await page.route("**/api/chat/models", route => route.fulfill({
    json: { items: [{ id: "deepseek", label: "DeepSeek" }, { id: "gemini", label: "Gemini 3 Flash" }] },
  }));
  await page.route("**/api/chat/dispatch", async route => {
    const body = route.request().postDataJSON();
    calls.push(body);
    await route.fulfill({ json: { kind: "chat", intent: "ask", reply: calls.length === 1 ? "第一条回复" : "第二条回复", model: body.model_id === "gemini" ? "Gemini 3 Flash" : "DeepSeek" } });
  });
  await page.route("**/api/courses/*/conversations/*/messages", route => route.fulfill({ json: { items: calls.flatMap((call, index) => [
    { role: "user", content: call.message }, { role: "assistant", content: index === 0 ? "第一条回复" : "第二条回复" },
  ]) } }));

  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("高等数学");
  await page.getByRole("button", { name: "保存课程" }).click();
  await page.getByRole("button", { name: "AI 对话" }).click();
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("DeepSeek");
  await page.getByRole("textbox", { name: /输入你的问题/ }).focus();
  const focusStyle = await page.getByRole("textbox", { name: /输入你的问题/ }).evaluate(element => {
    const textarea = getComputedStyle(element);
    const composer = getComputedStyle(element.closest(".composer")!);
    return { outline: textarea.outlineStyle, shadow: composer.boxShadow, border: composer.borderColor };
  });
  expect(focusStyle).toEqual({ outline: "none", shadow: "none", border: "rgb(179, 198, 211)" });
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("第一问");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText("第一条回复")).toBeVisible();

  await page.getByRole("button", { name: "选择聊天模型" }).click();
  await expect(page.getByRole("listbox", { name: "聊天模型" })).toBeVisible();
  await page.getByRole("option", { name: "Gemini 3 Flash" }).click();
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Gemini 3 Flash");
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("第二问");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText("第二条回复")).toBeVisible();
  expect(calls.map(call => call.model_id)).toEqual(["deepseek", "gemini"]);
  expect(calls[1].conversation_id).toBe(calls[0].conversation_id);
  expect(calls[1].mode).toBeUndefined();
});

test("note request creates an openable sourced draft", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  await page.route("**/api/chat/models", route => route.fulfill({
    json: { items: [{ id: "review", label: "Review" }] },
  }));
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 新建课程" }).click();
  await page.getByRole("dialog", { name: "新建课程" }).getByRole("textbox", { name: "课程名称" }).fill("计算机网络");
  await page.getByRole("button", { name: "保存课程" }).click();
  await expect(page.getByRole("button", { name: /当前课程 计算机网络/ })).toBeVisible();
  const courses = await (await request.get("http://127.0.0.1:8081/api/courses")).json();
  const courseId = courses.items[0].course_id as string;
  await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "历史恢复对照课程" } });
  const ingested = await request.post("http://127.0.0.1:8081/knowledge/ingest", {
    data: { course_id: courseId, title: "TCP 讲义", chapter: "TCP", source_type: "teacher_ppt",
      markdown: "# 三次握手\n\nTCP 三次握手同步双方初始序列号并确认双方收发能力。" },
  });
  expect(ingested.ok()).toBeTruthy();
  await page.getByRole("button", { name: "AI 对话" }).click();
  await page.getByRole("textbox", { name: "输入你的问题" }).fill("生成笔记，整理成适合背诵的考点清单");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText("补充笔记要求")).toBeVisible();
  await expect(page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "生成笔记" })).toBeDisabled();
  await page.reload();
  await expect(page.getByText("补充笔记要求")).toBeVisible();
  await page.getByRole("button", { name: "选择资料" }).click();
  const picker = page.getByRole("dialog", { name: "选择生成依据" });
  await expect(picker).toBeVisible();
  await expect(picker).toHaveCSS("background-color", "rgb(255, 254, 250)");
  const resizeHandle = picker.getByRole("separator", { name: "上下拖动调整资料列表高度" });
  const list = picker.getByRole("group", { name: "当前课程资料" });
  const originalList = await list.boundingBox();
  const handleBox = await resizeHandle.boundingBox();
  await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y - 200, { steps: 10 });
  await page.mouse.up();
  expect((await list.boundingBox())!.height).toBeGreaterThan(originalList!.height + 100);
  await expect(picker.getByRole("button", { name: "取消", exact: true })).toBeVisible();
  await resizeHandle.press("ArrowDown");
  expect((await list.boundingBox())!.height).toBeLessThan(originalList!.height + 200);
  await picker.getByRole("checkbox", { name: /选择 TCP 讲义/ }).check();
  await picker.getByRole("button", { name: "确认选择" }).click();
  await expect(page.getByText("TCP 讲义", { exact: true })).toBeVisible();
  await page.getByPlaceholder(/侧重请求头和状态码/).fill("侧重 TCP 三次握手");
  await page.getByRole("dialog", { name: "补充笔记要求" }).screenshot({ path: "test-results/note-config.png" });
  await page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "生成笔记" }).click();
  await expect(page.getByRole("dialog", { name: "补充笔记要求" })).toHaveCount(0);
  await page.getByRole("button", { name: "我的资料" }).click();
  await page.getByRole("button", { name: "AI 对话" }).click();
  await expect(page.getByRole("link", { name: /打开笔记草稿/ })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("link", { name: /打开笔记草稿/ })).toBeVisible();
  await expect(page.getByText(/指定资料 TCP 讲义/)).toBeVisible();
  const draftLink = await page.getByRole("link", { name: /打开笔记草稿/ }).getAttribute("href");
  await page.getByRole("textbox", { name: "输入你的问题" }).fill("现在可以聊天吗");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText(/可以继续聊天/).last()).toBeVisible();
  await page.getByRole("button", { name: "生成笔记" }).click();
  await page.getByRole("textbox", { name: "对话名称" }).fill("TCP 笔记记录");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByRole("button", { name: "TCP 笔记记录" })).toBeVisible();
  await page.getByRole("button", { name: /当前课程 计算机网络/ }).click();
  const conversations = page.getByRole("dialog", { name: "课程与对话" });
  await conversations.getByRole("button", { name: "切换课程" }).click();
  await conversations.getByRole("option", { name: "历史恢复对照课程" }).click();
  await expect(conversations.getByRole("button", { name: /TCP 笔记记录/ })).toHaveCount(0);
  await conversations.getByRole("button", { name: "切换课程" }).click();
  await conversations.getByRole("option", { name: "计算机网络" }).click();
  await conversations.getByRole("button", { name: /TCP 笔记记录/ }).click();
  await expect(page.getByText(/生成笔记，整理成适合背诵的考点清单/)).toBeVisible();
  await expect(page.getByText(/指定资料 TCP 讲义/)).toBeVisible();
  await expect(page.getByText(/可以继续聊天/).last()).toBeVisible();
  await expect(page.getByRole("link", { name: /打开笔记草稿/ })).toHaveAttribute("href", draftLink!);
  const savedNotes = await (await request.get(`http://127.0.0.1:8081/api/courses/${courseId}/notes`)).json();
  expect(savedNotes.items).toHaveLength(1);
  await page.getByRole("link", { name: /打开笔记草稿/ }).click();
  const preview = page.getByRole("region", { name: "笔记详情" });
  await expect(preview).toContainText("三次握手");
  await expect(preview).toContainText("TCP 讲义");
  await expect(preview).toContainText("已选 1 份资料");
  await expect(preview).toContainText("读取 1/1 个片段");
  await preview.getByRole("button", { name: "查看来源与引用" }).click();
  const sources = page.getByRole("dialog", { name: "来源与引用" });
  await expect(sources).toContainText("TCP 讲义");
  await expect(sources.getByRole("link", { name: "查看原文 →" })).toBeVisible();
  await page.route("**/api/courses/*/material-jobs", route => route.fulfill({ status: 500, json: {} }));
  await sources.getByRole("link", { name: "查看原文 →" }).first().click();
  await expect(sources).toHaveCount(0);
  await expect(page.getByRole("dialog", { name: "整理后的资料" })).toContainText("TCP 三次握手同步双方初始序列号");
  await expect(page.getByRole("status")).toContainText("处理状态加载失败");
});

test("one source file links to every cited location", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
    data: { name: "Web 服务端" },
  })).json();
  const chunkIds = ["chunk-one", "chunk-two", "chunk-three"];
  const chunks = chunkIds.map((chunk_id, index) => ({
    chunk_id, locator_id: chunk_id, position_kind: "slide", position: index + 2,
    text_start: null, text_end: null, excerpt: `第 ${index + 2} 张幻灯片内容`,
  }));
  await page.route("**/api/assets/example/revisions/draft", route => route.fulfill({ json: {
    history: [{ revision_id: "draft", revision_no: 1, state: "draft", title: "HTML 笔记" }],
    asset: { asset_id: "example", title: "HTML 笔记", status: "draft", course_id: course.course_id },
    revision: { revision_id: "draft", revision_no: 1, state: "draft", title: "HTML 笔记", markdown: "", points: [{ point_id: "point-one", heading: "HTML 本质", content: "考点内容",
      provenance: "source", references: chunkIds.map(chunk_id => ({
        document_id: "file-one", chunk_id, file_name: "HTML.pptx", source_type: "teacher_ppt",
      })) }] }, references: [],
  } }));
  await page.route("**/api/courses/*/documents/file-one/chunks?include_content=true", route => route.fulfill({ json: {
    document_id: "file-one", material_version_id: "v1", file_name: "HTML.pptx",
    source_type: "teacher_ppt", items: chunks,
  } }));
  await page.route("**/api/courses/*/documents/file-one/chunks/*", route => {
    const id = route.request().url().split("/").pop()!;
    const chunk = chunks.find(item => item.chunk_id === id)!;
    return route.fulfill({ json: { ...chunk, content: chunk.excerpt } });
  });
  await page.goto("/#note/example/draft");
  const note = page.getByRole("region", { name: "笔记详情" });
  await note.getByRole("button", { name: "查看来源与引用" }).click();
  const links = page.getByRole("dialog", { name: "来源与引用" }).getByRole("link", { name: "查看原文 →" });
  await expect(links).toHaveCount(3);
  for (let index = 0; index < chunkIds.length; index++) {
    await expect(links.nth(index)).toHaveAttribute("href", new RegExp(chunkIds[index]));
  }
  await links.last().click();
  const source = page.getByRole("dialog", { name: "整理后的资料" });
  await expect(source).toContainText("本条内容引用 1 处位置");
  await expect(source.locator(".material-reading-section.cited")).toHaveCount(1);
  await expect(source.locator(".material-preview-content")).toContainText("第 4 张幻灯片内容");
});

test("natural note request enters the note flow", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
    data: { name: "Web 服务端" },
  })).json();
  await page.route("**/api/chat/models", route => route.fulfill({
    json: { items: [{ id: "review", label: "Review" }] },
  }));
  await page.goto(`/#chat/${course.course_id}`);
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("给我生成一份笔记");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("dialog", { name: "补充笔记要求" })).toBeVisible();
});

test("note material picker distinguishes duplicates and excludes failed files", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "Web 服务端" } })).json();
  await page.route("**/api/chat/models", route => route.fulfill({ json: { items: [{ id: "review", label: "Review" }] } }));
  await page.route("**/api/courses/*/documents", route => route.fulfill({ json: { items: [
    { document_id: "same-one", title: "HTTP 讲义", file_name: "HTTP 讲义.pptx", source_type: "teacher_ppt", chapter: "第一章", parse_status: "ready", uploaded_at: "2026-10-01T08:00:00Z" },
    { document_id: "same-two", title: "HTTP 讲义", file_name: "HTTP 讲义.pptx", source_type: "homework", chapter: "", parse_status: "ready", uploaded_at: "2026-10-02T08:00:00Z" },
    { document_id: "broken-three", title: "损坏课件", file_name: "损坏课件.pptx", source_type: "teacher_ppt", chapter: "", parse_status: "failed", parse_error: "文件无法解析" },
  ] } }));
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: /当前课程 Web 服务端/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("生成笔记");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.getByRole("button", { name: "选择资料" }).click();
  const picker = page.getByRole("dialog", { name: "选择生成依据" });
  await expect(picker.getByText("HTTP 讲义.pptx", { exact: true })).toHaveCount(2);
  await expect(picker.getByText(/编号 same-one/)).toBeVisible();
  await expect(picker.getByText(/编号 same-two/)).toBeVisible();
  await expect(picker.getByRole("checkbox", { name: /选择 损坏课件/ })).toBeDisabled();
  await picker.getByRole("button", { name: "选择全部可用资料" }).click();
  await expect(picker.getByRole("checkbox", { checked: true })).toHaveCount(2);
  await picker.screenshot({ path: "test-results/note-source-picker.png" });
  await picker.getByRole("button", { name: "确认选择" }).click();
  await expect(page.locator(".note-config-files span")).toHaveCount(2);
  await expect(page.locator(".note-config-files")).toContainText("same-one");
  await expect(page.locator(".note-config-files")).toContainText("same-two");
  await expect(page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "生成笔记" })).toBeEnabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "修改资料" }).click();
  await expect(page.getByRole("dialog", { name: "选择生成依据" })).toBeVisible();
  await page.getByRole("dialog", { name: "选择生成依据" }).screenshot({ path: "test-results/note-source-picker-mobile.png" });
});

test("note picker keeps five selections when a sixth is clicked", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
    data: { name: "五份资料上限" },
  })).json();
  await page.route("**/api/chat/models", route => route.fulfill({
    json: { items: [{ id: "review", label: "Review" }] },
  }));
  await page.route("**/api/courses/*/documents", route => route.fulfill({
    json: { items: Array.from({ length: 6 }, (_, index) => ({
      document_id: `selection-${index}`, title: `讲义 ${index}.pptx`,
      file_name: `讲义 ${index}.pptx`, source_type: "teacher_ppt", parse_status: "ready",
    })) },
  }));
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("生成笔记");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.getByRole("button", { name: "选择资料" }).click();
  const picker = page.getByRole("dialog", { name: "选择生成依据" });
  for (let index = 0; index < 5; index++) {
    await picker.getByRole("checkbox", { name: new RegExp(`选择 讲义 ${index}`) }).check();
  }
  await picker.getByRole("checkbox", { name: /选择 讲义 5/ }).click();
  await expect(picker.getByRole("alert")).toContainText("最多选择 5 份");
  await expect(picker.getByRole("checkbox", { checked: true })).toHaveCount(5);
  await picker.getByRole("checkbox", { name: /选择 讲义 0/ }).uncheck();
  await picker.getByRole("checkbox", { name: /选择 讲义 5/ }).check();
  await expect(picker.getByRole("checkbox", { checked: true })).toHaveCount(5);
});

test("note request keeps the form open when chapter does not match chosen material", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "Web 服务端" } })).json();
  await request.post("http://127.0.0.1:8081/knowledge/ingest", { data: {
    course_id: course.course_id, title: "3.HTTP协议.pptx", source_type: "teacher_ppt",
    markdown: "TCP 三次握手与 HTTP 状态码",
  } });
  await page.route("**/api/chat/models", route => route.fulfill({ json: { items: [{ id: "review", label: "Review" }] } }));
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: /当前课程 Web 服务端/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("生成笔记");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.getByRole("button", { name: "选择资料" }).click();
  await page.getByRole("dialog", { name: "选择生成依据" }).getByRole("checkbox", { name: /选择 3.HTTP协议.pptx/ }).check();
  await page.getByRole("button", { name: "确认选择" }).click();
  await page.getByPlaceholder(/侧重请求头和状态码/).fill("只写第一章");
  await page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "生成笔记" }).click();
  await expect(page.getByText(/所选资料中未找到“第一章”/).first()).toBeVisible();
  await expect(page.locator(".note-config-files")).toContainText("3.HTTP协议.pptx");
  await expect(page.getByRole("link", { name: /打开笔记草稿/ })).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".note-config-files")).toContainText("3.HTTP协议.pptx");
  await expect(page.getByPlaceholder(/侧重请求头和状态码/)).toHaveValue("只写第一章");
  await page.getByPlaceholder(/侧重请求头和状态码/).fill("侧重 HTTP 状态码");
  await page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "生成笔记" }).click();
  await expect(page.getByRole("link", { name: /打开笔记草稿/ })).toBeVisible();
});

test("note config keeps composer fixed and cancellation restores ordinary chat", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "计算机网络" } })).json();
  await page.route("**/api/chat/models", route => route.fulfill({ json: { items: [{ id: "review", label: "Review" }] } }));
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  const composer = page.locator(".composer");
  const before = await composer.boundingBox();
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("生成笔记");
  await page.getByRole("button", { name: "发送消息" }).click();
  const dialog = page.getByRole("dialog", { name: "补充笔记要求" });
  await expect(dialog).toBeVisible();
  const during = await composer.boundingBox();
  expect(during?.y).toBeCloseTo(before!.y, 0);
  await dialog.getByRole("button", { name: "考点清单" }).click();
  const options = dialog.getByRole("listbox", { name: "笔记类型" });
  await expect(options).toBeVisible();
  await expect(options).toHaveCSS("background-color", "rgb(255, 254, 250)");
  await dialog.screenshot({ path: "test-results/note-type-menu.png" });
  await options.getByRole("option", { name: /章节笔记/ }).focus();
  await page.keyboard.press("ArrowDown");
  await expect(options.getByRole("option", { name: /考点清单/ })).toBeFocused();
  await options.getByRole("option", { name: /问答卡片/ }).click();
  await expect(dialog.getByRole("button", { name: "问答卡片" })).toBeVisible();
  await dialog.screenshot({ path: "test-results/note-config-dialog.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(dialog).toBeVisible();
  await dialog.screenshot({ path: "test-results/note-config-dialog-mobile.png" });
  const oldBackend = (route: import("@playwright/test").Route) => route.fulfill({ status: 404, json: { detail: "Not Found" } });
  await page.route("**/agent/cancel-note*", oldBackend);
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog.getByText(/取消接口尚未在当前后端生效/)).toBeVisible();
  await page.unroute("**/agent/cancel-note*", oldBackend);
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText(/已取消笔记生成/)).toBeVisible();
  await page.reload();
  await expect(page.getByRole("dialog", { name: "补充笔记要求" })).toHaveCount(0);
  await expect(page.getByText(/已取消笔记生成/)).toBeVisible();
  await page.route("**/api/chat/dispatch", route => route.fulfill({ json: { kind: "chat", intent: "ask", reply: "可以继续聊天", model: "Review" } }));
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("现在可以聊天吗");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText(/可以继续聊天/).first()).toBeVisible();
});

test("course switcher restores ordinary chat and shows five recent conversations", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const first = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "数学" } })).json();
  const second = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "物理" } })).json();
  const records = Array.from({ length: 7 }, (_, index) => ({ conversation_id: `chat-${index}`, title: `数学对话 ${index}`, updated_at: new Date(2026, 0, 7 - index).toISOString() }));
  const calls: Record<string, string>[] = [];
  await page.route("**/api/chat/models", route => route.fulfill({ json: { items: [{ id: "review", label: "Review" }] } }));
  await page.route("**/api/courses/*/conversations", route => route.fulfill({ json: { items: route.request().url().includes(first.course_id) ? records : [{ conversation_id: "physics", title: "物理对话", updated_at: new Date().toISOString() }] } }));
  await page.route("**/api/courses/*/conversations/*/messages", route => route.fulfill({ json: { items: [{ role: "user", content: "旧问题" }, { role: "assistant", content: "旧回答" }, ...calls.map(call => ({ role: "user", content: call.message }))] } }));
  await page.route("**/api/chat/dispatch", route => { calls.push(route.request().postDataJSON()); return route.fulfill({ json: { kind: "chat", intent: "ask", reply: "继续回答", model: "Review" } }); });
  await page.goto(`/#chat/${first.course_id}/chat-0`);
  await expect(page.getByRole("button", { name: /课程管理/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /课程与考试/ })).toHaveCount(0);
  await expect(page.getByText("旧回答")).toBeVisible();
  await page.getByRole("button", { name: /当前课程 数学/ }).click();
  const panel = page.getByRole("dialog", { name: "课程与对话" });
  await expect(panel).toHaveCSS("position", "fixed");
  await expect(panel).toHaveCSS("z-index", "10000");
  expect(await panel.evaluate(element => element.parentElement === document.body)).toBeTruthy();
  await expect(panel.locator(".course-conversations button")).toHaveCount(5);
  await panel.getByRole("button", { name: "查看更多" }).click();
  await expect(panel.locator(".course-conversations button")).toHaveCount(7);
  await panel.getByRole("button", { name: /数学对话 6/ }).click();
  await expect(page).toHaveURL(new RegExp(`#chat/${first.course_id}/chat-6$`));
  await page.reload();
  await expect(page.getByText("旧回答")).toBeVisible();
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("接着说");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText("继续回答")).toBeVisible();
  expect(calls[0].conversation_id).toBe("chat-6");
  await page.getByRole("button", { name: /当前课程 数学/ }).click();
  await panel.getByRole("button", { name: "切换课程" }).click();
  await panel.getByRole("option", { name: "物理" }).click();
  await expect(page.getByRole("button", { name: /当前课程 物理/ })).toBeVisible();
  await expect(panel.getByRole("button", { name: /物理对话/ })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: /当前课程 物理/ }).click();
  await expect(page.getByRole("dialog", { name: "课程与对话" })).toBeVisible();
});

test("conversation can be renamed from chat title and history context menu", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", { data: { name: "高等数学" } })).json();
  const conversation = await (await request.post(`http://127.0.0.1:8081/api/courses/${course.course_id}/conversations`, { data: { title: "未命名对话" } })).json();
  await page.route("**/api/chat/models", route => route.fulfill({ json: { items: [{ id: "review", label: "Review" }] } }));
  await page.goto(`/#chat/${course.course_id}/${conversation.conversation_id}`);
  await page.getByRole("button", { name: "未命名对话" }).click();
  await page.getByRole("textbox", { name: "对话名称" }).fill("微积分复习");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByRole("button", { name: "微积分复习" })).toBeVisible();
  await page.getByRole("button", { name: /当前课程 高等数学/ }).click();
  const panel = page.getByRole("dialog", { name: "课程与对话" });
  await panel.getByRole("button", { name: /微积分复习/ }).click({ button: "right" });
  await page.getByRole("menuitem", { name: "重命名对话" }).click();
  await panel.getByRole("textbox", { name: "对话名称" }).fill("期末重点");
  await panel.getByRole("button", { name: "保存" }).click();
  await expect(panel.getByRole("button", { name: /期末重点/ })).toBeVisible();
  await panel.getByRole("button", { name: "关闭切换面板" }).click();
  await expect(page.getByRole("button", { name: "期末重点" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "期末重点" })).toBeVisible();
});

for (const cancelBy of ["button", "message"] as const) {
  test(`queued note can be cancelled by ${cancelBy}`, async ({ page, request }) => {
    await request.post("http://127.0.0.1:8081/test/reset");
    const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
      data: { name: "计算机网络" },
    })).json();
    await request.post("http://127.0.0.1:8081/knowledge/ingest", { data: {
      course_id: course.course_id, title: "TCP 讲义", source_type: "teacher_ppt",
      markdown: "TCP 三次握手同步初始序列号。",
    } });
    await page.route("**/api/chat/models", route => route.fulfill({
      json: { items: [{ id: "review", label: "Review" }] },
    }));
    await page.goto(`/#chat/${course.course_id}`);
    await page.getByRole("textbox", { name: /输入你的问题/ }).fill("生成笔记");
    await page.getByRole("button", { name: "发送消息" }).click();
    await page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "选择资料" }).click();
    const picker = page.getByRole("dialog", { name: "选择生成依据" });
    await picker.getByRole("checkbox", { name: /选择 TCP 讲义/ }).check();
    await picker.getByRole("button", { name: "确认选择" }).click();
    await page.route("**/agent/queue-note*", route => route.fulfill({
      json: { job_id: "queued-test", status: "queued" },
    }));
    let cancellationCalls = 0;
    await page.route("**/agent/cancel-note*", route => {
      cancellationCalls += 1;
      return route.fulfill({ json: { cancelled: true } });
    });
    await page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "生成笔记" }).click();
    await expect(page.getByRole("button", { name: "取消生成" })).toBeVisible();
    if (cancelBy === "button") {
      await page.getByRole("status").screenshot({ path: "test-results/note-cancel-status.png" });
      await page.getByRole("button", { name: "取消生成" }).click();
    } else {
      await page.getByRole("textbox", { name: /输入你的问题/ }).fill("取消生成");
      await page.getByRole("button", { name: "发送消息" }).click();
    }
    await expect(page.getByText(/已取消笔记生成/)).toBeVisible();
    await expect(page.getByRole("button", { name: "取消生成" })).toHaveCount(0);
    expect(cancellationCalls).toBe(1);
  });
}

test("note request prefills teacher emphasis and uploads an external source", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
    data: { name: "Web 服务端" },
  })).json();
  await page.route("**/api/chat/models", route => route.fulfill({
    json: { items: [{ id: "review", label: "Review" }], note_model: "qwen-plus" },
  }));
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  await expect(page.getByText("今天想从哪里开始？")).toBeVisible();
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill(
    "给我生成可以背诵的笔记，老师说第2章和第3章重点",
  );
  await page.getByRole("button", { name: "发送消息" }).click();
  const dialog = page.getByRole("dialog", { name: "补充笔记要求" });
  await expect(dialog.getByRole("textbox", { name: /写作要求/ })).toHaveValue(
    "重点处理第二章和第三章，适合背诵",
  );
  await dialog.getByRole("textbox", { name: /写作要求/ }).fill("重点处理第二章和第三章，按简答题整理");
  await page.reload();
  await expect(dialog.getByRole("textbox", { name: /写作要求/ })).toHaveValue(
    "重点处理第二章和第三章，按简答题整理",
  );
  await expect(dialog).toContainText("qwen-plus");
  await dialog.getByRole("button", { name: "选择资料" }).click();
  const picker = page.getByRole("dialog", { name: "选择生成依据" });
  await picker.getByLabel("上传笔记资料文件").setInputFiles({
    name: "chapter.md", mimeType: "text/markdown", buffer: Buffer.from("第二章 HTTP 基础知识。第三章服务器环境。"),
  });
  await picker.getByRole("button", { name: "上传并处理" }).click();
  await expect(picker.getByText("外部上传")).toBeVisible();
  await expect(picker.getByRole("button", { name: "确认选择" })).toBeEnabled();
  await picker.screenshot({ path: "test-results/note-upload-picker.png" });
  await picker.getByRole("button", { name: "确认选择" }).click();
  await expect(dialog.getByText("chapter.md")).toBeVisible();
  await dialog.getByRole("button", { name: "生成笔记" }).click();
  await expect(page.getByRole("link", { name: /打开笔记草稿/ })).toBeVisible();
});

test("pending note upload blocks generation until removed", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
    data: { name: "Web 服务端" },
  })).json();
  await page.route("**/api/chat/models", route => route.fulfill({
    json: { items: [{ id: "review", label: "Review" }] },
  }));
  let uploaded = false;
  await page.route("**/api/courses/*/documents", route => route.fulfill({ json: {
    items: uploaded ? [{ document_id: "pending-doc", title: "pending.md",
      file_name: "pending.md", source_type: "external_upload", parse_status: "queued" }] : [],
  } }));
  await page.route("**/api/courses/*/material-jobs", route => route.fulfill({
    json: { items: uploaded ? [{ job_id: "pending-job", document_id: "pending-doc", status: "queued" }] : [] },
  }));
  await page.route("**/knowledge/upload", route => {
    uploaded = true;
    return route.fulfill({ status: 202, json: {
      document_id: "pending-doc", job_id: "pending-job", status: "queued", reused: false,
    } });
  });
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("生成笔记");
  await page.getByRole("button", { name: "发送消息" }).click();
  const dialog = page.getByRole("dialog", { name: "补充笔记要求" });
  await dialog.getByRole("button", { name: "选择资料" }).click();
  const picker = page.getByRole("dialog", { name: "选择生成依据" });
  await picker.getByLabel("上传笔记资料文件").setInputFiles({
    name: "pending.md", mimeType: "text/markdown", buffer: Buffer.from("等待处理"),
  });
  await picker.getByRole("button", { name: "上传并处理" }).click();
  await expect(picker.getByRole("button", { name: "确认选择" })).toBeDisabled();
  await picker.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "生成笔记" })).toBeDisabled();
  await dialog.getByRole("button", { name: "修改资料" }).click();
  await picker.getByRole("checkbox", { name: /选择 pending.md/ }).uncheck();
  await picker.getByRole("button", { name: "移除已选资料" }).click();
  await expect(dialog.getByRole("button", { name: "生成笔记" })).toBeDisabled();
});

test("note upload zone accepts a dropped file", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
    data: { name: "Web 服务端" },
  })).json();
  await page.route("**/api/chat/models", route => route.fulfill({
    json: { items: [{ id: "review", label: "Review" }] },
  }));
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  await page.getByRole("textbox", { name: /输入你的问题/ }).fill("生成笔记");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.getByRole("dialog", { name: "补充笔记要求" }).getByRole("button", { name: "选择资料" }).click();
  const picker = page.getByRole("dialog", { name: "选择生成依据" });
  await picker.locator(".note-upload-drop").evaluate(element => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["拖放资料"], "dropped.md", { type: "text/markdown" }));
    element.dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer }));
  });
  await expect(picker.getByRole("textbox", { name: "标题" })).toHaveValue("dropped.md");
});

test("chat composer accepts multiple dropped files and sends them with a question", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
    data: { name: "拖放附件课程" },
  })).json();
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  const composer = page.locator(".composer");
  await composer.locator("textarea").evaluate(element => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["HTTP 是无状态协议。"], "http.md", { type: "text/markdown" }));
    transfer.items.add(new File(["CSS 用于网页样式。"], "css.md", { type: "text/markdown" }));
    element.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: transfer }));
  });
  await expect(composer.getByText("松开以添加到当前课程资料")).toBeVisible();
  await composer.locator("textarea").evaluate(element => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["HTTP 是无状态协议。"], "http.md", { type: "text/markdown" }));
    transfer.items.add(new File(["CSS 用于网页样式。"], "css.md", { type: "text/markdown" }));
    element.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: transfer }));
  });
  await expect(composer.getByText("http.md", { exact: true })).toBeVisible();
  await expect(composer.getByText("css.md", { exact: true })).toBeVisible();
  await expect(composer.getByText("可检索", { exact: true })).toHaveCount(2);
  await composer.locator("textarea").fill("解释这两份资料");
  const dispatch = page.waitForRequest("**/api/chat/dispatch");
  await page.getByRole("button", { name: "发送消息" }).click();
  expect((await dispatch).postDataJSON().attachment_document_ids).toHaveLength(2);
  await expect(page.getByText(/可以继续聊天/).first()).toBeVisible();
  await expect(page.locator(".chat-error")).toHaveCount(0);
});

test("chat composer blocks pending attachments and lets the user remove them", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8081/test/reset");
  const course = await (await request.post("http://127.0.0.1:8081/api/courses", {
    data: { name: "等待处理课程" },
  })).json();
  await page.route("**/knowledge/upload", route => route.fulfill({ status: 202, json: {
    document_id: "pending-composer", status: "queued",
  } }));
  await page.goto(`/#chat/${course.course_id}`);
  await expect(page.getByRole("button", { name: "选择聊天模型" })).toContainText("Review");
  await page.getByLabel("选择聊天资料文件").setInputFiles({
    name: "pending.md", mimeType: "text/markdown", buffer: Buffer.from("等待处理"),
  });
  await page.locator(".composer textarea").fill("解释这个文件");
  await expect(page.locator(".composer").getByText("处理中", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "发送消息" })).toBeDisabled();
  await page.getByRole("button", { name: "移除 pending.md" }).click();
  await expect(page.getByRole("button", { name: "发送消息" })).toBeEnabled();
});
