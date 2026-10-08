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
import { api, type Course } from "./workbench-api";
import CourseSwitcher from "./CourseSwitcher";
import NavigationIcon, { type NavigationIconName } from "./NavigationIcon";

type Page = "home" | "workbench" | "materials" | "notes" | "quiz" | "report";
const navigation: [Page, NavigationIconName, string][] = [["home", "chat", "AI 对话"], ["workbench", "courses", "课程管理"], ["materials", "materials", "我的资料"], ["notes", "notes", "我的笔记"], ["quiz", "quiz", "模拟测验"], ["report", "report", "学习报告"]];
function App() {
  const [page, setPage] = useState<Page>(/^#materials(?:\/|$)/.test(window.location.hash) ? "materials" : /^#note\//.test(window.location.hash) || window.location.hash === "#notes" ? "notes" : /^#chat\//.test(window.location.hash) ? "home" : "workbench");
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
      } else { const target = hash.slice(1) as Page; setPage(["workbench", "quiz", "report", "home"].includes(target) ? target : "workbench"); }
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
  const navigate = (id: Page) => { if (id === "home" && course) window.location.hash = `#chat/${course.course_id}${conversationId ? `/${conversationId}` : ""}`; else if (id !== "home") window.location.hash = `#${id}`; };
  return <div className="shell"><aside><CourseSwitcher courses={courses} course={course} conversationId={conversationId} onCourse={selectCourse} onConversation={selectConversation} onNew={newConversation} onRenamed={() => setRefreshKey(key => key + 1)} refreshKey={refreshKey}/><nav>{navigation.map(([id, icon, text]) => <button className={page === id ? "active" : ""} onClick={() => navigate(id)} key={id}><NavigationIcon name={icon} />{text}</button>)}</nav><p className="quote">复习不是把资料看完，<br />是把会考的写出来。</p><small className="sign">— 考前笔记</small></aside><main className={page === "home" ? "dialog-main" : ""}>{page !== "home" && !(page === "notes" && /^#note\//.test(routeHash)) && <Topbar />}{page === "home" && <ChatHome key={`${course?.course_id}-${chatKey}`} courseId={course?.status === "deleted" ? null : course?.course_id ?? null} selectedConversationId={conversationId} titleRefreshKey={refreshKey} onConversationRenamed={() => setRefreshKey(key => key + 1)} onConversationCreated={id => { setConversationId(id); setRefreshKey(key => key + 1); if (course) localStorage.setItem(`current-conversation-${course.course_id}`, id); if (course) window.location.hash = `#chat/${course.course_id}/${id}`; }} onNewConversation={newConversation} />}{page === "workbench" && courseReady && <Workbench selectedId={course?.course_id ?? null} onSelect={onSelectCourse} />}{page === "materials" && <Materials selectedCourse={course} />}{page === "notes" && courseReady && <Notes hash={routeHash} course={course} onCourseResolved={id => { const linked = courses.find(item => item.course_id === id); if (linked && linked.course_id !== course?.course_id) { setCourse(linked); localStorage.setItem("current-course-id", linked.course_id); } }} />}{page === "quiz" && <QuizDrafts courseId={course?.status === "deleted" ? null : course?.course_id ?? null} />}{page === "report" && <Report />}</main><div className="mobile-course"><CourseSwitcher courses={courses} course={course} conversationId={conversationId} onCourse={selectCourse} onConversation={selectConversation} onNew={newConversation} onRenamed={() => setRefreshKey(key => key + 1)} refreshKey={refreshKey}/></div><div className="mobile-nav">{navigation.map(([id, icon, text]) => <button className={page === id ? "active" : ""} key={id} onClick={() => navigate(id)}><NavigationIcon name={icon} />{text}</button>)}</div></div>;
}
function Box({children,className=""}:{children:React.ReactNode,className?:string}){return <section className={"box "+className}>{children}</section>}
function Tag({children,tone="green"}:{children:React.ReactNode,tone?:string}){return <span className={"tag "+tone}>{children}</span>}
function Hero({title,sub,action}:{title:React.ReactNode,sub:string,action?:string}){return <div className="hero"><div><h1>{title}</h1><p>{sub}</p></div>{action&&<button className="primary">{action}</button>}</div>}

function RouteMap(){const data=[["✓","用例建模","掌握良好，正确率 82%","82 分","green"],["","需求分析","正在练习，正确率 60%","60%","gold"],["","需求规格说明书","薄弱环节，正确率 38%","38%","red"]];return <Box className="route"><h2>你的得分路线图 <small>基于最近三次模拟测验</small></h2>{data.map((r,i)=><article key={r[1]}><i className={r[4]}>{r[0]}</i><div><b>{r[1]}</b><p>{r[2]}</p></div><strong className={r[4]}>{r[3]}</strong><Tag tone={r[4]}>{i===0?"已掌握":i===1?"进行中":"优先复习"}</Tag></article>)}</Box>}

function Records(){return <Box className="records"><h2>最近测验记录</h2>{[["12 月 8 日","需求建模专项","10 题","82 分"],["12 月 5 日","第 3 章 · 需求分析","15 题","60 分"],["12 月 1 日","软件设计基础","10 题","38 分"]].map(r=><p key={r[0]}>{r.map(x=><span key={x}>{x}</span>)}<a>查看解析　›</a></p>)}</Box>}
function Report(){return <div><Hero title={<>你的复习，正在变得<em>更有把握。</em></>} sub="基于近三次模拟测验与本周完成的复习任务。" action="▣　近 7 天　⌄"/><div className="report-stats">{[["▧","累计练习","38 题"],["◎","平均正确率","68%"],["▰","已完成章节","5 / 8"]].map(x=><Box key={x[1]}><i>{x[0]}</i><small>{x[1]}</small><b>{x[2]}</b><p>较上周 <em>+12 ↗</em></p></Box>)}</div><div className="report-grid"><Box className="trend"><h2>得分趋势</h2><div className="chart"><i/><i/><i/><i/></div><p>▥　较上周提升 <strong>12 分</strong></p></Box><RouteMap/><Box className="weak"><h2>薄弱知识点</h2>{[["可验证性","38%"],["需求获取方法","52%"],["一致性与完整性","58%"]].map(x=><article key={x[0]}><b>{x[0]}</b><strong>{x[1]}</strong><p>建议完成 1 组简答题</p><button>开始练习　→</button></article>)}</Box></div></div>}
createRoot(document.getElementById("root")!).render(
  <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: "#123c5c", borderRadius: 6, fontFamily: '"Microsoft YaHei UI", "PingFang SC", sans-serif' } }}>
    <App />
  </ConfigProvider>,
);
