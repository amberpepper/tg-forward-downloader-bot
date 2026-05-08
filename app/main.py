from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import shlex
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response as FastAPIResponse
from starlette.background import BackgroundTask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

try:
    from .config import PROJECT_ROOT, Settings, apply_runtime_app_settings, load_env_files
    from .models import AllowlistPayload, DownloadJob, HookConfigPayload, JobCancelledError, JobIdsPayload, ManualJobPayload
    from .store import DEFAULT_RECENT_LIMIT, SQLiteStore
    from .tools import get_tools_status, normalize_tool_name, run_tool_action_script
    from .utils import (
        build_session_cookie,
        build_public_file_signature,
        clean_username,
        describe_process_failure,
        find_default_preview_index,
        get_preview_type,
        is_telegram_message_url,
        job_to_public_dict,
        now_utc,
        parse_allow_input,
        parse_progress_update,
        sanitize_filename,
        verify_public_file_signature,
        verify_session_cookie,
    )
except ImportError:
    from config import PROJECT_ROOT, Settings, apply_runtime_app_settings, load_env_files
    from models import AllowlistPayload, DownloadJob, HookConfigPayload, JobCancelledError, JobIdsPayload, ManualJobPayload
    from store import DEFAULT_RECENT_LIMIT, SQLiteStore
    from tools import get_tools_status, normalize_tool_name, run_tool_action_script
    from utils import (
        build_session_cookie,
        build_public_file_signature,
        clean_username,
        describe_process_failure,
        find_default_preview_index,
        get_preview_type,
        is_telegram_message_url,
        job_to_public_dict,
        now_utc,
        parse_allow_input,
        parse_progress_update,
        sanitize_filename,
        verify_public_file_signature,
        verify_session_cookie,
    )

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tg-forward-downloader")

URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
HOOK_RE = re.compile(r"(?:^|\s)(?:hook|webhook)\s*[:=]\s*(https?://\S+)", re.IGNORECASE)
ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
SESSION_COOKIE_NAME = "tgfd_session"

