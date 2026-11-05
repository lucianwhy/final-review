import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, downloadFile } from "./workbench-api";

type ExportJob = {
  export_id: string;
  revision_id: string;
  revision_no: number;
  format: "markdown" | "docx" | "pdf" | "print";
  status: "queued" | "running" | "succeeded" | "failed";
  error: string | null;
  content_hash: string;
  renderer_version: string;
};
const formats = [
  ["markdown", "Markdown", ".md"], ["docx", "Word", ".docx"],
  ["pdf", "PDF", ".pdf"], ["print", "打印预览", "HTML"],
] as const;
const pendingJob = (job: ExportJob) => ["queued", "running"].includes(job.status);

function ExportDialog({ label, className, onClose, children }: {
  label: string; className: string; onClose: () => void; children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    const previous = document.activeElement as HTMLElement | null;
    element.showModal();
    return () => {
      element.close();
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return <dialog ref={dialog} className={className} aria-label={label} aria-modal="true"
    onCancel={(event) => { event.preventDefault(); event.stopPropagation(); onClose(); }}>
    {children}
  </dialog>;
}

export default function NoteExport({ assetId, revisionId, revisionNo, historical }: {
  assetId: string; revisionId: string; revisionNo: number; historical: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState<ExportJob[]>([]);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<ExportJob | null>(null);
  const [previewRequested, setPreviewRequested] = useState<string | null>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const mounted = useRef(true);
  const storageKey = `note-export:${assetId}:${revisionId}`;
  const listKey = `${storageKey}:list`;

  function remember(value: ExportJob) {
    setJobs((previous) => {
      const next = previous.some((job) => job.export_id === value.export_id)
        ? previous.map((job) => job.export_id === value.export_id ? value : job)
        : [...previous, value];
      localStorage.setItem(listKey, JSON.stringify(next.map((job) => job.export_id)));
      return next;
    });
  }
  useEffect(() => {
    mounted.current = true;
    let active = true;
    async function restore() {
      try {
        const saved = localStorage.getItem(listKey);
        const legacy = localStorage.getItem(storageKey);
        const parsed: unknown = saved ? JSON.parse(saved) : legacy ? [legacy] : [];
        const ids = Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
        const results = await Promise.allSettled(ids.map((id) => api<ExportJob>(`/api/exports/${id}`)));
        if (!active) return;
        const restored = results.flatMap((result) => result.status === "fulfilled" && result.value.revision_id === revisionId && result.value.renderer_version === "note-export-v2" ? [result.value] : []);
        setJobs(restored);
        localStorage.setItem(listKey, JSON.stringify(restored.map((job) => job.export_id)));
      } catch {
        if (active) setError("读取导出记录失败，请重新生成文件。");
      } finally {
        if (active) setRestoring(false);
      }
    }
    void restore();
    return () => { active = false; mounted.current = false; };
  }, [listKey, storageKey, revisionId]);

  useEffect(() => {
    const pending = jobs.filter(pendingJob);
    if (!pending.length) return;
    let active = true;
    const timer = window.setTimeout(async () => {
      const results = await Promise.allSettled(pending.map((job) => api<ExportJob>(`/api/exports/${job.export_id}`)));
      if (!active) return;
      let failed = false;
      const updates = results.flatMap((result) => {
        if (result.status === "fulfilled") return [result.value];
        failed = true;
        return [];
      });
      setError(failed ? "读取导出状态失败，正在重新连接…" : "");
      setJobs((previous) => previous.map((job) => updates.find((value) => value.export_id === job.export_id) || { ...job }));
    }, 1000);
    return () => { active = false; window.clearTimeout(timer); };
  }, [jobs]);

  useEffect(() => {
    if (!previewRequested) return;
    const print = jobs.find((job) => job.export_id === previewRequested);
    if (print?.status === "succeeded") { setPreview(print); setPreviewRequested(null); }
    else if (print?.status === "failed") setPreviewRequested(null);
  }, [jobs, previewRequested]);

  async function create(format: ExportJob["format"]) {
    setBusy(true);
    setError("");
    try {
      const value = await api<ExportJob>(`/api/notes/${assetId}/exports`, "POST", {
        revision_id: revisionId, format,
      });
      if (mounted.current) remember(value);
      return value;
    } catch (reason) {
      if (mounted.current) {
        setError(reason instanceof Error ? reason.message : "导出失败");
        setPreviewRequested(null);
      }
    } finally { if (mounted.current) setBusy(false); }
  }
  async function showPreview() {
    const print = jobs.find((job) => job.format === "print" && job.status === "succeeded");
    if (print) { setPreview(print); return; }
    // All formats share the same saved revision; use its print rendition for preview.
    const value = await create("print");
    if (value && mounted.current) setPreviewRequested(value.export_id);
  }
  async function retry(job: ExportJob) {
    setBusy(true);
    setError("");
    try {
      const value = await api<ExportJob>(`/api/exports/${job.export_id}/retry`, "POST");
      if (mounted.current) remember(value);
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : "重试失败");
    } finally { if (mounted.current) setBusy(false); }
  }
  async function download(job: ExportJob) {
    setBusy(true);
    setError("");
    try { await downloadFile(`/api/exports/${job.export_id}/download`); }
    catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : "下载失败"); }
    finally { if (mounted.current) setBusy(false); }
  }
  const pending = restoring || busy || jobs.some(pendingJob);
  return <>
    <button type="button" className="note-print-trigger" onClick={() => setOpen(true)}>
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
        <path d="M7 8V3h10v5M7 17H4V8h16v9h-3M7 14h10v7H7z" /><path d="M16 11h1" />
      </svg>打印
    </button>
    {open && <ExportDialog label="打印与导出" className="note-export-dialog" onClose={() => { setOpen(false); setPreview(null); setPreviewRequested(null); }}>
      <header className="note-export-dialog-head">
        <div><small>我的笔记 · 第 {revisionNo} 版</small><h2>打印与导出</h2></div>
        <button type="button" className="note-dialog-close" onClick={() => { setOpen(false); setPreview(null); setPreviewRequested(null); }} aria-label="关闭打印与导出">×</button>
      </header>
      <section className="note-export" aria-label="笔记导出">
        <p className="note-export-description">导出{historical ? "历史" : "已确认"}第 {revisionNo} 版 · 使用已保存的正式内容</p>
        <div className="note-export-formats" aria-label="选择生成格式">
          {formats.map(([format, label, extension]) => <button type="button" key={format} disabled={pending} aria-label={label}
            onClick={() => { if (format === "print") void showPreview(); else void create(format); }}>
            <strong>{label}</strong><small>{format === "print" ? "查看排版并打印" : `${extension} 文件`}</small>
          </button>)}
        </div>
        <div className="note-export-files-head"><h3>生成的文件</h3><span>{jobs.filter((job) => job.status === "succeeded").length} 份已就绪</span></div>
        {!jobs.length && <p className="note-export-empty">选择上方格式生成文件，完成后可在这里预览和下载。</p>}
        <div className="note-export-files" aria-live="polite">
          {jobs.map((job) => {
            const label = formats.find(([format]) => format === job.format)![1];
            return <article className="note-export-file" key={job.export_id} aria-label={`${label} 文件`}>
              <span className="note-export-file-icon" aria-hidden="true">{job.format === "print" ? "HTML" : job.format === "docx" ? "DOCX" : job.format === "markdown" ? "MD" : "PDF"}</span>
              <div className="note-export-file-info"><strong>{label} · 第 {job.revision_no} 版</strong>
                <p role={job.status === "failed" ? "alert" : "status"}>
                  {job.status === "succeeded" ? `第 ${job.revision_no} 版导出已完成` : job.status === "failed" ? job.error || "导出失败" : job.status === "queued" ? "导出已排队" : "正在生成导出文件…"}
                </p>
              </div>
              <div className="note-actions">
                {job.status === "succeeded" && <>
                  <button disabled={pending} onClick={() => void showPreview()}>{job.format === "print" ? "查看打印预览" : "查看预览"}</button>
                  <button className="note-file-download" disabled={busy} onClick={() => void download(job)}>下载 {job.format === "print" ? "HTML" : label}</button>
                </>}
                {job.status === "failed" && <button disabled={pending} onClick={() => void retry(job)}>重试导出</button>}
              </div>
            </article>;
          })}
        </div>
        {error && <p role="alert" className="notes-error">{error}</p>}
      </section>
      <footer className="note-export-footer">文件按当前版本生成，关闭弹窗后仍会继续处理。</footer>
      {preview && <ExportDialog label="打印预览" className="note-print-dialog" onClose={() => setPreview(null)}>
        <header className="note-export-dialog-head">
          <div><small>第 {preview.revision_no} 版 · 已保存的正式内容</small><h2>打印预览</h2></div>
          <div className="note-actions">
            <button className="note-primary" onClick={() => frame.current?.contentWindow?.print()}>打印第 {preview.revision_no} 版</button>
            <button onClick={() => setPreview(null)}>关闭打印预览</button>
          </div>
        </header>
        <p className="note-preview-hint">预览展示当前版本的打印排版；下载文件保留所选格式。</p>
        <iframe ref={frame} title="笔记打印内容" sandbox="allow-same-origin allow-modals" src={`/api/exports/${preview.export_id}/preview`} />
      </ExportDialog>}
    </ExportDialog>}
  </>;
}
