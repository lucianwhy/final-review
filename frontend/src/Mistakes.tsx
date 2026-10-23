import { useEffect, useMemo, useState } from "react";
import { Modal } from "antd";
import ReactMarkdown from "react-markdown";
import { api, type Course } from "./workbench-api";
import "./mistakes.css";

type Answer = string | number | boolean;
type Question = { stem: string; question_type: string; options: string[]; knowledge_point: string; score: number; reference_answer: string; explanation: string; source_label: string; references: { file_name: string; quote: string }[] };
type Grade = { student_answer: Answer | null; score: number; max_score: number; feedback: string; missing_points: string[] };
type Mistake = { mistake_id: string; course_id: string; attempt_id: string; quiz_revision_id: string; question_revision_id: string; exam_id: string | null; exam_name: string | null; quiz_title: string; chapter: string; knowledge_point: string; question_type: string; question: Question; grade: Grade; reason: string; mastery_status: "mastered" | "unmastered"; created_at: string };
type Practice = { practice_id: string; mistake_id: string; status: "in_progress" | "submitted"; answer: Answer | null; result: { correct: boolean | null; score: number | null; reference_answer: string; explanation: string; grading_mode: "deterministic" | "self_review" } | null; created_at: string };
type PracticeDetail = { practice: Practice; mistake: Mistake };
type Filters = { course_id: string; exam_id: string; chapter: string; knowledge_point: string; question_type: string; mastery_status: string };
const empty: Filters = { course_id: "", exam_id: "", chapter: "", knowledge_point: "", question_type: "", mastery_status: "" };
const labels: Record<string, string> = { choice: "选择题", true_false: "判断题", fill_blank: "填空题", short_answer: "简答题", calculation: "计算题", proof: "证明题" };
function answerText(question: Question, answer: Answer | null) {
  if (answer === null) return "未作答";
  if (question.question_type === "choice" && typeof answer === "number") return question.options[answer] ?? "无效选项";
  if (question.question_type === "true_false" && typeof answer === "boolean") return answer ? "正确" : "错误";
  return String(answer);
}
function options(items: Mistake[], key: keyof Filters) { return [...new Set(items.map(item => item[key as keyof Mistake]).filter((value): value is string => typeof value === "string" && Boolean(value)))].sort(); }