class DownloaderBot:
    def __init__(self, settings: Settings, store: SQLiteStore) -> None:
        self.settings = settings
        self.store = store
        self.settings.download_root.mkdir(parents=True, exist_ok=True)
        self.application: Application | None = None
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker_tasks: list[asyncio.Task[Any]] = []
        self.stop_event = asyncio.Event()
        self.active_job_tasks: dict[str, asyncio.Task[Any]] = {}
        self.active_processes: dict[str, asyncio.subprocess.Process] = {}
        self.cancel_requested: set[str] = set()
        self.worker_pool_lock = asyncio.Lock()

    async def start_bot(self) -> None:
        application = Application.builder().token(self.settings.bot_token).build()
        self.application = application
        application.bot_data["service"] = self

        application.add_handler(CommandHandler("start", self.handle_start))
        application.add_handler(CommandHandler("whoami", self.handle_whoami))
        application.add_handler(MessageHandler(~filters.COMMAND, self.handle_message))

        logger.info("bot starting | download_root=%s | require_allowlist=%s | db=%s", self.settings.download_root, self.settings.require_allowlist, self.settings.db_path)
        await application.initialize()
        await application.start()
        await self.start_workers()
        await self.enqueue_recovered_jobs()
        await application.updater.start_polling(drop_pending_updates=True)
        try:
            await self.stop_event.wait()
        finally:
            await application.updater.stop()
            await self.stop_workers()
            await application.stop()
            await application.shutdown()

    async def stop(self) -> None:
        self.stop_event.set()

    async def start_workers(self) -> None:
        async with self.worker_pool_lock:
            self.worker_tasks = [task for task in self.worker_tasks if not task.done()]
            current_count = len(self.worker_tasks)
            target_count = max(1, int(self.settings.max_concurrent_jobs))
            for index in range(current_count, target_count):
                worker_id = index + 1
                task = asyncio.create_task(self.worker_loop(worker_id), name=f"job-worker-{worker_id}")
                self.worker_tasks.append(task)

    async def stop_workers(self) -> None:
        for task in self.worker_tasks:
            task.cancel()
        if self.worker_tasks:
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        self.worker_tasks.clear()

    async def refresh_workers(self) -> None:
        await self.start_workers()
        async with self.worker_pool_lock:
            self.worker_tasks = [task for task in self.worker_tasks if not task.done()]
            target_count = max(1, int(self.settings.max_concurrent_jobs))
            active_tasks = set(self.active_job_tasks.values())
            idle_tasks = [task for task in self.worker_tasks if task not in active_tasks]
            while len(self.worker_tasks) > target_count and idle_tasks:
                task = idle_tasks.pop()
                task.cancel()
                self.worker_tasks.remove(task)

    async def enqueue_recovered_jobs(self) -> None:
        recovered = await self.store.requeue_recoverable_jobs()
        for job_id in recovered:
            await self.queue.put(job_id)
        if recovered:
            logger.info("recovered queued jobs: %s", len(recovered))

    async def worker_loop(self, worker_id: int) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                current_task = asyncio.current_task()
                if current_task is not None:
                    self.active_job_tasks[job_id] = current_task
                await self.process_job_by_id(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("worker failed | worker=%s | job=%s | err=%s", worker_id, job_id, exc)
            finally:
                self.active_job_tasks.pop(job_id, None)
                self.queue.task_done()
            current_task = asyncio.current_task()
            should_retire = False
            async with self.worker_pool_lock:
                self.worker_tasks = [task for task in self.worker_tasks if not task.done()]
                if current_task is not None and len(self.worker_tasks) > max(1, int(self.settings.max_concurrent_jobs)):
                    self.worker_tasks = [task for task in self.worker_tasks if task is not current_task]
                    should_retire = True
            if should_retire:
                logger.info("worker retired | worker=%s | target=%s", worker_id, self.settings.max_concurrent_jobs)
                return

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.store.add_event("command.start", self.user_event_payload(update.effective_user, update.effective_chat.id if update.effective_chat else None))
        if update.effective_user:
            await self.store.touch_access_request(
                update.effective_user.id,
                update.effective_user.username,
                update.effective_user.full_name,
                update.effective_chat.id if update.effective_chat else None,
                note="start",
            )
        if update.effective_message:
            await update.effective_message.reply_text("已记录你的访问请求。如未开通权限，请等待管理员 allow。")

    async def handle_whoami(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.store.add_event("command.whoami", self.user_event_payload(update.effective_user, update.effective_chat.id if update.effective_chat else None))
        if not update.effective_message or not update.effective_user:
            return
        user = update.effective_user
        await update.effective_message.reply_text(str(user.id))

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        if not message:
            return
        text = message.text or message.caption or ""
        custom_webhook = self.extract_webhook(text)
        detected = self.detect_message_source(message, custom_webhook=custom_webhook)
        detected_source_type = detected["source_type"] if detected else "unknown"

        await self.store.add_event(
            "message.received",
            {
                **self.user_event_payload(user, message.chat_id),
                "message_id": message.message_id,
                "has_video": bool(message.video),
                "has_document": bool(message.document),
                "has_text": bool(message.text or message.caption),
                "detected_source_type": detected_source_type,
            },
        )

        if not await self.store.is_allowed(user):
            if user:
                await self.store.touch_access_request(
                    user.id,
                    user.username,
                    user.full_name,
                    message.chat_id,
                    note="message_rejected",
                )
            await self.store.add_event(
                "message.rejected",
                {
                    **self.user_event_payload(user, message.chat_id),
                    "message_id": message.message_id,
                    "reason": "not allowed",
                },
            )
            await message.reply_text("你没有下载权限，请先让管理员执行 /allow 你的 user_id")
            return

        job = self.build_job(message)
        if not job:
            await message.reply_text("没识别到可下载内容：请转发视频，或者发送包含 URL 的文本。")
            return

        job_dir = self.job_dir(job.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "job.json").write_text(json.dumps(asdict(job), ensure_ascii=False, indent=2), encoding="utf-8")
        await self.store.create_job(job)
        await self.store.add_event(
            "job.queued",
            {
                **self.user_event_payload(user, message.chat_id),
                "message_id": message.message_id,
                "job_id": job.job_id,
                "source_type": job.source_type,
            },
        )
        await self.send_webhook(job, "queued", job_dir, extra={"message": "job accepted"})
        await self.queue.put(job.job_id)

    async def enqueue_manual_job(self, source_value: str, webhook_url: str | None = None, from_user: str | None = "web-admin") -> DownloadJob:
        source_value = (source_value or "").strip()
        if not source_value:
            raise ValueError("source_value 不能为空")
        if not source_value.lower().startswith(("http://", "https://")):
            raise ValueError("只支持 http/https 链接")
        source_type = "telegram_link" if is_telegram_message_url(source_value) else "url"
        job = DownloadJob(
            job_id=self.new_job_id(),
            chat_id=0,
            message_id=0,
            source_type=source_type,
            source_value=source_value,
            webhook_url=(webhook_url or "").strip() or self.settings.default_webhook_url,
            submitted_at=now_utc(),
            from_user=from_user,
            from_user_id=None,
            caption_or_text=None,
            original_file_name=None,
        )
        job_dir = self.job_dir(job.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "job.json").write_text(json.dumps(asdict(job), ensure_ascii=False, indent=2), encoding="utf-8")
        await self.store.create_job(job)
        await self.store.add_event(
            "job.queued",
            {
                "chat_id": 0,
                "user_id": None,
                "username": clean_username(from_user) if from_user else None,
                "full_name": None,
                "message_id": 0,
                "job_id": job.job_id,
                "source_type": job.source_type,
            },
        )
        await self.send_webhook(job, "queued", job_dir, extra={"message": "job accepted"})
        await self.queue.put(job.job_id)
        return job

    def build_job(self, message: Message) -> DownloadJob | None:
        text = message.text or message.caption or ""
        custom_webhook = self.extract_webhook(text)
        webhook_url = custom_webhook or self.settings.default_webhook_url
        username = message.from_user.username if message.from_user else None
        user_id = message.from_user.id if message.from_user else None
        detected = self.detect_message_source(message, custom_webhook=custom_webhook)
        if detected:
            return DownloadJob(
                job_id=self.new_job_id(),
                chat_id=message.chat_id,
                message_id=message.message_id,
                source_type=detected["source_type"],
                source_value=detected["source_value"],
                webhook_url=webhook_url,
                submitted_at=now_utc(),
                from_user=username,
                from_user_id=user_id,
                caption_or_text=text or None,
                original_file_name=detected.get("original_file_name"),
            )
        return None

    async def process_job_by_id(self, job_id: str) -> None:
        job = await self.store.claim_job(job_id)
        if job is None:
            return
        if job.status not in {"downloading", "queued"}:
            return

        await self.store.add_event(
            "job.downloading",
            {
                "job_id": job.job_id,
                "chat_id": job.chat_id,
                "user_id": job.from_user_id,
                "username": clean_username(job.from_user) if job.from_user else None,
                "source_type": job.source_type,
            },
        )
        job_dir = self.job_dir(job.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        await self.send_webhook(job, "downloading", job_dir)

        try:
            if job.source_type in {"telegram_video", "telegram_document"}:
                files = await self.download_telegram_file(job, job_dir)
            elif job.source_type == "telegram_link":
                files = await self.download_telegram_link(job, job_dir)
            else:
                files = await self.download_url(job, job_dir)

            latest = await self.store.get_job(job.job_id)
            if job.job_id in self.cancel_requested or (latest is not None and latest.status == "cancelled"):
                raise JobCancelledError("cancelled by admin")

            await self.store.complete_job(job.job_id, "success", files=files)
            await self.store.add_event(
                "job.success",
                {
                    "job_id": job.job_id,
                    "chat_id": job.chat_id,
                    "user_id": job.from_user_id,
                    "username": clean_username(job.from_user) if job.from_user else None,
                    "file_count": len(files),
                },
            )
            await self.send_webhook(job, "success", job_dir, extra={"files": files, "file_count": len(files)})
            if self.settings.telegram_reply_on_finish and job.chat_id > 0:
                await self.send_job_success_reply(job, files)
        except JobCancelledError:
            await self.store.complete_job(job.job_id, "cancelled", error="cancelled by admin")
            await self.store.add_event(
                "job.cancelled",
                {
                    "job_id": job.job_id,
                    "chat_id": job.chat_id,
                    "user_id": job.from_user_id,
                    "username": clean_username(job.from_user) if job.from_user else None,
                },
            )
            await self.send_webhook(job, "cancelled", job_dir, extra={"error": "cancelled by admin"})
            if self.settings.telegram_reply_on_finish and job.chat_id > 0:
                await self.safe_send_message(job.chat_id, "任务已取消", reply_to_message_id=job.message_id)
        except Exception as exc:  # noqa: BLE001
            latest = await self.store.get_job(job.job_id)
            if job.job_id in self.cancel_requested or (latest is not None and latest.status == "cancelled"):
                await self.store.complete_job(job.job_id, "cancelled", error="cancelled by admin")
                await self.send_webhook(job, "cancelled", job_dir, extra={"error": "cancelled by admin"})
                return
            logger.exception("job failed: %s", job.job_id)
            await self.store.complete_job(job.job_id, "failed", error=str(exc))
            await self.store.add_event(
                "job.failed",
                {
                    "job_id": job.job_id,
                    "chat_id": job.chat_id,
                    "user_id": job.from_user_id,
                    "username": clean_username(job.from_user) if job.from_user else None,
                    "error": str(exc),
                },
            )
            await self.send_webhook(job, "failed", job_dir, extra={"error": str(exc)})
            if self.settings.telegram_reply_on_finish and job.chat_id > 0:
                await self.safe_send_message(job.chat_id, f"下载失败\nerror={exc}", reply_to_message_id=job.message_id)
        finally:
            self.cancel_requested.discard(job.job_id)

    async def download_telegram_file(self, job: DownloadJob, job_dir: Path) -> list[str]:
        bot = self.require_bot()
        await self.store.update_job_progress(job.job_id, 0.0, "Telegram 文件下载中")
        tg_file = await bot.get_file(job.source_value)
        raw_name = job.original_file_name or Path(urlparse(tg_file.file_path or "file.bin").path).name or "file.bin"
        target = job_dir / sanitize_filename(raw_name)
        await tg_file.download_to_drive(custom_path=str(target))
        await self.store.update_job_progress(job.job_id, 100.0, "下载完成")
        return [str(target)]

    async def download_telegram_link(self, job: DownloadJob, job_dir: Path) -> list[str]:
        command = self.settings.tdl_cmd.format(
            url=shlex.quote(job.source_value),
            output_dir=shlex.quote(str(job_dir)),
            output_template=shlex.quote(str(job_dir / "%(title)s.%(ext)s")),
        )
        return await self.run_download_command(job.job_id, job_dir, command, "tdl")

    async def download_url(self, job: DownloadJob, job_dir: Path) -> list[str]:
        command = self.settings.url_downloader_cmd.format(
            url=shlex.quote(job.source_value),
            output_dir=shlex.quote(str(job_dir)),
            output_template=shlex.quote(str(job_dir / "%(title)s.%(ext)s")),
        )
        return await self.run_download_command(job.job_id, job_dir, command, "URL 下载器")

    async def run_download_command(self, job_id: str, job_dir: Path, command: str, label: str) -> list[str]:
        before = {p.resolve() for p in job_dir.rglob("*") if p.is_file()}
        logger.info("run %s: %s", label, command)
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(job_dir),
        )
        self.active_processes[job_id] = proc
        progress_buffer = ""
        recent_output = ""
        last_progress_percent: float | None = None
        last_progress_text = ""
        log_path = job_dir / "downloader.log"
        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                while True:
                    chunk = await proc.stdout.read(2048) if proc.stdout else b""
                    if not chunk:
                        break
                    decoded = chunk.decode("utf-8", errors="ignore")
                    sanitized_chunk = ANSI_ESCAPE_RE.sub("", decoded).replace("\r\n", "\n").replace("\r", "\n")
                    log_file.write(sanitized_chunk)
                    log_file.flush()

                    progress_buffer += decoded
                    percent, text = parse_progress_update(progress_buffer)
                    if percent is not None or text:
                        should_update = False
                        if percent is not None and (last_progress_percent is None or abs(percent - last_progress_percent) >= 0.1):
                            should_update = True
                        elif text and text != last_progress_text:
                            should_update = True
                        if should_update:
                            last_progress_percent = percent if percent is not None else last_progress_percent
                            last_progress_text = text or last_progress_text
                            await self.store.update_job_progress(job_id, last_progress_percent, last_progress_text[:300] if last_progress_text else None)
                        if len(progress_buffer) > 8000:
                            progress_buffer = progress_buffer[-4000:]

                    recent_output += sanitized_chunk
                    if len(recent_output) > 20000:
                        recent_output = recent_output[-12000:]
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(describe_process_failure(label, proc.returncode, recent_output))
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        finally:
            self.active_processes.pop(job_id, None)

        after = [p.resolve() for p in job_dir.rglob("*") if p.is_file()]
        files = [str(p) for p in after if p not in before and p.name not in {"job.json", "downloader.log", "webhook.log"}]
        if not files:
            files = [str(p) for p in after if p.name not in {"job.json", "downloader.log", "webhook.log"}]
        if not files:
            raise RuntimeError("下载器执行成功，但没有发现输出文件")
        return sorted(files)

    async def send_webhook(self, job: DownloadJob, status: str, job_dir: Path, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "event": f"download.{status}",
            "status": status,
            "job_id": job.job_id,
            "chat_id": job.chat_id,
            "message_id": job.message_id,
            "source_type": job.source_type,
            "source_value": job.source_value,
            "submitted_at": job.submitted_at,
            "from_user": job.from_user,
            "from_user_id": job.from_user_id,
            "timestamp": now_utc(),
        }
        if extra:
            payload.update(extra)

        with (job_dir / "webhook.log").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

        await self.dispatch_hook_targets(payload, job_dir, job.webhook_url)

    async def dispatch_hook_targets(self, payload: dict[str, Any], job_dir: Path, webhook_url: str | None = None) -> None:
        target_url = (webhook_url or self.settings.default_webhook_url or "").strip() or None
        target_script = (self.settings.default_hook_script or "").strip() or None
        if target_url:
            await self.post_hook_http(target_url, payload, job_dir)
        if target_script:
            await self.run_hook_script(target_script, payload, job_dir)

    async def post_hook_http(self, webhook_url: str, payload: dict[str, Any], job_dir: Path) -> None:
        timeout = httpx.Timeout(15.0, connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(webhook_url, json=payload)
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("webhook post failed | url=%s | err=%s", webhook_url, exc)
            with (job_dir / "webhook.log").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"webhook_error": str(exc), "url": webhook_url, "timestamp": now_utc()}, ensure_ascii=False) + "\n")

    async def run_hook_script(self, command: str, payload: dict[str, Any], job_dir: Path) -> None:
        env = os.environ.copy()
        env.update(
            {
                "HOOK_EVENT": str(payload.get("event") or ""),
                "HOOK_STATUS": str(payload.get("status") or ""),
                "HOOK_JOB_ID": str(payload.get("job_id") or ""),
                "HOOK_CHAT_ID": str(payload.get("chat_id") or ""),
                "HOOK_MESSAGE_ID": str(payload.get("message_id") or ""),
                "HOOK_SOURCE_TYPE": str(payload.get("source_type") or ""),
                "HOOK_SOURCE_VALUE": str(payload.get("source_value") or ""),
            }
        )
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            output = stdout.decode("utf-8", errors="ignore")
            with (job_dir / "webhook.log").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"hook_script": command, "returncode": proc.returncode, "output": output, "timestamp": now_utc()}, ensure_ascii=False) + "\n")
            if proc.returncode != 0:
                logger.warning("hook script failed | cmd=%s | returncode=%s", command, proc.returncode)
        except Exception as exc:  # noqa: BLE001
            logger.warning("hook script failed | cmd=%s | err=%s", command, exc)
            with (job_dir / "webhook.log").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"hook_script_error": str(exc), "hook_script": command, "timestamp": now_utc()}, ensure_ascii=False) + "\n")

    async def safe_send_message(self, chat_id: int, text: str, reply_to_message_id: int | None = None) -> None:
        try:
            bot = self.require_bot()
            await bot.send_message(chat_id=chat_id, text=text, reply_to_message_id=reply_to_message_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("send message failed | chat=%s | err=%s", chat_id, exc)

    async def safe_send_message_with_markup(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        reply_to_message_id: int | None = None,
    ) -> None:
        try:
            bot = self.require_bot()
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("send message failed | chat=%s | err=%s", chat_id, exc)

    async def safe_send_photo(
        self,
        chat_id: int,
        photo_path: Path,
        caption: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        reply_to_message_id: int | None = None,
    ) -> None:
        try:
            bot = self.require_bot()
            with photo_path.open("rb") as photo_file:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=caption,
                    reply_markup=reply_markup,
                    reply_to_message_id=reply_to_message_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("send photo failed | chat=%s | err=%s", chat_id, exc)

    def build_public_preview_url(self, job_id: str, file_index: int) -> str | None:
        base_url = (self.settings.web_public_base_url or "").strip().rstrip("/")
        if not base_url:
            host = (self.settings.web_host or "").strip()
            if host and host not in {"0.0.0.0", "127.0.0.1", "localhost", "::", "::1"}:
                default_port = "" if self.settings.web_port in {80, 443} else f":{self.settings.web_port}"
                base_url = f"http://{host}{default_port}"
        if not base_url:
            return None
        expires = int(time.time()) + 24 * 3600
        sig = build_public_file_signature(job_id, file_index, expires, self.settings.web_secret_key)
        return f"{base_url}/preview/{quote(job_id)}/{file_index}?expires={expires}&sig={sig}"

    async def resolve_job_preview_cover(self, files: list[str], preview_index: int, job_dir: Path) -> Path | None:
        if preview_index < 0 or preview_index >= len(files):
            return None
        preview_path = Path(files[preview_index])
        preview_type = get_preview_type(preview_path)
        if preview_type == "image" and preview_path.exists():
            return preview_path
        if preview_type != "video" or not preview_path.exists():
            return None

        ffmpeg = shutil.which(self.settings.ffmpeg_bin or "ffmpeg")
        if not ffmpeg:
            candidate = Path(self.settings.ffmpeg_bin or "ffmpeg").expanduser()
            if candidate.exists():
                ffmpeg = str(candidate)
        if not ffmpeg:
            return None

        cover_path = job_dir / f".preview-{preview_index}.jpg"
        if cover_path.exists() and cover_path.stat().st_size > 0:
            return cover_path

        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-y",
            "-ss",
            "1",
            "-i",
            str(preview_path),
            "-vf",
            "thumbnail,scale=960:-1",
            "-frames:v",
            "1",
            str(cover_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode == 0 and cover_path.exists() and cover_path.stat().st_size > 0:
            return cover_path
        cover_path.unlink(missing_ok=True)
        return None

    async def send_job_success_reply(self, job: DownloadJob, files: list[str]) -> None:
        if job.chat_id <= 0:
            return
        preview_index = find_default_preview_index(files)
        if preview_index < 0:
            await self.safe_send_message(job.chat_id, "下载成功", reply_to_message_id=job.message_id)
            return

        preview_url = self.build_public_preview_url(job.job_id, preview_index)
        if not preview_url:
            await self.safe_send_message(job.chat_id, "下载成功", reply_to_message_id=job.message_id)
            return

        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("预览", url=preview_url)]])
        caption = ""
        cover_path = await self.resolve_job_preview_cover(files, preview_index, self.job_dir(job.job_id))
        if cover_path is not None:
            await self.safe_send_photo(
                job.chat_id,
                cover_path,
                caption,
                reply_markup=reply_markup,
                reply_to_message_id=job.message_id,
            )
            return
        await self.safe_send_message_with_markup(
            job.chat_id,
            "下载成功",
            reply_markup=reply_markup,
            reply_to_message_id=job.message_id,
        )

    def require_bot(self):
        if self.application is None:
            raise RuntimeError("bot application not initialized")
        return self.application.bot

    async def cancel_job(self, job_id: str) -> DownloadJob | None:
        before = await self.store.get_job(job_id)
        job = await self.store.cancel_job(job_id)
        if job is None:
            return None
        if before is not None and before.status in {"success", "failed", "cancelled"}:
            return job
        self.cancel_requested.add(job_id)
        proc = self.active_processes.get(job_id)
        if proc is not None and proc.returncode is None:
            proc.kill()
        await self.store.add_event("job.cancel.requested", {"job_id": job_id})
        updated = await self.store.get_job(job_id)
        if updated is not None and job_id not in self.active_job_tasks:
            await self.send_webhook(updated, "cancelled", self.job_dir(job_id), extra={"error": updated.error or "cancelled by admin"})
            self.cancel_requested.discard(job_id)
        return updated

    async def retry_job(self, job_id: str) -> DownloadJob | None:
        before = await self.store.get_job(job_id)
        job = await self.store.retry_job(job_id)
        if job is None:
            return None
        if before is not None and before.status not in {"queued", "downloading"} and job.status == "queued":
            await self.queue.put(job_id)
            await self.store.add_event("job.retry.requested", {"job_id": job_id})
        return await self.store.get_job(job_id)

    async def bulk_delete_jobs(self, job_ids: list[str]) -> dict[str, Any]:
        result = await self.store.delete_job_records(job_ids)
        for job_id in result["deleted"]:
            job_dir = self.job_dir(job_id)
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
            await self.store.add_event("job.delete.requested", {"job_id": job_id})
        return result

    async def resolve_job_file(self, job_id: str, file_index: int) -> Path:
        job = await self.store.get_job(job_id)
        if job is None:
            raise FileNotFoundError("job not found")
        files = job.files or []
        if file_index < 0 or file_index >= len(files):
            raise FileNotFoundError("file index out of range")
        target = Path(files[file_index]).resolve()
        root = self.settings.download_root.resolve()
        if root not in target.parents and target != root:
            raise FileNotFoundError("file out of root")
        if not target.exists() or not target.is_file():
            raise FileNotFoundError("file missing")
        return target

    async def resolve_job_log(self, job_id: str) -> str:
        job = await self.store.get_job(job_id)
        if job is None:
            raise FileNotFoundError("job not found")
        target = (self.job_dir(job_id) / "downloader.log").resolve()
        root = self.settings.download_root.resolve()
        if root not in target.parents and target != root:
            raise FileNotFoundError("log out of root")
        if not target.exists() or not target.is_file():
            return ""
        return target.read_text(encoding="utf-8", errors="ignore")

    def job_dir(self, job_id: str) -> Path:
        return self.settings.download_root / job_id

    @staticmethod
    def extract_webhook(text: str | None) -> str | None:
        if not text:
            return None
        match = HOOK_RE.search(text)
        return match.group(1) if match else None

    @staticmethod
    def extract_url(message: Message, skip_urls: set[str] | None = None) -> str | None:
        candidates: list[str] = []
        normalized_skip_urls = {item.strip() for item in (skip_urls or set()) if item}
        if message.entities and message.text:
            for entity in message.entities:
                if entity.type == "url":
                    candidates.append(message.text[entity.offset : entity.offset + entity.length])
                elif entity.type == "text_link" and entity.url:
                    candidates.append(entity.url)
        if message.caption_entities and message.caption:
            for entity in message.caption_entities:
                if entity.type == "url":
                    candidates.append(message.caption[entity.offset : entity.offset + entity.length])
                elif entity.type == "text_link" and entity.url:
                    candidates.append(entity.url)
        raw = message.text or message.caption or ""
        candidates.extend(URL_RE.findall(raw))
        for item in candidates:
            cleaned = item.strip()
            if cleaned in normalized_skip_urls:
                continue
            if cleaned.lower().startswith("http"):
                return cleaned
        return None

    @staticmethod
    def extract_forwarded_telegram_url(message: Message) -> str | None:
        forward_origin = getattr(message, "forward_origin", None)
        if forward_origin is not None:
            chat = getattr(forward_origin, "chat", None)
            username = getattr(chat, "username", None) if chat is not None else None
            message_id = getattr(forward_origin, "message_id", None)
            if username and message_id:
                return f"https://t.me/{username}/{message_id}"
        forward_from_chat = getattr(message, "forward_from_chat", None)
        forward_from_message_id = getattr(message, "forward_from_message_id", None)
        username = getattr(forward_from_chat, "username", None) if forward_from_chat is not None else None
        if username and forward_from_message_id:
            return f"https://t.me/{username}/{forward_from_message_id}"
        return None

    def detect_message_source(self, message: Message, custom_webhook: str | None = None) -> dict[str, Any] | None:
        skip_urls = {custom_webhook} if custom_webhook else None
        url = self.extract_url(message, skip_urls=skip_urls)
        forwarded_url = self.extract_forwarded_telegram_url(message)
        telegram_url = None
        for candidate in (url, forwarded_url):
            if candidate and is_telegram_message_url(candidate):
                telegram_url = candidate
                break
        if telegram_url:
            return {
                "source_type": "telegram_link",
                "source_value": telegram_url,
                "original_file_name": None,
            }
        if message.video:
            return {
                "source_type": "telegram_video",
                "source_value": message.video.file_id,
                "original_file_name": message.video.file_name,
            }
        if message.document and (message.document.mime_type or "").startswith("video/"):
            return {
                "source_type": "telegram_document",
                "source_value": message.document.file_id,
                "original_file_name": message.document.file_name,
            }
        if url:
            return {
                "source_type": "url",
                "source_value": url,
                "original_file_name": None,
            }
        return None

    @staticmethod
    def new_job_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]

    @staticmethod
    def user_event_payload(user: User | None, chat_id: int | None) -> dict[str, Any]:
        return {
            "chat_id": chat_id,
            "user_id": user.id if user else None,
            "username": clean_username(user.username) if user and user.username else None,
            "full_name": user.full_name if user else None,
        }


