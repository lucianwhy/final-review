import { useEffect, useState } from "react";
import { api } from "./workbench-api";

export type ChatCitation = {
  chunk_id: string; document_id: string; course_id: string; title: string;
  file_name?: string; citation_number?: number; chapter?: string;
  chunk_ordinal?: number; position_kind?: "document" | "page" | "slide"; position?: number | null;
};
export type ChatQuizCard = { session_id: string; question_count: number };
type Question = {
  id: string; knowledge_point: string; stem: string; options: string[];
  citations: ChatCitation[]; reference_answer?: string; explanation?: string; must_include?: string[];
};

export function ChatSources({ citations }: { citations: ChatCitation[] }) {
  return <div className="chat-sources" aria-label="回答资料来源">{citations.map(citation => {
    const position = citation.position_kind === "page" && citation.position != null
      ? `第 ${citation.position} 页` : citation.position_kind === "slide" && citation.position != null
        ? `第 ${citation.position} 张幻灯片` : `片段 ${(citation.chunk_ordinal ?? 0) + 1}`;
    const url = `#materials/${encodeURIComponent(citation.course_id)}/${encodeURIComponent(citation.document_id)}/${encodeURIComponent(citation.chunk_id)}`;
    return <a key={citation.chunk_id} href={url}>
      {citation.citation_number ? `[资料${citation.citation_number}] ` : ""}
      {citation.file_name || citation.title} · {citation.chapter ? `${citation.chapter} · ` : ""}{position} · 查看原文
    </a>;
  })}</div>;
}

export function ChatQuiz({ courseId, quiz }: { courseId: string; quiz: ChatQuizCard }) {
  const [questions, setQuestions] = useState<Question[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [showAnswers, setShowAnswers] = useState(false);
  const path = `/api/courses/${encodeURIComponent(courseId)}/chat-quizzes/${encodeURIComponent(quiz.session_id)}`;
  useEffect(() => {
    let active = true;
    setLoading(true); setError(""); setShowAnswers(false); setQuestions([]);
    api<{ questions: Question[] }>(path).then(data => {
      if (active) setQuestions(data.questions);
    }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : "练习题加载失败");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [path]);

  async function reveal() {
    setLoading(true); setError("");
    try {
      const data = await api<{ questions: Question[] }>(`${path}?include_answers=true`);
      setQuestions(data.questions); setShowAnswers(true);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "答案加载失败"); }
    finally { setLoading(false); }
  }
  return <section className="chat-quiz" aria-label="课程练习题">
    <strong>{quiz.question_count} 道课程练习题</strong>
    {error && <p role="alert">{error}</p>}
    {loading && <span role="status">正在读取练习题…</span>}
    {questions.map((question, index) => <article key={question.id}>
      <h3>{index + 1}. {question.stem}</h3>
      {question.options.length > 0 && <ul>{question.options.map((option, number) => <li key={number}>{option}</li>)}</ul>}
      <small>知识点：{question.knowledge_point}</small>
      <ChatSources citations={question.citations} />
    </article>)}
    {questions.length > 0 && !showAnswers && <button type="button" disabled={loading} onClick={() => void reveal()}>查看答案与解析</button>}
    {showAnswers && <div className="chat-quiz-answers"><h3>答案与解析</h3>{questions.map((question, index) => <article key={question.id}>
      <strong>第 {index + 1} 题</strong><p>{question.reference_answer}</p><p>{question.explanation}</p>
      {Boolean(question.must_include?.length) && <small>答题要点：{question.must_include!.join("、")}</small>}
    </article>)}</div>}
  </section>;
}
