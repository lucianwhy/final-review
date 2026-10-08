import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./workbench-api";
import NoteMaterialPicker, { materialLabel, type NoteMaterial } from "./NoteMaterialPicker";

const questionTypes = [
  ["choice", "选择题"], ["true_false", "判断题"], ["fill_blank", "填空题"],
  ["short_answer", "简答题"], ["calculation", "计算题"], ["proof", "证明题"],
] as const;
type QuestionType = typeof questionTypes[number][0];
export type QuizInput = {
  exam_id?: string | null; scope_mode?: string | null; chapter?: string;
  knowledge_points?: string[]; blueprint?: { question_type: QuestionType; question_count: number }[] | null;
  duration_mode?: string | null; duration_minutes?: number | null; difficulty?: string | null;
  emphasis?: string[]; excluded_topics?: string[]; include_imported_questions?: boolean | null;
  source_document_ids?: string[] | null; source_types?: string[]; allow_ai_supplement?: boolean | null;
  total_score?: number | null;
};
export type PendingQuiz = { quiz_input: QuizInput; scope?: { source_document_ids?: string[] | null; selectable_document_ids?: string[] | null; chapter?: string } };
export type QuizConfiguration = { status: string; quiz_input: QuizInput; conflicts: string[]; missing: string[]; prompt?: { message: string } | null };

// Shares the note dialog's custom dropdown classes, including the popup itself.
function QuizSelect({ label, value, options, onChange, disabled }: {
  label: string; value: string; options: [string, string][]; onChange: (value: string) => void; disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLButtonElement>('[aria-selected="true"]')?.focus();
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  return <div className="note-type-field" ref={root} onKeyDown={event => {
    if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus(); }
    if (!open || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const buttons = [...(root.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? [])];
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    buttons[event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
  }}><span id={id} className="note-field-label">{label}</span><button ref={trigger} type="button" className="note-type-trigger" aria-labelledby={id} aria-haspopup="listbox" aria-expanded={open} disabled={disabled} onClick={() => setOpen(!open)} onKeyDown={event => {
    if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); setOpen(true); }
  }}><span>{options.find(item => item[0] === value)?.[1] || "请选择"}</span><span aria-hidden="true">⌄</span></button>{open && <div className="note-type-menu" role="listbox" aria-label={label}>{[["", "请选择"], ...options].map(([key, text]) => <button type="button" role="option" aria-selected={value === key} key={key} onClick={() => { onChange(key); setOpen(false); trigger.current?.focus(); }}><strong>{text}</strong>{value === key && <span aria-hidden="true">✓</span>}</button>)}</div>}</div>;
}

const lines = (value: string) => [...new Set(value.split(/[\n、，,]/).map(item => item.trim()).filter(Boolean))];
const sourceNames: Record<string, string> = { past_exam: "历年真题", teacher_ppt: "老师 PPT", homework: "平时作业", other_practice: "其他练习", crash_course: "速成课", external_upload: "外部上传" };

