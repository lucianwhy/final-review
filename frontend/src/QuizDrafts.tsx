import { useEffect, useState } from "react";
import { api } from "./workbench-api";
import FormalAttemptView from "./FormalAttemptView";
import QuizDraftPreview, { type QuizDraftCard } from "./QuizDraftPreview";

type Asset = { asset_id: string; latest_revision_id: string; title: string; status: string };
type AttemptSummary = { attempt_id: string; quiz_revision_id: string; status: string; started_at: string };
export default function QuizDrafts({ courseId }: { courseId: string | null }) {
  const [items, setItems] = useState<Asset[]>([]);
  const [selected, setSelected] = useState<QuizDraftCard | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [attempts, setAttempts] = useState<AttemptSummary[]>([]);
  const [activeAttemptId, setActiveAttemptId] = useState<string | null>(null);
  const [attemptRefresh, setAttemptRefresh] = useState(0);
  useEffect(() => {
    if (!courseId) return;
    const key = `open-formal-attempt-${courseId}`;
    const pendingId = sessionStorage.getItem(key);
    if (pendingId) { setActiveAttemptId(pendingId); sessionStorage.removeItem(key); }
  }, [courseId]);
  useEffect(() => {
    let active = true;
    setItems([]); setSelected(null); setError(""); setLoading(Boolean(courseId));
    if (courseId) void api<{ items: Asset[] }>(`/api/courses/${encodeURIComponent(courseId)}/quizzes`)
      .then(data => { if (active) setItems(data.items.filter(item => Boolean(item.latest_revision_id))); })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : "试卷列表读取失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [courseId]);
  useEffect(() => {
    let active = true;
    setAttempts([]);
    if (courseId) void api<{ items: AttemptSummary[] }>(`/api/courses/${encodeURIComponent(courseId)}/formal-attempts`)
      .then(value => { if (active) setAttempts(value.items); })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : "作答记录读取失败"); });
    return () => { active = false; };
  }, [courseId, attemptRefresh]);
  return <section className="quiz-drafts-list"><h1>试卷与在线测试</h1><p>在 AI 对话中生成试卷草稿，编辑确认后开始测试；作答记录可在下方找回。</p>
    {error && <p role="alert">{error}</p>}
    {loading ? <p role="status">正在加载…</p> : !items.length && <p>{courseId ? "当前课程还没有试卷。" : "请先选择课程。"}</p>}
    {items.map(item => <button type="button" key={item.asset_id} onClick={() => setSelected({ asset_id: item.asset_id, revision_id: item.latest_revision_id, title: item.title, question_count: 0, total_score: 0 })}><strong>{item.title}</strong><span>{item.status === "confirmed" ? "查看最新版本 →" : "查看草稿 →"}</span></button>)}
    {attempts.length > 0 && <section className="quiz-attempt-list"><h2>我的作答</h2>
      {attempts.map(attempt => <button type="button" key={attempt.attempt_id} onClick={() => setActiveAttemptId(attempt.attempt_id)}>
        <strong>{new Date(attempt.started_at).toLocaleString("zh-CN")}</strong>
        <span>{attempt.status === "in_progress" ? "继续作答" : "查看已交卷记录"}</span>
      </button>)}
    </section>}
    {selected && courseId && <QuizDraftPreview courseId={courseId} card={selected} onClose={() => setSelected(null)} onAttemptStarted={attemptId => {
      setSelected(null); setActiveAttemptId(attemptId); setAttemptRefresh(value => value + 1);
    }} onRevisionSaved={revisionId => {
      setSelected(current => current ? { ...current, revision_id: revisionId } : null);
      setItems(current => current.map(item => item.asset_id === selected.asset_id ? { ...item, latest_revision_id: revisionId } : item));
    }} />}
    {activeAttemptId && courseId && <FormalAttemptView courseId={courseId} attemptId={activeAttemptId} onClose={() => setActiveAttemptId(null)} onSubmitted={() => setAttemptRefresh(value => value + 1)} />}
  </section>;
}
