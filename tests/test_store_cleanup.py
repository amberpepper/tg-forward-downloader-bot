import asyncio
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

telegram_module = types.ModuleType("telegram")
telegram_module.User = type("User", (), {})
sys.modules.setdefault("telegram", telegram_module)

from app.config import Settings  # noqa: E402
from app.store import SQLiteStore  # noqa: E402


class StoreCleanupTests(unittest.TestCase):
    def test_init_removes_deprecated_upload_back_columns_and_settings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "app.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT NOT NULL
                );

                INSERT INTO app_settings(key, value, updated_at) VALUES
                    ('telegram_upload_back_on_finish', 'true', '2026-05-06T00:00:00+00:00'),
                    ('telegram_upload_back_max_mb', '0', '2026-05-06T00:00:00+00:00');

                CREATE TABLE jobs (
                    job_id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    source_type TEXT NOT NULL,
                    source_value TEXT NOT NULL,
                    webhook_url TEXT,
                    submitted_at TEXT NOT NULL,
                    from_user TEXT,
                    from_user_id INTEGER,
                    caption_or_text TEXT,
                    original_file_name TEXT,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    files_json TEXT,
                    error TEXT,
                    progress_percent REAL,
                    progress_text TEXT,
                    upload_back_status TEXT,
                    upload_back_sent INTEGER NOT NULL DEFAULT 0,
                    upload_back_failed INTEGER NOT NULL DEFAULT 0,
                    upload_back_skipped INTEGER NOT NULL DEFAULT 0,
                    upload_back_detail TEXT
                );
                """
            )
            conn.commit()
            conn.close()

            settings = Settings(
                bot_token="token",
                download_root=Path(tmpdir) / "downloads",
                db_path=db_path,
                default_webhook_url=None,
                default_hook_script=None,
                tdl_bin="tdl",
                yt_dlp_bin="yt-dlp",
                ffmpeg_bin="ffmpeg",
                tdl_cmd="tdl dl -u {url} -d {output_dir}",
                url_downloader_cmd="yt-dlp -o {output_template} {url}",
                telegram_reply_on_finish=True,
                max_concurrent_jobs=1,
                require_allowlist=True,
                admin_user_ids=set(),
                admin_usernames=set(),
                initial_allowed_user_ids=set(),
                initial_allowed_usernames=set(),
                web_enabled=True,
                web_host="127.0.0.1",
                web_port=8090,
                web_admin_username="admin",
                web_admin_password="admin",
                web_secret_key="secret",
                web_session_hours=24,
            )

            store = SQLiteStore(db_path, settings)
            try:
                asyncio.run(store.init())
            finally:
                asyncio.run(store.close())

            conn = sqlite3.connect(db_path)
            try:
                job_columns = [row[1] for row in conn.execute("PRAGMA table_info(jobs)")]
                app_setting_keys = [row[0] for row in conn.execute("SELECT key FROM app_settings ORDER BY key")]
            finally:
                conn.close()

            self.assertNotIn("upload_back_status", job_columns)
            self.assertNotIn("upload_back_sent", job_columns)
            self.assertNotIn("upload_back_failed", job_columns)
            self.assertNotIn("upload_back_skipped", job_columns)
            self.assertNotIn("upload_back_detail", job_columns)
            self.assertNotIn("telegram_upload_back_on_finish", app_setting_keys)
            self.assertNotIn("telegram_upload_back_max_mb", app_setting_keys)


if __name__ == "__main__":
    unittest.main()
