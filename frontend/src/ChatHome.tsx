import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, api } from "./workbench-api";
import NoteConfigDialog from "./NoteConfigDialog";
import QuizConfigDialog, { type PendingQuiz, type QuizInput, type QuizConfiguration } from "./QuizConfigDialog";
import QuizDraftPreview, { quizStages, type QuizDraftCard, type QuizJob } from "./QuizDraftPreview";
import { ChatQuiz, ChatSources, type ChatCitation, type ChatQuizCard } from "./ChatSources";
import NoteMaterialPicker, { materialLabel, type NoteMaterial } from "./NoteMaterialPicker";

type ChatModel = { id: string; label: string };
type DraftCard = { asset_id: string; revision_id: string; title: string; note_type: string; url: string };
type AgentResult = { session_id: string; status: string; answer: string; prompt?: { message: string; required: string[] }; note_config?: { note_type?: string; scope?: string; duration_minutes?: number }; draft?: DraftCard };
type DispatchResult = { kind: "note"; intent: "note"; session_id: string; result: AgentResult; source_document_ids?: string[] | null }
  | { kind: "chat" | "quiz"; intent: "ask" | "quiz" | "clarify"; reply: string; model: string; citations?: ChatCitation[]; quiz?: ChatQuizCard; status?: string; quiz_configuration?: QuizConfiguration; quiz_scope?: PendingQuiz["scope"]; quiz_job?: QuizJob };
type NoteCoverage = { selected_files: number; readable_chunks: number; read_chunks: number; partial: boolean; files: { document_id: string; file_name: string; readable_chunks: number; read_chunks: number }[] };
type Message = { from: "agent" | "user"; text: string; model?: string; draft?: DraftCard; citations?: ChatCitation[]; quiz?: ChatQuizCard; quiz_draft?: QuizDraftCard };
type StoredMessage = { role: "user" | "assistant"; content: string; model?: string; draft?: DraftCard; citations?: ChatCitation[]; quiz?: ChatQuizCard; quiz_draft?: QuizDraftCard };
function restoreMessage(item: StoredMessage): Message {
  return { from: item.role === "user" ? "user" : "agent", text: item.content, model: item.model,
    draft: item.draft, citations: item.citations, quiz: item.quiz, quiz_draft: item.quiz_draft };
}
type ActiveNote = { session_id: string; status: "needs_input" | "queued" | "running" | "failed"; job_id?: string; stage?: string; error?: string; coverage?: NoteCoverage; prompt?: AgentResult["prompt"]; note_input?: { note_type?: string; scope?: string; duration_minutes?: number; source_document_ids?: string[] } };

function coverageSummary(coverage?: NoteCoverage): string {
  return coverage ? `已选 ${coverage.selected_files} 份资料；共有 ${coverage.readable_chunks} 个可读片段；本次读取 ${coverage.read_chunks} 个${coverage.partial ? "，部分覆盖" : "，已读取范围内全部片段"}。` : "";
}

async function noteRequest(path: string, body?: unknown): Promise<AgentResult> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 180_000);
  try {
    const response = await fetch(path, {
      method: "POST", credentials: "same-origin", signal: controller.signal,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "笔记请求失败");
    return data as AgentResult;
  } catch (error) {
    if (controller.signal.aborted) throw new Error("等待超过 3 分钟。任务可能仍在后台运行，请点击“恢复笔记任务”查看结果。");
    throw error;
  } finally { window.clearTimeout(timeout); }
}

