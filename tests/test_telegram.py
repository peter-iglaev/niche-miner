"""Offline checks for Telegram delivery. No network calls are made and no token is written out."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scripts import telegram

TOKEN = "123456789:AAtest-token-value-not-real"
CHAT = "100200300"


class TelegramTests(unittest.TestCase):
    def setUp(self):
        # The process environment wins over .env.local; a session's real bot must not leak in.
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".env.local").write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHAT_ID={CHAT}\n",
                                              encoding="utf-8")
        (self.root / "report.md").write_text("# Отчёт\n", encoding="utf-8")
        self._post = telegram._post

    def tearDown(self):
        telegram._post = self._post
        self._tmp.cleanup()

    def payload(self, body):
        return json.loads(body.decode("utf-8", errors="replace"))

    def test_missing_credentials_block_the_send_without_network(self):
        (self.root / ".env.local").write_text("", encoding="utf-8")
        check = telegram.credentials(root=self.root)
        self.assertFalse(check["available"])
        self.assertIn("TELEGRAM_BOT_TOKEN", check["reason"])
        self.assertFalse(check["network_used"])
        calls = []
        telegram._post = lambda *a, **k: calls.append(a) or (200, "application/json", b'{"ok": true}')
        with self.assertRaises(ValueError) as caught:
            telegram.send_document("tg-run", "report.md", root=self.root)
        self.assertIn("capability unavailable", str(caught.exception))
        self.assertEqual(calls, [])

    def test_credentials_check_never_returns_secret_values(self):
        check = telegram.credentials(root=self.root)
        rendered = json.dumps(check)
        self.assertTrue(check["available"])
        self.assertNotIn(TOKEN, rendered)
        self.assertNotIn(CHAT, rendered)

    def test_document_is_sent_and_archived_without_the_token(self):
        def fake_post(url, body, content_type):
            self.assertIn(TOKEN, url)
            self.assertIn("multipart/form-data", content_type)
            self.assertIn(b'name="chat_id"', body)
            self.assertIn(CHAT.encode(), body)
            self.assertIn(b'filename="report.md"', body)
            self.assertIn(b"# ", body)
            return 200, "application/json", json.dumps(
                {"ok": True, "result": {"message_id": 42, "date": 1,
                                        "chat": {"id": int(CHAT), "first_name": "Owner", "username": "owner"},
                                        "from": {"id": 7, "is_bot": True, "username": "the_bot"},
                                        "document": {"file_name": "report.md"}}}).encode()

        telegram._post = fake_post
        result = telegram.send_document("tg-run", "report.md", caption="Отчёт", root=self.root)
        self.assertEqual(result["telegram_status"], "sent")
        self.assertEqual(result["message_id"], 42)
        self.assertEqual(result["file"], "report.md")
        folder = self.root / "runs" / "tg-run" / "sources"
        sidecar = json.loads((folder / f"{result['source_id']}.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["request_url"], "https://api.telegram.org/bot<redacted>/sendDocument")
        self.assertNotIn(TOKEN, json.dumps(sidecar))
        self.assertEqual(sidecar["provider"], "telegram")
        self.assertNotIn("chat_id", result)

    def test_archived_response_drops_the_recipient_identity(self):
        telegram._post = lambda url, body, content_type: (200, "application/json", json.dumps(
            {"ok": True, "result": {"message_id": 42, "chat": {"id": int(CHAT), "username": "owner"},
                                    "from": {"id": 7, "username": "the_bot"}, "text": "hi"}}).encode())
        result = telegram.send_message("tg-run", "hi", root=self.root)
        folder = self.root / "runs" / "tg-run" / "sources"
        sidecar = json.loads((folder / f"{result['source_id']}.meta.json").read_text(encoding="utf-8"))
        archived = (self.root / "runs" / "tg-run" / sidecar["archive_path"]).read_bytes()
        self.assertEqual(json.loads(archived), {"ok": True, "result": {"message_id": 42, "text": "hi"}})
        self.assertNotIn(CHAT.encode(), archived)
        self.assertNotIn(b"owner", archived)
        self.assertIn("identity fields removed", sidecar["limitations"])

    def test_a_symlinked_run_folder_is_still_inside_the_project(self):
        with tempfile.TemporaryDirectory() as data:
            (Path(data) / "runs" / "tg-run").mkdir(parents=True)
            (Path(data) / "runs" / "tg-run" / "report.pdf").write_bytes(b"%PDF-1.4")
            (self.root / "runs").symlink_to(Path(data) / "runs")
            telegram._post = lambda url, body, content_type: (
                200, "application/json", b'{"ok": true, "result": {"message_id": 1}}')
            result = telegram.send_document("tg-run", "runs/tg-run/report.pdf", root=self.root)
            self.assertEqual(result["telegram_status"], "sent")
            self.assertEqual(result["file"], "runs/tg-run/report.pdf")
            with self.assertRaises(ValueError):
                telegram.send_document("tg-run", "runs/../../outside.pdf", root=self.root)

    def test_refusal_and_transport_failure_are_archived_as_failures(self):
        telegram._post = lambda url, body, content_type: (
            200, "application/json", b'{"ok": false, "description": "chat not found"}')
        refused = telegram.send_document("tg-run", "report.md", root=self.root)
        self.assertEqual(refused["telegram_status"], "error")
        self.assertEqual(refused["error"], "chat not found")

        def forbidden(url, body, content_type):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b'{"ok": false}'))

        telegram._post = forbidden
        blocked = telegram.send_document("tg-run", "report.md", root=self.root)
        self.assertEqual(blocked["telegram_status"], "error")
        self.assertEqual(blocked["http_status"], 403)
        self.assertTrue((self.root / "runs" / "tg-run" / "sources").is_dir())

    def test_sending_outside_the_project_is_refused(self):
        with self.assertRaises(ValueError):
            telegram.send_document("tg-run", "/etc/hosts", root=self.root)
        with self.assertRaises(ValueError):
            telegram.send_document("tg-run", "missing.md", root=self.root)


if __name__ == "__main__":
    unittest.main()
