from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from telegram import User

try:
    from .config import Settings
    from .models import DownloadJob
    from .utils import clean_username, is_admin_user, job_to_public_dict, normalize_username, now_utc
except ImportError:
    from config import Settings
    from models import DownloadJob
    from utils import clean_username, is_admin_user, job_to_public_dict, normalize_username, now_utc

DEFAULT_RECENT_LIMIT = 200
DEPRECATED_APP_SETTING_KEYS = (
    "telegram_upload_back_on_finish",
    "telegram_upload_back_max_mb",
)
DEPRECATED_JOB_COLUMNS = (
    "upload_back_status",
    "upload_back_sent",
    "upload_back_failed",
    "upload_back_skipped",
    "upload_back_detail",
)
JOBS_TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("job_id", "TEXT PRIMARY KEY"),
    ("chat_id", "INTEGER NOT NULL"),
    ("message_id", "INTEGER NOT NULL"),
    ("source_type", "TEXT NOT NULL"),
    ("source_value", "TEXT NOT NULL"),
    ("webhook_url", "TEXT"),
    ("submitted_at", "TEXT NOT NULL"),
    ("from_user", "TEXT"),
    ("from_user_id", "INTEGER"),
    ("caption_or_text", "TEXT"),
    ("original_file_name", "TEXT"),
    ("status", "TEXT NOT NULL"),
    ("updated_at", "TEXT NOT NULL"),
    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("files_json", "TEXT"),
    ("error", "TEXT"),
    ("progress_percent", "REAL"),
    ("progress_text", "TEXT"),
)
JOB_COLUMN_DEFAULTS: dict[str, str] = {
    "webhook_url": "NULL",
    "from_user": "NULL",
    "from_user_id": "NULL",
    "caption_or_text": "NULL",
    "original_file_name": "NULL",
    "status": "'queued'",
    "updated_at": "submitted_at",
    "attempts": "0",
    "files_json": "NULL",
    "error": "NULL",
    "progress_percent": "NULL",
    "progress_text": "NULL",
}