def create_web_app(service: DownloaderBot) -> FastAPI:
    app = FastAPI(title="tg-forward-downloader-bot", docs_url=None, redoc_url=None, openapi_url=None)
    tool_action_lock = asyncio.Lock()

    def is_authenticated(request: Request) -> bool:
        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        return verify_session_cookie(cookie, service.settings.web_secret_key)

    def require_api_login(request: Request) -> None:
        if not is_authenticated(request):
            raise HTTPException(status_code=401, detail="not logged in")

    async def safe_record_login_attempt(
        request: Request,
        *,
        username: str | None,
        success: bool,
        failure_reason: str | None = None,
    ) -> None:
        try:
            await service.store.add_login_log(
                username=username,
                success=success,
                ip=resolve_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                failure_reason=failure_reason,
            )
        except Exception:
            logger.exception("failed to persist login audit")

    def resolve_tdl_binary() -> str:
        candidate = (service.settings.tdl_bin or "tdl").strip() or "tdl"
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        candidate_path = Path(candidate).expanduser()
        if candidate_path.exists():
            return str(candidate_path)
        raise HTTPException(status_code=400, detail=f"tdl 未安装或不可执行：{candidate}")

    async def run_tdl_admin_command(args: list[str]) -> dict[str, Any]:
        binary = resolve_tdl_binary()
        proc = await asyncio.create_subprocess_exec(
            binary,
            *args,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="ignore")
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": output,
            "binary": binary,
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "db_path": str(service.settings.db_path),
            "queue_size": service.queue.qsize(),
            "workers": len(service.worker_tasks),
            "require_allowlist": service.settings.require_allowlist,
            "web_enabled": service.settings.web_enabled,
        }

    @app.get("/manifest.webmanifest")
    async def manifest() -> FastAPIResponse:
        payload = {
            "name": "TG Forward Downloader",
            "short_name": "TGDL",
            "display": "standalone",
            "start_url": "/",
            "background_color": "#0f172a",
            "theme_color": "#0f172a",
            "icons": [
                {
                    "src": "/favicon.svg",
                    "sizes": "any",
                    "type": "image/svg+xml",
                    "purpose": "any"
                }
            ],
        }
        return FastAPIResponse(content=json.dumps(payload, ensure_ascii=False), media_type="application/manifest+json")

    @app.get("/favicon.svg")
    async def favicon() -> FastAPIResponse:
        svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
