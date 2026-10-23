import { useEffect, useRef, useState } from "react";
import { Modal } from "antd";
import ReactMarkdown from "react-markdown";
import { api } from "./workbench-api";
import "./formal-attempt.css";

type AnswerValue = string | number | boolean | null;
type SavedAnswer = { answer: AnswerValue; saved_at: string };
type Grade = { question_revision_id: string; score: number; max_score: number; student_answer: AnswerValue; reference_answer: string; feedback: string; missing_points: string[]; improvement_advice: string; rubric: { criterion: string; met: boolean; reason: string }[] };
type GradeSnapshot = { total_score: number; max_score: number; disclaimer: string | null; items: Grade[] };
type ResultReference = { document_id: string; chunk_id: string; file_name: string; quote: string; available?: boolean };
type ResultItem = Grade & { explanation: string; source_label: string; references: ResultReference[] };
type FollowUp = { followup_id: string; question_revision_id: string; message: string; answer: string; citations: ResultReference[] };
type ResultDetail = { items: ResultItem[]; followups: FollowUp[] };
type Attempt = {
  attempt_id: string; quiz_revision_id: string; status: "in_progress" | "submitted";
  started_at: string; deadline_at: string | null; version: number;
  answers: Record<string, SavedAnswer>; elapsed_seconds: number | null;
  submission_reason: string | null;
  grading_snapshot?: GradeSnapshot | null;
};
type Question = { question_revision_id: string; question_type: string; knowledge_point: string; stem: string; options: string[]; order: number; score: number };
type Detail = { attempt: Attempt; quiz: { title: string; total_score: number }; questions: Question[] };

function shownAnswer(question: Question, value: AnswerValue): string {
  if (value == null) return "未作答";
  if (question.question_type === "choice" && typeof value === "number") {
    const option = question.options[value];
    return option == null ? "无效选项" : `${String.fromCharCode(65 + value)}. ${option.replace(/^[A-H][.．、:：]\s*/, "")}`;
  }
  if (question.question_type === "true_false" && typeof value === "boolean") {
    return value ? "正确" : "错误";
  }
  return String(value);
}

