import { useCallback, useEffect, useState } from "react";
import { Button } from "antd";
import { api, ApiError, type Course, type DeletionPreview, type Exam, type ExamInput } from "./workbench-api";
import "./workbench.css";

const questionTypes = [
  ["choice", "选择题"], ["fill_blank", "填空题"], ["true_false", "判断题"],
  ["short_answer", "简答题"], ["calculation", "计算题"], ["proof", "证明题"],
] as const;
const labels = Object.fromEntries(questionTypes);
const statusText = { active: "进行中", archived: "已归档", deleted: "回收期" };
const splitLines = (value: string) => value.split("\n").map(x => x.trim()).filter(Boolean);
const emptyExam = (): ExamInput => ({ name: "", exam_at: null, total_score: null, blueprint: [], emphasis: [], exclusions: [], notes: "", generation_preferences: {} });

type Props = { selectedId: string | null; onSelect: (course: Course | null) => void };
type CourseEdit = { kind: "create" | "edit"; course?: Course };

export default function Workbench({ selectedId, onSelect }: Props) {
  const [courses, setCourses] = useState<Course[]>([]);
  const [exams, setExams] = useState<Exam[]>([]);
  const [filter, setFilter] = useState<"active" | "archived" | "deleted">("active");
  const [courseEdit, setCourseEdit] = useState<CourseEdit | null>(null);
  const [examEdit, setExamEdit] = useState<Exam | "new" | null>(null);
  const [preview, setPreview] = useState<{ course: Course; data: DeletionPreview } | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [unauthorized, setUnauthorized] = useState(false);

  const refreshCourses = useCallback(async (preferId?: string) => {
    const data = await api<{ items: Course[] }>("/api/courses");
    const items = data.items;
    setCourses(items);
    const chosen = items.find(x => x.course_id === (preferId ?? selectedId)) ?? items.find(x => x.status !== "deleted") ?? null;
    onSelect(chosen);
    return chosen;
  }, [selectedId, onSelect]);

  useEffect(() => {
    let active = true;
    api<{ items: Course[] }>("/api/courses").then(data => {
      if (!active) return;
      setCourses(data.items);
      const chosen = data.items.find(x => x.course_id === selectedId) ?? data.items.find(x => x.status !== "deleted") ?? data.items[0] ?? null;
      onSelect(chosen);
      if (chosen?.status === "deleted") setFilter("deleted");
      setLoading(false);
    }).catch(e => {
      if (!active) return;
      setUnauthorized(e instanceof ApiError && e.status === 401);
      setError(e instanceof Error ? e.message : "课程加载失败");
      setLoading(false);
    });
    return () => { active = false; };
    // Initial load only. Subsequent mutations explicitly refresh the list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selected = courses.find(x => x.course_id === selectedId) ?? (courses.every(x => x.status === "deleted") ? courses[0] ?? null : null);
  useEffect(() => {
    if (!selected || selected.status === "deleted") { setExams([]); return; }
    let active = true;
    api<{ items: Exam[] }>(`/api/courses/${selected.course_id}/exams`).then(data => {
      if (active) setExams(data.items);
    }).catch(e => { if (active) setError(e instanceof Error ? e.message : "考试加载失败"); });
    return () => { active = false; };
  }, [selected?.course_id, selected?.status]);

  async function run(action: () => Promise<unknown>, success: string, preferId?: string) {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await action();
      const current = await refreshCourses(preferId ?? (result as Course | undefined)?.course_id);
      if (result && typeof result === "object" && "course_id" in result && !("exam_id" in result)) {
        setFilter(((result as Course).status ?? "active") as "active" | "archived" | "deleted");
      }
      if (current && current.status !== "deleted") {
        const data = await api<{ items: Exam[] }>(`/api/courses/${current.course_id}/exams`);
        setExams(data.items);
      }
      setNotice(success);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败，请重试");
      if (e instanceof ApiError && e.status === 409) {
        await refreshCourses(preferId).catch(() => {});
      }
      return false;
    } finally { setBusy(false); }
  }

  async function openPreview(course: Course) {
    setBusy(true); setError(""); setNotice("");
    try {
      const data = await api<DeletionPreview>(`/api/courses/${course.course_id}/deletion-preview`, "POST");
      setPreview({ course, data });
    } catch (e) { setError(e instanceof Error ? e.message : "无法取得删除影响"); }
    finally { setBusy(false); }
  }

  async function confirmDelete() {
    if (!preview) return;
    const { course, data } = preview;
    setBusy(true); setError("");
    try {
      const deleted = await api<Course>(`/api/courses/${course.course_id}/delete`, "POST", { confirmation_id: data.confirmation_id });
      setPreview(null);
      await refreshCourses();
      setFilter("deleted");
      setNotice(`“${course.name}”已删除，可在 ${new Date(deleted.purge_after ?? "").toLocaleDateString("zh-CN")} 前恢复。`);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 409 ? "影响范围或确认期限已变化，请重新查看后确认。" : e instanceof Error ? e.message : "删除失败");
      setPreview(null);
    } finally { setBusy(false); }
  }

  const visible = courses.filter(x => (x.status ?? "active") === filter);
  const counts = { active: courses.filter(x => !x.status || x.status === "active").length, archived: courses.filter(x => x.status === "archived").length, deleted: courses.filter(x => x.status === "deleted").length };

  return <div className="workbench">
    <div className="wb-heading"><div><span className="wb-eyebrow">课程管理</span><h1>把每一场考试，放回它的课程里。</h1><p>先整理课程，再记录考试范围与老师的要求。</p></div><Button type="primary" size="large" className="wb-create-button" onClick={() => setCourseEdit({ kind: "create" })}>＋ 新建课程</Button></div>
    {notice && <p className="wb-notice" role="status">{notice}</p>}
    {error && <p className="wb-error" role="alert">{error} <button onClick={() => { setError(""); refreshCourses().catch(e => setError(String(e))); }}>重新加载</button></p>}
    {unauthorized ? <SignIn onDone={() => { setUnauthorized(false); setError(""); setLoading(true); refreshCourses().then(() => setLoading(false)).catch(e => { setError(String(e)); setLoading(false); }); }} /> :
      <div className="wb-layout">
        <section className="wb-courses" aria-label="课程列表"><div className="wb-section-head"><h2>我的课程</h2><span>{courses.length} 门</span></div>
          <div className="wb-tabs" role="group" aria-label="课程状态">{(["active", "archived", "deleted"] as const).map(status => <button key={status} className={filter === status ? "selected" : ""} onClick={() => setFilter(status)}>{statusText[status]} <b>{counts[status]}</b></button>)}</div>
          {loading ? <p className="wb-empty">正在加载课程…</p> : visible.length === 0 ? <p className="wb-empty">{filter === "active" ? "还没有进行中的课程。点击“新建课程”开始。" : filter === "archived" ? "没有已归档课程。" : "回收期内没有已删除课程。"}</p> :
            <div className="wb-course-list">{visible.map(course => <button key={course.course_id} className={selectedId === course.course_id ? "wb-course-card current" : "wb-course-card"} onClick={() => onSelect(course)}><span className="wb-course-top"><span>{statusText[course.status ?? "active"]}</span><span>↗</span></span><strong>{course.name}</strong><small>{course.subject || "未填写学科"}{course.status === "deleted" && course.purge_after ? ` · ${new Date(course.purge_after).toLocaleDateString("zh-CN")} 前可恢复` : ""}</small></button>)}</div>}
        </section>
        <section className="wb-detail" aria-label="课程详情">{!selected ? <div className="wb-detail-empty"><span>✦</span><h2>选一门课程，开始整理考试。</h2><p>课程管理资料会保存在你的账户中。</p></div> : <>
          <div className="wb-detail-head"><div><span className="wb-eyebrow">{statusText[selected.status ?? "active"]} · 课程项目</span><h2>{selected.name}</h2><p>{selected.subject || "尚未填写学科"}</p></div><div className="wb-actions">{selected.status === "deleted" ? <button disabled={busy} onClick={() => run(() => api(`/api/courses/${selected.course_id}/restore`, "POST"), "课程已恢复。", selected.course_id)}>恢复课程</button> : <><button disabled={busy} onClick={() => setCourseEdit({ kind: "edit", course: selected })}>编辑课程</button>{selected.status === "archived" ? <button disabled={busy} onClick={() => run(() => api(`/api/courses/${selected.course_id}/restore`, "POST"), "课程已恢复。", selected.course_id)}>恢复课程</button> : <button disabled={busy} onClick={() => run(() => api(`/api/courses/${selected.course_id}/archive`, "POST"), "课程已归档。", selected.course_id)}>归档课程</button>}<button className="wb-danger-link" disabled={busy} onClick={() => openPreview(selected)}>删除课程</button></>}</div></div>
          {selected.status === "deleted" ? <div className="wb-recovery"><h3>课程处于回收期</h3><p>资料、考试、学习资产和作答记录已暂停使用。{selected.purge_after && `请在 ${new Date(selected.purge_after).toLocaleDateString("zh-CN")} 前恢复。`}</p></div> : <><div className="wb-exam-head"><div><span className="wb-eyebrow">考试安排</span><h3>关联考试 <small>{exams.length} 场</small></h3></div><button className="wb-primary" disabled={busy} onClick={() => setExamEdit("new")}>＋ 添加考试</button></div>
            {exams.length === 0 ? <p className="wb-empty wb-exam-empty">还没有考试。添加日期、题型和老师重点，之后可以继续补全。</p> : <div className="wb-exams">{exams.map(exam => <article key={exam.exam_id} className="wb-exam"><div className="wb-exam-date"><b>{exam.exam_at ? new Date(exam.exam_at).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" }) : "待定"}</b><small>{exam.exam_at ? new Date(exam.exam_at).getFullYear() : "日期"}</small></div><div className="wb-exam-main"><span>{exam.status === "archived" ? "已归档" : "进行中"}</span><h4>{exam.name}</h4><p>{exam.blueprint.length ? exam.blueprint.map(item => `${labels[item.question_type] ?? item.question_type} ${item.question_count} 题`).join(" · ") : "题型结构待补充"}{exam.total_score ? ` · 满分 ${exam.total_score}` : ""}</p>{exam.emphasis.length > 0 && <small>重点：{exam.emphasis.join("、")}</small>}</div><div className="wb-exam-actions">{exam.status === "active" ? <><button onClick={() => setExamEdit(exam)}>编辑</button><button disabled={busy} onClick={() => run(() => api(`/api/exams/${exam.exam_id}/archive`, "POST"), "考试已归档。", selected.course_id)}>归档</button></> : <span>只读</span>}</div></article>)}</div>}
          </>}
        </>}</section>
      </div>}
    {courseEdit && <CourseDialog edit={courseEdit} busy={busy} onClose={() => setCourseEdit(null)} onSave={async (name, subject) => {
      const ok = await run(async () => courseEdit.kind === "create" ? api<Course>("/api/courses", "POST", { name }) : api<Course>(`/api/courses/${courseEdit.course!.course_id}`, "PATCH", { name, subject, expected_updated_at: courseEdit.course!.updated_at }), courseEdit.kind === "create" ? "课程已创建。" : "课程已更新。", courseEdit.course?.course_id);
      if (ok) setCourseEdit(null);
    }} />}
    {examEdit && selected && <ExamDialog exam={examEdit} busy={busy} onClose={() => setExamEdit(null)} onSave={async input => {
      const ok = await run(() => examEdit === "new" ? api<Exam>(`/api/courses/${selected.course_id}/exams`, "POST", input) : api<Exam>(`/api/exams/${examEdit.exam_id}`, "PATCH", { ...input, expected_updated_at: examEdit.updated_at }), examEdit === "new" ? "考试已添加。" : "考试已更新。", selected.course_id);
      if (ok) setExamEdit(null);
    }} />}
    {preview && <div className="wb-overlay" role="presentation"><section className="wb-dialog wb-confirm" role="dialog" aria-modal="true" aria-labelledby="delete-title"><span className="wb-eyebrow">删除影响</span><h2 id="delete-title">删除“{preview.course.name}”？</h2><p>课程将在回收期内保留 30 天。确认后，该课程及关联内容会暂停使用。</p><div className="wb-impact"><span>资料 <b>{preview.data.impact.materials}</b></span><span>考试 <b>{preview.data.impact.exams}</b></span><span>学习资产 <b>{preview.data.impact.learning_assets}</b></span><span>作答记录 <b>{preview.data.impact.attempts}</b></span></div><p className="wb-expiry">本次确认有效至 {new Date(preview.data.expires_at).toLocaleString("zh-CN")}</p><div className="wb-dialog-actions"><button onClick={() => setPreview(null)} disabled={busy}>取消</button><button className="wb-danger" onClick={confirmDelete} disabled={busy}>{busy ? "正在删除…" : "确认删除课程"}</button></div></section></div>}
  </div>;
}

function SignIn({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState(""); const [password, setPassword] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  return <form className="wb-signin" onSubmit={async e => { e.preventDefault(); setBusy(true); setError(""); try { await api("/api/auth/sign-in", "POST", { email, password }); onDone(); } catch (err) { setError(err instanceof Error ? err.message : "登录失败"); } finally { setBusy(false); } }}><h2>登录后管理课程</h2><label>邮箱<input type="email" required value={email} onChange={e => setEmail(e.target.value)} /></label><label>密码<input type="password" required value={password} onChange={e => setPassword(e.target.value)} /></label>{error && <p role="alert">{error}</p>}<button className="wb-primary" disabled={busy}>{busy ? "正在登录…" : "登录"}</button></form>;
}

function CourseDialog({ edit, busy, onClose, onSave }: { edit: CourseEdit; busy: boolean; onClose: () => void; onSave: (name: string, subject: string) => void }) {
  const [name, setName] = useState(edit.course?.name ?? ""); const [subject, setSubject] = useState(edit.course?.subject ?? "");
  return <div className="wb-overlay" role="presentation"><form className="wb-dialog" role="dialog" aria-modal="true" aria-labelledby="course-title" onSubmit={e => { e.preventDefault(); onSave(name.trim(), subject.trim()); }}><span className="wb-eyebrow">课程项目</span><h2 id="course-title">{edit.kind === "create" ? "新建课程" : "编辑课程"}</h2><label>课程名称<input required maxLength={100} value={name} onChange={e => setName(e.target.value)} placeholder="例如：高等数学" /></label>{edit.kind === "edit" && <label>学科<input maxLength={100} value={subject} onChange={e => setSubject(e.target.value)} placeholder="可选" /></label>}<div className="wb-dialog-actions"><button type="button" onClick={onClose} disabled={busy}>取消</button><button className="wb-primary" disabled={busy || !name.trim()}>{busy ? "正在保存…" : "保存课程"}</button></div></form></div>;
}

function ExamDialog({ exam, busy, onClose, onSave }: { exam: Exam | "new"; busy: boolean; onClose: () => void; onSave: (input: ExamInput) => void }) {
  const initial = exam === "new" ? emptyExam() : exam;
  const [name, setName] = useState(initial.name); const [date, setDate] = useState(initial.exam_at?.slice(0, 10) ?? "");
  const [total, setTotal] = useState(initial.total_score?.toString() ?? ""); const [blueprint, setBlueprint] = useState(initial.blueprint);
  const [emphasis, setEmphasis] = useState(initial.emphasis.join("\n")); const [exclusions, setExclusions] = useState(initial.exclusions.join("\n"));
  const [notes, setNotes] = useState(initial.notes); const [preferences, setPreferences] = useState(String(initial.generation_preferences.instructions ?? ""));
  const [validation, setValidation] = useState("");
  function save(e: React.FormEvent) {
    e.preventDefault();
    if (blueprint.some(item => item.question_count < 1 || item.question_count > 100 || item.score <= 0)) { setValidation("题数应为 1–100，分值必须大于 0。"); return; }
    if (splitLines(emphasis).length > 30 || splitLines(exclusions).length > 30) { setValidation("重点与排除范围各最多 30 条。"); return; }
    onSave({ name: name.trim(), exam_at: date || null, total_score: total ? Number(total) : null, blueprint, emphasis: splitLines(emphasis), exclusions: splitLines(exclusions), notes, generation_preferences: { ...initial.generation_preferences, instructions: preferences.trim() } });
  }
  return <div className="wb-overlay" role="presentation"><form className="wb-dialog wb-exam-dialog" role="dialog" aria-modal="true" aria-labelledby="exam-title" onSubmit={save}><span className="wb-eyebrow">考试画像</span><h2 id="exam-title">{exam === "new" ? "添加考试" : "编辑考试"}</h2><p>可以先保存基本信息，之后再补全题型与分值。</p><div className="wb-form-grid"><label>考试名称<input required maxLength={100} value={name} onChange={e => setName(e.target.value)} placeholder="例如：期末考试" /></label><label>考试日期<input type="date" value={date} onChange={e => setDate(e.target.value)} /></label><label>满分<input type="number" min="0.01" max="10000" step="0.01" value={total} onChange={e => setTotal(e.target.value)} placeholder="暂不确定可留空" /></label></div><div className="wb-blueprint"><div className="wb-field-head"><strong>题型蓝图</strong><button type="button" disabled={blueprint.length >= 6} onClick={() => setBlueprint([...blueprint, { question_type: questionTypes.find(([id]) => !blueprint.some(x => x.question_type === id))?.[0] ?? "choice", question_count: 1, score: 1 }])}>＋ 添加题型</button></div>{blueprint.length === 0 ? <p>尚未填写题型，可以稍后补充。</p> : blueprint.map((item, index) => <div className="wb-blueprint-row" key={index}><select aria-label="题型" value={item.question_type} onChange={e => setBlueprint(blueprint.map((x, i) => i === index ? { ...x, question_type: e.target.value } : x))}>{questionTypes.map(([id, label]) => <option key={id} value={id} disabled={blueprint.some((x, i) => i !== index && x.question_type === id)}>{label}</option>)}</select><label>题数<input type="number" min="1" max="100" value={item.question_count} onChange={e => setBlueprint(blueprint.map((x, i) => i === index ? { ...x, question_count: Number(e.target.value) } : x))} /></label><label>该题型总分<input type="number" min="0.01" max="10000" step="0.01" value={item.score} onChange={e => setBlueprint(blueprint.map((x, i) => i === index ? { ...x, score: Number(e.target.value) } : x))} /></label><button type="button" aria-label="删除题型" onClick={() => setBlueprint(blueprint.filter((_, i) => i !== index))}>×</button></div>)}</div><div className="wb-form-grid"><label>老师重点 <small>每行一条</small><textarea value={emphasis} onChange={e => setEmphasis(e.target.value)} rows={3} /></label><label>排除范围 <small>每行一条</small><textarea value={exclusions} onChange={e => setExclusions(e.target.value)} rows={3} /></label></div><label>备注<textarea maxLength={4000} value={notes} onChange={e => setNotes(e.target.value)} rows={2} /></label><label>出题偏好<textarea value={preferences} onChange={e => setPreferences(e.target.value)} placeholder="例如：计算题多一些，先易后难" rows={2} /></label>{validation && <p className="wb-error" role="alert">{validation}</p>}<div className="wb-dialog-actions"><button type="button" disabled={busy} onClick={onClose}>取消</button><button className="wb-primary" disabled={busy || !name.trim()}>{busy ? "正在保存…" : "保存考试"}</button></div></form></div>;
}
