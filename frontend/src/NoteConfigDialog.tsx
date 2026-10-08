import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { materialLabel, type NoteMaterial } from "./NoteMaterialPicker";

const noteTypes = [
  { id: "chapter", label: "章节笔记", detail: "按章节整理知识脉络" },
  { id: "key_points", label: "考点清单", detail: "提炼可背诵的重点" },
  { id: "qa_cards", label: "问答卡片", detail: "以问题和答案记忆" },
  { id: "mnemonic", label: "口诀速记", detail: "用简短提示辅助回忆" },
];

export default function NoteConfigDialog({ noteType, onNoteType, duration, onDuration,
  materials, onChooseMaterials, requirements, onRequirements, onGenerate, onCancel,
  busy, error, promptMessage, generationModel, contextLabel = "AI 对话" }: {
  noteType: string;
  onNoteType: (value: string) => void;
  duration: string;
  onDuration: (value: string) => void;
  materials: NoteMaterial[];
  onChooseMaterials: () => void;
  requirements: string;
  onRequirements: (value: string) => void;
  onGenerate: () => void;
  onCancel: () => void;
  busy: boolean;
  error: string;
  promptMessage?: string;
  generationModel: string;
  contextLabel?: string;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const dialogRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const selectedType = noteTypes.find(item => item.id === noteType) ?? noteTypes[1];
  const canGenerate = materials.length > 0 && materials.every(item => item.parse_status === "ready") && Number(duration) >= 1 && Number(duration) <= 240;

  useEffect(() => { triggerRef.current?.focus(); }, []);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (menuOpen) { setMenuOpen(false); triggerRef.current?.focus(); }
        else if (!busy) onCancel();
        return;
      }
      if (event.key !== "Tab") return;
      const controls = [...(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled),input:not(:disabled),textarea:not(:disabled)'
      ) ?? [])].filter(element => element.getClientRects().length > 0);
      if (!controls.length) return;
      if (event.shiftKey && document.activeElement === controls[0]) {
        event.preventDefault(); controls[controls.length - 1].focus();
      } else if (!event.shiftKey && document.activeElement === controls[controls.length - 1]) {
        event.preventDefault(); controls[0].focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [busy, menuOpen, onCancel]);

  useEffect(() => {
    if (!menuOpen) return;
    menuRef.current?.querySelector<HTMLButtonElement>('[aria-selected="true"]')?.focus();
  }, [menuOpen]);

  function menuKeys(event: React.KeyboardEvent<HTMLDivElement>) {
    const options = [...(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? [])];
    const current = options.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      options[(current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length]?.focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault(); options[event.key === "Home" ? 0 : options.length - 1]?.focus();
    }
  }

  return createPortal(<div className="note-dialog-backdrop"><section ref={dialogRef}
    className="note-dialog" role="dialog" aria-modal="true" aria-labelledby="note-dialog-title">
    <header className="note-dialog-head"><div><small>{contextLabel} · 生成笔记</small><h2 id="note-dialog-title">补充笔记要求</h2><p>先确定生成依据，再补充你希望重点写的内容。</p></div><button type="button" aria-label="取消笔记生成" className="note-dialog-close" disabled={busy} onClick={onCancel}>×</button></header>
    <div className="note-dialog-body">
      <div className="note-dialog-grid"><div className="note-type-field"><span className="note-field-label">笔记类型</span><button ref={triggerRef} type="button" className="note-type-trigger" aria-haspopup="listbox" aria-expanded={menuOpen} disabled={busy} onClick={() => setMenuOpen(open => !open)} onKeyDown={event => { if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) { event.preventDefault(); setMenuOpen(true); } }}><span>{selectedType.label}</span><span aria-hidden="true">⌄</span></button>{menuOpen && <div ref={menuRef} className="note-type-menu" role="listbox" aria-label="笔记类型" onKeyDown={menuKeys}>{noteTypes.map(item => <button type="button" role="option" aria-selected={item.id === noteType} key={item.id} onClick={() => { onNoteType(item.id); setMenuOpen(false); triggerRef.current?.focus(); }}><strong>{item.label}</strong><small>{item.detail}</small>{item.id === noteType && <span aria-hidden="true">✓</span>}</button>)}</div>}</div><label>阅读时长（分钟）<input type="number" min="1" max="240" value={duration} disabled={busy} onChange={event => onDuration(event.target.value)} /></label></div>
      <div className="note-dialog-source"><div className="note-dialog-source-head"><strong>指定资料 · 必选</strong><button type="button" disabled={busy} onClick={onChooseMaterials}>{materials.length ? "修改资料" : "选择资料"}</button></div>{materials.length ? <div className="note-config-files">{materials.map(item => { const label = materialLabel(item); const duplicate = materials.filter(other => materialLabel(other) === label).length > 1; return <span key={item.document_id} title={`${label} · 编号 ${item.document_id.slice(0, 8)}`}>{label}{duplicate ? ` · ${item.document_id.slice(0, 8)}` : ""}{item.parse_status !== "ready" ? ` · ${item.parse_status === "failed" ? "处理失败" : "处理中"}` : ""}</span>; })}</div> : <small>只会使用你选中的当前课程资料。</small>}{materials.some(item => item.parse_status !== "ready") && <small>请等待处理完成；失败的文件请在资料选择中重试或移除。</small>}</div>
      <label className="note-dialog-writing">写作要求 · 可选<textarea value={requirements} maxLength={1000} disabled={busy} onChange={event => onRequirements(event.target.value)} placeholder="例如：侧重请求头和状态码，按简答题得分点整理" /><small>{requirements.length} / 1000 字，可修改识别出的要求</small></label>
      {promptMessage?.includes("未找到") && <p className="note-dialog-warning" role="alert">{promptMessage}</p>}
      {error && <p className="note-dialog-error" role="alert">{error}</p>}
    </div>
    <footer className="note-dialog-footer"><p>确认后将使用{generationModel || "笔记模型"}生成带资料引用的草稿；聊天模型负责理解意图。</p><div><button type="button" className="note-dialog-cancel" disabled={busy} onClick={onCancel}>取消</button><button type="button" className="note-dialog-submit" disabled={busy || !canGenerate} onClick={onGenerate}>{busy ? "正在生成…" : "生成笔记"}</button></div></footer>
  </section></div>, document.body);
}
