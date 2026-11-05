import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { ConfigProvider, Select } from "antd";
import { api, type Course } from "./workbench-api";
import "./notes.css";
import NoteExport from "./NoteExport";
import Topbar from "./Topbar";
import NoteGeneration from "./NoteGeneration";

type Reference = {
  document_id: string;
  chunk_id: string;
  quote: string;
  file_name?: string;
  source_type?: string;
  position_kind?: string;
  position?: number;
  chunk_ordinal?: number;
  available?: boolean;
  snapshot?: { excerpt: string } | null;
};
type Point = {
  point_id: string;
  heading: string;
  content: string;
  provenance: "source" | "synthesis" | "ai_supplement";
  references: Reference[];
};
type Revision = {
  revision_id: string;
  revision_no: number;
  title: string;
  markdown: string;
  body_markdown?: string;
  source_appendix?: string;
  state: string;
  confirmed_at?: string;
  note_type: string;
  points?: Point[];
  edit_source?: string;
  coverage?: {
    partial: boolean;
    read_chunks: number;
    readable_chunks: number;
    selected_files?: number;
    files: {
      document_id: string;
      file_name: string;
      read_chunks: number;
      readable_chunks: number;
    }[];
  };
};
type Asset = {
  asset_id: string;
  course_id: string;
  title: string;
  status: string;
  current_revision_id?: string;
  latest_revision_id: string;
  note_type: string;
  generation_method: string;
  updated_at: string;
  sources: string[];
  has_pending_changes: boolean;
};
type Detail = { asset: Asset; revision: Revision; history: Revision[]; references?: Reference[] };
type Confirmation = {
  confirmation_id: string;
  expires_at: string;
  replaces_confirmed: boolean;
  changes: {
    title_changed: boolean;
    added_points: number;
    removed_points: number;
    changed_points: number;
  };
  before: Revision | null;
  after: Revision;
};
const types: Record<string, string> = {
  chapter: "章节笔记",
  key_points: "考点清单",
  qa_cards: "问答卡片",
  mnemonic: "口诀",
};
const provenance = {
  source: "资料来源",
  synthesis: "综合改编",
  ai_supplement: "AI 补充",
};
const sourceNames: Record<string, string> = {
  past_exam: "历年真题",
  teacher_ppt: "老师 PPT",
  homework: "平时作业",
  other_practice: "其他练习",
  external_upload: "外部上传",
  crash_course: "速成课",
  ai_supplement: "AI 补充",
  other: "其他资料",
};
const errorText = (reason: unknown) =>
  reason instanceof Error ? reason.message : "操作失败，请重试";
const stateName = (state: string) =>
  ({
    draft: "待确认",
    confirmed: "已确认",
    superseded: "历史版本",
    archived: "已归档",
  })[state] || state;

