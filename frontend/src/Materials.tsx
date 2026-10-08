import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Course } from "./workbench-api";
import MaterialSelect from "./MaterialSelect";
import "./materials.css";

type SourceType = "past_exam" | "teacher_ppt" | "homework" | "other_practice" | "external_upload" | "crash_course" | "ai_supplement";
type Material = { document_id: string; title: string; file_name?: string; source_type: SourceType; chapter?: string; parse_status: string; parse_error?: string; quality_status?: string; processing_pipeline?: string; updated_at?: string | null };
type MaterialJob = { job_id: string; document_id: string; status: "queued" | "running" | "succeeded" | "failed"; stage?: string | null; error_message?: string | null; attempts: number };
type UploadSelection = { file: File; key: string; metadata?: { courseId: string; title: string; chapter: string; sourceType: SourceType } };
type UploadResult = { key: string; name: string; status: "waiting" | "uploading" | "accepted" | "reused" | "processing_failed" | "failed"; error?: string };
const MAX_UPLOAD_FILES = 5;
type DeletePreview = { blocking_references: number; affected_assets: number; confirmation_id: string; expires_at: string };
type SourceChunk = { chunk_id: string; locator_id: string; position_kind: string; position: number | null; text_start: number | null; text_end: number | null; excerpt: string; content?: string; historical?: boolean };
type SourceBlock = { block_id: string; title: string; quality: "verified" | "review_needed"; issues: string[]; evidence?: { source_id: string; origin: "native" | "note" | "image"; quote: string }[] };
type SourcePage = { position: number; title: string; kind: "knowledge" | "navigation"; quality: "verified" | "partial" | "review_needed"; issues: string[]; blocks?: SourceBlock[] };
type SourcePreview = { document_id: string; material_version_id: string; file_name: string; source_type: SourceType; items: SourceChunk[]; processing_pipeline?: string; quality_status?: string; pages?: SourcePage[] };
const stageLabel: Record<string, string> = { parse: "解析", convert: "转换 PDF", ocr: "文字识别", clean: "清洗", index: "建立索引" };

const sourceOptions: [SourceType, string][] = [
  ["past_exam", "历年真题"], ["teacher_ppt", "老师 PPT"], ["homework", "平时作业"],
  ["other_practice", "其他练习"], ["external_upload", "外部上传"], ["crash_course", "速成课"], ["ai_supplement", "AI 补充"],
];
const sourceLabel = Object.fromEntries(sourceOptions);
const sourceSelectOptions = sourceOptions.map(([value, label]) => ({ value, label }));
const documentPath = (courseId: string, documentId: string) => `/api/courses/${encodeURIComponent(courseId)}/documents/${encodeURIComponent(documentId)}`;

