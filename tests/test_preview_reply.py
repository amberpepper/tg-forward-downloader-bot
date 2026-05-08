import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

telegram_module = sys.modules.get("telegram")
if telegram_module is None:
    telegram_module = types.ModuleType("telegram")
    sys.modules["telegram"] = telegram_module
telegram_module.Message = getattr(telegram_module, "Message", type("Message", (), {}))
telegram_module.Update = getattr(telegram_module, "Update", type("Update", (), {}))
telegram_module.User = getattr(telegram_module, "User", type("User", (), {}))


class InlineKeyboardButton:
    def __init__(self, text: str, url: str | None = None):
        self.text = text
        self.url = url


class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


telegram_module.InlineKeyboardButton = InlineKeyboardButton
telegram_module.InlineKeyboardMarkup = InlineKeyboardMarkup

ext_module = sys.modules.get("telegram.ext")
if ext_module is None:
    ext_module = types.ModuleType("telegram.ext")
    sys.modules["telegram.ext"] = ext_module


class DummyApplicationBuilder:
    def token(self, _value: str):
        return self

    def build(self):
        return types.SimpleNamespace(bot=None)


class DummyApplication:
    @classmethod
    def builder(cls):
        return DummyApplicationBuilder()


class DummyContextTypes:
    DEFAULT_TYPE = object


ext_module.Application = DummyApplication
ext_module.CommandHandler = lambda *args, **kwargs: None
ext_module.ContextTypes = DummyContextTypes
ext_module.MessageHandler = lambda *args, **kwargs: None
ext_module.filters = types.SimpleNamespace(COMMAND=object())

from app.config import Settings  # noqa: E402
from app.main import DownloaderBot, create_web_app  # noqa: E402
from app.models import DownloadJob  # noqa: E402
from app.store import SQLiteStore  # noqa: E402
from app.utils import now_utc  # noqa: E402


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []
        self.sent_photos: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent_messages.append(kwargs)

    async def send_photo(self, **kwargs):
        self.sent_photos.append(kwargs)


class PreviewReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = Settings(
            bot_token="token",
            download_root=self.root / "downloads",
            db_path=self.root / "app.db",
            default_webhook_url=None,
            default_hook_script=None,
            tdl_bin="tdl",
            yt_dlp_bin="yt-dlp",
            ffmpeg_bin="ffmpeg-missing",
            tdl_cmd="tdl dl -u {url} -d {output_dir}",
            url_downloader_cmd="yt-dlp -o {output_template} {url}",
            telegram_reply_on_finish=True,
            max_concurrent_jobs=1,
            require_allowlist=False,
            admin_user_ids=set(),
            admin_usernames=set(),
            initial_allowed_user_ids=set(),
            initial_allowed_usernames=set(),
            web_enabled=True,
            web_host="127.0.0.1",
            web_port=8090,
            web_admin_username="admin",
            web_admin_password="secret",
            web_secret_key="secret-key",
            web_session_hours=24,
            web_public_base_url="https://preview.example.com",
        )
        self.store = SQLiteStore(self.settings.db_path, self.settings)
        asyncio.run(self.store.init())
        self.service = DownloaderBot(self.settings, self.store)
        self.fake_bot = FakeBot()
        self.service.application = types.SimpleNamespace(bot=self.fake_bot)

    def tearDown(self) -> None:
        asyncio.run(self.store.close())
        self.temp_dir.cleanup()

    def make_job(self) -> DownloadJob:
        return DownloadJob(
            job_id="job-1",
            chat_id=123,
            message_id=456,
            source_type="url",
            source_value="https://example.com/file.mp4",
            webhook_url=None,
            submitted_at=now_utc(),
            from_user="tester",
            from_user_id=1,
            caption_or_text=None,
            original_file_name="file.mp4",
        )

    def test_success_reply_sends_photo_when_preview_file_is_image(self) -> None:
        job = self.make_job()
        job_dir = self.service.job_dir(job.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        image_path = job_dir / "cover.jpg"
        image_path.write_bytes(b"fake-image")

        asyncio.run(self.service.send_job_success_reply(job, [str(image_path)]))

        self.assertEqual(len(self.fake_bot.sent_photos), 1)
        payload = self.fake_bot.sent_photos[0]
        self.assertEqual(payload["chat_id"], 123)
        self.assertIn("下载完成", payload["caption"])
        self.assertEqual(payload["reply_markup"].inline_keyboard[0][0].text, "预览")
        self.assertIn("/preview/job-1/0?", payload["reply_markup"].inline_keyboard[0][0].url)
        self.assertEqual(len(self.fake_bot.sent_messages), 0)

    def test_success_reply_falls_back_to_text_when_no_cover_exists(self) -> None:
        job = self.make_job()
        job_dir = self.service.job_dir(job.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = job_dir / "result.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        asyncio.run(self.service.send_job_success_reply(job, [str(pdf_path)]))

        self.assertEqual(len(self.fake_bot.sent_photos), 0)
        self.assertEqual(len(self.fake_bot.sent_messages), 1)
        payload = self.fake_bot.sent_messages[0]
        self.assertIn("下载完成", payload["text"])
        self.assertEqual(payload["reply_markup"].inline_keyboard[0][0].text, "预览")

    def test_public_preview_route_serves_signed_file_without_login(self) -> None:
        job = self.make_job()
        file_path = self.service.job_dir(job.job_id) / "cover.jpg"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"fake-image")
        asyncio.run(self.store.create_job(job))
        asyncio.run(self.store.complete_job(job.job_id, "success", files=[str(file_path)]))

        app = create_web_app(self.service)
        client = TestClient(app)
        try:
            url = self.service.build_public_preview_url(job.job_id, 0)
            self.assertIsNotNone(url)
            path = str(url).replace("https://preview.example.com", "")
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"fake-image")
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