def build_jobs_table_sql(table_name: str = "jobs") -> str:
    columns_sql = ",\n                    ".join(f"{name} {definition}" for name, definition in JOBS_TABLE_COLUMNS)
    return f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    {columns_sql}
                );
    """

class SQLiteStore:
    def __init__(self, path: Path, settings: Settings) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()

    async def init(self) -> None:
        async with self.lock:
            self.conn.executescript(
                f"""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS allowlist_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    username TEXT UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS access_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    full_name TEXT,
                    chat_id INTEGER,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    note TEXT
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    chat_id INTEGER,
                    user_id INTEGER,
                    username TEXT,
                    full_name TEXT,
                    message_id INTEGER,
                    detail_json TEXT
                );

                CREATE TABLE IF NOT EXISTS login_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    username TEXT,
                    success INTEGER NOT NULL,
                    ip TEXT,
                    user_agent TEXT,
                    failure_reason TEXT
                );

                {build_jobs_table_sql()}

                CREATE INDEX IF NOT EXISTS idx_jobs_status_updated_at ON jobs(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_time ON events(time DESC);
                CREATE INDEX IF NOT EXISTS idx_login_logs_created_at ON login_logs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_login_logs_success_created_at ON login_logs(success, created_at DESC);
                """
            )
            self.conn.commit()
            self._ensure_jobs_extra_columns_locked()
            self._cleanup_deprecated_app_settings_locked()
            self._seed_initial_allowlist_locked()
            self._seed_app_settings_locked()
            self._reset_inflight_jobs_locked()

    def _ensure_jobs_extra_columns_locked(self) -> None:
        rows = self.conn.execute("PRAGMA table_info(jobs)").fetchall()
        columns = {row["name"] for row in rows}
        if any(column in columns for column in DEPRECATED_JOB_COLUMNS):
            self._rebuild_jobs_table_locked(columns)
            rows = self.conn.execute("PRAGMA table_info(jobs)").fetchall()
            columns = {row["name"] for row in rows}
        if "progress_percent" not in columns:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN progress_percent REAL")
        if "progress_text" not in columns:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN progress_text TEXT")
        req_rows = self.conn.execute("PRAGMA table_info(access_requests)").fetchall()
        req_columns = {row["name"] for row in req_rows}
        if req_rows and "status" not in req_columns:
            self.conn.execute("ALTER TABLE access_requests ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        if req_rows and "note" not in req_columns:
            self.conn.execute("ALTER TABLE access_requests ADD COLUMN note TEXT")
        self.conn.commit()

    def _rebuild_jobs_table_locked(self, existing_columns: set[str]) -> None:
        target_columns = [name for name, _ in JOBS_TABLE_COLUMNS]
        select_parts: list[str] = []
        for column in target_columns:
            if column in existing_columns:
                select_parts.append(column)
            else:
                select_parts.append(f"{JOB_COLUMN_DEFAULTS.get(column, 'NULL')} AS {column}")
        self.conn.execute("DROP TABLE IF EXISTS jobs__new")
        self.conn.execute(build_jobs_table_sql("jobs__new"))
        self.conn.execute(
            f"""
            INSERT INTO jobs__new ({", ".join(target_columns)})
            SELECT {", ".join(select_parts)}
            FROM jobs
            """
        )
        self.conn.execute("DROP TABLE jobs")
        self.conn.execute("ALTER TABLE jobs__new RENAME TO jobs")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_updated_at ON jobs(status, updated_at DESC)")

    def _cleanup_deprecated_app_settings_locked(self) -> None:
        self.conn.executemany(
            "DELETE FROM app_settings WHERE key = ?",
            [(key,) for key in DEPRECATED_APP_SETTING_KEYS],
        )
        self.conn.commit()

    def _seed_initial_allowlist_locked(self) -> None:
        now = now_utc()
        for user_id in sorted(self.settings.initial_allowed_user_ids):
            self.conn.execute(
                "INSERT OR IGNORE INTO allowlist_entries(user_id, username, created_at) VALUES (?, NULL, ?)",
                (user_id, now),
            )
        for username in sorted(self.settings.initial_allowed_usernames):
            self.conn.execute(
                "INSERT OR IGNORE INTO allowlist_entries(user_id, username, created_at) VALUES (NULL, ?, ?)",
                (username, now),
            )
        self.conn.commit()

    def _reset_inflight_jobs_locked(self) -> None:
        now = now_utc()
        self.conn.execute(
            "UPDATE jobs SET status='queued', updated_at=? WHERE status IN ('queued', 'downloading')",
            (now,),
        )
        self.conn.commit()

    def _seed_app_settings_locked(self) -> None:
        now = now_utc()
        defaults = {
            "default_webhook_url": self.settings.default_webhook_url or "",
            "default_hook_script": self.settings.default_hook_script or "",
            "telegram_reply_on_finish": "true" if self.settings.telegram_reply_on_finish else "false",
            "require_allowlist": "true" if self.settings.require_allowlist else "false",
            "max_concurrent_jobs": str(max(1, int(self.settings.max_concurrent_jobs))),
        }
        for key, value in defaults.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO app_settings(key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )
        self.conn.commit()

    async def close(self) -> None:
        async with self.lock:
            self.conn.close()

    async def get_app_settings(self) -> dict[str, str]:
        async with self.lock:
            rows = self.conn.execute("SELECT key, value FROM app_settings").fetchall()
        return {str(row["key"]): str(row["value"] or "") for row in rows}

    async def set_app_settings(self, values: dict[str, str]) -> dict[str, str]:
        now = now_utc()
        async with self.lock:
            for key, value in values.items():
                self.conn.execute(
                    """
                    INSERT INTO app_settings(key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (key, value, now),
                )
            self.conn.commit()
        return await self.get_app_settings()

    async def is_allowed(self, user: User | None) -> bool:
        if user is None:
            return False
        if is_admin_user(user, self.settings):
            return True
        if not self.settings.require_allowlist:
            return True

        username = normalize_username(user.username)
        async with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM allowlist_entries WHERE user_id = ? OR username = ? LIMIT 1",
                (user.id, username),
            ).fetchone()
        return row is not None

    async def allow_user(self, user_id: int | None, username: str | None) -> dict[str, Any]:
        if user_id is None and not username:
            raise ValueError("user_id 和 username 不能同时为空")
        username = clean_username(username) if username else None
        async with self.lock:
            now = now_utc()
            if user_id is not None:
                self.conn.execute(
                    "INSERT OR IGNORE INTO allowlist_entries(user_id, username, created_at) VALUES (?, NULL, ?)",
                    (user_id, now),
                )
            if username:
                self.conn.execute(
                    "INSERT OR IGNORE INTO allowlist_entries(user_id, username, created_at) VALUES (NULL, ?, ?)",
                    (username, now),
                )
            if user_id is not None:
                self.conn.execute("UPDATE access_requests SET status='allowed', last_seen_at=? WHERE user_id = ?", (now, user_id))
            if username:
                self.conn.execute("UPDATE access_requests SET status='allowed', last_seen_at=? WHERE username = ?", (now, username))
            self.conn.commit()
        return await self.allowlist_summary()

    async def deny_user(self, user_id: int | None, username: str | None) -> dict[str, Any]:
        if user_id is None and not username:
            raise ValueError("user_id 和 username 不能同时为空")
        username = clean_username(username) if username else None
        async with self.lock:
            now = now_utc()
            if user_id is not None:
                self.conn.execute("DELETE FROM allowlist_entries WHERE user_id = ?", (user_id,))
                self.conn.execute("UPDATE access_requests SET status='denied', last_seen_at=? WHERE user_id = ?", (now, user_id))
            if username:
                self.conn.execute("DELETE FROM allowlist_entries WHERE username = ?", (username,))
                self.conn.execute("UPDATE access_requests SET status='denied', last_seen_at=? WHERE username = ?", (now, username))
            self.conn.commit()
        return await self.allowlist_summary()

    async def touch_access_request(
        self,
        user_id: int | None,
        username: str | None,
        full_name: str | None,
        chat_id: int | None,
        *,
        note: str | None = None,
    ) -> None:
        if user_id is None and not username:
            return
        username = clean_username(username) if username else None
        now = now_utc()
        async with self.lock:
            row = None
            if user_id is not None:
                row = self.conn.execute("SELECT id, status FROM access_requests WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
            if row is None and username:
                row = self.conn.execute("SELECT id, status FROM access_requests WHERE username = ? LIMIT 1", (username,)).fetchone()
            if row is None:
                self.conn.execute(
                    """
                    INSERT INTO access_requests(user_id, username, full_name, chat_id, first_seen_at, last_seen_at, status, note)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (user_id, username, full_name, chat_id, now, now, note),
                )
            else:
                status = row["status"] or "pending"
                next_status = "allowed" if status == "allowed" else "pending"
                self.conn.execute(
                    """
                    UPDATE access_requests
                    SET username = COALESCE(?, username),
                        full_name = COALESCE(?, full_name),
                        chat_id = COALESCE(?, chat_id),
                        last_seen_at = ?,
                        status = ?,
                        note = COALESCE(?, note)
                    WHERE id = ?
                    """,
                    (username, full_name, chat_id, now, next_status, note, row["id"]),
                )
            self.conn.commit()

    async def allowlist_summary(self) -> dict[str, Any]:
        async with self.lock:
            rows = self.conn.execute(
                "SELECT user_id, username FROM allowlist_entries ORDER BY COALESCE(username, ''), COALESCE(user_id, 0)"
            ).fetchall()
            request_rows = self.conn.execute(
                """
                SELECT user_id, username, full_name, chat_id, first_seen_at, last_seen_at, status, note
                FROM access_requests
                ORDER BY
                  CASE status WHEN 'pending' THEN 0 WHEN 'allowed' THEN 1 ELSE 2 END,
                  last_seen_at DESC
                LIMIT 200
                """
            ).fetchall()
        return {
            "allowed_user_ids": sorted([int(row["user_id"]) for row in rows if row["user_id"] is not None]),
            "allowed_usernames": sorted([row["username"] for row in rows if row["username"]]),
            "access_requests": [
                {
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "full_name": row["full_name"],
                    "chat_id": row["chat_id"],
                    "first_seen_at": row["first_seen_at"],
                    "last_seen_at": row["last_seen_at"],
                    "status": row["status"],
                    "note": row["note"],
                }
                for row in request_rows
            ],
        }

    async def add_event(self, event_type: str, detail: dict[str, Any]) -> None:
        detail_copy = dict(detail)
        chat_id = detail_copy.pop("chat_id", None)
        user_id = detail_copy.pop("user_id", None)
        username = detail_copy.pop("username", None)
        full_name = detail_copy.pop("full_name", None)
        message_id = detail_copy.pop("message_id", None)
        async with self.lock:
            self.conn.execute(
                """
                INSERT INTO events(time, event_type, chat_id, user_id, username, full_name, message_id, detail_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_utc(),
                    event_type,
                    chat_id,
                    user_id,
                    username,
                    full_name,
                    message_id,
                    json.dumps(detail_copy, ensure_ascii=False) if detail_copy else None,
                ),
            )
            self.conn.commit()

    async def add_login_log(
        self,
        *,
        username: str | None,
        success: bool,
        ip: str | None,
        user_agent: str | None,
        failure_reason: str | None = None,
    ) -> None:
        async with self.lock:
            self.conn.execute(
                """
                INSERT INTO login_logs(created_at, username, success, ip, user_agent, failure_reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    now_utc(),
                    username,
                    1 if success else 0,
                    ip,
                    user_agent,
                    failure_reason,
                ),
            )
            self.conn.commit()

    async def search_login_logs(
        self,
        page: int = 1,
        page_size: int = 20,
        q: str = "",
        success: str = "",
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        clauses: list[str] = []
        params: list[Any] = []

        if success == "true":
            clauses.append("success = 1")
        elif success == "false":
            clauses.append("success = 0")

        q = q.strip()
        if q:
            like = f"%{q}%"
            clauses.append("(IFNULL(username, '') LIKE ? OR IFNULL(ip, '') LIKE ? OR IFNULL(user_agent, '') LIKE ?)")
            params.extend([like, like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self.lock:
            total = int(
                self.conn.execute(
                    f"SELECT COUNT(*) AS c FROM login_logs {where_sql}",
                    tuple(params),
                ).fetchone()["c"]
            )
            rows = self.conn.execute(
                f"SELECT * FROM login_logs {where_sql} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()

        items = [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "username": row["username"],
                "success": bool(row["success"]),
                "ip": row["ip"],
                "user_agent": row["user_agent"],
                "failure_reason": row["failure_reason"],
            }
            for row in rows
        ]
        pages = max(1, (total + page_size - 1) // page_size)
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "q": q,
            "success": success,
        }

    async def create_job(self, job: DownloadJob) -> None:
        payload = asdict(job)
        now = now_utc()
        async with self.lock:
            self.conn.execute(
                """
                INSERT INTO jobs(
                    job_id, chat_id, message_id, source_type, source_value, webhook_url,
                    submitted_at, from_user, from_user_id, caption_or_text, original_file_name,
                    status, updated_at, attempts, files_json, error, progress_percent, progress_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["job_id"],
                    payload["chat_id"],
                    payload["message_id"],
                    payload["source_type"],
                    payload["source_value"],
                    payload["webhook_url"],
                    payload["submitted_at"],
                    payload["from_user"],
                    payload["from_user_id"],
                    payload["caption_or_text"],
                    payload["original_file_name"],
                    "queued",
                    now,
                    0,
                    None,
                    None,
                    None,
                    None,
                ),
            )
            self.conn.commit()

    async def get_job(self, job_id: str) -> DownloadJob | None:
        async with self.lock:
            row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return DownloadJob.from_row(row) if row else None

    async def list_jobs(self, limit: int = DEFAULT_RECENT_LIMIT) -> list[dict[str, Any]]:
        async with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [job_to_public_dict(DownloadJob.from_row(row)) for row in rows]

    async def search_jobs(
        self,
        page: int = 1,
        page_size: int = 20,
        q: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        clauses: list[str] = []
        params: list[Any] = []

        if status:
            clauses.append("status = ?")
            params.append(status)

        q = q.strip()
        if q:
            clauses.append(
                "("
                "job_id LIKE ? OR source_value LIKE ? OR IFNULL(from_user, '') LIKE ? "
                "OR source_type LIKE ? OR CAST(IFNULL(from_user_id, '') AS TEXT) LIKE ?"
                ")"
            )
            like = f"%{q}%"
            params.extend([like, like, like, like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self.lock:
            total = int(
                self.conn.execute(
                    f"SELECT COUNT(*) AS c FROM jobs {where_sql}",
                    tuple(params),
                ).fetchone()["c"]
            )
            rows = self.conn.execute(
                f"SELECT * FROM jobs {where_sql} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()

        items = [job_to_public_dict(DownloadJob.from_row(row)) for row in rows]
        pages = max(1, (total + page_size - 1) // page_size)
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "q": q,
            "status": status,
        }

    async def list_events(self, limit: int = DEFAULT_RECENT_LIMIT) -> list[dict[str, Any]]:
        async with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY time DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            detail = json.loads(row["detail_json"]) if row["detail_json"] else {}
            result.append(
                {
                    "id": row["id"],
                    "time": row["time"],
                    "event_type": row["event_type"],
                    "chat_id": row["chat_id"],
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "full_name": row["full_name"],
                    "message_id": row["message_id"],
                    **detail,
                }
            )
        return result

    async def search_events(
        self,
        page: int = 1,
        page_size: int = 20,
        q: str = "",
        event_type: str = "",
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        clauses: list[str] = []
        params: list[Any] = []

        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)

        q = q.strip()
        if q:
            clauses.append(
                "("
                "event_type LIKE ? OR IFNULL(username, '') LIKE ? OR IFNULL(full_name, '') LIKE ? "
                "OR IFNULL(detail_json, '') LIKE ? OR CAST(IFNULL(user_id, '') AS TEXT) LIKE ? "
                "OR CAST(IFNULL(chat_id, '') AS TEXT) LIKE ?"
                ")"
            )
            like = f"%{q}%"
            params.extend([like, like, like, like, like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self.lock:
            total = int(
                self.conn.execute(
                    f"SELECT COUNT(*) AS c FROM events {where_sql}",
                    tuple(params),
                ).fetchone()["c"]
            )
            rows = self.conn.execute(
                f"SELECT * FROM events {where_sql} ORDER BY time DESC LIMIT ? OFFSET ?",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            detail = json.loads(row["detail_json"]) if row["detail_json"] else {}
            items.append(
                {
                    "id": row["id"],
                    "time": row["time"],
                    "event_type": row["event_type"],
                    "chat_id": row["chat_id"],
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "full_name": row["full_name"],
                    "message_id": row["message_id"],
                    **detail,
                }
            )
        pages = max(1, (total + page_size - 1) // page_size)
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "q": q,
            "event_type": event_type,
        }

    async def job_stats(self) -> dict[str, Any]:
        async with self.lock:
            by_source_rows = self.conn.execute(
                "SELECT source_type, COUNT(*) AS c FROM jobs GROUP BY source_type ORDER BY c DESC"
            ).fetchall()
            by_status_rows = self.conn.execute(
                "SELECT status, COUNT(*) AS c FROM jobs GROUP BY status ORDER BY c DESC"
            ).fetchall()
            by_user_rows = self.conn.execute(
                """
                SELECT COALESCE(from_user, '') AS from_user, COALESCE(from_user_id, 0) AS from_user_id, COUNT(*) AS c
                FROM jobs
                GROUP BY COALESCE(from_user, ''), COALESCE(from_user_id, 0)
                ORDER BY c DESC, from_user_id DESC
                LIMIT 20
                """
            ).fetchall()
        return {
            "by_source_type": [
                {"source_type": row["source_type"], "count": int(row["c"])} for row in by_source_rows
            ],
            "by_status": [
                {"status": row["status"], "count": int(row["c"])} for row in by_status_rows
            ],
            "by_user": [
                {
                    "from_user": row["from_user"] or None,
                    "from_user_id": row["from_user_id"] if row["from_user_id"] != 0 else None,
                    "count": int(row["c"]),
                }
                for row in by_user_rows
            ],
        }

    async def delete_job_records(self, job_ids: list[str]) -> dict[str, Any]:
        deleted: list[str] = []
        skipped: list[dict[str, Any]] = []
        terminal_statuses = {"success", "failed", "cancelled"}
        async with self.lock:
            for job_id in job_ids:
                row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
                if row is None:
                    skipped.append({"job_id": job_id, "reason": "not found"})
                    continue
                job = DownloadJob.from_row(row)
                if job.status not in terminal_statuses:
                    skipped.append({"job_id": job_id, "reason": f"status={job.status}"})
                    continue
                self.conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
                deleted.append(job_id)
            self.conn.commit()
        return {"deleted": deleted, "skipped": skipped}

    async def job_counts(self) -> dict[str, int]:
        async with self.lock:
            rows = self.conn.execute("SELECT status, COUNT(*) AS c FROM jobs GROUP BY status").fetchall()
        counts = {"queued": 0, "downloading": 0, "success": 0, "failed": 0, "cancelled": 0}
        for row in rows:
            counts[row["status"]] = int(row["c"])
        return counts

    async def claim_job(self, job_id: str) -> DownloadJob | None:
        async with self.lock:
            row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            if row["status"] not in {"queued", "downloading"}:
                return DownloadJob.from_row(row)
            self.conn.execute(
                "UPDATE jobs SET status='downloading', updated_at=?, attempts=attempts+1, error=NULL, progress_percent=0, progress_text='准备中' WHERE job_id = ?",
                (now_utc(), job_id),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return DownloadJob.from_row(row) if row else None

    async def complete_job(self, job_id: str, status: str, files: list[str] | None = None, error: str | None = None) -> None:
        async with self.lock:
            self.conn.execute(
                "UPDATE jobs SET status=?, updated_at=?, files_json=?, error=?, progress_percent=?, progress_text=? WHERE job_id = ?",
                (status, now_utc(), json.dumps(files, ensure_ascii=False) if files else None, error, 100.0 if status == "success" else None, None if status == "success" else None, job_id),
            )
            self.conn.commit()

    async def cancel_job(self, job_id: str) -> DownloadJob | None:
        async with self.lock:
            row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            current = DownloadJob.from_row(row)
            if current.status in {"success", "failed", "cancelled"}:
                return current
            self.conn.execute(
                "UPDATE jobs SET status='cancelled', updated_at=?, error=?, progress_percent=NULL, progress_text=NULL WHERE job_id = ?",
                (now_utc(), "cancelled by admin", job_id),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return DownloadJob.from_row(row) if row else None

    async def retry_job(self, job_id: str) -> DownloadJob | None:
        async with self.lock:
            row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            current = DownloadJob.from_row(row)
            if current.status in {"queued", "downloading"}:
                return current
            self.conn.execute(
                "UPDATE jobs SET status='queued', updated_at=?, files_json=NULL, error=NULL, progress_percent=NULL, progress_text=NULL WHERE job_id = ?",
                (now_utc(), job_id),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return DownloadJob.from_row(row) if row else None

    async def update_job_progress(self, job_id: str, progress_percent: float | None, progress_text: str | None) -> None:
        async with self.lock:
            self.conn.execute(
                "UPDATE jobs SET updated_at=?, progress_percent=?, progress_text=? WHERE job_id = ?",
                (now_utc(), progress_percent, progress_text, job_id),
            )
            self.conn.commit()

    async def requeue_recoverable_jobs(self) -> list[str]:
        async with self.lock:
            rows = self.conn.execute(
                "SELECT job_id FROM jobs WHERE status='queued' ORDER BY submitted_at ASC"
            ).fetchall()
        return [row["job_id"] for row in rows]

    async def snapshot(self, queue_size: int, worker_count: int) -> dict[str, Any]:
        allowlist = await self.allowlist_summary()
        jobs = await self.list_jobs()
        events = await self.list_events()
        counts = await self.job_counts()
        return {
            **allowlist,
            "recent_jobs": jobs,
            "recent_events": events,
            "job_counts": counts,
            "queue": {
                "in_memory_size": queue_size,
                "worker_count": worker_count,
            },
            "updated_at": now_utc(),
        }
