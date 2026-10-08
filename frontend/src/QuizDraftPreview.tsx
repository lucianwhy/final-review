import { useEffect, useState } from "react";
import { Modal } from "antd";
import ReactMarkdown from "react-markdown";
import { api } from "./workbench-api";
import "./quiz-drafts.css";

export type QuizDraftCard = { asset_id: string; revision_id: string; title: string; question_count: number; total_score: number };
export type QuizJob = { job_id: string; status: "queued" | "running" | "failed" | "succeeded"; stage: string; progress?: { completed_questions: number; total_questions: number }; error?: string; result?: QuizDraftCard };
export const quizStages: Record<string, string> = { queued: "等待生成", retrieving: "读取资料", planning: "分配考点与题型", generating: "生成题目", validating: "检查题目结构与来源", reviewing: "复核答案与解析", repairing: "修复题目", publishing: "保存草稿" };
type Ref = { document_id: string; chunk_id: string; file_name: string; quote: string; available?: boolean; position_kind?: string; position?: number };
type Question = { question_revision_id: string; order: number; score: number; question_type: string; knowledge_point: string; stem: string; options: string[]; reference_answer: string; explanation: string; must_include: string[]; common_mistakes: string[]; scoring_tips: string; source_label: string; references: Ref[] };
type Paper = { revision: { title: string; state: string; total_score: number; questions: Question[]; coverage: { read_chunks: number; available_chunks: number; partial: boolean } } };
const typeLabels: Record<string, string> = { choice: "选择题", true_false: "判断题", fill_blank: "填空题", short_answer: "简答题", calculation: "计算题", proof: "证明题" };

export default function QuizDraftPreview({ courseId, card, onClose }: { courseId: string; card: QuizDraftCard; onClose: () => void }) {
  const [paper, setPaper] = useState<Paper | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setPaper(null); setError("");
    void api<Paper>(`/api/courses/${encodeURIComponent(courseId)}/quizzes/${encodeURIComponent(card.asset_id)}/revisions/${encodeURIComponent(card.revision_id)}`)
      .then(value => { if (active) setPaper(value); })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : "草稿读取失败"); });
    return () => { active = false; };
  }, [courseId, card.asset_id, card.revision_id]);
  return <Modal open title="试卷草稿预览" onCancel={onClose} footer={null} width={880} styles={{ body: { maxHeight: "72vh", overflowY: "auto" } }}>
    {error ? <p role="alert">{error}</p> : !paper ? <p role="status">正在读取草稿…</p> : <div className="quiz-draft-preview">
      <h2>{paper.revision.title}</h2>
      <p>{paper.revision.questions.length} 题 · 共 {paper.revision.total_score} 分 · 草稿</p>
      <p>已读取 {paper.revision.coverage.read_chunks} / {paper.revision.coverage.available_chunks} 个资料片段{paper.revision.coverage.partial ? "，本次仅覆盖部分资料。" : "。"}</p>
      <p>编辑、确认与开始测试将在后续开放。</p>
      {paper.revision.questions.map(q => <article key={q.question_revision_id}>
        <div className="quiz-question-meta"><strong>第 {q.order} 题 · {typeLabels[q.question_type]} · {q.score} 分</strong><span>{q.source_label}</span></div>
        <small>考点：{q.knowledge_point}</small>
        <ReactMarkdown>{q.stem}</ReactMarkdown>
        {q.options.length > 0 && <ol type="A">{q.options.map((option, index) => <li key={index}>{option.replace(/^[A-H][.．、:：]\s*/, "")}</li>)}</ol>}
        <details><summary>查看答案、解析与得分点</summary>
          <h4>参考答案</h4><ReactMarkdown>{q.reference_answer}</ReactMarkdown>
          <h4>解析</h4><ReactMarkdown>{q.explanation}</ReactMarkdown>
          <h4>必答点</h4><ul>{q.must_include.map((point, i) => <li key={i}>{point}</li>)}</ul>
          <h4>常见失分点</h4><ul>{q.common_mistakes.map((point, i) => <li key={i}>{point}</li>)}</ul>
          <p>{q.scoring_tips}</p>
        </details>
        {q.references.map((ref, index) => <details key={index}><summary>来源：{ref.file_name}{ref.position != null ? ` · 第 ${ref.position} ${ref.position_kind === "slide" ? "张幻灯片" : "页"}` : ""}</summary>
          <blockquote>{ref.quote}</blockquote>
          {ref.available !== false ? <a href={`#materials/${encodeURIComponent(courseId)}/${encodeURIComponent(ref.document_id)}/${encodeURIComponent(ref.chunk_id)}`} onClick={onClose}>查看原文 →</a> : <span>原资料已不可用，保留来源摘录。</span>}
        </details>)}
      </article>)}
    </div>}
  </Modal>;
}
