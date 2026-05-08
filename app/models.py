from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass
class DownloadJob:
    job_id: str
    chat_id: int
    message_id: int
    source_type: str
    source_value: str
    webhook_url: str | None
    submitted_at: str
    from_user: str | None
    from_user_id: int | None
    caption_or_text: str | None
    original_file_name: str | None = None
    status: str = "queued"
    updated_at: str | None = None
    attempts: int = 0
    files: list[str] | None = None
    error: str | None = None
    progress_percent: float | None = None
    progress_text: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DownloadJob":
        return cls(
            job_id=row["job_id"],
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            source_type=row["source_type"],
            source_value=row["source_value"],
            webhook_url=row["webhook_url"],
            submitted_at=row["submitted_at"],
            from_user=row["from_user"],
            from_user_id=row["from_user_id"],
            caption_or_text=row["caption_or_text"],
            original_file_name=row["original_file_name"],
            status=row["status"],
            updated_at=row["updated_at"],
            attempts=row["attempts"],
            files=json.loads(row["files_json"]) if row["files_json"] else None,
            error=row["error"],
            progress_percent=row["progress_percent"] if "progress_percent" in row.keys() else None,
            progress_text=row["progress_text"] if "progress_text" in row.keys() else None,
        )


class AllowlistPayload(BaseModel):
    user_id: int | None = None
    username: str | None = None


class JobIdsPayload(BaseModel):
    job_ids: list[str]


class ManualJobPayload(BaseModel):
    source_value: str
    webhook_url: str | None = None


class HookConfigPayload(BaseModel):
    webhook_url: str | None = None
    hook_script: str | None = None
    telegram_reply_on_finish: bool | None = None
    require_allowlist: bool | None = None
    max_concurrent_jobs: int | None = None


class JobCancelledError(Exception):
    pass
