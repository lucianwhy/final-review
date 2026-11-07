import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import "./styles.css";
import Workbench from "./Workbench";
import Materials from "./Materials";
import Notes from "./Notes";
import Topbar from "./Topbar";
import ChatHome from "./ChatHome";
import QuizDrafts from "./QuizDrafts";
import Mistakes from "./Mistakes";
import { api, type Course } from "./workbench-api";
import CourseSwitcher from "./CourseSwitcher";
import NavigationIcon, { type NavigationIconName } from "./NavigationIcon";

type Page = "home" | "workbench" | "materials" | "notes" | "quiz" | "mistakes" | "report";
const navigation: [Page, NavigationIconName, string][] = [["home", "chat", "AI 对话"], ["workbench", "courses", "课程管理"], ["materials", "materials", "我的资料"], ["notes", "notes", "我的笔记"], ["quiz", "quiz", "模拟测验"], ["mistakes", "mistakes", "我的错题"], ["report", "report", "学习报告"]];
function initialPage(): Page {
  const hash = window.location.hash;
  if (/^#materials(?:\/|$)/.test(hash)) return "materials";
  if (/^#note\//.test(hash) || hash === "#notes") return "notes";
  if (/^#chat\//.test(hash) || hash === "#home") return "home";
  if (hash === "#quiz") return "quiz";
  if (hash === "#mistakes") return "mistakes";
  if (hash === "#report") return "report";
  return "workbench";
}
function App() {
  const [page, setPage] = useState<Page>(initialPage);
  const [routeHash, setRouteHash] = useState(window.location.hash);
  const acceptedHash = useRef(window.location.hash);
  const [course, setCourse] = useState<Course | null>(null);
  const [courseReady, setCourseReady] = useState(false);
  const [courses, setCourses] = useState<Course[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(() => /^#chat\/[^/]+\/([^/]+)$/.exec(window.location.hash)?.[1] ?? null);
  const [chatKey, setChatKey] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  useEffect(() => {
    let active = true;
    api<{ items: Course[] }>("/api/courses").then(data => {
      if (!active) return;
      setCourses(data.items);
      const linked = /^#chat\/([^/]+)/.exec(window.location.hash)?.[1] ?? (/^#materials\/([^/]+)/.exec(window.location.hash)?.[1] ?? "");
      const stored = localStorage.getItem("current-course-id");
      const chosen = data.items.find(item => item.course_id === linked && item.status !== "deleted")
        ?? data.items.find(item => item.course_id === stored && item.status !== "deleted")
        ?? data.items.find(item => item.status === "active") ?? data.items.find(item => item.status === "archived") ?? null;
      setCourse(chosen);
      if (chosen) localStorage.setItem("current-course-id", chosen.course_id);
      else localStorage.removeItem("current-course-id");
      if (chosen && !window.location.hash.startsWith("#chat/")) setConversationId(localStorage.getItem(`current-conversation-${chosen.course_id}`));
      setCourseReady(true);
      if (linked && chosen?.course_id !== linked && window.location.hash.startsWith("#chat/")) { setConversationId(null); window.location.hash = chosen ? `#chat/${chosen.course_id}` : ""; }
    }).catch(() => { if (active) { setCourses([]); setCourseReady(true); } });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    const onHashChange = (event: HashChangeEvent) => {
      if (window.location.hash === acceptedHash.current) return;
      if (!window.dispatchEvent(new Event("note-navigation", { cancelable: true }))) { event.stopImmediatePropagation(); window.history.replaceState(null, "", acceptedHash.current); return; }
      acceptedHash.current = window.location.hash;
      setRouteHash(window.location.hash);
      const hash = window.location.hash;
      if (/^#materials(?:\/|$)/.test(hash)) setPage("materials");
      else if (hash.startsWith("#note/") || hash === "#notes") setPage("notes");
      else if (hash.startsWith("#chat/")) {
        setPage("home");
        const match = /^#chat\/([^/]+)(?:\/([^/]+))?$/.exec(hash);
        if (match) { setConversationId(match[2] ?? null); const linked = courses.find(item => item.course_id === match[1] && item.status !== "deleted"); if (linked) { setCourse(linked); localStorage.setItem("current-course-id", linked.course_id); if (match[2]) localStorage.setItem(`current-conversation-${linked.course_id}`, match[2]); } }
      } else { const target = hash.slice(1) as Page; setPage(["workbench", "quiz", "mistakes", "report", "home"].includes(target) ? target : "workbench"); }
    };
    window.addEventListener("hashchange", onHashChange, true);
    return () => window.removeEventListener("hashchange", onHashChange, true);
  }, [courses]);
  const onSelectCourse = useCallback((value: Course | null) => {
    if (course?.course_id !== value?.course_id) { setConversationId(value ? localStorage.getItem(`current-conversation-${value.course_id}`) : null); setChatKey(key => key + 1); }
    setCourse(value);
    if (value) setCourses(items => [...items.filter(item => item.course_id !== value.course_id), value]);
    void api<{ items: Course[] }>("/api/courses").then(data => setCourses(data.items)).catch(() => {});
    if (value && value.status !== "deleted") localStorage.setItem("current-course-id", value.course_id);
  }, [course?.course_id]);
  const selectCourse = (value: Course) => {
    if (!window.dispatchEvent(new Event("note-navigation", { cancelable: true }))) return;
    onSelectCourse(value);
    if (page === "notes") window.location.hash = "#notes";
    if (page === "home") { const remembered = localStorage.getItem(`current-conversation-${value.course_id}`); window.location.hash = `#chat/${value.course_id}${remembered ? `/${remembered}` : ""}`; }
    if (page === "materials" && window.location.hash.startsWith("#materials/")) window.location.hash = "#materials";
  };
  const selectConversation = (id: string) => {
    if (!window.dispatchEvent(new Event("note-navigation", { cancelable: true }))) return;
    if (!course) return;
    localStorage.setItem(`current-conversation-${course.course_id}`, id);
    setConversationId(id); setPage("home"); window.location.hash = `#chat/${course.course_id}/${id}`;
  };
  const newConversation = () => {
    if (!window.dispatchEvent(new Event("note-navigation", { cancelable: true }))) return;
    setConversationId(null); setChatKey(key => key + 1); setPage("home");
    if (course) { localStorage.removeItem(`current-conversation-${course.course_id}`); window.location.hash = `#chat/${course.course_id}`; }
  };
  const navigate = (id: Page) => { window.location.hash = id === "home" && course ? `#chat/${course.course_id}${conversationId ? `/${conversationId}` : ""}` : `#${id}`; };
  return <div className="shell"><aside><CourseSwitcher courses={courses} course={course} conversationId={conversationId} onCourse={selectCourse} onConversation={selectConversation} onNew={newConversation} onRenamed={() => setRefreshKey(key => key + 1)} refreshKey={refreshKey}/><nav>{navigation.map(([id, icon, text]) => <button className={page === id ? "active" : ""} onClick={() => navigate(id)} key={id}><NavigationIcon name={icon} />{text}</button>)}</nav><p className="quote">复习不是把资料看完，<br />是把会考的写出来。</p><small className="sign">— 考前笔记</small></aside><main className={page === "home" ? "dialog-main" : ""}>{page !== "home" && !(page === "notes" && /^#note\//.test(routeHash)) && <Topbar />}{page === "home" && <ChatHome key={`${course?.course_id}-${chatKey}`} courseId={course?.status === "deleted" ? null : course?.course_id ?? null} selectedConversationId={conversationId} titleRefreshKey={refreshKey} onConversationRenamed={() => setRefreshKey(key => key + 1)} onConversationCreated={id => { setConversationId(id); setRefreshKey(key => key + 1); if (course) localStorage.setItem(`current-conversation-${course.course_id}`, id); if (course) window.location.hash = `#chat/${course.course_id}/${id}`; }} onNewConversation={newConversation} />}{page === "workbench" && courseReady && <Workbench selectedId={course?.course_id ?? null} onSelect={onSelectCourse} />}{page === "materials" && <Materials selectedCourse={course} />}{page === "notes" && courseReady && <Notes hash={routeHash} course={course} onCourseResolved={id => { const linked = courses.find(item => item.course_id === id); if (linked && linked.course_id !== course?.course_id) { setCourse(linked); localStorage.setItem("current-course-id", linked.course_id); } }} />}{page === "quiz" && <QuizDrafts courseId={course?.status === "deleted" ? null : course?.course_id ?? null} />}{page === "mistakes" && courseReady && <Mistakes courses={courses} selectedCourseId={course?.status === "deleted" ? null : course?.course_id ?? null} />}{page === "report" && <Report courseId={course?.status === "deleted" ? null : course?.course_id ?? null} />}</main><div className="mobile-course"><CourseSwitcher courses={courses} course={course} conversationId={conversationId} onCourse={selectCourse} onConversation={selectConversation} onNew={newConversation} onRenamed={() => setRefreshKey(key => key + 1)} refreshKey={refreshKey}/></div><div className="mobile-nav">{navigation.map(([id, icon, text]) => <button className={page === id ? "active" : ""} key={id} onClick={() => navigate(id)}><NavigationIcon name={icon} />{text}</button>)}</div></div>;
}
function Box({children,className=""}:{children:React.ReactNode,className?:string}){return <section className={"box "+className}>{children}</section>}
type ReportData = { attempt_count: number; question_count: number; average_score: number; topics: { name: string; score: number }[]; attempts: { attempt_id: string; score?: number; question_count?: number; submitted_at?: string; created_at?: string }[] };
function Report({courseId}:{courseId:string|null}) {
  const [data,setData]=useState<ReportData|null>(null);
  const [error,setError]=useState("");
  useEffect(()=>{let active=true;setData(null);setError("");if(!courseId)return;api<ReportData>(`/api/courses/${encodeURIComponent(courseId)}/report`).then(value=>{if(active)setData(value)}).catch(reason=>{if(active)setError(reason instanceof Error?reason.message:"报告读取失败")});return()=>{active=false}},[courseId]);
  if (!courseId) return <section className="report-state">请先选择一门课程。</section>;
  if (error) return <section className="report-state" role="alert">{error}</section>;
  if (!data) return <section className="report-state" role="status">正在读取学习报告…</section>;
  return <div className="report-page"><header className="report-heading"><div><h1>学习报告</h1><p>基于当前课程已提交并完成评分的正式测验。</p></div><a href="#mistakes">查看错题</a></header>
    <div className="report-stats"><Box><small>已完成测验</small><b>{data.attempt_count}</b></Box><Box><small>已评分题目</small><b>{data.question_count}</b></Box><Box><small>平均得分</small><b>{data.attempt_count ? data.average_score : "—"}</b></Box></div>
    <div className="report-grid"><Box className="weak"><h2>薄弱知识点</h2>{data.topics.length ? data.topics.map(topic=><article key={topic.name}><b>{topic.name}</b><strong>{topic.score} 分</strong><p>按已评分题目的平均得分从低到高排列</p><a href="#mistakes">查看相关错题 →</a></article>) : <p className="report-empty">完成并提交正式测验后，这里会显示各知识点得分。</p>}</Box>
    <Box className="records"><h2>最近测验</h2>{data.attempts.length ? data.attempts.map(item=><p key={item.attempt_id}><span>{new Date(item.submitted_at ?? item.created_at ?? "").toLocaleDateString("zh-CN")}</span><span>{item.question_count ?? 0} 题</span><strong>{item.score ?? "—"} 分</strong></p>) : <p className="report-empty">暂无已完成测验。</p>}</Box></div>
  </div>;
}
createRoot(document.getElementById("root")!).render(
  <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: "#123c5c", borderRadius: 6, fontFamily: '"Microsoft YaHei UI", "PingFang SC", sans-serif' } }}>
    <App />
  </ConfigProvider>,
);
