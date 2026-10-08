import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./workbench-api";
import "./note-material-picker.css";

export type NoteMaterial = {
  document_id: string;
  title: string;
  file_name?: string;
  source_type: string;
  chapter?: string;
  parse_status: string;
  parse_error?: string;
  uploaded_at?: string;
  file_size?: number;
};

const sourceNames: Record<string, string> = {
  past_exam: "历年真题", teacher_ppt: "老师 PPT", homework: "平时作业",
  other_practice: "其他练习", external_upload: "外部上传", crash_course: "速成课", ai_supplement: "AI 补充",
};
const statusNames: Record<string, string> = {
  ready: "可检索", queued: "排队中", running: "处理中", failed: "处理失败",
};

export function materialLabel(item: NoteMaterial): string {
  return item.file_name || item.title;
}

type MaterialJob = { job_id: string; document_id: string; status: string; stage?: string; error_message?: string };

export default function NoteMaterialPicker({ courseId, selected, onUploaded, onConfirm, onClose, maxSelection = 5, purpose = "笔记", allowedIds }: {
  maxSelection?: number;
  purpose?: string;
  allowedIds?: string[] | null;
  courseId: string;
  selected: NoteMaterial[];
  onUploaded: (item: NoteMaterial) => void;
  onConfirm: (items: NoteMaterial[]) => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<NoteMaterial[]>([]);
  const [checked, setChecked] = useState<string[]>(selected.map(item => item.document_id));
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [jobs, setJobs] = useState<MaterialJob[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadChapter, setUploadChapter] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState("");
  const [dragging, setDragging] = useState(false);
  const [layout, setLayout] = useState<{ controlsHeight: number; dialogHeight: number; maximum: number } | null>(null);
  const [resizing, setResizing] = useState(false);
  const dialogRef = useRef<HTMLElement>(null);
  const controlsRef = useRef<HTMLDivElement>(null);
  const resizeStart = useRef<{ y: number; height: number; dialogHeight: number; maximum: number } | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function resizeBounds() {
    const dialog = dialogRef.current!;
    const dialogHeight = dialog.getBoundingClientRect().height;
    const fixedHeight = [".note-source-head", ".note-source-footer", ".note-source-resize", ".note-source-error"]
      .reduce((height, selector) => height + (dialog.querySelector(selector)?.getBoundingClientRect().height ?? 0), 0);
    return { dialogHeight, maximum: Math.max(80, dialogHeight - fixedHeight - 152) };
  }

  function resizeControls(height: number, bounds: { dialogHeight: number; maximum: number }) {
    setLayout({ controlsHeight: Math.max(80, Math.min(bounds.maximum, height)), dialogHeight: bounds.dialogHeight, maximum: bounds.maximum });
  }

  async function refresh() {
    const [documents, currentJobs] = await Promise.all([
      api<{ items: NoteMaterial[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`),
      api<{ items: MaterialJob[] }>(`/api/courses/${encodeURIComponent(courseId)}/material-jobs`),
    ]);
    setItems(documents.items.filter(item => item.parse_status !== "deleted" && (!allowedIds || allowedIds.includes(item.document_id))));
    setJobs(currentJobs.items);
    return documents.items;
  }

  useEffect(() => {
    let active = true;
    refresh()
      .then(() => {})
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "资料列表加载失败"); })
      .finally(() => { if (active) setLoading(false); });
    searchRef.current?.focus();
    return () => { active = false; };
  }, [courseId]);

  useEffect(() => {
    if (!items.some(item => ["queued", "running"].includes(item.parse_status))) return;
    const timer = window.setInterval(() => { void refresh().catch(() => {}); }, 2000);
    return () => window.clearInterval(timer);
  }, [courseId, items]);

  function chooseFile(next: File | null) {
    setFile(next); setUploadTitle(next?.name ?? "");
    if (next) setUploadMessage("");
  }

  async function uploadFile() {
    if (!file || uploading) return;
    setUploading(true); setError(""); setUploadMessage("");
    const body = new FormData();
    body.append("course_id", courseId);
    body.append("title", uploadTitle.trim() || file.name);
    body.append("chapter", uploadChapter.trim());
    body.append("source_type", "external_upload");
    body.append("file", file);
    try {
      const response = await fetch("/knowledge/upload", {
        method: "POST", credentials: "same-origin", body,
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "上传失败");
      const documents = await refresh();
      const item = documents.find(candidate => candidate.document_id === result.document_id)
        ?? { document_id: result.document_id, title: uploadTitle.trim() || file.name,
             file_name: file.name, chapter: uploadChapter.trim(), source_type: "external_upload",
             parse_status: result.status };
      if (allowedIds && !allowedIds.includes(item.document_id)) {
        setUploadMessage("已上传到课程，但不在当前对话限定的资料范围内，请另开对话使用。");
      } else if (checked.length < maxSelection || checked.includes(item.document_id)) {
        setChecked(current => [...new Set([...current, item.document_id])]);
        onUploaded(item);
        setUploadMessage(result.reused ? "已复用当前课程中的同名同内容资料。" : "已上传，处理完成后自动选中。");
      } else {
        setUploadMessage(`已上传到课程。本次已选满 ${maxSelection} 份；如需使用，请先移除一份再勾选。`);
      }
      chooseFile(null); setUploadChapter("");
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "上传失败");
    } finally { setUploading(false); }
  }

  async function retry(item: NoteMaterial) {
    const job = jobs.find(candidate => candidate.document_id === item.document_id);
    if (!job) { setError("找不到处理任务，请重新上传文件。"); return; }
    try {
      await api(`/api/courses/${encodeURIComponent(courseId)}/material-jobs/${encodeURIComponent(job.job_id)}/retry`, "POST");
      setUploadMessage("已重新排队处理。");
      onUploaded({ ...item, parse_status: "queued", parse_error: undefined });
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "重试失败"); }
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
      if (event.key !== "Tab") return;
      const controls = [...(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled),input:not(:disabled),a[href],[role="separator"]'
      ) ?? [])].filter(element => element.getClientRects().length > 0);
      if (!controls.length) return;
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const available = items.filter(item => item.parse_status === "ready");
  const filtered = useMemo(() => items.filter(item =>
    `${item.file_name ?? ""} ${item.title} ${item.chapter ?? ""} ${sourceNames[item.source_type] ?? ""}`
      .toLocaleLowerCase("zh-CN").includes(query.trim().toLocaleLowerCase("zh-CN"))
  ), [items, query]);
  const selectedItems = items.filter(item => checked.includes(item.document_id));
  const selectedReady = selectedItems.length > 0 && selectedItems.every(item => item.parse_status === "ready");

  return createPortal(<div className="note-source-backdrop" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose();
  }}><section className={`note-source-dialog${resizing ? " resizing" : ""}`} style={layout ? { height: layout.dialogHeight } : undefined} role="dialog" aria-modal="true" aria-labelledby="note-source-title" ref={dialogRef}>
    <header className="note-source-head"><div><small>当前课程 · {purpose}资料</small><h2 id="note-source-title">选择生成依据</h2><p>{purpose}只会引用你在这里勾选的资料。</p></div><button type="button" className="note-source-close" aria-label="关闭资料选择" onClick={onClose}>×</button></header>
    <div className="note-source-controls" ref={controlsRef} style={layout ? { flexBasis: layout.controlsHeight } : undefined}>
    <div className="note-upload-panel"><div className={`note-upload-drop${dragging ? " dragging" : ""}`} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (event.dataTransfer.files.length > 1) { setError("一次只能上传一份文件，请分别拖入。"); return; } chooseFile(event.dataTransfer.files[0] ?? null); }}><strong>把课程文件拖到这里</strong><span>或</span><button type="button" onClick={() => fileInputRef.current?.click()}>选择文件</button><input ref={fileInputRef} type="file" accept=".md,.txt,.pdf,.ppt,.pptx,.doc,.docx,.png,.jpg,.jpeg,.webp" aria-label={`上传${purpose}资料文件`} onChange={event => chooseFile(event.target.files?.[0] ?? null)} /></div><div className="note-upload-details"><label>标题<input value={uploadTitle} onChange={event => setUploadTitle(event.target.value)} placeholder="默认文件名" maxLength={200} /></label><label>章节 · 可选<input value={uploadChapter} onChange={event => setUploadChapter(event.target.value)} placeholder="例如：第二章" maxLength={200} /></label><button type="button" disabled={!file || uploading} onClick={() => void uploadFile()}>{uploading ? "正在上传…" : "上传并处理"}</button></div><small>新文件默认标为“外部上传”；可到“我的资料”修改来源类型。单个文件不超过 10 MB。</small>{uploadMessage && <p role="status">{uploadMessage}</p>}</div>
    <div className="note-source-tools"><label className="note-source-search"><span>查找资料</span><input ref={searchRef} type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索文件名、章节或来源" /></label><div className="note-source-select-all"><span>已选 {selectedItems.length} / {available.length} 份可用资料 · 单次最多 {maxSelection} 份</span><button type="button" disabled={!available.length} onClick={() => { if (available.length > maxSelection) { setError(`单次最多选择 ${maxSelection} 份资料，请逐份选择。`); return; } setError(""); setChecked(available.map(item => item.document_id)); }}>选择全部可用资料</button><button type="button" disabled={!checked.length} onClick={() => { setChecked([]); setError(""); }}>清空</button></div></div>
    </div>
    <div className="note-source-resize" role="separator" tabIndex={0} aria-label="上下拖动调整资料列表高度" aria-orientation="horizontal" aria-controls="note-source-list" aria-valuemin={0} aria-valuemax={100} aria-valuenow={layout ? Math.round(100 * Math.max(0, Math.min(1, 1 - (layout.controlsHeight - 80) / Math.max(1, layout.maximum - 80)))) : 0}
      onPointerDown={event => {
        if (event.button !== 0) return;
        event.preventDefault(); event.currentTarget.focus();
        const bounds = resizeBounds();
        resizeStart.current = { y: event.clientY, height: controlsRef.current!.getBoundingClientRect().height, ...bounds };
        event.currentTarget.setPointerCapture(event.pointerId);
        resizeControls(resizeStart.current.height, bounds); setResizing(true);
      }}
      onPointerMove={event => { const start = resizeStart.current; if (start) resizeControls(start.height + event.clientY - start.y, start); }}
      onPointerUp={event => { resizeStart.current = null; setResizing(false); if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
      onLostPointerCapture={() => { resizeStart.current = null; setResizing(false); }}
      onPointerCancel={() => { resizeStart.current = null; setResizing(false); }}
      onKeyDown={event => {
        if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
        event.preventDefault(); const bounds = resizeBounds();
        const height = controlsRef.current!.getBoundingClientRect().height;
        resizeControls(event.key === "Home" ? 80 : event.key === "End" ? bounds.maximum : height + (event.key === "ArrowUp" ? -32 : 32), bounds);
      }}><span aria-hidden="true">⋮</span><span>上下拖动调整列表高度</span><span aria-hidden="true">↕</span></div>
    {error && <p className="note-source-state note-source-error" role="alert">{error}</p>}
    <div id="note-source-list" className="note-source-list" role="group" aria-label="当前课程资料">
      {loading ? <p className="note-source-state">正在读取课程资料…</p> : filtered.length === 0 ? <p className="note-source-state">{query ? "没有符合搜索条件的资料。" : "当前课程还没有资料。请先到“我的资料”上传。"}</p> : filtered.map(item => {
        const ready = item.parse_status === "ready";
        const isChecked = checked.includes(item.document_id);
        return <label key={item.document_id} className={`note-source-row${isChecked ? " selected" : ""}${ready ? "" : " unavailable"}`}>
          <input type="checkbox" checked={isChecked} disabled={!ready && !isChecked} onChange={() => { if (!isChecked && checked.length >= maxSelection) { setError(`单次最多选择 ${maxSelection} 份资料，请先移除一份。`); return; } setError(""); setChecked(current => isChecked ? current.filter(id => id !== item.document_id) : [...current, item.document_id]); }} aria-label={`选择 ${materialLabel(item)}，编号 ${item.document_id.slice(0, 8)}`} />
          <span className="note-source-row-body"><strong>{materialLabel(item)}</strong><span>{sourceNames[item.source_type] ?? item.source_type} · {item.chapter || "未归类"} · {item.uploaded_at ? new Date(item.uploaded_at).toLocaleString("zh-CN") : "上传时间未知"} · 编号 {item.document_id.slice(0, 8)}</span>{!ready && item.parse_error && <em>{item.parse_error}</em>}{item.parse_status === "failed" && isChecked && <button type="button" onClick={event => { event.preventDefault(); void retry(item); }}>重试处理</button>}</span>
          <span className={`note-source-status ${item.parse_status}`}>{statusNames[item.parse_status] ?? item.parse_status}</span>
        </label>;
      })}
    </div>
    <footer className="note-source-footer"><span>{selectedItems.length ? `已选 ${selectedItems.length} 份${selectedReady ? "可检索资料" : "资料；请等待处理或移除未就绪文件"}` : "至少选择一份可检索资料"}</span><div>{!loading && !available.length && <button type="button" className="note-source-cancel" onClick={() => { onClose(); window.location.hash = `#materials/${courseId}`; }}>管理资料</button>}<button type="button" className="note-source-cancel" onClick={onClose}>取消</button><button type="button" className="note-source-confirm" disabled={!selectedReady && !(selected.length > 0 && selectedItems.length === 0)} onClick={() => onConfirm(selectedItems)}>{selectedItems.length ? "确认选择" : "移除已选资料"}</button></div></footer>
  </section></div>, document.body);
}