<rect width="64" height="64" rx="16" fill="#0F172A"/>
<rect x="4" y="4" width="56" height="56" rx="12" fill="url(#bg)"/>
<path d="M49.8 18.7 42.6 47c-.5 2-1.9 2.5-3.6 1.6l-9.1-6.2-4.4 4.3c-.5.5-.9.9-1.9.9l.7-9.4 17.1-15.5c.7-.7-.2-1-1.1-.5L19.1 35.6l-9-2.8c-2-.6-2-2 .4-2.9l35-13.5c1.6-.6 3.1.4 2.3 3.3Z" fill="#60A5FA"/>
<path d="M32 29v14" stroke="#F8FAFC" stroke-width="4" stroke-linecap="round"/>
<path d="m26.5 37.5 5.5 5.5 5.5-5.5" stroke="#F8FAFC" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M22 50h20" stroke="#A78BFA" stroke-width="4" stroke-linecap="round"/>
<defs>
  <linearGradient id="bg" x1="8" y1="8" x2="56" y2="56" gradientUnits="userSpaceOnUse">
    <stop stop-color="#1E293B"/>
    <stop offset="1" stop-color="#111827"/>
  </linearGradient>
</defs>
</svg>"""
        return FastAPIResponse(content=svg, media_type="image/svg+xml")

    spa_dir = Path(__file__).resolve().parent / "static" / "frontend"
    spa_index = spa_dir / "index.html"

    @app.get("/")
    async def index() -> Response:
        if spa_index.exists():
            return FileResponse(str(spa_index), media_type="text/html")
        return RedirectResponse(url="/login", status_code=302)

    @app.get("/login")
    async def login_page(request: Request) -> Response:
        if is_authenticated(request):
            return RedirectResponse(url="/admin", status_code=302)
        if spa_index.exists():
            return FileResponse(str(spa_index), media_type="text/html")
        return HTMLResponse(render_login_page(""))

    @app.post("/login")
    async def login_submit(
        request: Request,
        response: Response,
        username: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        if username != service.settings.web_admin_username or password != service.settings.web_admin_password:
            await safe_record_login_attempt(
                request,
                username=username,
                success=False,
                failure_reason="invalid_credentials",
            )
            return HTMLResponse(render_login_page("用户名或密码错误"), status_code=401)
        await safe_record_login_attempt(
            request,
            username=username,
            success=True,
            failure_reason=None,
        )
        redirect = RedirectResponse(url="/admin", status_code=302)
        redirect.set_cookie(
            SESSION_COOKIE_NAME,
            build_session_cookie(username, service.settings.web_secret_key, service.settings.web_session_hours),
            httponly=True,
            samesite="lax",
            max_age=service.settings.web_session_hours * 3600,
        )
        return redirect

    @app.post("/logout")
    async def logout() -> Response:
        redirect = RedirectResponse(url="/login", status_code=302)
        redirect.delete_cookie(SESSION_COOKIE_NAME)
        return redirect

    @app.get("/api/state")
    async def api_state(request: Request) -> dict[str, Any]:
        require_api_login(request)
        snapshot = await service.store.snapshot(service.queue.qsize(), len(service.worker_tasks))
        snapshot["config"] = {
            "download_root": str(service.settings.download_root),
            "db_path": str(service.settings.db_path),
            "require_allowlist": service.settings.require_allowlist,
            "web_host": service.settings.web_host,
            "web_port": service.settings.web_port,
            "default_webhook_url": service.settings.default_webhook_url,
            "default_hook_script": service.settings.default_hook_script,
            "telegram_reply_on_finish": service.settings.telegram_reply_on_finish,
            "max_concurrent_jobs": service.settings.max_concurrent_jobs,
            "tdl_bin": service.settings.tdl_bin,
            "yt_dlp_bin": service.settings.yt_dlp_bin,
            "ffmpeg_bin": service.settings.ffmpeg_bin,
            "tdl_cmd": service.settings.tdl_cmd,
            "url_downloader_cmd": service.settings.url_downloader_cmd,
        }
        return snapshot

    @app.get("/api/jobs")
    async def api_jobs(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        q: str = Query(default=""),
        status: str = Query(default=""),
    ) -> dict[str, Any]:
        require_api_login(request)
        return await service.store.search_jobs(page=page, page_size=page_size, q=q, status=status)

    @app.post("/api/jobs/manual")
    async def api_manual_job(request: Request, payload: ManualJobPayload) -> JSONResponse:
        require_api_login(request)
        try:
            job = await service.enqueue_manual_job(payload.source_value, payload.webhook_url, from_user="web-admin")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"ok": True, "job": job_to_public_dict(job)})

    @app.get("/api/jobs/{job_id}")
    async def api_job_detail(request: Request, job_id: str) -> dict[str, Any]:
        require_api_login(request)
        job = await service.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {"job": job_to_public_dict(job)}

    @app.get("/api/jobs/{job_id}/files/{file_index}")
    async def api_job_file(request: Request, job_id: str, file_index: int) -> FileResponse:
        require_api_login(request)
        try:
            target = await service.resolve_job_file(job_id, file_index)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path=target, filename=target.name)

    @app.get("/preview/{job_id}/{file_index}")
    async def public_preview_file(job_id: str, file_index: int, expires: int = Query(...), sig: str = Query(...)) -> FileResponse:
        if not verify_public_file_signature(job_id, file_index, expires, sig, service.settings.web_secret_key):
            raise HTTPException(status_code=403, detail="invalid preview signature")
        try:
            target = await service.resolve_job_file(job_id, file_index)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        media_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        return FileResponse(
            path=target,
            media_type=media_type,
            headers={"Content-Disposition": f'inline; filename="{target.name}"'},
        )

    @app.get("/api/jobs/{job_id}/log")
    async def api_job_log(request: Request, job_id: str) -> PlainTextResponse:
        require_api_login(request)
        try:
            text = await service.resolve_job_log(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return PlainTextResponse(text)

    @app.post("/api/jobs/{job_id}/cancel")
    async def api_cancel_job(request: Request, job_id: str) -> JSONResponse:
        require_api_login(request)
        job = await service.cancel_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return JSONResponse({"ok": True, "job": job_to_public_dict(job)})

    @app.post("/api/jobs/{job_id}/retry")
    async def api_retry_job(request: Request, job_id: str) -> JSONResponse:
        require_api_login(request)
        job = await service.retry_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return JSONResponse({"ok": True, "job": job_to_public_dict(job)})

    @app.post("/api/jobs/bulk-cancel")
    async def api_bulk_cancel_jobs(request: Request, payload: JobIdsPayload) -> JSONResponse:
        require_api_login(request)
        items = []
        for job_id in payload.job_ids:
            job = await service.cancel_job(job_id)
            if job is not None:
                items.append(job_to_public_dict(job))
        return JSONResponse({"ok": True, "items": items, "count": len(items)})

    @app.post("/api/jobs/bulk-retry")
    async def api_bulk_retry_jobs(request: Request, payload: JobIdsPayload) -> JSONResponse:
        require_api_login(request)
        items = []
        for job_id in payload.job_ids:
            job = await service.retry_job(job_id)
            if job is not None:
                items.append(job_to_public_dict(job))
        return JSONResponse({"ok": True, "items": items, "count": len(items)})

    @app.post("/api/jobs/bulk-delete")
    async def api_bulk_delete_jobs(request: Request, payload: JobIdsPayload) -> JSONResponse:
        require_api_login(request)
        result = await service.bulk_delete_jobs(payload.job_ids)
        return JSONResponse({"ok": True, **result, "count": len(result["deleted"])})

    @app.get("/api/events")
    async def api_events(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        q: str = Query(default=""),
        event_type: str = Query(default=""),
    ) -> dict[str, Any]:
        require_api_login(request)
        return await service.store.search_events(page=page, page_size=page_size, q=q, event_type=event_type)

    @app.get("/api/login-logs")
    async def api_login_logs(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        q: str = Query(default=""),
        success: str = Query(default=""),
    ) -> dict[str, Any]:
        require_api_login(request)
        if success not in {"", "true", "false"}:
            raise HTTPException(status_code=400, detail="success must be '', 'true', or 'false'")
        return await service.store.search_login_logs(page=page, page_size=page_size, q=q, success=success)

    @app.get("/api/stats/summary")
    async def api_stats_summary(request: Request) -> dict[str, Any]:
        require_api_login(request)
        return await service.store.job_stats()

    @app.get("/api/tools/status")
    async def api_tools_status(request: Request) -> dict[str, Any]:
        require_api_login(request)
        return get_tools_status(service.settings)

    @app.get("/api/hook/config")
    async def api_hook_config(request: Request) -> dict[str, Any]:
        require_api_login(request)
        return {
            "default_webhook_url": service.settings.default_webhook_url,
            "default_hook_script": service.settings.default_hook_script,
            "telegram_reply_on_finish": service.settings.telegram_reply_on_finish,
            "require_allowlist": service.settings.require_allowlist,
            "max_concurrent_jobs": service.settings.max_concurrent_jobs,
        }

    @app.post("/api/hook/config")
    async def api_save_hook_config(request: Request, payload: HookConfigPayload) -> JSONResponse:
        require_api_login(request)
        webhook_url = (payload.webhook_url or "").strip() or None
        hook_script = (payload.hook_script or "").strip() or None
        reply_on_finish = bool(payload.telegram_reply_on_finish)
        require_allowlist = bool(payload.require_allowlist)
        max_concurrent_jobs = max(1, int(payload.max_concurrent_jobs or 1))
        saved = await service.store.set_app_settings(
            {
                "default_webhook_url": webhook_url or "",
                "default_hook_script": hook_script or "",
                "telegram_reply_on_finish": "true" if reply_on_finish else "false",
                "require_allowlist": "true" if require_allowlist else "false",
                "max_concurrent_jobs": str(max_concurrent_jobs),
            }
        )
        apply_runtime_app_settings(service.settings, saved)
        await service.refresh_workers()
        return JSONResponse(
            {
                "ok": True,
                "default_webhook_url": service.settings.default_webhook_url,
                "default_hook_script": service.settings.default_hook_script,
                "telegram_reply_on_finish": service.settings.telegram_reply_on_finish,
                "require_allowlist": service.settings.require_allowlist,
                "max_concurrent_jobs": service.settings.max_concurrent_jobs,
            }
        )

    @app.post("/api/hook/test")
    async def api_test_hook(request: Request) -> JSONResponse:
        require_api_login(request)
        if not service.settings.default_webhook_url and not service.settings.default_hook_script:
            raise HTTPException(status_code=400, detail="hook not configured")
        job_dir = service.settings.download_root / "_hook_test"
        job_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "event": "download.success",
            "status": "success",
            "job_id": "hook-test",
            "chat_id": 0,
            "message_id": 0,
            "source_type": "test",
            "source_value": "test://hook",
            "submitted_at": now_utc(),
            "from_user": "admin",
            "from_user_id": 0,
            "timestamp": now_utc(),
            "files": [],
            "file_count": 0,
            "test": True,
        }
        await service.dispatch_hook_targets(payload, job_dir, service.settings.default_webhook_url)
        return JSONResponse({"ok": True})

    @app.post("/api/tools/{tool_name}/{action}")
    async def api_tools_action(request: Request, tool_name: str, action: str) -> JSONResponse:
        require_api_login(request)
        tool_name = normalize_tool_name(tool_name)
        action = (action or "").strip().lower()
        if tool_name not in {"tdl", "yt-dlp", "ffmpeg"}:
            raise HTTPException(status_code=400, detail="unsupported tool")
        if action not in {"install", "update"}:
            raise HTTPException(status_code=400, detail="unsupported action")
        if tool_action_lock.locked():
            raise HTTPException(status_code=409, detail="another tool action is running")
        async with tool_action_lock:
            result = await run_tool_action_script(service.settings, tool_name, action)
            load_env_files(override=True)
            refreshed = Settings.load()
            apply_runtime_app_settings(refreshed, await service.store.get_app_settings())
            service.settings = refreshed
            service.store.settings = refreshed
            status = get_tools_status(service.settings)
            code = 200 if result["ok"] else 500
            return JSONResponse(
                {
                    "ok": result["ok"],
                    "tool": tool_name,
                    "action": action,
                    "returncode": result["returncode"],
                    "output": result["output"],
                    "status": status,
                },
                status_code=code,
            )

    @app.get("/api/tools/tdl/session/export")
    async def api_export_tdl_session(request: Request) -> FileResponse:
        require_api_login(request)
        if tool_action_lock.locked():
            raise HTTPException(status_code=409, detail="当前有工具任务正在执行，请稍后再试")
        async with tool_action_lock:
            fd, tmp_path = tempfile.mkstemp(prefix="tdl-session-", suffix=".tdl")
            os.close(fd)
            backup_path = Path(tmp_path)
            try:
                result = await run_tdl_admin_command(["backup", "-d", str(backup_path)])
                if not result["ok"] or not backup_path.exists() or backup_path.stat().st_size <= 0:
                    raise HTTPException(
                        status_code=500,
                        detail=(result["output"] or f"tdl backup 失败，退出码 {result['returncode']}")[-2000:],
                    )
                filename = f"tdl-session-{now_utc().replace(':', '').replace('+', '_')}.tdl"
                return FileResponse(
                    path=str(backup_path),
                    filename=filename,
                    media_type="application/octet-stream",
                    background=BackgroundTask(lambda p=backup_path: p.unlink(missing_ok=True)),
                )
            except Exception:
                backup_path.unlink(missing_ok=True)
                raise

    @app.post("/api/tools/tdl/session/import")
    async def api_import_tdl_session(request: Request, file: UploadFile = File(...)) -> JSONResponse:
        require_api_login(request)
        if service.active_processes:
            raise HTTPException(status_code=409, detail="当前有下载任务正在执行，不能覆盖 tdl 登录状态")
        if tool_action_lock.locked():
            raise HTTPException(status_code=409, detail="当前有工具任务正在执行，请稍后再试")
        async with tool_action_lock:
            suffix = Path(file.filename or "session.tdl").suffix or ".tdl"
            fd, tmp_path = tempfile.mkstemp(prefix="tdl-import-", suffix=suffix)
            os.close(fd)
            import_path = Path(tmp_path)
            try:
                content = await file.read()
                if not content:
                    raise HTTPException(status_code=400, detail="上传文件为空")
                import_path.write_bytes(content)
                result = await run_tdl_admin_command(["recover", "-f", str(import_path)])
                if not result["ok"]:
                    raise HTTPException(
                        status_code=500,
                        detail=(result["output"] or f"tdl recover 失败，退出码 {result['returncode']}")[-2000:],
                    )
                return JSONResponse({"ok": True, "message": "tdl 登录状态已导入"})
            finally:
                import_path.unlink(missing_ok=True)
                await file.close()

    @app.get("/api/allowlist")
    async def api_allowlist(request: Request) -> dict[str, Any]:
        require_api_login(request)
        return await service.store.allowlist_summary()

    @app.post("/api/allowlist/allow")
    async def api_allow(request: Request, payload: AllowlistPayload) -> JSONResponse:
        require_api_login(request)
        try:
            snapshot = await service.store.allow_user(payload.user_id, payload.username)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"ok": True, **snapshot})

    @app.post("/api/allowlist/deny")
    async def api_deny(request: Request, payload: AllowlistPayload) -> JSONResponse:
        require_api_login(request)
        try:
            snapshot = await service.store.deny_user(payload.user_id, payload.username)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"ok": True, **snapshot})

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request) -> Response:
        if not is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        if spa_index.exists():
            return FileResponse(str(spa_index), media_type="text/html")
        snapshot = await service.store.snapshot(service.queue.qsize(), len(service.worker_tasks))
        return HTMLResponse(render_admin_page(snapshot))

    if spa_dir.exists():
        from fastapi.staticfiles import StaticFiles
        assets_dir = spa_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="spa-assets")

        @app.get("/admin/{path:path}")
        async def spa_fallback(request: Request, path: str) -> Response:
            if not is_authenticated(request):
                return RedirectResponse(url="/login", status_code=302)
            return FileResponse(str(spa_index), media_type="text/html")

    return app


async def run_web(service: DownloaderBot) -> None:
    app = create_web_app(service)
    config = uvicorn.Config(app, host=service.settings.web_host, port=service.settings.web_port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    settings = Settings.load()
    store = SQLiteStore(settings.db_path, settings)
    await store.init()
    apply_runtime_app_settings(settings, await store.get_app_settings())
    service = DownloaderBot(settings, store)

    tasks = [asyncio.create_task(service.start_bot(), name="telegram-bot")]
    if settings.web_enabled:
        tasks.append(asyncio.create_task(run_web(service), name="web-admin"))

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception()
            if exc:
                raise exc
    finally:
        await service.stop_workers()
        await store.close()



def render_login_page(error: str) -> str:
    error_html = f'<div class="error">{error}</div>' if error else ""
    template = (Path(__file__).resolve().parent / "templates" / "login.html").read_text(encoding="utf-8")
    return template.replace("__ERROR_HTML__", error_html)


def render_admin_page(snapshot: dict[str, Any]) -> str:
    data_js = json.dumps(snapshot, ensure_ascii=False)
    template = (Path(__file__).resolve().parent / "templates" / "index.html").read_text(encoding="utf-8")
    return template.replace("__BOOT_JSON__", data_js)


def resolve_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        for item in forwarded_for.split(","):
            candidate = item.strip()
            if candidate:
                return candidate
    if request.client:
        return request.client.host
    return None


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("shutdown by keyboard interrupt")
