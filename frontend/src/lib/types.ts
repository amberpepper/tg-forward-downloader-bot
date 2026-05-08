export interface AppState {
  queue: { in_memory_size: number; worker_count: number };
  job_counts: { success: number; failed: number; queued: number; cancelled: number };
  allowed_user_ids: number[];
  allowed_usernames: string[];
  access_requests: AccessRequest[];
}

export interface AccessRequest {
  user_id: number;
  username: string;
  full_name: string;
  chat_id: number;
  first_seen_at: string;
  last_seen_at: string;
  status: string;
}

export interface JobItem {
  job_id: string;
  status: string;
  source_type: string;
  source_value: string;
  from_user: string;
  from_user_id: string;
  message_id: number;
  chat_id: number;
  submitted_at: string;
  updated_at: string;
  attempts: number;
  error: string;
  files: string[];
  progress_percent: number;
  progress_text: string;
  can_cancel: boolean;
  can_retry: boolean;
  original_file_name: string;
}

export interface JobsResponse {
  items: JobItem[];
  page: number;
  pages: number;
  total: number;
}

export interface EventItem {
  id: number;
  time: string;
  event_type: string;
  chat_id: number;
  user_id: number;
  username: string;
  full_name: string;
  message_id: number;
  [key: string]: unknown;
}

export interface EventsResponse {
  items: EventItem[];
  page: number;
  pages: number;
  total: number;
}

export interface LoginLogItem {
  id: number;
  created_at: string;
  success: boolean;
  username: string;
  ip: string;
  user_agent: string;
  failure_reason: string;
}

export interface LoginLogsResponse {
  items: LoginLogItem[];
  page: number;
  pages: number;
  total: number;
}

export interface StatsSummary {
  by_source_type: { source_type: string; count: number }[];
  by_status: { status: string; count: number }[];
}

export interface ToolInfo {
  installed: boolean;
  binary?: string;
  version: string | null;
  path: string | null;
  hint?: string;
}

export interface ToolsStatusResponse {
  tools: Record<string, ToolInfo>;
  updated_at: string;
}

export interface HookConfig {
  default_webhook_url: string;
  default_hook_script: string;
  telegram_reply_on_finish: boolean;
  require_allowlist: boolean;
  max_concurrent_jobs: number;
}