export default function Materials({ selectedCourse }: { selectedCourse: Course | null }) {
  const [courses, setCourses] = useState<Course[]>([]);
  const [courseId, setCourseId] = useState("");
  const [items, setItems] = useState<Material[]>([]);
  const [jobs, setJobs] = useState<MaterialJob[]>([]);
  const [files, setFiles] = useState<UploadSelection[]>([]);
  const [uploadResults, setUploadResults] = useState<UploadResult[]>([]);
  const [title, setTitle] = useState("");
  const [chapter, setChapter] = useState("");
  const [sourceType, setSourceType] = useState<SourceType>("homework");
  const [filterChapter, setFilterChapter] = useState("");
  const [filterSource, setFilterSource] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [editing, setEditing] = useState<Material | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editChapter, setEditChapter] = useState("");
  const [editSource, setEditSource] = useState<SourceType>("homework");
  const [deleting, setDeleting] = useState<{ item: Material; preview: DeletePreview } | null>(null);
  const [sourcePreview, setSourcePreview] = useState<SourcePreview | null>(null);
  const [previewTab, setPreviewTab] = useState<"content" | "excluded" | "review">("content");
  const readingRef = useRef<HTMLDivElement>(null);
  const [originalPage, setOriginalPage] = useState<number | null>(null);
  const [referencedChunkIds, setReferencedChunkIds] = useState<string[]>([]);
  const [previewError, setPreviewError] = useState("");
  const [retainSnapshot, setRetainSnapshot] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const openedHash = useRef("");
  const [sourceHash, setSourceHash] = useState(window.location.hash);
  const hasActiveJobs = jobs.some(job => job.status === "queued" || job.status === "running")
    || items.some(item => item.parse_status === "queued" || item.parse_status === "running");
  const chapters = [...new Set(items.map(item => item.chapter ?? ""))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const visible = items.filter(item => (!filterChapter || (filterChapter === "__empty__" ? !item.chapter : item.chapter === filterChapter))
    && (!filterSource || item.source_type === filterSource)
    && (!filterStatus || item.parse_status === filterStatus));
  const citedChunks = sourcePreview?.items.filter(chunk => referencedChunkIds.includes(chunk.chunk_id)) ?? [];
  const previewChunks = sourcePreview?.items ?? [];
  const excludedPages = sourcePreview?.pages?.filter(page => page.kind === "navigation" && page.quality !== "review_needed") ?? [];
  const reviewPages = sourcePreview?.pages?.filter(page => page.quality !== "verified") ?? [];

  async function refresh() {
    const [documents, currentJobs] = await Promise.all([
      api<{ items: Material[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`),
      api<{ items: MaterialJob[] }>(`/api/courses/${encodeURIComponent(courseId)}/material-jobs`),
    ]);
    setItems(documents.items); setJobs(currentJobs.items);
  }

  useEffect(() => {
    const onHashChange = () => setSourceHash(window.location.hash);
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    if (!sourceHash.startsWith("#materials/")) return;
    const linkedCourse = decodeURIComponent(sourceHash.split("/")[1] || "");
    if (linkedCourse && linkedCourse !== courseId) setCourseId(linkedCourse);
  }, [sourceHash, courseId]);

  useEffect(() => {
    api<{ items: Course[] }>("/api/courses")
      .then(data => {
        const available = data.items.filter(item => item.status !== "deleted");
        setCourses(available);
        const linkedCourse = window.location.hash.startsWith("#materials/") ? decodeURIComponent(window.location.hash.split("/")[1] || "") : "";
        setCourseId(available.find(item => item.course_id === linkedCourse)?.course_id
          ?? available.find(item => item.course_id === selectedCourse?.course_id)?.course_id ?? available[0]?.course_id ?? "");
      })
      .catch(error => setMessage(error instanceof Error ? error.message : "课程加载失败"));
  }, [selectedCourse?.course_id]);

  useEffect(() => {
    if (!courseId) { setItems([]); return; }
    let active = true;
    async function refresh() {
      const [documents, currentJobs] = await Promise.allSettled([
        api<{ items: Material[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`),
        api<{ items: MaterialJob[] }>(`/api/courses/${encodeURIComponent(courseId)}/material-jobs`),
      ]);
      if (!active) return;
      if (documents.status === "fulfilled") setItems(documents.value.items);
      if (currentJobs.status === "fulfilled") setJobs(currentJobs.value.items);
      if (documents.status === "rejected") setMessage(`资料列表加载失败：${documents.reason instanceof Error ? documents.reason.message : "请稍后重试"}`);
      else if (currentJobs.status === "rejected") setMessage(`处理状态加载失败：${currentJobs.reason instanceof Error ? currentJobs.reason.message : "请稍后重试"}`);
    }
    void refresh();
    const timer = hasActiveJobs ? window.setInterval(() => { void refresh(); }, 2000) : undefined;
    return () => { active = false; window.clearInterval(timer); };
  }, [courseId, hasActiveJobs]);

  useEffect(() => {
    const hash = sourceHash;
    if (!hash.startsWith("#materials/") || hash === openedHash.current) return;
    const [, encodedCourse, encodedDocument, encodedChunks] = hash.slice(1).split("/");
    const linkedCourse = decodeURIComponent(encodedCourse || "");
    const linkedDocument = decodeURIComponent(encodedDocument || "");
    const linkedChunkIds = [...new Set((encodedChunks || "").split(",").filter(Boolean).map(decodeURIComponent))];
    if (linkedCourse !== courseId || !linkedDocument || !linkedChunkIds.length) return;
    openedHash.current = hash;
    const base = documentPath(courseId, linkedDocument);
    void api<SourcePreview>(base + "/chunks?include_content=true").then(async preview => {
      const missing = linkedChunkIds.filter(id => !preview.items.some(chunk => chunk.chunk_id === id));
      const recovered = await Promise.all(missing.map(id => api<SourceChunk>(base + `/chunks/${encodeURIComponent(id)}`).catch(() => null)));
      preview.items.push(...recovered.filter((chunk): chunk is SourceChunk => chunk !== null));
      const availableIds = linkedChunkIds.filter(id => preview.items.some(chunk => chunk.chunk_id === id));
      setPreviewError(""); setOriginalPage(null); setPreviewTab("content");
      setSourcePreview(preview);
      setReferencedChunkIds(availableIds);
      if (availableIds.length < linkedChunkIds.length) setPreviewError("部分引用片段已失效，以下显示仍可查看的位置。");
      if (!availableIds.length) return;

    }).catch(error => setMessage(`来源片段加载失败：${error instanceof Error ? error.message : "请稍后重试"}`));
  }, [courseId, sourceHash]);

  async function retry(jobId: string) {
    try {
      const response = await fetch(`/api/courses/${encodeURIComponent(courseId)}/material-jobs/${jobId}/retry`, { method: "POST", credentials: "same-origin" });
      if (!response.ok) throw new Error("重试失败，请刷新后再试");
      setMessage("已重新排队处理。");
      setJobs(current => current.map(job => job.job_id === jobId ? { ...job, status: "queued", stage: null, error_message: null } : job));
    } catch (error) { setMessage(error instanceof Error ? error.message : "重试失败"); }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!courseId || !files.length || busy) return;
    if (files.length > MAX_UPLOAD_FILES) { setMessage("每次最多上传 5 个文件，请重新选择。"); return; }
    const batch = files.map(item => ({ ...item, metadata: item.metadata ?? {
      courseId, title: files.length > 1 ? item.file.name : title.trim() || item.file.name,
      chapter: chapter.trim(), sourceType,
    } }));
    setBusy(true);
    setMessage("");
    setUploadResults(batch.map(item => ({ key: item.key, name: item.file.name, status: "waiting" })));
    const failed: UploadSelection[] = [];
    let accepted = 0;
    let reusedReady = 0;
    let processingFailed = 0;
    function updateResult(key: string, status: UploadResult["status"], error?: string) {
      setUploadResults(current => current.map(item => item.key === key ? { ...item, status, error } : item));
    }
    try {
      for (const item of batch) {
        updateResult(item.key, "uploading");
        const form = new FormData();
        form.append("course_id", item.metadata.courseId);
        form.append("title", item.metadata.title);
        form.append("chapter", item.metadata.chapter);
        form.append("source_type", item.metadata.sourceType);
        form.append("file", item.file);
        try {
          const response = await fetch("/knowledge/upload", {
            method: "POST", credentials: "same-origin", body: form,
            headers: { "Idempotency-Key": item.key },
          });
          const body = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "上传失败");
          if (body.status === "failed") {
            processingFailed += 1;
            updateResult(item.key, "processing_failed");
            continue;
          }
          accepted += 1;
          const ready = body.status === "ready" || body.status === "succeeded";
          if (ready) reusedReady += 1;
          updateResult(item.key, ready ? "reused" : "accepted");
        } catch (error) {
          failed.push(item);
          updateResult(item.key, "failed", error instanceof Error ? error.message : "上传失败");
        }
      }
      setFiles(failed);
      setMessage(failed.length
        ? `已接收 ${accepted} 个文件，${failed.length} 个上传失败。点击“重试失败文件”可重试。`
        : processingFailed ? `${processingFailed} 份已有资料处理失败，请在下方资料列表点击“重试”。其他文件的结果见上传列表。`
        : reusedReady ? `上传完成，其中 ${reusedReady} 份资料已可检索并直接复用。其他文件的处理状态见下方资料列表。`
        : batch.length === 1 ? "资料已接收，正在排队处理。" : `已接收 ${accepted} 个文件，正在排队处理。`);
      if (!failed.length) { setTitle(""); setChapter(""); }
      const input = document.getElementById("material-file") as HTMLInputElement | null;
      if (input) input.value = "";
    } finally {
      const [data, currentJobs] = await Promise.all([
        api<{ items: Material[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`).catch(() => null),
        api<{ items: MaterialJob[] }>(`/api/courses/${encodeURIComponent(courseId)}/material-jobs`).catch(() => null),
      ]);
      if (data) setItems(data.items);
      if (currentJobs) setJobs(currentJobs.items);
      setBusy(false);
    }
  }

  function openEdit(item: Material) {
    setEditing(item); setEditTitle(item.title); setEditChapter(item.chapter ?? "");
    setEditSource(item.source_type); setDialogError("");
  }

  async function saveEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing || busy) return;
    setBusy(true); setDialogError("");
    try {
      await api(documentPath(courseId, editing.document_id), "PATCH", {
        title: editTitle.trim(), chapter: editChapter.trim(), source_type: editSource,
        expected_updated_at: editing.updated_at ?? null,
      });
      setEditing(null); setMessage("资料信息已更新。"); await refresh();
    } catch (error) { setDialogError(error instanceof Error ? error.message : "保存失败"); }
    finally { setBusy(false); }
  }

  async function openDelete(item: Material) {
    setBusy(true); setDialogError("");
    try {
      const preview = await api<DeletePreview>(documentPath(courseId, item.document_id) + "/deletion-preview", "POST");
      setDeleting({ item, preview }); setRetainSnapshot(false);
    } catch (error) { setMessage(error instanceof Error ? error.message : "删除预览失败"); }
    finally { setBusy(false); }
  }

  async function openSource(item: Material) {
    setOriginalPage(null);
    setPreviewError(""); setPreviewTab("content"); setReferencedChunkIds([]);
    try {
      const preview = await api<SourcePreview>(documentPath(courseId, item.document_id) + "/chunks?include_content=true");
      setSourcePreview(preview);
    } catch (error) { setMessage(error instanceof Error ? error.message : "预览失败"); }
  }

  useEffect(() => {
    if (!sourcePreview || previewTab !== "content" || originalPage !== null) return;
    const target = referencedChunkIds[0];
    if (target) readingRef.current?.querySelector<HTMLElement>(`[data-chunk-id="${CSS.escape(target)}"]`)?.scrollIntoView({ block: "start" });
  }, [sourcePreview, referencedChunkIds, previewTab, originalPage]);

  function changePreviewTab(tab: "content" | "excluded" | "review") {
    setPreviewTab(tab); setOriginalPage(null);
    readingRef.current?.scrollTo({ top: 0 });
  }

  function locationLabel(chunk: SourceChunk) {
    if (chunk.position_kind === "page" && chunk.position) return `第 ${chunk.position} 页`;
    if (chunk.position_kind === "slide" && chunk.position) return `第 ${chunk.position} 张幻灯片`;
    return "文档片段";
  }

  async function confirmDelete() {
    if (!deleting || busy || (deleting.preview.blocking_references > 0 && !retainSnapshot)) return;
    setBusy(true); setDialogError("");
    try {
      await api(documentPath(courseId, deleting.item.document_id) + "/delete", "POST", {
        confirmation_id: deleting.preview.confirmation_id,
        mode: retainSnapshot ? "retain_source_snapshot" : "block",
      });
      setDeleting(null);
      setMessage(retainSnapshot ? "资料已删除，正式内容的来源快照已保留。" : "资料已删除。");
      await refresh();
    } catch (error) { setDialogError(error instanceof Error ? error.message : "删除失败，请重新预览"); }
    finally { setBusy(false); }
  }

  return <div className="materials-page">
    <header className="materials-heading"><h1>我的复习资料</h1><p>上传课程文件或学习通作业截图，将其中的文字整理为可检索资料。</p></header>
    <form className="materials-upload box" onSubmit={submit}>
      <h2>添加资料</h2>
      <fieldset className="materials-fields" disabled={busy} style={{ border: 0, padding: 0, minWidth: 0 }}>
        <div className="materials-control"><span>课程</span><MaterialSelect label="课程" value={courseId} disabled={busy} onChange={value => { setCourseId(value); setFilterChapter(""); setFilterSource(""); setFilterStatus(""); }} options={[{ value: "", label: "选择课程" }, ...courses.map(item => ({ value: item.course_id, label: item.name }))]} /></div>
        <div className="materials-control"><span>来源类型</span><MaterialSelect label="来源类型" value={sourceType} onChange={value => setSourceType(value as SourceType)} disabled={busy} options={sourceSelectOptions} /></div>
        <label>标题<input value={title} onChange={event => setTitle(event.target.value)} placeholder={files.length > 1 ? "批量上传使用各自文件名" : "留空则使用文件名"} disabled={files.length > 1 || files.some(item => item.metadata)} maxLength={200}/></label>
        <label>章节<input value={chapter} onChange={event => setChapter(event.target.value)} placeholder="可选" maxLength={200}/></label>
        <label className="materials-file">文件<input id="material-file" aria-label="文件" type="file" multiple accept=".md,.txt,.pdf,.ppt,.pptx,.doc,.docx,.png,.jpg,.jpeg,.webp" onChange={event => {
          const selected = Array.from(event.target.files ?? []);
          setUploadResults([]);
          if (selected.length > MAX_UPLOAD_FILES) {
            setFiles([]); event.target.value = "";
            setMessage("每次最多上传 5 个文件，请重新选择。"); return;
          }
          setFiles(selected.map(file => ({ file, key: crypto.randomUUID() })));
          setMessage("");
        }}/><small>按住 Ctrl 多选，或按 Shift 连续选择；每次最多 5 个文件，每个最大 10 MB。支持 MD、TXT、PDF、PPT、PPTX、DOC、DOCX、PNG、JPG、WebP；图片会先进行文字识别。批量上传使用各自文件名作为标题，共用所选课程、来源类型和章节。</small></label>
      </fieldset>
      {files.length > 0 && <div className="materials-selected"><p>已选择 {files.length} / 5 个文件</p><ul>{files.map(item => <li key={item.key}>{item.file.name}</li>)}</ul></div>}
      <button className="primary" disabled={busy || !courseId || !files.length}>{busy ? `正在上传… ${uploadResults.filter(item => !["waiting", "uploading"].includes(item.status)).length} / ${uploadResults.length}` : files.some(item => item.metadata) ? "重试失败文件" : "上传并处理"}</button>
      {uploadResults.length > 0 && <ul className="materials-upload-results" aria-label="上传结果" aria-live="polite">{uploadResults.map(item => <li key={item.key} className={item.status}><span>{item.name}</span><span>{item.status === "waiting" ? "等待上传" : item.status === "uploading" ? "正在上传…" : item.status === "accepted" ? "已接收，排队处理中" : item.status === "reused" ? "已存在，资料可检索" : item.status === "processing_failed" ? "已有资料处理失败，请在下方点击重试" : `上传失败：${item.error}`}</span></li>)}</ul>}
    </form>
    {message && <p className="materials-message" role="status">{message}</p>}
    <section className="materials-list box"><h2>当前课程资料</h2><p className="materials-count">共 {items.length} 份，当前显示 {visible.length} 份</p>
      <div className="materials-filters" aria-label="筛选资料">
        <div className="materials-control"><span>筛选章节</span><MaterialSelect label="筛选章节" value={filterChapter} onChange={setFilterChapter} options={[{ value: "", label: "全部章节" }, ...chapters.map(value => ({ value: value || "__empty__", label: value || "未归类" }))]} /></div>
        <div className="materials-control"><span>筛选来源</span><MaterialSelect label="筛选来源" value={filterSource} onChange={setFilterSource} options={[{ value: "", label: "全部来源" }, ...sourceSelectOptions]} /></div>
        <div className="materials-control"><span>筛选状态</span><MaterialSelect label="筛选状态" value={filterStatus} onChange={setFilterStatus} options={[{ value: "", label: "全部状态" }, { value: "queued", label: "排队中" }, { value: "running", label: "处理中" }, { value: "ready", label: "可检索" }, { value: "failed", label: "处理失败" }]} /></div>
      </div>
      {items.length === 0 ? <p className="materials-empty">暂无资料。选择文件开始上传。</p> : visible.length === 0 ? <p className="materials-empty">没有符合筛选条件的资料。</p> : <ul>{visible.map(item => {
      const job = jobs.find(candidate => candidate.document_id === item.document_id);
      const status = item.parse_status;
      const editable = status === "ready" || status === "failed";
      return <li key={item.document_id}><div className="material-detail"><strong>{item.title}</strong><small>{item.file_name} · {sourceLabel[item.source_type] ?? item.source_type} · {item.chapter || "未归类"}</small>{item.processing_pipeline === "visual-slides-v1" && <small>{item.quality_status === "partial" ? "部分知识已通过核验；待核对内容已排除检索" : item.quality_status === "review_needed" ? "存在待核对内容，可在预览中查看" : "知识内容已通过逐块视觉核验"}</small>}{status === "failed" && <p className="material-error">{job?.error_message ?? item.parse_error}</p>}</div><span className={`material-status ${status}`}>{status === "ready" ? "可检索" : status === "failed" ? "处理失败" : status === "queued" ? "排队中" : `处理中${job?.stage ? ` · ${stageLabel[job.stage] ?? job.stage}` : ""}`}</span><div className="material-actions">{status === "ready" && <button type="button" onClick={() => void openSource(item)} disabled={busy}>预览</button>}<button type="button" onClick={() => openEdit(item)} disabled={!editable || busy} title={!editable ? "处理完成后可编辑" : undefined}>编辑</button>{status === "failed" && job && <button type="button" onClick={() => void retry(job.job_id)} disabled={busy}>重试</button>}<button type="button" className="material-delete" onClick={() => void openDelete(item)} disabled={busy}>删除</button></div></li>;
    })}</ul>}</section>
    {sourcePreview && <div className="wb-overlay" role="presentation"><section className="wb-dialog material-dialog material-preview" role="dialog" aria-modal="true" aria-labelledby="material-preview-title">
      <header className="material-reading-header">
        <div className="material-reading-title"><span className="wb-eyebrow">资料阅读</span><h2 id="material-preview-title">整理后的资料</h2></div>
        <div className="material-reading-metadata"><p>{sourcePreview.file_name}</p><small>{sourceLabel[sourcePreview.source_type] ?? sourcePreview.source_type} · {previewChunks.filter(chunk => !chunk.historical).length} 个知识片段</small></div>
      </header>
      <nav className="material-reading-tabs" aria-label="资料内容分类">
        <button type="button" aria-pressed={previewTab === "content"} onClick={() => changePreviewTab("content")}>整理内容</button>
        <button type="button" aria-pressed={previewTab === "excluded"} onClick={() => changePreviewTab("excluded")}>不参与检索 <span>{excludedPages.length}</span></button>
        <button type="button" aria-pressed={previewTab === "review"} onClick={() => changePreviewTab("review")}>待审核 <span>{reviewPages.length}</span></button>
      </nav>
      {referencedChunkIds.length > 0 && <p className="material-citation-count">本条内容引用 {citedChunks.length} 处位置，已在正文中标记。</p>}
      <div className="material-preview-content" ref={readingRef}>
        {originalPage !== null ? <section className="material-original-view">
          <button className="material-reading-link" type="button" onClick={() => setOriginalPage(null)}>← 返回{previewTab === "content" ? "整理内容" : previewTab === "excluded" ? "不参与检索" : "待审核"}</button>
          <h3>第 {originalPage} 页 · {sourcePreview.pages?.find(page => page.position === originalPage)?.title}</h3>
          <img className="material-original-page" src={documentPath(courseId, sourcePreview.document_id) + `/pages/${originalPage}`} alt={`第 ${originalPage} 页课件`} />
          {sourcePreview.pages?.find(page => page.position === originalPage)?.issues.map((issue, index) => <p className="material-review-issue" key={index}>{issue}</p>)}
        </section> : previewTab === "content" ? <div className="material-reading-notes">
          {previewChunks.length === 0 && <p className="material-reading-empty">暂无可阅读的整理内容。</p>}
          {previewChunks.map(chunk => {
            const text = chunk.content ?? chunk.excerpt;
            const split = text.indexOf("\n");
            const hasTitle = chunk.position_kind === "slide" && split > 0 && split < 200;
            const heading = hasTitle ? text.slice(0, split) : "";
            const body = hasTitle ? text.slice(split).trim() : text;
            return <article key={chunk.chunk_id} data-chunk-id={chunk.chunk_id} className={`material-reading-section${referencedChunkIds.includes(chunk.chunk_id) ? " cited" : ""}`}>
              <div className="material-reading-section-head"><div>{heading && <h3>{heading}</h3>}<small>{locationLabel(chunk)}{referencedChunkIds.includes(chunk.chunk_id) && <span className="material-reference-tag">引用位置</span>}</small></div>
                {sourcePreview.pages?.some(page => page.position === chunk.position) && <button className="material-reading-link" type="button" onClick={() => setOriginalPage(chunk.position)}>查看原页 ↗</button>}
              </div>
              {chunk.historical && <p className="material-quality">历史引用内容，来自重新处理前的版本，不参与当前检索。</p>}
              <div className="material-reading-text">{body.split(/\n\s*\n/).filter(Boolean).map((paragraph, index) => <p key={index} className={/^(?:public |private |protected |import |package |class |function |const |let |var |<\/?[a-z]|[.#][\w-]+\s*\{)/.test(paragraph.trim()) ? "material-reading-code" : undefined}>{paragraph}</p>)}</div>
            </article>;
          })}
        </div> : <div className="material-reading-pages">
          <p className="material-reading-intro">{previewTab === "excluded" ? "这些页面是封面、目录或分隔页，保留原页供查阅，不参与知识检索。" : "以下页面存在待核对内容。仅未通过的知识块排除检索，已通过内容仍可使用；可对照原页查看问题。"}</p>
          {(previewTab === "excluded" ? excludedPages : reviewPages).length === 0 && <p className="material-reading-empty">{previewTab === "excluded" ? "没有不参与检索的页面。" : "没有待审核的页面。"}</p>}
          {(previewTab === "excluded" ? excludedPages : reviewPages).map(page => <article key={page.position} className="material-review-card">
            <div className="material-reading-section-head"><div><small>第 {page.position} 页</small><h3>{page.title}</h3></div><button type="button" className="material-reading-link" onClick={() => setOriginalPage(page.position)}>查看原页 ↗</button></div>
            {previewTab === "review" ? <><h4>AI 核验提示</h4>{page.quality === "partial" && <p className="material-quality">部分通过 · 已核验知识保留，待核对知识不参与检索</p>}{page.blocks?.map(block => <div key={block.block_id}><strong>{block.title} · {block.quality === "verified" ? "已通过" : "待核对"}</strong>{block.evidence?.length ? <small>依据：{Array.from(new Set(block.evidence.map(reference => reference.origin))).map(origin => ({ native: "原生文字", note: "教学备注", image: "原页图片" }[origin])).join("、")}</small> : null}{block.quality !== "verified" && block.issues?.map((issue, index) => <p key={index}>{issue}</p>)}</div>)}{page.issues.length ? <ul>{page.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul> : <p>本页未通过核验，AI 未记录具体原因，请对照原页核对。</p>}</> : <p>导航页 · 不包含可检索的知识内容</p>}
          </article>)}
        </div>}
      </div>
      {previewError && <p className="material-dialog-error" role="alert">{previewError}</p>}
      <div className="wb-dialog-actions"><a href={documentPath(courseId, sourcePreview.document_id) + "/download"}>下载原文件</a><button type="button" onClick={() => { setSourcePreview(null); setPreviewTab("content"); setReferencedChunkIds([]); setPreviewError(""); openedHash.current = ""; setSourceHash(""); if (window.location.hash.startsWith("#materials/")) history.replaceState(null, "", window.location.pathname + window.location.search); }}>关闭</button></div>
    </section></div>}
    {editing && <div className="wb-overlay" role="presentation"><form className="wb-dialog material-dialog" role="dialog" aria-modal="true" aria-labelledby="material-edit-title" onSubmit={saveEdit}><h2 id="material-edit-title">编辑资料信息</h2><p>文件内容保持不变，章节和来源会同步用于资料检索。</p><label>标题<input value={editTitle} onChange={event => setEditTitle(event.target.value)} maxLength={200} required/></label><label>章节<input value={editChapter} onChange={event => setEditChapter(event.target.value)} maxLength={200} placeholder="可选"/></label><div className="materials-control"><span>来源类型</span><MaterialSelect label="来源类型" value={editSource} onChange={value => setEditSource(value as SourceType)} disabled={busy} options={sourceSelectOptions} /></div>{dialogError && <p className="material-dialog-error" role="alert">{dialogError}</p>}<div className="wb-dialog-actions"><button type="button" onClick={() => setEditing(null)} disabled={busy}>取消</button><button className="wb-primary" disabled={busy || !editTitle.trim()}>{busy ? "正在保存…" : "保存资料信息"}</button></div></form></div>}
    {deleting && <div className="wb-overlay" role="presentation"><section className="wb-dialog material-dialog" role="dialog" aria-modal="true" aria-labelledby="material-delete-title"><span className="wb-eyebrow">删除影响</span><h2 id="material-delete-title">删除“{deleting.item.title}”？</h2><p>删除后，原文件和检索内容将不可再使用。</p><div className="material-impact"><span>正式资产 <b>{deleting.preview.affected_assets}</b></span><span>正式版本引用 <b>{deleting.preview.blocking_references}</b></span></div>{deleting.preview.blocking_references > 0 ? <div className="material-snapshot-choice"><p>这份资料仍被正式内容引用。默认禁止删除；若继续，系统会保留文件名、来源类型、当前可用的文档级定位和必要文本摘录，供这些内容说明来源。原文件与检索内容仍会删除。</p><label><input type="checkbox" checked={retainSnapshot} onChange={event => setRetainSnapshot(event.target.checked)}/> 我了解影响，保留来源快照后删除</label></div> : <p>目前没有正式内容引用这份资料。确认后将删除原文件和检索内容。</p>}<p className="wb-expiry">本次确认有效至 {new Date(deleting.preview.expires_at).toLocaleString("zh-CN")}</p>{dialogError && <p className="material-dialog-error" role="alert">{dialogError} <button type="button" onClick={() => { const item = deleting.item; setDeleting(null); void openDelete(item); }}>重新预览</button></p>}<div className="wb-dialog-actions"><button type="button" onClick={() => setDeleting(null)} disabled={busy}>取消</button><button type="button" className="wb-danger" onClick={() => void confirmDelete()} disabled={busy || (deleting.preview.blocking_references > 0 && !retainSnapshot)}>{busy ? "正在删除…" : "确认删除资料"}</button></div></section></div>}
  </div>;
}
