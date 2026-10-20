"""Offline checks for the run-scoped source archive. No network calls are made."""
import json
import hashlib
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scripts import sources


FAILURE = lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError("network unreachable"))


class SourceArchiveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._http_get = sources.http_get
        self._http_post = sources.http_post_json

    def tearDown(self):
        sources.http_get = self._http_get
        sources.http_post_json = self._http_post
        self._tmp.cleanup()

    def test_archive_stores_raw_body_and_locator_sidecar(self):
        body = b'{"hits": [{"title": "synthetic"}]}'
        record = sources.archive("archive-run", "hn_algolia", "https://example.com/api", query="synthetic",
                                 status=200, content_type="application/json", body=body, root=self.root)
        folder = self.root / "runs" / "archive-run" / "sources"
        self.assertEqual(record["retrieval_status"], "collected")
        self.assertEqual(record["byte_length"], len(body))
        self.assertEqual(record["archive_path"], f"sources/{record['source_id']}.json")
        self.assertEqual((folder / f"{record['source_id']}.json").read_bytes(), body)
        sidecar = json.loads((folder / f"{record['source_id']}.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar, record)
        self.assertEqual(sidecar["query"], "synthetic")

    def test_first_fetcher_is_stamped_and_later_users_are_appended(self):
        body = b'{"hits": []}'
        sources.http_get = lambda url, headers=None: (200, "application/json", body)
        first = sources.hn_search("usage-run", "security questionnaire", assignment_id="cand-001-discovery",
                                  role="discovery", worker_id="/root/scout_a", root=self.root)
        self.assertEqual(first["first_assignment_id"], "cand-001-discovery")
        self.assertEqual(first["first_role"], "discovery")
        again = sources.hn_search("usage-run", "security questionnaire", assignment_id="cand-002-skeptic",
                                  role="skeptic", worker_id="/root/skeptic_c", root=self.root)
        self.assertEqual(again["source_id"], first["source_id"])
        self.assertEqual(again["first_assignment_id"], "cand-001-discovery")
        usage = [json.loads(line) for line in
                 (self.root / "runs" / "usage-run" / "source_usage.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual([row["assignment_id"] for row in usage], ["cand-001-discovery", "cand-002-skeptic"])
        self.assertEqual({row["tool"] for row in usage}, {"hn_algolia"})
        sources.record_usage("usage-run", first["source_id"], tool="hn_algolia",
                             assignment_id="cand-002-skeptic", role="skeptic", worker_id="/root/skeptic_c", root=self.root)
        usage = [json.loads(line) for line in
                 (self.root / "runs" / "usage-run" / "source_usage.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual(len(usage), 2)

    def test_unattributed_capture_keeps_working_and_records_nulls(self):
        sources.http_get = lambda url, headers=None: (200, "application/json", b'{"hits": []}')
        record = sources.hn_search("anonymous-run", "workflow", root=self.root)
        self.assertIsNone(record["first_assignment_id"])
        usage = [json.loads(line) for line in
                 (self.root / "runs" / "anonymous-run" / "source_usage.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual(usage[0]["assignment_id"], None)

    def test_pdf_and_binary_are_saved_byte_for_byte_with_original_sha(self):
        examples = [(b"%PDF-1.7\n%\xff\x80\x00\xfe\n", "application/pdf", ".pdf"),
                    (bytes(range(256)), "application/octet-stream", ".bin"),
                    (b"caf\xe9", "text/plain; charset=iso-8859-1", ".txt")]
        for body, mime, suffix in examples:
            with self.subTest(mime=mime):
                record = sources.archive("archive-run", "direct_fetch", "https://example.com/file",
                                         status=200, content_type=mime, body=body, root=self.root)
                path = self.root / "runs" / "archive-run" / record["archive_path"]
                self.assertEqual(path.suffix, suffix)
                self.assertEqual(path.read_bytes(), body)
                self.assertEqual(record["sha256"], hashlib.sha256(body).hexdigest())
                self.assertFalse(Path(record["archive_path"]).is_absolute())
                self.assertTrue(record["archive_path"].startswith("sources/"))

    def test_pdf_magic_corrects_generic_content_type(self):
        record = sources.archive("archive-run", "direct_fetch", "https://example.com/file",
                                 content_type="application/octet-stream", body=b"%PDF-1.7\xff", root=self.root)
        self.assertEqual(record["content_type"], "application/pdf")
        self.assertTrue(record["archive_path"].endswith(".pdf"))

    def test_repeated_source_is_stable_and_different_origins_do_not_collide(self):
        arguments = dict(run_id="archive-run", provider="direct_fetch", body=b"same", root=self.root)
        first = sources.archive(url="https://one.example", **arguments)
        meta = self.root / "runs/archive-run/sources" / (first["source_id"] + ".meta.json")
        original = meta.read_bytes()
        self.assertEqual(first, sources.archive(url="https://one.example", **arguments))
        self.assertEqual(meta.read_bytes(), original)
        other = sources.archive(url="https://two.example", **arguments)
        self.assertNotEqual(first["source_id"], other["source_id"])
        self.assertEqual(first["sha256"], other["sha256"])

    def test_decoded_text_is_rejected_as_raw_source(self):
        with self.assertRaisesRegex(TypeError, "must be bytes"):
            sources.archive("archive-run", "direct_fetch", "https://example.com", body="text", root=self.root)

    def test_http_failure_keeps_original_body_without_claiming_success(self):
        body = b"%PDF-1.7\xff\x00"
        def fail(*args, **kwargs):
            raise urllib.error.HTTPError("https://example.com", 403, "error",
                                         {"Content-Type": "application/pdf"}, io.BytesIO(body))
        sources.http_get = fail
        record = sources.wayback_snapshot("archive-run", "example.com", root=self.root)
        self.assertEqual(record["retrieval_status"], "error")
        self.assertEqual((self.root / "runs/archive-run" / record["archive_path"]).read_bytes(), body)
        self.assertEqual(record["sha256"], hashlib.sha256(body).hexdigest())

    def test_access_failure_is_archived_as_failure_not_as_data(self):
        sources.http_get = FAILURE
        record = sources.wayback_snapshot("archive-run", "example.com/synthetic", root=self.root)
        self.assertEqual(record["retrieval_status"], "error")
        self.assertIsNone(record["http_status"])
        self.assertIn("URLError", record["error"])
        self.assertIsNone(record["archive_path"])
        folder = self.root / "runs" / "archive-run" / "sources"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [f"{record['source_id']}.meta.json"])

    def test_ted_search_sends_query_and_archives_response(self):
        seen = {}

        def fake_post(url, payload, headers=None):
            seen.update({"url": url, "payload": payload})
            return 200, "application/json", b'{"notices": []}'

        sources.http_post_json = fake_post
        record = sources.ted_search("archive-run", 'FT="bookkeeping"', limit=3, fields=["notice-title"], root=self.root)
        self.assertEqual(seen["payload"]["query"], 'FT="bookkeeping"')
        self.assertEqual(seen["payload"]["limit"], 3)
        self.assertEqual(seen["payload"]["fields"], ["notice-title"])
        self.assertEqual(record["retrieval_status"], "collected")
        self.assertIsNone(record["error"])

    def test_default_field_list_is_sent_because_the_api_rejects_an_empty_one(self):
        seen = {}

        def fake_post(url, payload, headers=None):
            seen.update(payload)
            return 200, "application/json", b'{"notices": []}'

        sources.http_post_json = fake_post
        sources.ted_search("archive-run", 'FT="bookkeeping"', root=self.root)
        self.assertTrue(seen["fields"])
        self.assertIn("publication-number", seen["fields"])

    def test_run_id_must_stay_inside_runs(self):
        for bad in ("../escape", "Upper", ""):
            with self.assertRaises(ValueError):
                sources.sources_dir(bad, root=self.root)

    def test_list_reports_collected_and_failed_counts(self):
        sources.archive("archive-run", "hn_algolia", "https://example.com/a", body=b"{}",
                        status=200, content_type="application/json", root=self.root)
        sources.archive("archive-run", "ted", "https://example.com/b", error="HTTP 429: slow down", root=self.root)
        listing = sources.list_sources("archive-run", root=self.root)
        self.assertEqual((listing["count"], listing["collected"], listing["failed"]), (2, 1, 1))


if __name__ == "__main__":
    unittest.main()