export default function ChatHome({ courseId, selectedConversationId, titleRefreshKey, onConversationCreated, onConversationRenamed, onNewConversation }: { courseId: string | null; selectedConversationId: string | null; titleRefreshKey: number; onConversationCreated: (id: string) => void; onConversationRenamed: () => void; onNewConversation: () => void }) {
  const [models, setModels] = useState<ChatModel[]>([]);
  const [noteModel, setNoteModel] = useState("");
  const [modelId, setModelId] = useState("");
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [modelError, setModelError] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyFailed, setHistoryFailed] = useState(false);
  const [conversationTitle, setConversationTitle] = useState("新对话");
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [savingTitle, setSavingTitle] = useState(false);
  const [draft, setDraft] = useState("");
  const [composerMaterials, setComposerMaterials] = useState<NoteMaterial[]>([]);
  const [composerUploading, setComposerUploading] = useState(false);
  const [composerDragOver, setComposerDragOver] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [noteSession, setNoteSession] = useState<string | null>(null);
  const [notePrompt, setNotePrompt] = useState<AgentResult["prompt"]>();
  const [noteRecovery, setNoteRecovery] = useState<"queued" | "running" | "failed" | null>(null);
  const [noteJobId, setNoteJobId] = useState<string | null>(null);
  const [noteJobError, setNoteJobError] = useState("");
  const [noteJobStage, setNoteJobStage] = useState("");
  const [noteCoverage, setNoteCoverage] = useState<NoteCoverage | undefined>();
  const [noteType, setNoteType] = useState("key_points");
  const [noteScope, setNoteScope] = useState("");
  const [noteDuration, setNoteDuration] = useState("10");
  const [noteMaterials, setNoteMaterials] = useState<NoteMaterial[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [noteCancelling, setNoteCancelling] = useState(false);
  const [pendingQuiz, setPendingQuiz] = useState<PendingQuiz | null>(null);
  const [quizError, setQuizError] = useState("");
  const [quizJob, setQuizJob] = useState<QuizJob | null>(null);
  const [previewQuiz, setPreviewQuiz] = useState<QuizDraftCard | null>(null);
  const quizSubmitKey = useRef(crypto.randomUUID());
  const composerTextareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  const modelPickerRef = useRef<HTMLDivElement>(null);
  const modelTriggerRef = useRef<HTMLButtonElement>(null);
  const composerFileInputRef = useRef<HTMLInputElement>(null);
  const composerUploadLock = useRef(false);
  const conversationId = useRef(`chat-${crypto.randomUUID()}`);
  const mounted = useRef(true);
  const quizWasOpen = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (quizWasOpen.current && !pendingQuiz) composerTextareaRef.current?.focus();
    quizWasOpen.current = Boolean(pendingQuiz);
  }, [pendingQuiz]);

  useLayoutEffect(() => {
    const textarea = composerTextareaRef.current;
    if (!textarea) return;
    const resize = () => {
      textarea.style.height = "0px";
      textarea.style.height = `${Math.min(textarea.scrollHeight, 132)}px`;
      textarea.style.overflowY = textarea.scrollHeight > 132 ? "auto" : "hidden";
    };
    resize();
    let width = textarea.getBoundingClientRect().width;
    const observer = new ResizeObserver(entries => {
      const nextWidth = entries[0].contentRect.width;
      if (nextWidth !== width) { width = nextWidth; resize(); }
    });
    observer.observe(textarea);
    return () => observer.disconnect();
  }, [draft]);

  function restoreNoteInput(activeNote: ActiveNote | null | undefined, currentCourse: string, active: () => boolean) {
    const input = activeNote?.note_input;
    if (!input) return;
    if (input.note_type) setNoteType(input.note_type);
    const savedScope = localStorage.getItem(`note-scope-${currentCourse}-${activeNote?.session_id}`);
    if (savedScope !== null) setNoteScope(savedScope);
    else if (input.scope !== undefined) setNoteScope(input.scope);
    if (input.duration_minutes) setNoteDuration(String(input.duration_minutes));
    const uploaded = JSON.parse(localStorage.getItem(`note-uploads-${currentCourse}-${activeNote?.session_id}`) || "[]") as string[];
    const selectedIds = new Set([...(input.source_document_ids ?? []), ...uploaded]);
    if (selectedIds.size) {
      void api<{ items: NoteMaterial[] }>(`/api/courses/${encodeURIComponent(currentCourse)}/documents`)
        .then(data => { if (active()) setNoteMaterials(data.items.filter(item => selectedIds.has(item.document_id))); })
        .catch(() => {});
    }
  }

  useEffect(() => {
    let active = true;
    api<{ items: ChatModel[]; note_model?: string }>("/api/chat/models").then(data => {
      if (!active) return;
      setModels(data.items);
      setNoteModel(data.note_model ?? "");
      setModelId(current => data.items.some(item => item.id === current) ? current : data.items[0]?.id ?? "");
      setModelError(data.items.length ? "" : "尚未配置可用的聊天模型");
    }).catch(error => {
      if (active) setModelError(error instanceof Error ? error.message : "模型列表加载失败");
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setMessages([]); setModelError(""); setHistoryFailed(false);
    setComposerMaterials([]);
    setComposerDragOver(false);
    setNoteSession(null);
    setNotePrompt(undefined);
    setNoteRecovery(null);
    setNoteJobId(null);
    setNoteJobError("");
    setNoteJobStage(""); setNoteCoverage(undefined);
    setNoteMaterials([]);
    setNoteScope("");
    setPickerOpen(false);
    setNoteCancelling(false);
    setPendingQuiz(null); setQuizError("");
    setQuizJob(null); setPreviewQuiz(null); quizSubmitKey.current = crypto.randomUUID();
    conversationId.current = selectedConversationId ?? `chat-${crypto.randomUUID()}`;
    if (courseId && selectedConversationId) {
      setHistoryLoading(true);
      api<{ items: StoredMessage[]; active_note?: ActiveNote | null; pending_quiz?: PendingQuiz | null; active_quiz?: QuizJob | null }>(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(selectedConversationId)}/messages`)
        .then(data => { if (active) {
          setMessages(data.items.map(restoreMessage));
          setPendingQuiz(data.pending_quiz ?? null);
          setQuizJob(data.active_quiz ?? null);
          setNoteSession(data.active_note?.session_id ?? null);
          setNoteJobId(data.active_note?.job_id ?? null);
          setNoteJobError(data.active_note?.error ?? "");
          setNoteJobStage(data.active_note?.stage ?? "");
          setNoteCoverage(data.active_note?.coverage);
          setNotePrompt(data.active_note?.status === "needs_input" ? data.active_note.prompt : undefined);
          setNoteRecovery(["queued", "running", "failed"].includes(data.active_note?.status ?? "") ? data.active_note!.status as "queued" | "running" | "failed" : null);
          restoreNoteInput(data.active_note, courseId, () => active);
        } })
        .catch(error => { if (active) { setHistoryFailed(true); setModelError(error instanceof Error ? error.message : "对话读取失败"); } })
        .finally(() => { if (active) setHistoryLoading(false); });
    } else setHistoryLoading(false);
    return () => { active = false; };
  }, [courseId, selectedConversationId]);

  useEffect(() => {
    if (!courseId || !selectedConversationId || !quizJob || !["queued", "running"].includes(quizJob.status)) return;
    let active = true;
    const timer = window.setInterval(() => {
      void api<{ items: StoredMessage[]; active_quiz?: QuizJob | null }>(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(selectedConversationId)}/messages`)
        .then(data => { if (active) { setMessages(data.items.map(restoreMessage)); setQuizJob(data.active_quiz ?? null); } })
        .catch(error => { if (active) setModelError(error instanceof Error ? error.message : "任务进度读取失败"); });
    }, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [courseId, selectedConversationId, quizJob?.job_id, quizJob?.status]);

  useEffect(() => {
    if (!courseId || !selectedConversationId || !noteJobId || !["queued", "running"].includes(noteRecovery ?? "")) return;
    let active = true;
    const timer = window.setInterval(() => {
      void api<{ items: StoredMessage[]; active_note?: ActiveNote | null }>(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(selectedConversationId)}/messages`)
        .then(data => {
          if (!active) return;
          setMessages(data.items.map(restoreMessage));
          setNoteSession(data.active_note?.session_id ?? null);
          setNoteJobId(data.active_note?.job_id ?? null);
          setNoteJobError(data.active_note?.error ?? "");
          setNoteJobStage(data.active_note?.stage ?? "");
          setNoteCoverage(data.active_note?.coverage);
          setNotePrompt(data.active_note?.status === "needs_input" ? data.active_note.prompt : undefined);
          setNoteRecovery(["queued", "running", "failed"].includes(data.active_note?.status ?? "") ? data.active_note!.status as "queued" | "running" | "failed" : null);
        }).catch(() => {});
    }, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [courseId, selectedConversationId, noteJobId, noteRecovery]);

  useEffect(() => {
    if (!courseId || !notePrompt || !noteMaterials.some(item => ["queued", "running"].includes(item.parse_status))) return;
    let active = true;
    const timer = window.setInterval(() => {
      void api<{ items: NoteMaterial[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`)
        .then(data => {
          if (active) setNoteMaterials(current => current.map(item =>
            data.items.find(next => next.document_id === item.document_id) ?? item));
        }).catch(() => {});
    }, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [courseId, notePrompt, noteMaterials]);

  useEffect(() => {
    if (!courseId || !composerMaterials.some(item => ["queued", "running"].includes(item.parse_status))) return;
    let active = true;
    const timer = window.setInterval(() => {
      void api<{ items: NoteMaterial[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`)
        .then(data => {
          if (active) setComposerMaterials(current => current.map(item =>
            data.items.find(next => next.document_id === item.document_id) ?? item));
        }).catch(() => {});
    }, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [courseId, composerMaterials]);

  async function attachComposerFiles(files: File[]) {
    if (!files.length || composerUploadLock.current) return;
    if (!courseId) { setModelError("请先到“课程管理”选择一门课程，再添加文件。"); return; }
    if (noteSession || isSending) {
      setModelError("请先完成或取消当前笔记任务，再添加新资料。"); return;
    }
    if (composerMaterials.length + files.length > 100) {
      setModelError("一次最多添加 100 份资料，请先移除一些文件。"); return;
    }
    const requestConversationId = conversationId.current;
    const current = () => mounted.current && conversationId.current === requestConversationId;
    composerUploadLock.current = true;
    setComposerUploading(true); setModelError("");
    const errors: string[] = [];
    try {
      for (const file of files) {
        if (!current()) break;
        try {
          const body = new FormData();
          body.append("course_id", courseId);
          body.append("title", file.name.slice(0, 200));
          body.append("chapter", "");
          body.append("source_type", "external_upload");
          body.append("file", file);
          const response = await fetch("/knowledge/upload", {
            method: "POST", credentials: "same-origin", body,
            headers: { "Idempotency-Key": crypto.randomUUID() },
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "上传失败");
          if (!current()) break;
          const item: NoteMaterial = { document_id: result.document_id, title: file.name,
            file_name: file.name, source_type: "external_upload", parse_status: result.status };
          // Keep the accepted upload even if a subsequent status refresh fails.
          setComposerMaterials(items => items.some(existing => existing.document_id === item.document_id)
            ? items.map(existing => existing.document_id === item.document_id ? item : existing)
            : [...items, item]);
          const documents = await api<{ items: NoteMaterial[] }>(
            `/api/courses/${encodeURIComponent(courseId)}/documents`,
          ).catch(() => null);
          if (current() && documents) setComposerMaterials(items => items.map(existing =>
            documents.items.find(next => next.document_id === existing.document_id) ?? existing));
        } catch (error) {
          errors.push(`${file.name}：${error instanceof Error ? error.message : "文件上传失败"}`);
        }
      }
      if (current() && errors.length) setModelError(errors.join("；"));
    } finally {
      composerUploadLock.current = false;
      setComposerUploading(false);
      if (composerFileInputRef.current) composerFileInputRef.current.value = "";
    }
  }

  useEffect(() => {
    setEditingTitle(false);
    setConversationTitle("新对话");
    if (!courseId || !selectedConversationId) return;
    let active = true;
    api<{ items: { conversation_id: string; title: string }[] }>(`/api/courses/${encodeURIComponent(courseId)}/conversations`)
      .then(data => { if (active) setConversationTitle(data.items.find(item => item.conversation_id === selectedConversationId)?.title || "新对话"); })
      .catch(() => {});
    return () => { active = false; };
  }, [courseId, selectedConversationId, titleRefreshKey]);

  async function saveTitle() {
    if (!courseId || !selectedConversationId || savingTitle) return;
    const title = titleDraft.trim();
    if (!title) { setModelError("对话名称不能为空"); return; }
    setSavingTitle(true); setModelError("");
    try {
      await api(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(selectedConversationId)}`, "PATCH", { title });
      setConversationTitle(title); setEditingTitle(false); onConversationRenamed();
    } catch (error) { setModelError(error instanceof Error ? error.message : "重命名失败"); }
    finally { setSavingTitle(false); }
  }



  function showAgentResult(result: AgentResult) {
    setNoteRecovery(null);
    if (result.status === "needs_input") {
      const savedScope = localStorage.getItem(`note-scope-${courseId}-${result.session_id}`);
      if (savedScope !== null) setNoteScope(savedScope);
      else if (result.note_config?.scope) setNoteScope(result.note_config.scope);
      if (result.note_config?.note_type) setNoteType(result.note_config.note_type);
      if (result.note_config?.duration_minutes) setNoteDuration(String(result.note_config.duration_minutes));
      setNotePrompt(result.prompt);
      setMessages(current => [...current, { from: "agent", text: result.prompt?.message ?? "请补充笔记要求" }]);
    } else {
      setNoteSession(null);
      setNotePrompt(undefined);
      setMessages(current => [...current, { from: "agent", text: result.answer || "笔记任务已完成", draft: result.draft }]);
    }
  }

  async function resumeNote() {
    if (!courseId || !noteSession || isSending || !noteMaterials.length
        || noteMaterials.some(item => item.parse_status !== "ready")) return;
    setIsSending(true);
    setModelError("");
    try {
      const result = await api<{ job_id: string; status: "queued" }>(`/agent/queue-note?conversation_id=${encodeURIComponent(conversationId.current)}&event_id=${crypto.randomUUID()}`, "POST", {
        course_id: courseId, session_id: noteSession,
        note_input: { note_type: noteType, scope: noteScope.trim(), duration_minutes: Number(noteDuration),
          source_document_ids: noteMaterials.map(item => item.document_id) },
      });
      setMessages(current => [...current, { from: "user", text: `补充笔记要求：使用 ${noteMaterials.map(materialLabel).join("、")}；${noteScope.trim() ? `写作要求 ${noteScope.trim()}；` : ""}阅读时长 ${noteDuration} 分钟` }]);
      setNotePrompt(undefined);
      localStorage.removeItem(`note-uploads-${courseId}-${noteSession}`);
      localStorage.removeItem(`note-scope-${courseId}-${noteSession}`);
      setNoteJobId(result.job_id);
      setNoteRecovery("queued");
      setNoteJobStage("");
      setNoteCoverage(undefined);
    } catch (error) {
      setModelError(error instanceof Error ? error.message : "笔记任务提交失败");
    } finally { setIsSending(false); }
  }

  async function cancelNote() {
    if (!courseId || !noteSession || isSending || noteCancelling) return;
    setNoteCancelling(true); setModelError("");
    try {
      await api(`/agent/cancel-note?conversation_id=${encodeURIComponent(conversationId.current)}`,
        "POST", { course_id: courseId, session_id: noteSession });
      setNotePrompt(undefined);
      setNoteSession(null);
      setNoteRecovery(null);
      setNoteJobId(null);
      setNoteJobStage("");
      setNoteJobError("");
      setNoteMaterials([]);
      localStorage.removeItem(`note-uploads-${courseId}-${noteSession}`);
      localStorage.removeItem(`note-scope-${courseId}-${noteSession}`);
      setNoteScope("");
      setMessages(current => [...current, { from: "agent", text: "已取消笔记生成。你可以继续聊天，或重新发起笔记任务。" }]);
    } catch (error) {
      setModelError(error instanceof ApiError && error.status === 404
        ? "取消接口尚未在当前后端生效。请重启后端并刷新页面，然后重试取消。"
        : error instanceof Error ? error.message : "取消笔记任务失败");
    }
    finally { setNoteCancelling(false); }
  }

  async function recoverNote() {
    if (!courseId || !noteSession || isSending) return;
    setIsSending(true); setModelError("");
    try {
      if (noteJobId) {
        await api(`/agent/retry-note?course_id=${encodeURIComponent(courseId)}&conversation_id=${encodeURIComponent(conversationId.current)}&job_id=${encodeURIComponent(noteJobId)}`, "POST");
        setNoteRecovery("queued"); setNoteJobError(""); setNoteJobStage("");
        return;
      }
      const result = await noteRequest(`/agent/recover?course_id=${encodeURIComponent(courseId)}&session_id=${encodeURIComponent(noteSession)}&conversation_id=${encodeURIComponent(conversationId.current)}`);
      showAgentResult(result);
      const history = await api<{ items: StoredMessage[]; active_note?: ActiveNote | null }>(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(conversationId.current)}/messages`);
      setMessages(history.items.map(restoreMessage));
      setNoteSession(history.active_note?.session_id ?? null);
      setNoteJobId(history.active_note?.job_id ?? null);
      setNoteJobStage(history.active_note?.stage ?? "");
      setNoteCoverage(history.active_note?.coverage);
      setNotePrompt(history.active_note?.status === "needs_input" ? history.active_note.prompt : undefined);
      setNoteRecovery(["queued", "running", "failed"].includes(history.active_note?.status ?? "") ? history.active_note!.status as "queued" | "running" | "failed" : null);
      restoreNoteInput(history.active_note, courseId, () => mounted.current);
    } catch (error) { setModelError(error instanceof Error ? error.message : "恢复笔记任务失败"); setNoteRecovery("failed"); }
    finally { setIsSending(false); }
  }

  useEffect(() => {
    const container = messagesRef.current;
    if (container) container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }, [messages, isSending]);

  useEffect(() => {
    if (!modelMenuOpen) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (!modelPickerRef.current?.contains(event.target as Node)) setModelMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    modelPickerRef.current?.querySelector<HTMLButtonElement>(".model-option[aria-selected='true']")?.focus();
    return () => document.removeEventListener("pointerdown", closeOnOutsideClick);
  }, [modelMenuOpen]);

  function handleModelKeys(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      setModelMenuOpen(false);
      modelTriggerRef.current?.focus();
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (!modelMenuOpen) { setModelMenuOpen(true); return; }
    const options = [...(modelPickerRef.current?.querySelectorAll<HTMLButtonElement>(".model-option") ?? [])];
    const current = options.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
      : (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
    options[next]?.focus();
  }

  async function send(value = draft) {
    const message = value.trim();
    if (!message || isSending) return;
    if (composerUploading) { setModelError("请等待文件上传完成后再发送。"); return; }
    if (composerMaterials.some(item => item.parse_status !== "ready")) {
      setModelError("请等待附件处理完成；处理失败的文件请移除后重新上传。"); return;
    }
    if (!courseId) { setModelError("请先到“课程管理”选择一门课程"); return; }
    if (historyLoading || historyFailed) return;
    if (noteSession && ["取消生成", "取消笔记生成", "停止生成"].includes(message.replace(/[！!。\s]/g, ""))) {
      setDraft("");
      await cancelNote();
      return;
    }
    if (noteSession) { setModelError("请先完成或恢复当前笔记任务，再发送新消息"); return; }
    if (!modelId) { setModelError("请先选择一个可用模型"); return; }
    const history = messages;
    const requestConversationId = conversationId.current;
    const selectedLabel = models.find(item => item.id === modelId)?.label ?? modelId;
    setModelError("");
    setModelMenuOpen(false);
    setMessages([...history, { from: "user", text: message }]);
    setDraft("");
    setIsSending(true);
    try {
      const result = await api<DispatchResult>("/api/chat/dispatch", "POST", {
        message,
        course_id: courseId,
        conversation_id: requestConversationId,
        model_id: modelId,
        attachment_document_ids: composerMaterials.map(item => item.document_id),
      });
      if (!mounted.current || conversationId.current !== requestConversationId) return;
      if (result.kind === "note") {
        setNoteSession(result.session_id);
        if (composerMaterials.length) {
          setNoteMaterials(current => [...current.filter(item => !composerMaterials.some(
            attached => attached.document_id === item.document_id)), ...composerMaterials]);
          localStorage.setItem(`note-uploads-${courseId}-${result.session_id}`,
            JSON.stringify(composerMaterials.map(item => item.document_id)));
          setComposerMaterials([]);
        }
        showAgentResult(result.result);
        if (result.source_document_ids?.length && result.source_document_ids.length <= 5) {
          restoreNoteInput({ session_id: result.session_id, status: "needs_input",
            note_input: { source_document_ids: result.source_document_ids } }, courseId, () => mounted.current);
        }
      } else {
        setMessages(current => [...current, { from: "agent", text: result.reply,
          model: result.model || selectedLabel, citations: result.citations, quiz: result.quiz }]);
        setComposerMaterials([]);
        if (result.quiz_configuration && result.status === "needs_input") {
          setPendingQuiz({ quiz_input: result.quiz_configuration.quiz_input, scope: result.quiz_scope });
          setQuizError(result.quiz_configuration.conflicts.join("；"));
        }
      }
      if (!selectedConversationId) onConversationCreated(requestConversationId);
    } catch (error) {
      if (!mounted.current || conversationId.current !== requestConversationId) return;
      const detail = error instanceof Error ? error.message : "模型服务暂时不可用";
      let saved = false;
      try {
        const data = await api<{ items: StoredMessage[]; active_note?: ActiveNote | null }>(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(requestConversationId)}/messages`);
        if (!mounted.current || conversationId.current !== requestConversationId) return;
        saved = data.items.length > history.length;
        if (saved) {
          setMessages(data.items.map(restoreMessage));
          if (!selectedConversationId) onConversationCreated(requestConversationId);
          setNoteSession(data.active_note?.session_id ?? null);
          setNoteJobId(data.active_note?.job_id ?? null);
          setNoteRecovery(data.active_note?.status === "failed" ? "failed" : null);
        }
      } catch { /* The server may have rejected the message before saving it. */ }
      if (!saved) {
        setMessages(history);
        setDraft(message);
        setNoteSession(null);
      }
      setModelError(`发送失败：${detail}`);
    } finally {
      if (mounted.current && conversationId.current === requestConversationId) setIsSending(false);
    }
  }

  function newConversation() {
    if (isSending) return;
    onNewConversation();
  }

  async function confirmQuiz(input: QuizInput) {
    if (!courseId || isSending) return;
    const id = conversationId.current;
    setIsSending(true); setQuizError("");
    try {
      const response = await fetch("/api/chat/dispatch", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "Idempotency-Key": quizSubmitKey.current },
        body: JSON.stringify({ course_id: courseId, conversation_id: id, model_id: modelId,
          message: "确认试题配置", quiz_input: input }),
      });
      const result = await response.json() as DispatchResult & { detail?: string };
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "试卷生成请求失败");
      if (!mounted.current || conversationId.current !== id) return;
      if (result.kind !== "note" && result.quiz_configuration) {
        if (result.quiz_job || result.status === "configured") {
          setPendingQuiz(null);
          setQuizJob(result.quiz_job?.status === "succeeded" ? null : result.quiz_job ?? null); quizSubmitKey.current = crypto.randomUUID();
          setMessages(current => [...current, { from: "user", text: "确认试题配置" }, { from: "agent", text: result.reply, model: result.model }]);
          if (result.quiz_job?.status === "succeeded") {
            const history = await api<{ items: StoredMessage[] }>(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(id)}/messages`);
            if (mounted.current && conversationId.current === id) setMessages(history.items.map(restoreMessage));
          }
          composerTextareaRef.current?.focus();
        } else setQuizError(result.quiz_configuration.prompt?.message || "请检查试题配置");
      }
    } catch (error) {
      if (mounted.current && conversationId.current === id) setQuizError(error instanceof Error ? error.message : "配置保存失败");
    } finally { if (mounted.current && conversationId.current === id) setIsSending(false); }
  }

  async function retryQuiz() {
    if (!courseId || !quizJob) return;
    const id = conversationId.current;
    setIsSending(true); setModelError("");
    try {
      const value = await api<QuizJob>(`/api/courses/${encodeURIComponent(courseId)}/quiz-jobs/${encodeURIComponent(quizJob.job_id)}/retry`, "POST");
      if (mounted.current && conversationId.current === id) setQuizJob(value);
    } catch (error) {
      if (mounted.current && conversationId.current === id) setModelError(error instanceof Error ? error.message : "重试失败");
    } finally { if (mounted.current && conversationId.current === id) setIsSending(false); }
  }

  async function cancelQuiz() {
    if (!courseId || isSending) return;
    const id = conversationId.current;
    setIsSending(true); setQuizError("");
    try {
      await api(`/api/courses/${encodeURIComponent(courseId)}/conversations/${encodeURIComponent(id)}/cancel-quiz`, "POST");
      if (!mounted.current || conversationId.current !== id) return;
      setPendingQuiz(null);
      composerTextareaRef.current?.focus();
    } catch (error) {
      if (mounted.current && conversationId.current === id) setQuizError(error instanceof Error ? error.message : "取消失败");
    } finally { if (mounted.current && conversationId.current === id) setIsSending(false); }
  }

  return <div className="dialog-page" inert={Boolean(notePrompt || pendingQuiz)}><section className="chat">
    <header><div className="chat-title-area">{editingTitle ? <form className="chat-title-editor" onSubmit={event => { event.preventDefault(); void saveTitle(); }}><input autoFocus aria-label="对话名称" maxLength={100} value={titleDraft} onChange={event => setTitleDraft(event.target.value)} onKeyDown={event => { if (event.key === "Escape") setEditingTitle(false); }} /><button type="submit" disabled={savingTitle}>保存</button><button type="button" onClick={() => setEditingTitle(false)}>取消</button></form> : <button className="chat-title-button" type="button" disabled={!selectedConversationId} title={selectedConversationId ? "点击重命名对话" : "发送消息后可重命名"} onClick={() => { setTitleDraft(conversationTitle); setEditingTitle(true); }}><h2>{conversationTitle}</h2>{selectedConversationId && <span aria-hidden="true">✎</span>}</button>}</div><button className="new-chat" type="button" onClick={newConversation} disabled={isSending}>＋ 新对话</button></header>
    <div className="messages" ref={messagesRef} aria-live="polite">
      {historyLoading ? <div className="empty-chat" role="status">正在加载对话…</div> : messages.length === 0 && <div className="empty-chat chat-welcome">
        <div className="chat-welcome-content">
          <div className="chat-welcome-heading">
            <i className="chat-welcome-icon" aria-hidden="true"><span>✦</span></i>
            <h1>今天想从哪里开始？</h1>
          </div>
          <div className="chat-starters" role="group" aria-label="快捷提问">
            {[
              { label: "生成练习题", emoji: "📝", prompt: "请根据当前课程资料，给我生成一组练习题，并附上答案和解析。" },
              { label: "整理课程笔记", emoji: "📒", prompt: "请根据当前课程资料，帮我生成一份重点清晰的复习笔记。" },
              { label: "总结课程重点", emoji: "💡", prompt: "请根据当前课程资料，帮我总结核心知识点、常考内容和易错点。" },
            ].map(item => <button key={item.label} type="button" disabled={!courseId || isSending || historyFailed} onClick={() => {
              setDraft(item.prompt);
              composerTextareaRef.current?.focus();
            }}><span className="chat-starter-emoji" aria-hidden="true">{item.emoji}</span>{item.label}<span className="chat-starter-arrow" aria-hidden="true">↗</span></button>)}
          </div>
        </div>
      </div>}
      {messages.map((item, index) => <article className={`message ${item.from}${item.quiz ? " quiz-message" : ""}`} key={index}>
        <i aria-hidden="true">{item.from === "agent" ? "✦" : "你"}</i>
        <div><label>{item.from === "agent" ? item.model || "助手" : "你"}</label><p>{item.text}</p>{Boolean(item.citations?.length) && <ChatSources citations={item.citations!} />}{item.quiz && courseId && <ChatQuiz courseId={courseId} quiz={item.quiz} />}{item.quiz_draft && <button type="button" className="quiz-draft-card" onClick={() => setPreviewQuiz(item.quiz_draft!)}><strong>{item.quiz_draft.title}</strong><span>{item.quiz_draft.question_count} 题 · {item.quiz_draft.total_score} 分 · 查看试卷草稿 →</span></button>}{item.draft && <a className="note-draft-card" href={item.draft.url}><strong>{item.draft.title}</strong><span>打开笔记草稿 →</span></a>}</div>
      </article>)}
      {noteRecovery && noteSession && <article className="message agent note-job-message" role="status"><i aria-hidden="true">✦</i><div><label>助手</label><div className="note-job-status"><div className="note-job-copy"><span>{noteRecovery === "queued" ? "笔记任务已排队，正在等待生成。" : noteRecovery === "running" ? `正在生成笔记${noteJobStage ? ` · ${noteJobStage}` : "…"}` : `笔记生成失败${noteJobError ? `：${noteJobError}` : ""}`}</span>{noteCoverage && <small>{coverageSummary(noteCoverage)}</small>}</div><div className="note-job-actions">{noteRecovery === "failed" && <button type="button" className="note-job-retry" disabled={isSending} onClick={() => void recoverNote()}>{noteJobId ? "重试生成" : "恢复笔记任务"}</button>}<button type="button" className="note-job-cancel" disabled={noteCancelling} onClick={() => void cancelNote()}>{noteCancelling ? "正在取消…" : noteRecovery === "failed" ? "结束任务" : "取消生成"}</button></div></div></div></article>}
      {quizJob && <article className="message agent note-job-message" role="status"><i aria-hidden="true">✦</i><div><label>助手</label><p>{quizJob.status === "failed" ? `试卷生成失败：${quizJob.error || "请稍后重试"}` : `正在生成试卷 · ${quizStages[quizJob.stage] || "处理中"}${quizJob.progress ? ` · 已完成 ${quizJob.progress.completed_questions}/${quizJob.progress.total_questions} 题` : ""}`}</p>{quizJob.status === "failed" && <button type="button" disabled={isSending} onClick={() => void retryQuiz()}>重试生成试卷</button>}</div></article>}
      {previewQuiz && courseId && <QuizDraftPreview courseId={courseId} card={previewQuiz} onClose={() => setPreviewQuiz(null)} />}
      {isSending && <div className="chat-thinking" role="status">✦　正在生成回复…</div>}
    </div>
    {modelError && <p className="chat-error" role="alert">{modelError}</p>}
    {pendingQuiz && courseId && <QuizConfigDialog courseId={courseId} pending={pendingQuiz} busy={isSending} error={quizError} onConfirm={input => void confirmQuiz(input)} onCancel={() => void cancelQuiz()} />}
    {notePrompt && !pickerOpen && <NoteConfigDialog noteType={noteType} onNoteType={setNoteType} duration={noteDuration} onDuration={setNoteDuration} materials={noteMaterials} onChooseMaterials={() => setPickerOpen(true)} requirements={noteScope} onRequirements={value => { setNoteScope(value); if (courseId && noteSession) localStorage.setItem(`note-scope-${courseId}-${noteSession}`, value); }} onGenerate={() => void resumeNote()} onCancel={() => void cancelNote()} busy={isSending || noteCancelling} error={modelError} promptMessage={notePrompt.message} generationModel={noteModel} />}
    {pickerOpen && courseId && <NoteMaterialPicker courseId={courseId} selected={noteMaterials} onUploaded={item => { setNoteMaterials(current => current.some(existing => existing.document_id === item.document_id) ? current.map(existing => existing.document_id === item.document_id ? item : existing) : [...current, item]); if (noteSession) { const key = `note-uploads-${courseId}-${noteSession}`; const ids = JSON.parse(localStorage.getItem(key) || "[]") as string[]; localStorage.setItem(key, JSON.stringify([...new Set([...ids, item.document_id])])); } }} onConfirm={items => { setNoteMaterials(items); if (noteSession) { const key = `note-uploads-${courseId}-${noteSession}`; const ids = JSON.parse(localStorage.getItem(key) || "[]") as string[]; localStorage.setItem(key, JSON.stringify(ids.filter(id => items.some(item => item.document_id === id)))); } setPickerOpen(false); }} onClose={() => setPickerOpen(false)} />}
    <div className={`composer composer-attachment${composerDragOver ? " drag-over" : ""}`} onDragOver={event => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; setComposerDragOver(true); } }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setComposerDragOver(false); }} onDrop={event => { event.preventDefault(); setComposerDragOver(false); void attachComposerFiles(Array.from(event.dataTransfer.files)); }}>
      {composerDragOver && <div className="composer-drop-hint">松开以添加到当前课程资料</div>}
      {(composerUploading || composerMaterials.length > 0) && <div className="composer-attachments" aria-label="已添加的资料">{composerUploading && <span className="composer-attachment-chip">正在上传文件…</span>}{composerMaterials.map(item => <span className="composer-attachment-chip" key={item.document_id}><strong>{item.file_name || item.title}</strong><small>{item.parse_status === "ready" ? "可检索" : item.parse_status === "failed" ? "处理失败" : "处理中"}</small><button type="button" aria-label={`移除 ${item.file_name || item.title}`} onClick={() => setComposerMaterials(current => current.filter(candidate => candidate.document_id !== item.document_id))}>×</button></span>)}</div>}
      <div className="composer-row">
      <textarea ref={composerTextareaRef} rows={1} aria-label="输入你的问题" value={draft} disabled={!courseId || isSending || historyLoading || historyFailed} onChange={event => setDraft(event.target.value)} onKeyDown={event => {
        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
      }} placeholder={isSending ? "助手正在思考……" : "输入你的问题…"} />
      <div className="composer-controls"><div className="composer-left-actions"><button type="button" className="composer-attach-trigger" aria-label="添加课程文件" title="添加文件，也可直接拖入输入框" disabled={Boolean(noteSession) || isSending || composerUploading} onClick={() => composerFileInputRef.current?.click()}>＋</button><input ref={composerFileInputRef} className="composer-file-input" type="file" multiple accept=".md,.txt,.pdf,.ppt,.pptx,.doc,.docx,.png,.jpg,.jpeg,.webp" aria-label="选择聊天资料文件" onChange={event => void attachComposerFiles(Array.from(event.target.files ?? []))} /><div className="model-picker" ref={modelPickerRef} onKeyDown={handleModelKeys}>
        <button className="model-trigger" ref={modelTriggerRef} type="button" aria-label="选择聊天模型" title={models.find(model => model.id === modelId)?.label ?? "加载模型中…"} aria-haspopup="listbox" aria-expanded={modelMenuOpen} disabled={isSending || models.length === 0} onClick={() => setModelMenuOpen(open => !open)}>
          <span>{models.find(model => model.id === modelId)?.label ?? "加载模型中…"}</span>
        </button>
        {modelMenuOpen && <div className="model-menu" role="listbox" aria-label="聊天模型">
          <div className="model-menu-title">选择聊天模型</div>
          {models.map(model => <button className="model-option" type="button" role="option" aria-selected={model.id === modelId} key={model.id} onClick={() => {
            setModelId(model.id);
            setModelMenuOpen(false);
            modelTriggerRef.current?.focus();
          }}><span className="model-option-icon" aria-hidden="true">✦</span><span>{model.label}</span>{model.id === modelId && <span className="model-check" aria-hidden="true">✓</span>}</button>)}
        </div>}
      </div></div><button className="send" disabled={isSending || historyLoading || historyFailed || composerUploading || composerMaterials.some(item => item.parse_status !== "ready") || !draft.trim() || !modelId} onClick={() => void send()} aria-label="发送消息">{isSending ? "…" : "➤"}</button></div>
    </div>
    </div>
  </section></div>;
}
