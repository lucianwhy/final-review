export type CourseStatus = "active" | "archived" | "deleted";
export type Course = {
  course_id: string;
  name: string;
  subject?: string | null;
  status?: CourseStatus;
  updated_at: string;
  purge_after?: string | null;
};
export type BlueprintItem = { question_type: string; question_count: number; score: number };
export type ExamInput = {
  name: string;
  exam_at: string | null;
  total_score: number | null;
  blueprint: BlueprintItem[];
  emphasis: string[];
  exclusions: string[];
  notes: string;
  generation_preferences: Record<string, string | number | boolean>;
};
export type Exam = ExamInput & {
  exam_id: string;
  course_id: string;
  status: "active" | "archived";
  updated_at: string;
};
export type DeletionPreview = {
  impact: { materials: number; exams: number; learning_assets: number; attempts: number };
  confirmation_id: string;
  expires_at: string;
};

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function api<T>(path: string, method = "GET", data?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: data === undefined ? undefined : { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : "请求失败，请稍后重试";
    throw new ApiError(detail, response.status);
  }
  return body as T;
}

export async function downloadFile(path: string): Promise<void> {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(typeof body.detail === "string" ? body.detail : "下载失败", response.status);
  }
  const disposition = response.headers.get("Content-Disposition") || "";
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  const quoted = /filename="([^"]+)"/i.exec(disposition);
  const filename = encoded ? decodeURIComponent(encoded[1]) : quoted?.[1] || "笔记导出";
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