export default function Mistakes({ courses, selectedCourseId }: { courses: Course[]; selectedCourseId: string | null }) {
  const [filters, setFilters] = useState<Filters>({ ...empty, course_id: selectedCourseId ?? "" });
  const [all, setAll] = useState<Mistake[]>([]);
  const [items, setItems] = useState<Mistake[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [practice, setPractice] = useState<PracticeDetail | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [history, setHistory] = useState<{ mistakeId: string; items: Practice[] } | null>(null);
  const courseItems = useMemo(() => all.filter(item => !filters.course_id || item.course_id === filters.course_id), [all, filters.course_id]);
  useEffect(() => { setFilters(current => ({ ...current, course_id: selectedCourseId ?? "" })); }, [selectedCourseId]);
  const reload = () => api<{ items: Mistake[] }>("/api/mistakes").then(value => setAll(value.items));
  useEffect(() => { void reload().catch(failure => setError(failure instanceof Error ? failure.message : "错题读取失败")); }, []);
  useEffect(() => {
    let active = true;
    const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => Boolean(value)));
    setLoading(true); setError("");
    void api<{ items: Mistake[] }>(`/api/mistakes?${query}`).then(value => { if (active) setItems(value.items); })
      .catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "错题筛选失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [filters]);
  function setFilter(key: keyof Filters, value: string) { setFilters(current => ({ ...current, [key]: value })); }
  async function mark(item: Mistake) {
    setBusyId(item.mistake_id); setError("");
    try {
      const updated = await api<Mistake>(`/api/courses/${encodeURIComponent(item.course_id)}/mistakes/${encodeURIComponent(item.mistake_id)}/mastery`, "PUT", { status: item.mastery_status === "mastered" ? "unmastered" : "mastered" });
      setItems(current => current.map(value => value.mistake_id === item.mistake_id ? updated : value)
        .filter(value => !filters.mastery_status || value.mastery_status === filters.mastery_status));
      setAll(current => current.map(value => value.mistake_id === item.mistake_id ? updated : value));
    } catch (failure) { setError(failure instanceof Error ? failure.message : "标记失败"); }
    finally { setBusyId(null); }
  }
  async function start(item: Mistake) {
    setBusyId(item.mistake_id); setError("");
    try {
      const value = await api<PracticeDetail>(`/api/courses/${encodeURIComponent(item.course_id)}/mistakes/${encodeURIComponent(item.mistake_id)}/practices`, "POST");
      setPractice(value); setAnswer(null);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "重练创建失败"); }
    finally { setBusyId(null); }
  }
  async function showHistory(item: Mistake) {
    try {
      const value = await api<{ items: Practice[] }>(`/api/courses/${encodeURIComponent(item.course_id)}/mistakes/${encodeURIComponent(item.mistake_id)}/practices`);
      setHistory({ mistakeId: item.mistake_id, items: value.items });
    } catch (failure) { setError(failure instanceof Error ? failure.message : "练习记录读取失败"); }
  }
  async function openPractice(item: Mistake, task: Practice) {
    try {
      const value = await api<PracticeDetail>(`/api/courses/${encodeURIComponent(item.course_id)}/mistake-practices/${encodeURIComponent(task.practice_id)}`);
      setPractice(value); setAnswer(value.practice.answer);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "练习读取失败"); }
  }
  async function submit() {
    if (!practice || answer === null || answer === "") return;
    setBusyId(practice.practice.practice_id); setError("");
    try {
      const value = await api<PracticeDetail>(`/api/courses/${encodeURIComponent(practice.mistake.course_id)}/mistake-practices/${encodeURIComponent(practice.practice.practice_id)}/submit`, "POST", { answer });
      setPractice(value);
      if (history?.mistakeId === value.mistake.mistake_id) void showHistory(value.mistake);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "重练提交失败"); }
    finally { setBusyId(null); }
  }
  const q = practice?.mistake.question;
  const finished = practice?.practice.status === "submitted";
  return <section className="mistakes-page"><header><h1>我的错题</h1><p>正式测验评分后自动收录。筛选、标记和重练均保留原始作答与题目版本。</p></header>
    <div className="mistake-filters" aria-label="错题筛选">
      <label>课程<select aria-label="课程筛选" value={filters.course_id} onChange={e => setFilter("course_id", e.target.value)}><option value="">全部课程</option>{courses.filter(item => item.status !== "deleted").map(item => <option key={item.course_id} value={item.course_id}>{item.name}</option>)}</select></label>
      <label>考试<select aria-label="考试筛选" value={filters.exam_id} onChange={e => setFilter("exam_id", e.target.value)}><option value="">全部考试</option>{options(courseItems, "exam_id").map(value => <option key={value} value={value}>{courseItems.find(item => item.exam_id === value)?.exam_name || value}</option>)}</select></label>
      <label>章节<select aria-label="章节筛选" value={filters.chapter} onChange={e => setFilter("chapter", e.target.value)}><option value="">全部章节</option>{options(courseItems, "chapter").map(value => <option key={value}>{value}</option>)}</select></label>
      <label>知识点<select aria-label="知识点筛选" value={filters.knowledge_point} onChange={e => setFilter("knowledge_point", e.target.value)}><option value="">全部知识点</option>{options(courseItems, "knowledge_point").map(value => <option key={value}>{value}</option>)}</select></label>
      <label>题型<select aria-label="题型筛选" value={filters.question_type} onChange={e => setFilter("question_type", e.target.value)}><option value="">全部题型</option>{options(courseItems, "question_type").map(value => <option key={value} value={value}>{labels[value] || value}</option>)}</select></label>
      <label>掌握状态<select aria-label="掌握状态筛选" value={filters.mastery_status} onChange={e => setFilter("mastery_status", e.target.value)}><option value="">全部状态</option><option value="unmastered">未掌握</option><option value="mastered">已掌握</option></select></label>
    </div>
    <div className="mistake-summary"><span>{loading ? "正在加载…" : `共 ${items.length} 道错题`}</span><button type="button" onClick={() => setFilters({ ...empty, course_id: selectedCourseId ?? "" })}>清除筛选</button></div>
    {error && <p role="alert" className="mistake-error">{error}</p>}
    {!loading && items.length === 0 && <div className="mistake-empty">{Object.values(filters).some(Boolean) ? "当前筛选下没有错题。" : "还没有错题。完成并评分一场正式测验后，这里会自动收录答错的题目。"}</div>}
    <div className="mistake-list">{items.map(item => <article className="mistake-card" key={item.mistake_id}>
      <div className="mistake-meta"><strong>{item.quiz_title}</strong><span>{item.exam_name || "未关联考试"}</span><span>{item.chapter || "未标章节"}</span><span>{labels[item.question_type] || item.question_type}</span><span>{item.mastery_status === "mastered" ? "已掌握" : "未掌握"}</span></div>
      <h2><ReactMarkdown>{item.question.stem}</ReactMarkdown></h2><p>知识点：{item.knowledge_point}</p>
      <details><summary>查看原作答、答案与解析</summary><p>你的答案：{answerText(item.question, item.grade.student_answer)}</p><p>参考答案：{item.question.reference_answer}</p><p>错因：{item.reason}</p><ReactMarkdown>{item.question.explanation}</ReactMarkdown><p>来源：{item.question.source_label}</p>{item.question.references?.map((ref, index) => <blockquote key={index}>{ref.file_name}：{ref.quote}</blockquote>)}<small>原作答 {item.attempt_id} · 题目版本 {item.question_revision_id}</small></details>
      <div className="mistake-actions"><button type="button" disabled={busyId === item.mistake_id} onClick={() => void mark(item)}>{item.mastery_status === "mastered" ? "标记未掌握" : "标记已掌握"}</button><button type="button" disabled={busyId === item.mistake_id} onClick={() => void start(item)}>重新练习</button><button type="button" onClick={() => void showHistory(item)}>练习记录</button></div>
      {history?.mistakeId === item.mistake_id && <div className="mistake-history"><strong>练习记录</strong>{history.items.length ? history.items.map(task => <button type="button" key={task.practice_id} onClick={() => void openPractice(item, task)}>{new Date(task.created_at).toLocaleString("zh-CN")} · {task.status === "submitted" ? "已提交" : "待作答"}</button>) : <p>暂无重练记录</p>}</div>}
    </article>)}</div>
    <Modal open={Boolean(practice)} title="错题重练" onCancel={() => setPractice(null)} footer={null} destroyOnHidden width={680}>
      {practice && q && <div className="mistake-practice"><p>练习记录：{practice.practice.practice_id}</p><p>来源：{practice.mistake.quiz_title} · {practice.mistake.question_revision_id}</p><ReactMarkdown>{q.stem}</ReactMarkdown>
        {q.question_type === "choice" ? q.options.map((option, index) => <label key={index}><input type="radio" name="practice-choice" disabled={finished} checked={answer === index} onChange={() => setAnswer(index)} />{String.fromCharCode(65 + index)}. {option}</label>) : q.question_type === "true_false" ? <div><label><input type="radio" name="practice-bool" disabled={finished} checked={answer === true} onChange={() => setAnswer(true)} />正确</label><label><input type="radio" name="practice-bool" disabled={finished} checked={answer === false} onChange={() => setAnswer(false)} />错误</label></div> : <textarea aria-label="重练答案" disabled={finished} value={typeof answer === "string" ? answer : ""} onChange={event => setAnswer(event.target.value)} rows={5} placeholder="在此作答" />}
        {!finished && <button type="button" disabled={busyId !== null || answer === null || answer === ""} onClick={() => void submit()}>提交重练答案</button>}
        {finished && practice.practice.result && <div className="mistake-practice-result" role="status"><strong>{practice.practice.result.grading_mode === "self_review" ? "已提交，请对照参考答案自查" : practice.practice.result.correct ? "重练正确" : "重练仍需复习"}</strong><p>你的答案：{answerText(q, practice.practice.answer)}</p><p>参考答案：{practice.practice.result.reference_answer}</p><ReactMarkdown>{practice.practice.result.explanation}</ReactMarkdown><p>掌握状态请在错题卡片中手动标记。</p></div>}
      </div>}
    </Modal>
  </section>;
}
