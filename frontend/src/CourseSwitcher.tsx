import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type Course } from "./workbench-api";

export type Conversation = { conversation_id: string; title: string; updated_at: string };

export default function CourseSwitcher({ courses, course, conversationId, onCourse, onConversation, onNew, onRenamed, refreshKey }: {
  courses: Course[]; course: Course | null; conversationId: string | null;
  onCourse: (course: Course) => void; onConversation: (id: string) => void; onNew: () => void; onRenamed: () => void; refreshKey: number;
}) {
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const [courseListOpen, setCourseListOpen] = useState(false);
  const [items, setItems] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [context, setContext] = useState<{ id: string; title: string; x: number; y: number } | null>(null);
  const [editing, setEditing] = useState<{ id: string; title: string } | null>(null);
  const [renameError, setRenameError] = useState("");
  const [renaming, setRenaming] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const switchRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const pickerRef = useRef<HTMLDivElement>(null);
  const optionsRef = useRef<HTMLDivElement>(null);
  const contextRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState({ top: 0, left: 0, width: 360, maxHeight: 600 });
  useEffect(() => {
    if (courseListOpen) optionsRef.current?.querySelector<HTMLButtonElement>('[aria-selected="true"]')?.focus();
  }, [courseListOpen]);
  useEffect(() => {
    if (!open) return;
    const wasMobile = window.matchMedia("(max-width: 900px)").matches;
    const place = () => {
      const trigger = triggerRef.current?.getBoundingClientRect();
      if (!trigger) return;
      const mobile = window.matchMedia("(max-width: 900px)").matches;
      const width = Math.min(mobile ? 360 : 380, window.innerWidth - 24);
      const left = mobile ? Math.max(12, Math.min(trigger.left, window.innerWidth - width - 12))
        : Math.max(12, Math.min(trigger.right + 12, window.innerWidth - width - 12));
      const top = mobile ? trigger.bottom + 8 : Math.min(trigger.top, Math.max(12, window.innerHeight - 240));
      setPlacement({ top, left, width, maxHeight: Math.max(180, window.innerHeight - top - 12) });
    };
    place();
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!panelRef.current?.contains(target) && !triggerRef.current?.contains(target) && !switchRef.current?.contains(target) && !contextRef.current?.contains(target)) setOpen(false);
      else if (!pickerRef.current?.contains(target)) setCourseListOpen(false);
      if (!contextRef.current?.contains(target)) setContext(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (context) setContext(null);
      else if (courseListOpen) setCourseListOpen(false);
      else { setOpen(false); (openerRef.current ?? triggerRef.current)?.focus(); }
    };
    const onResize = () => {
      if (window.matchMedia("(max-width: 900px)").matches !== wasMobile) setOpen(false);
      else place();
    };
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", place, true);
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", place, true);
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, courseListOpen, context]);
  async function saveRename() {
    if (!course || !editing || renaming) return;
    const title = editing.title.trim();
    if (!title) { setRenameError("请输入对话名称"); return; }
    setRenaming(true); setRenameError("");
    try {
      const updated = await api<Conversation>(`/api/courses/${encodeURIComponent(course.course_id)}/conversations/${encodeURIComponent(editing.id)}`, "PATCH", { title });
      setItems(current => current.map(item => item.conversation_id === editing.id ? updated : item));
      setEditing(null);
      onRenamed();
    } catch (error) { setRenameError(error instanceof Error ? error.message : "重命名失败"); }
    finally { setRenaming(false); }
  }
  function handleCourseKeys(event: React.KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (!courseListOpen) { setCourseListOpen(true); return; }
    const options = [...(optionsRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? [])];
    const current = options.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
      : (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
    options[next]?.focus();
  }
  useEffect(() => {
    if (!open || !course) return;
    let active = true;
    setLoading(true); setError(""); setItems([]); setAll(false);
    api<{ items: Conversation[] }>(`/api/courses/${encodeURIComponent(course.course_id)}/conversations`)
      .then(data => { if (active) setItems(data.items); })
      .catch(e => { if (active) setError(e instanceof Error ? e.message : "对话加载失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, course?.course_id, refreshKey]);
  return <div className="course-switcher">
    <div className="course-switcher-heading"><span className="course-label">当前课程</span><button ref={switchRef} className="course-switch-action" type="button" aria-label="打开课程切换面板" aria-expanded={open} aria-haspopup="dialog" onClick={event => { openerRef.current = event.currentTarget; setOpen(value => !value); }}>切换<span className="course-chevron" aria-hidden="true" /></button></div>
    <button ref={triggerRef} className="course course-button" type="button" aria-expanded={open} aria-haspopup="dialog" aria-label={`当前课程 ${course?.name ?? "选择课程"}`} onClick={event => { openerRef.current = event.currentTarget; setOpen(value => !value); }}>
      <span className="course-card-icon" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M8 4h16a2 2 0 0 1 2 2v22H10a4 4 0 0 1-4-4V6a2 2 0 0 1 2-2Z" /><path d="M10 4v20M6 24h20M14 10h8M14 14h8M14 18h5" /></svg></span>
      <strong className="course-name">{course?.name ?? "选择课程"}</strong>
      <span className="course-chevron" aria-hidden="true" />
    </button>
    {open && createPortal(<div ref={panelRef} className="course-panel" role="dialog" aria-label="课程与对话" style={placement}>
      <div className="course-panel-heading course-panel-title"><div><small>学习空间</small><strong>切换课程与对话</strong></div><button type="button" onClick={() => setOpen(false)} aria-label="关闭切换面板">×</button></div>
      <div className="course-panel-course" ref={pickerRef} onKeyDown={handleCourseKeys}><span className="course-picker-label">当前课程</span><button type="button" className="course-picker-trigger" aria-label="切换课程" aria-haspopup="listbox" aria-expanded={courseListOpen} onClick={() => setCourseListOpen(value => !value)}><span className="course-picker-mark">▤</span><strong>{course?.name ?? "选择课程"}</strong><span className="course-picker-chevron" aria-hidden="true" /></button>{courseListOpen && <div className="course-picker-options" ref={optionsRef} role="listbox" aria-label="课程列表">{courses.filter(item => item.status !== "deleted").map(item => <button type="button" role="option" aria-selected={item.course_id === course?.course_id} key={item.course_id} onClick={() => { onCourse(item); setCourseListOpen(false); }}><span className="course-option-mark" aria-hidden="true">▤</span><span>{item.name}</span></button>)}</div>}</div>
      <div className="course-panel-heading course-panel-section"><div><strong>课程对话</strong><small>{loading ? "加载中" : `${items.length} 个对话`}</small></div><button className="course-panel-new" type="button" onClick={() => { onNew(); setOpen(false); }} disabled={!course}>＋ 新对话</button></div>
      {loading ? <p className="course-panel-state">正在加载对话…</p> : error ? <p className="course-panel-state" role="alert">{error} <button onClick={() => { setOpen(false); setTimeout(() => setOpen(true), 0); }}>重试</button></p> : items.length === 0 ? <p className="course-panel-state">这门课程还没有对话。点击“新对话”开始。</p> : <div className="course-conversations">{(all ? items : items.slice(0, 5)).map(item => <div className="course-conversation-row" key={item.conversation_id}>{editing?.id === item.conversation_id ? <form className="conversation-rename-form" onSubmit={event => { event.preventDefault(); void saveRename(); }}><input autoFocus aria-label="对话名称" maxLength={100} value={editing.title} onChange={event => setEditing({ id: item.conversation_id, title: event.target.value })} onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); setEditing(null); } }} /><div><button type="submit" disabled={renaming}>保存</button><button type="button" onClick={() => setEditing(null)}>取消</button></div>{renameError && <small role="alert">{renameError}</small>}</form> : <button type="button" className={conversationId === item.conversation_id ? "selected" : ""} onContextMenu={event => { event.preventDefault(); setContext({ id: item.conversation_id, title: item.title, x: Math.min(event.clientX, window.innerWidth - 132), y: Math.min(event.clientY, window.innerHeight - 60) }); }} onClick={() => { onConversation(item.conversation_id); setOpen(false); }} title="右键可重命名"><strong>{item.title || "未命名对话"}</strong><small>{new Date(item.updated_at).toLocaleString("zh-CN")}</small></button>}</div>)}</div>}
      {!loading && !error && items.length > 5 && <button className="more-conversations" type="button" onClick={() => setAll(value => !value)}>{all ? "收起列表 ↑" : `查看更多（共 ${items.length} 条） →`}</button>}
    </div>, document.body)}
    {open && context && createPortal(<div className="conversation-context-menu" ref={contextRef} role="menu" style={{ left: context.x, top: context.y }}><button type="button" role="menuitem" onClick={() => { setEditing({ id: context.id, title: context.title }); setRenameError(""); setContext(null); }}>重命名对话</button></div>, document.body)}
  </div>;
}
