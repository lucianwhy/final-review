import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ApiError, api } from "./workbench-api";
import NoteConfigDialog from "./NoteConfigDialog";
import NoteMaterialPicker, { type NoteMaterial } from "./NoteMaterialPicker";

type NoteInput = { note_type: string; duration_minutes: number; scope: string; source_document_ids: string[] };
type Task = { conversation: string; session: string; event: string; input: NoteInput };
type Progress = { status: "queued" | "running" | "failed" | "needs_input"; job_id?: string; stage?: string; error?: string;
  coverage?: { selected_files: number; readable_chunks: number; read_chunks: number; partial: boolean } };
type History = { items: { draft?: { asset_id: string } }[]; active_note?: Progress | null };

function readTask(key: string): Task | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? "null");
    return value && typeof value.conversation === "string" && typeof value.session === "string"
      && typeof value.event === "string" && Array.isArray(value.input?.source_document_ids) ? value : null;
  } catch { return null; }
}

// Keep the shared dialogs and backend pipeline, but render progress in the notes list.
export default function NoteGeneration({ courseId, progressTarget, onStarted, onCompleted }: {
  courseId: string | null;
  progressTarget: HTMLElement | null;
  onStarted: () => void;
  onCompleted: () => Promise<boolean>;
}) {
  const storageKey = `notes-generation-${courseId}`;
  const [pending, setPending] = useState<Task | null>(() => readTask(storageKey));
  const [progress, setProgress] = useState<Progress>({ status: "queued" });
  const [open, setOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [noteType, setNoteType] = useState("key_points");
  const [duration, setDuration] = useState("10");
  const [materials, setMaterials] = useState<NoteMaterial[]>([]);
  const [requirements, setRequirements] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const trigger = useRef<HTMLButtonElement>(null);
  const mounted = useRef(true);
  const completed = useRef(onCompleted);
  completed.current = onCompleted;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  // Keep identifiers on retry so a lost response cannot create duplicate tasks.
  const task = useRef<Task | null>(pending);

  function clearTask() {
    localStorage.removeItem(storageKey);
    task.current = null;
    setPending(null);
    setError("");
    setConnectionError("");
  }

  useEffect(() => {
    if (!courseId || !pending) return;
    let active = true;
    let fetching = false;
    async function refresh() {
      if (fetching || document.hidden) return;
      fetching = true;
      try {
        const history = await api<History>(`/api/courses/${encodeURIComponent(courseId!)}/conversations/${encodeURIComponent(pending!.conversation)}/messages`);
        if (!active) return;
        if (history.active_note) {
          setProgress(history.active_note);
          setConnectionError("");
        } else {
          // Refresh the saved notes first, then remove the progress row to avoid a gap.
          if (history.items.some(item => item.draft) && !await completed.current()) return;
          if (active) clearTask();
        }
      } catch (reason) {
        if (!active) return;
        if (reason instanceof ApiError && [403, 404].includes(reason.status)) clearTask();
        else setConnectionError("暂时无法更新生成进度，正在重新连接…");
      } finally { fetching = false; }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    const focus = () => void refresh();
    window.addEventListener("focus", focus);
    document.addEventListener("visibilitychange", focus);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", focus);
    };
  }, [courseId, pending]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    api<{ note_model?: string }>("/api/chat/models")
      .then(data => { if (active) setModel(data.note_model ?? ""); })
      .catch(() => {});
    return () => { active = false; };
  }, [open]);

  useEffect(() => {
    if (!open || !courseId || !materials.some(item => ["queued", "running"].includes(item.parse_status))) return;
    let active = true;
    const timer = window.setInterval(() => {
      void api<{ items: NoteMaterial[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`)
        .then(data => { if (active) setMaterials(current => current.map(item =>
          data.items.find(next => next.document_id === item.document_id) ?? item)); })
        .catch(() => {});
    }, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [open, courseId, materials]);

  function close() {
    setOpen(false);
    setPickerOpen(false);
    trigger.current?.focus();
  }

  async function generate() {
    if (!courseId || busy || pending) return;
    setBusy(true);
    setError("");
    const ids = task.current ?? {
      conversation: `chat-${crypto.randomUUID()}`,
      session: `note-${crypto.randomUUID()}`,
      event: crypto.randomUUID(),
      input: { note_type: noteType, duration_minutes: Number(duration), scope: requirements.trim(),
        source_document_ids: materials.map(item => item.document_id) },
    };
    // An unsuccessful submission can be corrected in the still-open dialog.
    ids.input = { note_type: noteType, duration_minutes: Number(duration), scope: requirements.trim(),
      source_document_ids: materials.map(item => item.document_id) };
    task.current = ids;
    try {
      await api(`/agent/invoke?conversation_id=${encodeURIComponent(ids.conversation)}`, "POST", {
        course_id: courseId, session_id: ids.session, intent: "note", message: "生成笔记",
      });
      localStorage.setItem(storageKey, JSON.stringify(ids));
      await api(`/agent/queue-note?conversation_id=${encodeURIComponent(ids.conversation)}&event_id=${ids.event}`, "POST", {
        course_id: courseId, session_id: ids.session,
        note_input: ids.input,
      });
      if (!mounted.current) return;
      setProgress({ status: "queued" });
      setPending(ids);
      onStarted();
      close();
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : "笔记任务提交失败，请重试");
    } finally { setBusy(false); }
  }

  async function cancel() {
    if (!pending || !courseId || busy) return;
    setBusy(true);
    try {
      await api(`/agent/cancel-note?conversation_id=${encodeURIComponent(pending.conversation)}`, "POST",
        { course_id: courseId, session_id: pending.session });
      if (mounted.current) clearTask();
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : "取消生成失败，请重试");
    } finally { setBusy(false); }
  }

  async function retry() {
    if (!pending || !courseId || busy) return;
    setBusy(true);
    setError("");
    try {
      if (progress.job_id) {
        await api(`/agent/retry-note?course_id=${encodeURIComponent(courseId)}&conversation_id=${encodeURIComponent(pending.conversation)}&job_id=${encodeURIComponent(progress.job_id)}`, "POST");
      } else {
        // A completed job may ask for additional input; it needs a fresh queue event.
        const next = { ...pending, event: crypto.randomUUID() };
        localStorage.setItem(storageKey, JSON.stringify(next));
        task.current = next;
        await api(`/agent/queue-note?conversation_id=${encodeURIComponent(next.conversation)}&event_id=${next.event}`, "POST",
          { course_id: courseId, session_id: next.session, note_input: next.input });
        if (mounted.current) setPending(next);
      }
      if (mounted.current) setProgress({ status: "queued" });
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : "重试失败，请重试");
    } finally { setBusy(false); }
  }

  const failed = ["failed", "needs_input"].includes(progress.status);
  const coverage = progress.coverage;

  return <>
    <button ref={trigger} type="button" className="note-primary notes-generate" disabled={!courseId || Boolean(pending) || busy}
      onClick={() => { setError(""); setOpen(true); }}>生成笔记</button>
    {open && !pickerOpen && <NoteConfigDialog noteType={noteType} onNoteType={setNoteType}
      duration={duration} onDuration={setDuration} materials={materials}
      onChooseMaterials={() => setPickerOpen(true)} requirements={requirements} onRequirements={setRequirements}
      onGenerate={() => void generate()} onCancel={close} busy={busy} error={error}
      generationModel={model} contextLabel="我的笔记" />}
    {open && pickerOpen && courseId && <NoteMaterialPicker courseId={courseId} selected={materials}
      onUploaded={item => setMaterials(current => [...current.filter(existing => existing.document_id !== item.document_id), item])}
      onConfirm={items => { setMaterials(items); setPickerOpen(false); }} onClose={() => setPickerOpen(false)} />}
    {pending && progressTarget && createPortal(<article className={`notes-generation-row${failed ? " failed" : ""}`} aria-label="笔记生成任务">
      <small>AI 生成 · {({ chapter: "章节笔记", key_points: "考点清单", qa_cards: "问答卡片", mnemonic: "口诀" } as Record<string, string>)[pending.input.note_type]}</small>
      <div className="note-job-status">
        <div className="note-job-copy" role="status" aria-live="polite">
          <span>{!failed && <i className="notes-generation-spinner" aria-hidden="true" />}{failed
            ? `笔记生成失败：${progress.error || "任务尚未提交成功，请重试"}`
            : progress.status === "queued" ? "笔记任务已排队，正在等待生成。"
            : `正在生成笔记${progress.stage ? ` · ${progress.stage}` : "…"}`}</span>
          {coverage && <small>已选 {coverage.selected_files} 份资料；共有 {coverage.readable_chunks} 个可读片段；本次读取 {coverage.read_chunks} 个，{coverage.partial ? "部分覆盖" : "已读取范围内全部片段"}。</small>}
        </div>
        <div className="note-job-actions">
          {failed && <button type="button" className="note-job-retry" disabled={busy} onClick={() => void retry()}>重试生成</button>}
          <button type="button" className="note-job-cancel" disabled={busy} onClick={() => void cancel()}>{busy ? "正在处理…" : failed ? "结束任务" : "取消生成"}</button>
        </div>
      </div>
      {error && <p role="alert" className="notes-generation-error">{error}</p>}
      {connectionError && <p role="alert" className="notes-generation-error">{connectionError}</p>}
    </article>, progressTarget)}
  </>;
}
