const BASE = "";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    credentials: "same-origin",
    ...options,
  });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.message || `HTTP ${res.status}`);
  }
  return res.json();
}

async function requestRaw(url: string, options?: RequestInit): Promise<Response> {
  const res = await fetch(`${BASE}${url}`, {
    credentials: "same-origin",
    ...options,
  });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  return res;
}

import type {
  AppState,
  JobsResponse,
  EventsResponse,
  LoginLogsResponse,
  StatsSummary,
  ToolsStatusResponse,
  HookConfig,
} from "./types";

export const api = {
  state: () => request<AppState>("/api/state"),

  stats: () => request<StatsSummary>("/api/stats/summary"),

  toolsStatus: () => request<ToolsStatusResponse>("/api/tools/status"),
  toolAction: (name: string, action: string) =>
    request<Record<string, unknown>>(`/api/tools/${encodeURIComponent(name)}/${encodeURIComponent(action)}`, { method: "POST" }),
  tdlSessionExport: () => requestRaw("/api/tools/tdl/session/export"),
  tdlSessionImport: (formData: FormData) =>
    request<{ message: string }>("/api/tools/tdl/session/import", {
      method: "POST",
      body: formData,
    }),

  hookConfig: () => request<HookConfig>("/api/hook/config"),
  saveHookConfig: (data: Record<string, unknown>) =>
    request<{ ok: boolean }>("/api/hook/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  testHook: () => request<{ ok: boolean }>("/api/hook/test", { method: "POST" }),

  jobs: (params: { page?: number; page_size?: number; q?: string; status?: string }) => {
    const sp = new URLSearchParams();
    if (params.page) sp.set("page", String(params.page));
    if (params.page_size) sp.set("page_size", String(params.page_size));
    if (params.q) sp.set("q", params.q);
    if (params.status) sp.set("status", params.status);
    return request<JobsResponse>(`/api/jobs?${sp}`);
  },
  jobDetail: (id: string) => request<{ job: Record<string, unknown> }>(`/api/jobs/${id}`),
  jobLog: (id: string) => requestRaw(`/api/jobs/${id}/log`).then(r => r.text()),
  jobFileUrl: (id: string, index: number) => `/api/jobs/${id}/files/${index}`,
  cancelJob: (id: string) => request<{ ok: boolean }>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  retryJob: (id: string) => request<{ ok: boolean }>(`/api/jobs/${id}/retry`, { method: "POST" }),
  bulkCancel: (ids: string[]) =>
    request<{ count: number }>("/api/jobs/bulk-cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: ids }),
    }),
  bulkRetry: (ids: string[]) =>
    request<{ count: number }>("/api/jobs/bulk-retry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: ids }),
    }),
  bulkDelete: (ids: string[]) =>
    request<{ count: number; deleted: string[] }>("/api/jobs/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: ids }),
    }),
  manualJob: (source_value: string) =>
    request<{ ok: boolean }>("/api/jobs/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_value }),
    }),

  events: (params: { page?: number; page_size?: number; q?: string; event_type?: string }) => {
    const sp = new URLSearchParams();
    if (params.page) sp.set("page", String(params.page));
    if (params.page_size) sp.set("page_size", String(params.page_size));
    if (params.q) sp.set("q", params.q);
    if (params.event_type) sp.set("event_type", params.event_type);
    return request<EventsResponse>(`/api/events?${sp}`);
  },

  loginLogs: (params: { page?: number; page_size?: number; q?: string; success?: string }) => {
    const sp = new URLSearchParams();
    if (params.page) sp.set("page", String(params.page));
    if (params.page_size) sp.set("page_size", String(params.page_size));
    if (params.q) sp.set("q", params.q);
    if (params.success) sp.set("success", params.success);
    return request<LoginLogsResponse>(`/api/login-logs?${sp}`);
  },

  allowlist: () => request<unknown>("/api/allowlist"),
  allowUser: (userId: number | null, username: string | null) =>
    request<{ ok: boolean }>("/api/allowlist/allow", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, username }),
    }),
  denyUser: (userId: number | null, username: string | null) =>
    request<{ ok: boolean }>("/api/allowlist/deny", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, username }),
    }),
};
