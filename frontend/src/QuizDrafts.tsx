import { useEffect, useState } from "react";
import { api } from "./workbench-api";
import QuizDraftPreview, { type QuizDraftCard } from "./QuizDraftPreview";

type Asset = { asset_id: string; latest_revision_id: string; title: string; status: string };
export default function QuizDrafts({ courseId }: { courseId: string | null }) {
  const [items, setItems] = useState<Asset[]>([]);
  const [selected, setSelected] = useState<QuizDraftCard | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let active = true;
    setItems([]); setSelected(null); setError(""); setLoading(Boolean(courseId));
    if (courseId) void api<{ items: Asset[] }>(`/api/courses/${encodeURIComponent(courseId)}/quizzes`)
      .then(data => { if (active) setItems(data.items.filter(item => Boolean(item.latest_revision_id))); })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : "试卷列表读取失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [courseId]);
  return <section className="quiz-drafts-list"><h1>试卷草稿</h1><p>在 AI 对话中说明出卷要求，确认配置后生成试卷草稿。</p>
    {error && <p role="alert">{error}</p>}
    {loading ? <p role="status">正在加载…</p> : !items.length && <p>{courseId ? "当前课程还没有试卷草稿。" : "请先选择课程。"}</p>}
    {items.map(item => <button type="button" key={item.asset_id} onClick={() => setSelected({ asset_id: item.asset_id, revision_id: item.latest_revision_id, title: item.title, question_count: 0, total_score: 0 })}><strong>{item.title}</strong><span>查看草稿 →</span></button>)}
    {selected && courseId && <QuizDraftPreview courseId={courseId} card={selected} onClose={() => setSelected(null)} />}
  </section>;
}