export default function FormalAttemptView({ courseId, attemptId, onClose, onSubmitted }: {
  courseId: string; attemptId: string; onClose: () => void; onSubmitted?: () => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [drafts, setDrafts] = useState<Record<string, AnswerValue>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [result, setResult] = useState<ResultDetail | null>(null);
  const [followUpDrafts, setFollowUpDrafts] = useState<Record<string, string>>({});
  const timeoutRequested = useRef(false);
  const gradingRequested = useRef(false);
  const root = `/api/courses/${encodeURIComponent(courseId)}/formal-attempts/${encodeURIComponent(attemptId)}`;

  async function load() {
    const value = await api<Detail>(root);
    setDetail(value);
    setDrafts(Object.fromEntries(Object.entries(value.attempt.answers).map(([id, item]) => [id, item.answer])));
    if (value.attempt.status === "submitted") onSubmitted?.();
  }

  useEffect(() => {
    let active = true;
    timeoutRequested.current = false;
    gradingRequested.current = false;
    setDetail(null); setError("");
    setResult(null);
    void api<Detail>(root).then(value => {
      if (!active) return;
      setDetail(value);
      setDrafts(Object.fromEntries(Object.entries(value.attempt.answers).map(([id, item]) => [id, item.answer])));
    }).catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "作答读取失败"); });
    return () => { active = false; };
  }, [root]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const remaining = detail?.attempt.deadline_at
    ? Math.max(0, Math.ceil((new Date(detail.attempt.deadline_at).getTime() - now) / 1000)) : null;
  useEffect(() => {
    if (remaining !== 0 || detail?.attempt.status !== "in_progress" || timeoutRequested.current) return;
    timeoutRequested.current = true;
    void load().catch(failure => setError(failure instanceof Error ? failure.message : "超时交卷状态读取失败"));
  }, [remaining, detail?.attempt.status]);

  useEffect(() => {
    if (detail?.attempt.status !== "submitted" || detail.attempt.grading_snapshot || gradingRequested.current) return;
    gradingRequested.current = true;
    void api<Detail>(root + "/grade", "POST")
      .then(value => { setDetail(value); onSubmitted?.(); })
      .catch(failure => setError(failure instanceof Error ? failure.message : "评分暂未完成，可重试"));
  }, [detail?.attempt.status, detail?.attempt.grading_snapshot, root]);

  useEffect(() => {
    if (!detail?.attempt.grading_snapshot) return;
    let active = true;
    void api<ResultDetail>(root + "/result")
      .then(value => { if (active) setResult(value); })
      .catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "逐题结果读取失败"); });
    return () => { active = false; };
  }, [detail?.attempt.grading_snapshot, root]);

  async function save(questionId: string) {
    if (!detail) return;
    setBusy(true); setError("");
    try {
      const updated = await api<Detail>(root + "/answers", "PUT", {
        question_revision_id: questionId,
        answer: drafts[questionId] ?? null,
        expected_version: detail.attempt.version,
      });
      setDetail(updated);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "答案保存失败，请重新加载");
    } finally { setBusy(false); }
  }

  async function submit() {
    setBusy(true); setError("");
    try {
      const updated = await api<Detail>(root + "/submit", "POST");
      setDetail(updated); onSubmitted?.();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "交卷失败");
    } finally { setBusy(false); }
  }

  async function retryGrade() {
    setBusy(true); setError("");
    try { setDetail(await api<Detail>(root + "/grade", "POST")); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "评分重试失败"); }
    finally { setBusy(false); }
  }

  async function ask(questionId: string) {
    const message = followUpDrafts[questionId]?.trim();
    if (!message) return;
    setBusy(true); setError("");
    try {
      await api(root + `/result/questions/${encodeURIComponent(questionId)}/follow-up`, "POST", { message });
      setResult(await api<ResultDetail>(root + "/result"));
      setFollowUpDrafts(current => ({ ...current, [questionId]: "" }));
    } catch (failure) { setError(failure instanceof Error ? failure.message : "追问失败"); }
    finally { setBusy(false); }
  }

  const active = detail?.attempt.status === "in_progress";
  const snapshot = detail?.attempt.grading_snapshot;
  return <Modal open title={detail?.quiz.title ?? "在线测试"} onCancel={onClose} footer={null} width={920} styles={{ body: { maxHeight: "76vh", overflowY: "auto" } }}>
    {error && <p role="alert" className="formal-error">{error}</p>}
    {!detail ? <p role="status">正在读取作答…</p> : <div className="formal-attempt">
      <p>共 {detail.questions.length} 题 · 满分 {detail.quiz.total_score} 分 · {active ? "作答中" : "已交卷"}</p>
      <p role="timer">{remaining == null ? "不限时" : active ? `剩余 ${Math.floor(remaining / 60)} 分 ${remaining % 60} 秒` : `用时 ${detail.attempt.elapsed_seconds ?? 0} 秒`}</p>
      {snapshot && <section className="formal-grade-summary"><h3>本次得分：{snapshot.total_score} / {snapshot.max_score}</h3>
        {snapshot.disclaimer && <p>{snapshot.disclaimer}</p>}
        <button type="button" onClick={() => { onClose(); window.location.hash = "#mistakes"; }}>查看我的错题</button></section>}
      {detail.questions.map(question => {
        const id = question.question_revision_id;
        const value = drafts[id];
        const saved = detail.attempt.answers[id]?.answer;
        const grade = snapshot?.items.find(item => item.question_revision_id === id);
        const resultItem = result?.items.find(item => item.question_revision_id === id);
        return <article key={id}>
          <h3>第 {question.order} 题 · {question.score} 分</h3>
          <small>考点：{question.knowledge_point}</small>
          <ReactMarkdown>{question.stem}</ReactMarkdown>
          {question.question_type === "choice" && <div className="formal-options">{question.options.map((option, index) => <label key={index}>
            <input type="radio" name={id} disabled={!active} checked={value === index} onChange={() => setDrafts(current => ({ ...current, [id]: index }))} />
            {String.fromCharCode(65 + index)}. {option.replace(/^[A-H][.．、:：]\s*/, "")}
          </label>)}</div>}
          {question.question_type === "true_false" && <div className="formal-options">{[true, false].map(item => <label key={String(item)}>
            <input type="radio" name={id} disabled={!active} checked={value === item} onChange={() => setDrafts(current => ({ ...current, [id]: item }))} />{item ? "正确" : "错误"}
          </label>)}</div>}
          {question.question_type === "fill_blank" && <label>填写答案<input disabled={!active} value={typeof value === "string" ? value : ""} onChange={event => setDrafts(current => ({ ...current, [id]: event.target.value }))} /></label>}
          {!["choice", "true_false", "fill_blank"].includes(question.question_type) && <label>文字作答<textarea disabled={!active} value={typeof value === "string" ? value : ""} onChange={event => setDrafts(current => ({ ...current, [id]: event.target.value }))} /></label>}
          {active && <div className="formal-save"><button type="button" disabled={busy} onClick={() => void save(id)}>保存本题答案</button>
            <span>{saved === undefined ? "尚未保存" : value === saved ? "已保存" : "有未保存修改"}</span></div>}
          {grade && <section className="formal-grade"><h4>得分 {grade.score} / {grade.max_score}</h4>
            <p><strong>你的答案：</strong>{shownAnswer(question, grade.student_answer)}</p>
            <p><strong>参考答案：</strong>{grade.reference_answer}</p>
            <p><strong>反馈：</strong>{grade.feedback}</p>
            {grade.rubric.length > 0 && <ul>{grade.rubric.map((item, index) => <li key={index}>{item.met ? "已覆盖" : "未覆盖"}：{item.criterion}（{item.reason}）</li>)}</ul>}
            {grade.missing_points.length > 0 && <p><strong>缺失点：</strong>{grade.missing_points.join("；")}</p>}
            {grade.improvement_advice && <p><strong>改进建议：</strong>{grade.improvement_advice}</p>}
          </section>}
          {resultItem && <section className="formal-result-extra">
            <h4>解析</h4><ReactMarkdown>{resultItem.explanation}</ReactMarkdown>
            <p>来源：{resultItem.source_label}</p>
            {resultItem.references.map(ref => <details key={ref.chunk_id}><summary>{ref.file_name}</summary>
              <blockquote>{ref.quote}</blockquote>
              {ref.available !== false ? <a href={`#materials/${encodeURIComponent(courseId)}/${encodeURIComponent(ref.document_id)}/${encodeURIComponent(ref.chunk_id)}`} onClick={onClose}>查看原文 →</a> : <span>原资料已不可用，保留引用摘录。</span>}
            </details>)}
            <h4>针对本题追问</h4>
            {result?.followups.filter(item => item.question_revision_id === id).map(item => <div className="formal-followup" key={item.followup_id}>
              <p><strong>你问：</strong>{item.message}</p><p><strong>回答：</strong>{item.answer}</p>
            </div>)}
            <label>继续追问<input value={followUpDrafts[id] ?? ""} maxLength={4000} onChange={event => setFollowUpDrafts(current => ({ ...current, [id]: event.target.value }))} /></label>
            <button type="button" disabled={busy || !followUpDrafts[id]?.trim()} onClick={() => void ask(id)}>发送追问</button>
          </section>}
        </article>;
      })}
      {active ? <div className="formal-submit"><p>交卷时只提交已保存的答案。</p><button type="button" disabled={busy} onClick={() => void submit()}>{busy ? "处理中…" : "交卷"}</button></div>
        : !snapshot && <div className="formal-submit"><p role="status">作答已保存，评分暂未完成。</p>
          <button type="button" disabled={busy} onClick={() => void retryGrade()}>重试评分</button></div>}
    </div>}
  </Modal>;
}
