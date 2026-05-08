import importlib.util
import sys
import unittest
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODELS_PATH = ROOT / "app" / "models.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class RemoveUploadBackTests(unittest.TestCase):
    def test_hook_config_and_job_payload_no_longer_expose_upload_back_fields(self):
        models = load_module("models_module", MODELS_PATH)

        self.assertNotIn("telegram_upload_back_on_finish", models.HookConfigPayload.model_fields)
        self.assertNotIn("telegram_upload_back_max_mb", models.HookConfigPayload.model_fields)

        job = models.DownloadJob(
            job_id="job-1",
            chat_id=1,
            message_id=2,
            source_type="url",
            source_value="https://example.com/video.mp4",
            webhook_url=None,
            submitted_at="2026-05-06T00:00:00+00:00",
            from_user=None,
            from_user_id=None,
            caption_or_text=None,
        )
        payload = asdict(job)

        self.assertNotIn("upload_back_status", payload)
        self.assertNotIn("upload_back_sent", payload)
        self.assertNotIn("upload_back_failed", payload)
        self.assertNotIn("upload_back_skipped", payload)
        self.assertNotIn("upload_back_detail", payload)


if __name__ == "__main__":
    unittest.main()
