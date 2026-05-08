export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

const EVENT_TYPE_MAP: Record<string, string> = {
  "job.queued": "任务入队",
  "job.downloading": "下载中",
  "job.success": "下载成功",
  "job.failed": "下载失败",
  "job.cancelled": "任务取消",
  "message.rejected": "消息拒绝",
  "allowlist.allow": "允许用户",
  "allowlist.deny": "移除用户",
  "login.success": "登录成功",
  "login.failed": "登录失败",
};

export function translateEventType(value: string): string {
  return EVENT_TYPE_MAP[value] || value || "";
}

export function getFileBaseName(path: string): string {
  return String(path || "").split("/").pop() || String(path || "") || "file";
}

export function getPreviewType(path: string): string {
  const lower = String(path || "").toLowerCase();
  if (/\.(mp4|m4v|mov|mkv|webm|avi|flv|wmv|mpeg|mpg|3gp|ts)$/.test(lower)) return "video";
  if (/\.(jpg|jpeg|png|gif|webp|bmp|svg)$/.test(lower)) return "image";
  if (/\.(mp3|m4a|aac|wav|ogg|flac|opus)$/.test(lower)) return "audio";
  if (/\.pdf$/.test(lower)) return "pdf";
  if (/\.(txt|log|json|md|csv|xml|yml|yaml|html|htm)$/.test(lower)) return "text";
  return "";
}

export function statusColor(status: string): string {
  const map: Record<string, string> = {
    queued: "primary",
    downloading: "warning",
    success: "success",
    failed: "danger",
    cancelled: "danger",
  };
  return map[status] || "primary";
}

export function statusLabel(status: string): string {
  const map: Record<string, string> = {
    queued: "排队中",
    downloading: "下载中",
    success: "成功",
    failed: "失败",
    cancelled: "已取消",
  };
  return map[status] || status;
}