export default function Notes({
  hash,
  course,
  onCourseResolved,
}: {
  hash: string;
  course: Course | null;
  onCourseResolved: (id: string) => void;
}) {
  const [items, setItems] = useState<Asset[]>([]);
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [generationTarget, setGenerationTarget] = useState<HTMLDivElement | null>(null);
  const refreshNotes = useRef<() => Promise<boolean>>(async () => false);
  const match = /^#note\/([\w.-]+)\/([\w.-]+)$/.exec(hash);
  useEffect(() => {
    let active = true;
    setError("");
    setItems([]);
    setLoading(true);
    if (!course || match) {
      refreshNotes.current = async () => false;
      setLoading(false);
      return;
    }
    let fetching = false;
    const refresh = async () => {
      if (fetching || document.hidden) return false;
      fetching = true;
      try {
        const data = await api<{ items: Asset[] }>(`/api/courses/${course.course_id}/notes`);
        if (!active) return false;
        setItems(data.items);
        setError("");
        return true;
      } catch (reason) {
        if (active) setError(errorText(reason));
        return false;
      } finally {
        fetching = false;
        if (active) setLoading(false);
      }
    };
    refreshNotes.current = refresh;
    refresh();
    const timer = window.setInterval(refresh, 5000);
    window.addEventListener("focus", refresh);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [course?.course_id, hash]);
  if (match)
    return (
      <NoteEditor
        key={`${match[1]}/${match[2]}`}
        assetId={match[1]}
        revisionId={match[2]}
        onCourseResolved={onCourseResolved}
      />
    );
  return (
    <section className="notes-page" aria-label="我的笔记">
      <header className="notes-heading">
        <div>
          <small>{course?.name || "课程笔记"}</small>
          <h1>
            把会考的，<em>留下来。</em>
          </h1>
        </div>
        <NoteGeneration key={course?.course_id ?? "no-course"} courseId={course?.course_id ?? null}
          progressTarget={generationTarget} onStarted={() => setFilter("all")} onCompleted={() => refreshNotes.current()} />
      </header>
      <div className="notes-filters" role="group" aria-label="笔记状态筛选">
        {[
          ["all", "全部"],
          ["draft", "待确认"],
          ["confirmed", "已确认"],
        ].map(([id, label]) => (
          <button
            key={id}
            aria-pressed={filter === id}
            onClick={() => setFilter(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {error && (
        <p role="alert" className="notes-error">
          {error}
        </p>
      )}
      <div className="notes-list">
      <div ref={setGenerationTarget} className="notes-generation-tasks" />
      {!course ? (
        <p className="notes-empty">请先选择课程，再查看笔记。</p>
      ) : loading ? (
        <p role="status">正在读取笔记…</p>
      ) : (
        <div className="notes-items">
          {items
            .filter(
              (item) =>
                filter === "all" ||
                (filter === "draft"
                  ? item.has_pending_changes
                  : item.status === "confirmed"),
            )
            .map((item) => (
              <a
                className="note-row"
                key={item.asset_id}
                href={`#note/${item.asset_id}/${item.latest_revision_id}`}
              >
                <div>
                  <small>
                    {types[item.note_type] || item.note_type} ·{" "}
                    {item.generation_method === "ai" ? "AI 生成" : "历史笔记"}
                  </small>
                  <h2>{item.title}</h2>
                  <p>{item.sources.join("、") || "无资料引用 / AI 补充"}</p>
                </div>
                <div className="note-row-meta">
                  <span className={`note-status ${item.status}`}>
                    {stateName(item.status)}
                    {item.status === "confirmed" && item.has_pending_changes
                      ? " · 有待确认修改"
                      : ""}
                  </span>
                  <time>
                    {new Date(item.updated_at).toLocaleString("zh-CN")}
                  </time>
                  <b>打开笔记 →</b>
                </div>
              </a>
            ))}
          {!items.filter(
            (item) =>
              filter === "all" ||
              (filter === "draft"
                ? item.has_pending_changes
                : item.status === "confirmed"),
          ).length &&
            !error && (
              <div className="notes-empty">
                <h2>这里会保存你的复习笔记。</h2>
                <p>生成后的草稿无需确认就会出现在这里，刷新后也能继续修改。</p>
                <p>点击右上方「生成笔记」开始整理资料。</p>
              </div>
            )}
        </div>
      )}
      </div>
      <p className="notes-footer-hint">草稿先保存，核对后确认。每条考点都带着它的出处。</p>
    </section>
  );
}

function NoteEditor({
  assetId,
  revisionId,
  onCourseResolved,
}: {
  assetId: string;
  revisionId: string;
  onCourseResolved: (id: string) => void;
}) {
  const [data, setData] = useState<Detail | null>(null);
  const [title, setTitle] = useState("");
  const [points, setPoints] = useState<Point[]>([]);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [sourcePoint, setSourcePoint] = useState<string | null>(null);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  useModalKeyboard(Boolean(confirmation || sourcePoint), () => {
    if (!busy) {
      setConfirmation(null);
      setSourcePoint(null);
    }
  });
  useEffect(() => {
    let active = true;
    api<Detail>(`/api/assets/${assetId}/revisions/${revisionId}`)
      .then((value) => {
        if (!active) return;
        setData(value);
        setTitle(value.revision.title);
        setPoints(value.revision.points || []);
        onCourseResolved(value.asset.course_id);
      })
      .catch((reason) => {
        if (active) setError(errorText(reason));
      });
    return () => {
      active = false;
    };
  }, [assetId, revisionId]);
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    const leave = (event: Event) => {
      if (dirtyRef.current && !window.confirm("有未保存的修改，确定离开笔记？"))
        event.preventDefault();
    };
    window.addEventListener("beforeunload", unload);
    window.addEventListener("note-navigation", leave);
    return () => {
      window.removeEventListener("beforeunload", unload);
      window.removeEventListener("note-navigation", leave);
    };
  }, []);
  function updatePoint(id: string, changes: Partial<Point>) {
    setPoints((previous) =>
      previous.map((point) =>
        point.point_id === id ? { ...point, ...changes } : point,
      ),
    );
    setDirty(true);
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      const saved = await api<Detail>(
        `/api/notes/${assetId}/revisions`,
        "POST",
        {
          base_revision_id: revisionId,
          title,
          points: points.map((point) => ({
            ...point,
            references: point.references.map((ref) => ({
              document_id: ref.document_id,
              chunk_id: ref.chunk_id,
              quote: ref.quote,
            })),
          })),
        },
      );
      if (window.location.hash !== `#note/${assetId}/${revisionId}`) return;
      setDirty(false);
      dirtyRef.current = false;
      setData(saved);
      setEditing(false);
      window.location.hash = `#note/${assetId}/${saved.revision.revision_id}`;
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
  async function previewConfirm() {
    setBusy(true);
    setError("");
    try {
      setConfirmation(
        await api<Confirmation>(
          `/api/notes/${assetId}/confirm-preview`,
          "POST",
          { revision_id: revisionId },
        ),
      );
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
  async function confirm() {
    if (!confirmation) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/notes/${assetId}/confirm`, "POST", {
        revision_id: revisionId,
        confirmation_id: confirmation.confirmation_id,
      });
      const saved = await api<Detail>(
        `/api/assets/${assetId}/revisions/${revisionId}`,
      );
      setData(saved);
      setConfirmation(null);
      setEditing(false);
    } catch (reason) {
      setError(errorText(reason));
      setConfirmation(null);
    } finally {
      setBusy(false);
    }
  }
  if (!data)
    return (
      <section className="notes-page note-detail">
        <div className="note-detail-toolbar"><BackToNotes /></div>
        <Topbar />
        {error ? (
          <p role="alert">{error}</p>
        ) : (
          <p role="status">正在读取笔记…</p>
        )}
      </section>
    );
  const revision = data.revision;
  const latest = data.history.at(-1)?.revision_id === revisionId;
  const editable =
    latest && data.asset.status !== "archived" && points.length > 0;
  return (
    <section className="notes-page note-detail" aria-label="笔记详情">
      <div className="note-detail-toolbar">
        <BackToNotes />
        {revision.confirmed_at && ["confirmed", "superseded", "archived"].includes(revision.state) && (
          <NoteExport key={revisionId} assetId={assetId} revisionId={revisionId}
            revisionNo={revision.revision_no} historical={data.asset.current_revision_id !== revisionId} />
        )}
      </div>
      <Topbar />
      <header className="note-detail-head">
        <div>
          <small>
            {types[revision.note_type] || "笔记"} · 版本 {revision.revision_no}{" "}
            · {revision.edit_source === "student" ? "学生编辑" : "生成内容"}
          </small>
          <h1>{title}</h1>
          <span className={`note-status ${revision.state}`}>
            {stateName(revision.state)}
          </span>
          {data.asset.current_revision_id && revision.state === "draft" && (
            <p>当前正式版本仍保留；确认此草稿后才会替换。</p>
          )}
        </div>
        <div className="note-actions">
          {editable && (
            <button
              disabled={busy}
              onClick={() => setEditing((value) => !value)}
            >
              {editing ? "预览笔记" : "编辑笔记"}
            </button>
          )}
          {dirty && (
            <button
              className="note-primary"
              disabled={busy || !title.trim() || !points.length}
              onClick={() => void save()}
            >
              {busy ? "正在保存…" : "保存新版本"}
            </button>
          )}
          {latest && revision.state === "draft" && (
            <button
              className="note-primary"
              disabled={busy || dirty || editing}
              onClick={() => void previewConfirm()}
            >
              确认归档
            </button>
          )}
        </div>
      </header>
      <div className="note-version-bar">
        <span>{dirty ? "有未保存修改 · 保存后请重新核对来源" : "已保存"}</span>
        <div className="note-version-field">
          <span>查看版本</span>
          <NoteSelect
            label="查看版本"
            value={revisionId}
            onChange={(value) => {
              window.location.hash = `#note/${assetId}/${value}`;
            }}
            options={data.history.map((row) => ({
              value: row.revision_id,
              label: `v${row.revision_no} · ${stateName(row.state)} · ${row.title}`,
            }))}
          />
        </div>
      </div>
      {!(revision.confirmed_at && ["confirmed", "superseded", "archived"].includes(revision.state)) && (
        <p className="note-export-hint">草稿需确认后才能导出。
          {data.asset.current_revision_id && <a href={`#note/${assetId}/${data.asset.current_revision_id}`}>打开正式版本</a>}
        </p>
      )}
      {error && (
        <p className="notes-error" role="alert">
          {error}
        </p>
      )}
      {revision.coverage && (
        <details className="note-coverage">
          <summary>
            已选{" "}
            {revision.coverage.selected_files ?? revision.coverage.files.length}{" "}
            份资料；生成时读取 {revision.coverage.read_chunks}/
            {revision.coverage.readable_chunks} 个片段 ·{" "}
            {revision.coverage.partial ? "部分覆盖" : "已读取范围内全部片段"}
          </summary>
          {revision.coverage.files.map((file) => (
            <p key={file.document_id}>
              {file.file_name}：读取 {file.read_chunks}/{file.readable_chunks}{" "}
              个片段
            </p>
          ))}
        </details>
      )}
      {editing && (
        <label className="note-title-input">
          笔记标题
          <input
            aria-label="笔记标题"
            maxLength={200}
            value={title}
            onChange={(event) => {
              setTitle(event.target.value);
              setDirty(true);
            }}
          />
        </label>
      )}
      {points.length ? (
        points.map((point, index) => (
          <article className={`note-point${editing ? "" : " note-point-reading"}`} key={point.point_id}>
            <div className="note-point-body">
              <small className="note-point-number">考点 {index + 1}</small>
              {editing ? (
                <>
                  <label>
                    考点标题
                    <input
                      aria-label={`考点 ${index + 1} 标题`}
                      maxLength={200}
                      value={point.heading}
                      onChange={(event) =>
                        updatePoint(point.point_id, {
                          heading: event.target.value,
                        })
                      }
                    />
                  </label>
                  <label>
                    正文 · Markdown
                    <textarea
                      aria-label={`考点 ${index + 1} 正文`}
                      rows={8}
                      maxLength={50000}
                      value={point.content}
                      onChange={(event) =>
                        updatePoint(point.point_id, {
                          content: event.target.value,
                        })
                      }
                    />
                  </label>
                  <button
                    className="note-remove"
                    onClick={() => {
                      setPoints(
                        points.filter((row) => row.point_id !== point.point_id),
                      );
                      setDirty(true);
                    }}
                  >
                    删除此考点
                  </button>
                </>
              ) : (
                <>
                  <h2>{point.heading}</h2>
                  <div className="note-markdown">
                    <ReactMarkdown>{point.content}</ReactMarkdown>
                  </div>
                </>
              )}
            </div>
            {editing && <aside
              className="note-point-sources"
              aria-label={`考点 ${index + 1} 来源`}
            >
              <small>核对出处</small>
              {editing ? (
                <div className="note-source-field">
                  <span>来源标记</span>
                  <NoteSelect
                    label={`考点 ${index + 1} 来源标记`}
                    value={point.provenance}
                    onChange={(value) =>
                      updatePoint(point.point_id, {
                        provenance: value as Point["provenance"],
                      })
                    }
                    options={Object.entries(provenance).map(([value, label]) => ({ value, label }))}
                  />
                </div>
              ) : (
                <strong>{provenance[point.provenance]}</strong>
              )}
              {point.references.map((ref, number) => (
                <div
                  className="note-reference"
                  key={`${ref.chunk_id}-${number}`}
                >
                  <b>{ref.file_name || ref.document_id}</b>
                  <small>
                    {sourceNames[ref.source_type || ""] || ref.source_type} ·{" "}
                    {ref.position_kind === "slide"
                      ? `第 ${ref.position} 张幻灯片`
                      : ref.position_kind === "page"
                        ? `第 ${ref.position} 页`
                        : `片段 ${(ref.chunk_ordinal || 0) + 1}`}
                  </small>
                  {editing ? (
                    <label>
                      引用摘录
                      <textarea
                        aria-label={`考点 ${index + 1} 引用 ${number + 1} 摘录`}
                        rows={4}
                        maxLength={10000}
                        value={ref.quote}
                        onChange={(event) =>
                          updatePoint(point.point_id, {
                            references: point.references.map((row, i) =>
                              i === number
                                ? { ...row, quote: event.target.value }
                                : row,
                            ),
                          })
                        }
                      />
                    </label>
                  ) : null}
                  {ref.available !== false ? (
                    <a
                      href={`#materials/${data.asset.course_id}/${ref.document_id}/${ref.chunk_id}`}
                    >
                      查看原文 →
                    </a>
                  ) : (
                    <>
                      <span>原资料已删除或不可用</span>
                      {ref.snapshot && (
                        <details>
                          <summary>查看来源快照</summary>
                          <p>{ref.snapshot.excerpt}</p>
                        </details>
                      )}
                    </>
                  )}
                  {editing && (
                    <button
                      className="note-remove"
                      onClick={() =>
                        updatePoint(point.point_id, {
                          references: point.references.filter(
                            (_, i) => i !== number,
                          ),
                        })
                      }
                    >
                      移除此引用
                    </button>
                  )}
                </div>
              ))}
              {point.provenance === "ai_supplement" && (
                <p>AI 补充内容，没有课程资料引用。</p>
              )}
              {editing && (
                <button onClick={() => setSourcePoint(point.point_id)}>
                  添加资料引用
                </button>
              )}
            </aside>}
          </article>
        ))
      ) : (
        <div className="note-markdown">
          <ReactMarkdown>{revision.body_markdown ?? revision.markdown}</ReactMarkdown>
          <p>历史笔记没有考点结构，暂只支持查看。</p>
        </div>
      )}
      {editing && (
        <button
          className="note-add"
          onClick={() => {
            setPoints([
              ...points,
              {
                point_id: `p-${crypto.randomUUID()}`,
                heading: "新增考点",
                content: "请填写内容",
                provenance: "ai_supplement",
                references: [],
              },
            ]);
            setDirty(true);
          }}
        >
          ＋ 添加考点
        </button>
      )}
      {!editing && <footer className="note-sources-footer">
        <button type="button" onClick={() => setSourcesOpen(true)}>查看来源与引用</button>
      </footer>}
      {sourcesOpen && <NoteSources points={points} references={data.references || []}
        appendix={revision.source_appendix || ""} courseId={data.asset.course_id}
        revisionNo={revision.revision_no} onClose={() => setSourcesOpen(false)} />}
      {sourcePoint && (
        <SourcePicker
          courseId={data.asset.course_id}
          onClose={() => setSourcePoint(null)}
          onAdd={(ref) => {
            const point = points.find((row) => row.point_id === sourcePoint)!;
            if (!point.references.some((row) => row.chunk_id === ref.chunk_id))
              updatePoint(point.point_id, {
                references: [...point.references, ref],
                provenance:
                  new Set([
                    ...point.references.map((row) => row.document_id),
                    ref.document_id,
                  ]).size > 1
                    ? "synthesis"
                    : "source",
              });
            setSourcePoint(null);
          }}
        />
      )}
      {confirmation && (
        <div className="wb-overlay">
          <section
            className="wb-dialog note-confirm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="note-confirm-title"
          >
            <h2 id="note-confirm-title">
              {confirmation.replaces_confirmed
                ? "确认替换正式版本？"
                : "确认归档这份笔记？"}
            </h2>
            <p>
              {confirmation.replaces_confirmed
                ? "确认后，此版本成为当前正式笔记，旧版本保留在历史中。"
                : "确认后，这份草稿成为当前课程的正式笔记。"}
            </p>
            <p className="note-change-summary">
              {confirmation.changes.title_changed ? "标题已修改；" : ""}新增{" "}
              {confirmation.changes.added_points} 条、删除{" "}
              {confirmation.changes.removed_points} 条、修改{" "}
              {confirmation.changes.changed_points} 条考点（含引用变更）。
            </p>
            <div className="note-diff">
              <div>
                <h3>
                  {confirmation.before
                    ? `修改前 · v${confirmation.before.revision_no}`
                    : "修改前"}
                </h3>
                <pre>
                  {confirmation.before
                    ? `${confirmation.before.title}\n\n${confirmation.before.markdown}\n\n${referenceSummary(confirmation.before)}`
                    : "首次生成，没有先前版本。"}
                </pre>
              </div>
              <div>
                <h3>待确认 · v{confirmation.after.revision_no}</h3>
                <pre>
                  {confirmation.after.title}
                  {"\n\n"}
                  {confirmation.after.markdown}
                  {"\n\n"}
                  {referenceSummary(confirmation.after)}
                </pre>
              </div>
            </div>
            <p>
              请核对正文与来源。确认有效至{" "}
              {new Date(confirmation.expires_at).toLocaleTimeString("zh-CN")}。
            </p>
            <div className="wb-dialog-actions">
              <button disabled={busy} onClick={() => setConfirmation(null)}>
                返回检查
              </button>
              <button
                className="note-primary"
                disabled={busy}
                onClick={() => void confirm()}
              >
                {busy
                  ? "正在确认…"
                  : confirmation.replaces_confirmed
                    ? "确认替换正式版本"
                    : "确认成为正式笔记"}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}

function BackToNotes() {
  return (
    <button type="button" className="note-back" onClick={() => { window.location.hash = "#notes"; }}>
      <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="m8 5-5 5 5 5M3 10h14" />
      </svg>
      我的笔记
    </button>
  );
}

function NoteSources({ points, references, appendix, courseId, revisionNo, onClose }: {
  points: Point[]; references: Reference[]; appendix: string; courseId: string;
  revisionNo: number; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    const previous = document.activeElement as HTMLElement | null;
    element.showModal();
    return () => { element.close(); if (previous?.isConnected) previous.focus(); };
  }, []);
  const groups = points.length ? points : [{ point_id: "legacy", heading: "历史笔记来源",
    provenance: "source" as const, references }];
  return <dialog ref={dialog} className="note-sources-dialog" aria-label="来源与引用" aria-modal="true"
    onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header className="note-export-dialog-head">
      <div><small>第 {revisionNo} 版</small><h2>来源与引用</h2></div>
      <button type="button" onClick={onClose} aria-label="关闭来源与引用">关闭</button>
    </header>
    {!points.length && <p>历史笔记未保存逐条考点来源关系，以下为笔记级来源。</p>}
    {groups.map((point) => <section className="note-source-group" key={point.point_id}>
      <h3>{point.heading}</h3>
      {points.length > 0 && <p>{provenance[point.provenance]}</p>}
      {point.references.map((ref, index) => <div className="note-reference" key={`${ref.chunk_id}-${index}`}>
        <b>{ref.file_name || ref.document_id}</b>
        <small>{sourceNames[ref.source_type || ""] || ref.source_type || "资料"} · {ref.position != null
          ? ref.position_kind === "slide" ? `第 ${ref.position} 张幻灯片` : ref.position_kind === "page"
            ? `第 ${ref.position} 页` : `片段 ${ref.position}`
          : `片段 ${(ref.chunk_ordinal || 0) + 1}`}</small>
        <span>引用摘录</span><blockquote>{ref.quote || ref.snapshot?.excerpt || "未保存引用摘录"}</blockquote>
        {ref.available !== false ? <a href={`#materials/${courseId}/${ref.document_id}/${ref.chunk_id}`} onClick={onClose}>查看原文 →</a>
          : <span>原资料已删除或不可用，以上为保留的引用内容。</span>}
      </div>)}
      {!point.references.length && <p>{point.provenance === "ai_supplement"
        ? "AI 补充内容，没有课程资料引用。" : "未保存来源引用。"}</p>}
    </section>)}
    {appendix && <div className="note-markdown"><ReactMarkdown>{appendix}</ReactMarkdown></div>}
  </dialog>;
}

function NoteSelect({ label, value, options, onChange }: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <ConfigProvider theme={{
      token: { motion: false, colorText: "#193650", colorBorder: "#bdcccd", colorBgElevated: "#fffefa", controlItemBgActiveHover: "#d5e8db", controlHeight: 42, fontSize: 13, borderRadius: 6 },
      components: { Select: { optionPadding: "10px 12px", optionSelectedBg: "#e2eee5", optionSelectedColor: "#246756", optionActiveBg: "#edf5ef", activeBorderColor: "#73a999", hoverBorderColor: "#78a48f", activeOutlineColor: "#73a99926" } },
    }}>
      <Select
        className="note-select"
        aria-label={label}
        value={value}
        options={options}
        onChange={onChange}
        virtual={false}
        showSearch={false}
        classNames={{ popup: { root: "note-select-menu" } }}
        optionRender={(option) => (
          <span className="note-select-option">
            <span>{option.label}</span>
            <span aria-hidden="true">{option.value === value ? "✓" : ""}</span>
          </span>
        )}
        suffixIcon={<span className="note-select-chevron" aria-hidden="true" />}
      />
    </ConfigProvider>
  );
}

function referenceSummary(revision: Revision) {
  return (revision.points || [])
    .map(
      (point) =>
        `${point.heading} · ${provenance[point.provenance]}\n${point.references.map((ref) => `${ref.file_name || ref.document_id} · ${ref.chunk_id}\n摘录：${ref.quote}`).join("\n")}`,
    )
    .join("\n\n");
}

function useModalKeyboard(active: boolean, close: () => void) {
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    if (!active) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = document.querySelector<HTMLElement>(
      ".notes-page [role=dialog]",
    );
    const focusable = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          "button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled)",
        ) || [],
      ).filter((element) => element.getClientRects().length > 0);
    focusable()[0]?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
      }
      if (event.key !== "Tab") return;
      const elements = focusable();
      const first = elements[0],
        last = elements.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("keydown", keydown);
      if (previous?.isConnected) previous.focus();
    };
  }, [active]);
}
function SourcePicker({
  courseId,
  onAdd,
  onClose,
}: {
  courseId: string;
  onAdd: (ref: Reference) => void;
  onClose: () => void;
}) {
  const [documents, setDocuments] = useState<
    { document_id: string; title: string; parse_status: string }[]
  >([]);
  const [selected, setSelected] = useState("");
  const [chunks, setChunks] = useState<
    {
      chunk_id: string;
      content: string;
      position_kind: string;
      position?: number;
    }[]
  >([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [metadata, setMetadata] = useState<{
    file_name: string;
    source_type: string;
  } | null>(null);
  useEffect(() => {
    let active = true;
    api<{ items: typeof documents }>(`/api/courses/${courseId}/documents`)
      .then((value) => {
        if (active)
          setDocuments(
            value.items.filter((row) => row.parse_status === "ready"),
          );
      })
      .catch((reason) => {
        if (active) setError(errorText(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [courseId]);
  useEffect(() => {
    let active = true;
    setChunks([]);
    setError("");
    if (!selected) return;
    setLoading(true);
    api<{ items: typeof chunks; file_name: string; source_type: string }>(
      `/api/courses/${courseId}/documents/${selected}/chunks?include_content=true`,
    )
      .then((value) => {
        if (active) {
          setChunks(value.items);
          setMetadata(value);
        }
      })
      .catch((reason) => {
        if (active) setError(errorText(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selected, courseId]);
  return (
    <div className="wb-overlay">
      <section
        className="wb-dialog note-source-picker"
        role="dialog"
        aria-modal="true"
        aria-labelledby="note-source-picker-title"
      >
        <h2 id="note-source-picker-title">选择引用片段</h2>
        <p>选择当前课程的资料片段，保存时会再次核对出处。</p>
        <div className="note-source-docs">
          {documents.map((document) => (
            <button
              key={document.document_id}
              aria-pressed={selected === document.document_id}
              onClick={() => setSelected(document.document_id)}
            >
              {document.title} · {document.document_id.slice(0, 8)}
            </button>
          ))}
        </div>
        {error && <p role="alert">{error}</p>}
        {loading ? (
          <p role="status">正在读取…</p>
        ) : !documents.length ? (
          <p>当前课程没有可用资料。</p>
        ) : !selected ? (
          <p>先选择一份资料。</p>
        ) : (
          <div className="note-source-chunks">
            {chunks.map((chunk, index) => (
              <article key={chunk.chunk_id}>
                <b>
                  {chunk.position_kind === "slide"
                    ? `第 ${chunk.position} 张幻灯片`
                    : chunk.position_kind === "page"
                      ? `第 ${chunk.position} 页`
                      : `片段 ${index + 1}`}
                </b>
                <pre>{chunk.content}</pre>
                <button
                  onClick={() =>
                    onAdd({
                      document_id: selected,
                      chunk_id: chunk.chunk_id,
                      quote: chunk.content.slice(0, 1000),
                      ...metadata,
                      position_kind: chunk.position_kind,
                      position: chunk.position,
                      chunk_ordinal: index,
                    })
                  }
                >
                  引用此片段
                </button>
              </article>
            ))}
          </div>
        )}
        <div className="wb-dialog-actions">
          <button onClick={onClose}>取消</button>
        </div>
      </section>
    </div>
  );
}