export default function QuizConfigDialog({ courseId, pending, busy, error, onConfirm, onCancel }: {
  courseId: string; pending: PendingQuiz; busy: boolean; error: string;
  onConfirm: (input: QuizInput) => void; onCancel: () => void;
}) {
  const [input, setInput] = useState<QuizInput>(pending.quiz_input);
  const [counts, setCounts] = useState<Record<string, string>>(() => Object.fromEntries(questionTypes.map(([type]) => [type, String(pending.quiz_input.blueprint?.find(item => item.question_type === type)?.question_count ?? 0)])));
  const [points, setPoints] = useState((input.knowledge_points ?? []).join("\n"));
  const [emphasis, setEmphasis] = useState((input.emphasis ?? []).join("\n"));
  const [excluded, setExcluded] = useState((input.excluded_topics ?? []).join("\n"));
  const [minutes, setMinutes] = useState(String(input.duration_minutes ?? ""));
  const [score, setScore] = useState(String(input.total_score ?? ""));
  const [materials, setMaterials] = useState<NoteMaterial[]>([]);
  const [picker, setPicker] = useState(false);
  const [localError, setLocalError] = useState("");
  const dialog = useRef<HTMLElement>(null);
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;
  const patch = (changes: Partial<QuizInput>) => setInput(current => ({ ...current, ...changes }));
  const total = Object.values(counts).reduce((sum, count) => sum + Number(count), 0);
  const valid = Object.values(counts).every(count => count !== "" && Number.isInteger(Number(count)) && Number(count) >= 0) && total >= 1 && total <= 100
    && Boolean(input.scope_mode && input.duration_mode && input.difficulty)
    && (input.scope_mode !== "chapter" || Boolean(input.chapter?.trim()))
    && (input.scope_mode !== "knowledge_points" || lines(points).length > 0)
    && (input.duration_mode !== "timed" || (Number.isInteger(Number(minutes)) && Number(minutes) >= 1 && Number(minutes) <= 240))
    && input.source_document_ids != null && (input.source_document_ids.length === 0 || (materials.length === input.source_document_ids.length && materials.every(item => item.parse_status === "ready")))
    && typeof input.allow_ai_supplement === "boolean" && input.include_imported_questions === false
    && lines(points).length <= 100 && lines(emphasis).length <= 30 && lines(excluded).length <= 30
    && [...lines(points), ...lines(emphasis), ...lines(excluded)].every(item => item.length <= 200)
    && (!score || (Number.isFinite(Number(score)) && Number(score) > 0 && Number(score) <= 10000));

  useEffect(() => {
    let active = true;
    api<{ items: NoteMaterial[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`).then(data => {
      if (active) setMaterials(data.items.filter(item => input.source_document_ids?.includes(item.document_id)));
    }).catch(reason => { if (active) setLocalError(reason instanceof Error ? reason.message : "资料读取失败"); });
    return () => { active = false; };
  }, [courseId, input.source_document_ids]);
  useEffect(() => {
    if (picker) return;
    dialog.current?.querySelector<HTMLButtonElement>(".note-type-trigger")?.focus();
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) { event.preventDefault(); if (!busy) cancelRef.current(); }
      if (event.key !== "Tab") return;
      const controls = [...(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled)') ?? [])].filter(item => item.getClientRects().length);
      if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0]?.focus(); }
    };
    document.addEventListener("keydown", keys);
    return () => document.removeEventListener("keydown", keys);
  }, [picker, busy]);

  return <>{createPortal(<div className="note-dialog-backdrop" style={picker ? { display: "none" } : undefined}><section ref={dialog} className="note-dialog quiz-config-dialog" role="dialog" aria-modal="true" aria-labelledby="quiz-dialog-title">
    <header className="note-dialog-head"><div><small>AI 对话 · 生成试题</small><h2 id="quiz-dialog-title">补充试题要求</h2><p>确认出题范围、题型和生成依据，已识别的要求可直接修改。</p></div><button type="button" className="note-dialog-close" aria-label="取消试题配置" disabled={busy} onClick={onCancel}>×</button></header>
    <div className="note-dialog-body">
      <div className="note-dialog-grid"><QuizSelect label="出题范围" value={input.scope_mode ?? ""} options={[["course", "全课程"], ["chapter", "指定章节"], ["knowledge_points", "指定知识点"]]} disabled={busy} onChange={value => patch({ scope_mode: value || null, chapter: value === "course" ? "" : pending.scope?.chapter || input.chapter, knowledge_points: [] })} /><QuizSelect label="难度" value={input.difficulty ?? ""} options={[["basic", "基础"], ["standard", "标准"], ["advanced", "进阶"], ["mixed", "混合"]]} disabled={busy} onChange={value => patch({ difficulty: value || null })} /></div>
      {(input.scope_mode === "chapter" || (input.scope_mode === "knowledge_points" && pending.scope?.chapter)) && <label>章节<input maxLength={200} value={input.chapter ?? ""} disabled={busy} onChange={event => patch({ chapter: event.target.value })} placeholder="例如：第三章" /></label>}
      {input.scope_mode === "knowledge_points" && <label>知识点<textarea value={points} disabled={busy} onChange={event => setPoints(event.target.value)} placeholder="每行一个知识点，每个知识点最多 6 题" /></label>}
      <div className="note-dialog-source"><div className="note-dialog-source-head"><strong>题型与数量</strong><span className={total > 100 ? "quiz-count-error" : ""}>共 {total || 0} / 100 题</span></div><div className="note-dialog-grid">{questionTypes.map(([type, label]) => <label key={type}>{label}<input type="number" min={0} max={100} step={1} value={counts[type]} disabled={busy} onChange={event => setCounts(current => ({ ...current, [type]: event.target.value }))} /></label>)}</div><small>不需要的题型填 0，总计 1～100 题。</small></div>
      <div className="note-dialog-grid"><QuizSelect label="计时方式" value={input.duration_mode ?? ""} options={[["untimed", "不限时"], ["timed", "限时"]]} disabled={busy} onChange={value => patch({ duration_mode: value || null, duration_minutes: null })} />{input.duration_mode === "timed" ? <label>限时（分钟）<input type="number" min={1} max={240} step={1} value={minutes} disabled={busy} onChange={event => setMinutes(event.target.value)} /></label> : <label>总分 · 可选<input type="number" min={0.01} max={10000} step="any" value={score} disabled={busy} onChange={event => setScore(event.target.value)} /></label>}</div>
      {input.duration_mode === "timed" && <label>总分 · 可选<input type="number" min={0.01} max={10000} step="any" value={score} disabled={busy} onChange={event => setScore(event.target.value)} /></label>}
      <div className="note-dialog-source"><div className="note-dialog-source-head"><strong>生成依据 · 必选</strong><button type="button" disabled={busy} onClick={() => setPicker(true)}>选择资料</button></div><QuizSelect label="资料范围" value={input.source_document_ids == null ? "" : input.source_document_ids.length ? "selected" : "all"} options={[["all", "当前允许范围的全部可用资料"], ["selected", "指定资料"]]} disabled={busy} onChange={value => { if (value === "selected") setPicker(true); else patch({ source_document_ids: value === "all" ? [] : null }); }} /><div className="note-config-files">{materials.map(item => <span key={item.document_id}>{materialLabel(item)}{item.parse_status !== "ready" ? " · 未就绪" : ""}</span>)}</div>{pending.scope?.chapter && <small>当前对话限定章节：{pending.scope.chapter}</small>}{pending.scope?.source_document_ids && <small>资料选择受当前对话指定的文件范围限制。</small>}{Boolean(input.source_types?.length) && <small>保留已识别的来源类别限制：{input.source_types?.map(type => sourceNames[type] ?? type).join("、")}<button type="button" disabled={busy} onClick={() => patch({ source_types: [] })}>清除类别限制</button></small>}</div>
      <div className="note-dialog-grid"><QuizSelect label="AI 补充题" value={input.allow_ai_supplement == null ? "" : String(input.allow_ai_supplement)} options={[["false", "不允许"], ["true", "允许（资料不足时补充）"]]} disabled={busy} onChange={value => patch({ allow_ai_supplement: value === "" ? null : value === "true" })} /><QuizSelect label="纳入导入题" value={input.include_imported_questions == null ? "" : String(input.include_imported_questions)} options={[["false", "不纳入"]]} disabled={busy} onChange={value => patch({ include_imported_questions: value === "" ? null : false })} /></div>
      <div className="note-dialog-grid"><label>重点 · 可选<textarea value={emphasis} disabled={busy} onChange={event => setEmphasis(event.target.value)} placeholder="每行一项" /></label><label>排除内容 · 可选<textarea value={excluded} disabled={busy} onChange={event => setExcluded(event.target.value)} placeholder="每行一项" /></label></div>
      <p className="quiz-config-hint">请选择必填项后确认。当前支持配置保存，正式试卷生成将在后续版本开放；独立导入题暂不支持。</p>
      {(error || localError) && <p className="note-dialog-error" role="alert">{error || localError}</p>}
    </div>
    <footer className="note-dialog-footer"><p>聊天模型负责理解意图；确认后保存本次试题配置。</p><div><button type="button" className="note-dialog-cancel" disabled={busy} onClick={onCancel}>取消</button><button type="button" className="note-dialog-submit" disabled={busy || !valid} onClick={() => onConfirm({ ...input, chapter: input.scope_mode === "course" ? "" : input.chapter?.trim() || "", knowledge_points: input.scope_mode === "knowledge_points" ? lines(points) : [], blueprint: questionTypes.filter(([type]) => Number(counts[type]) > 0).map(([type]) => ({ question_type: type, question_count: Number(counts[type]) })), duration_minutes: input.duration_mode === "timed" ? Number(minutes) : null, total_score: score ? Number(score) : null, emphasis: lines(emphasis), excluded_topics: lines(excluded) })}>{busy ? "正在保存…" : "确认试题配置"}</button></div></footer>
  </section></div>, document.body)}{picker && <NoteMaterialPicker courseId={courseId} selected={materials} maxSelection={100} purpose="试题" allowedIds={pending.scope?.selectable_document_ids ?? pending.scope?.source_document_ids} onUploaded={() => {}} onConfirm={items => { setMaterials(items); patch({ source_document_ids: items.map(item => item.document_id) }); setPicker(false); }} onClose={() => setPicker(false)} />}</>;
}
