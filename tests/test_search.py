"""Offline checks for the swappable search layer. No network calls are made."""
import io
import hashlib
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scripts import search


def http_error(code, url="https://example.com"):
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(b'{"error": "synthetic"}'))


BRAVE_BODY = json.dumps({"web": {"results": [
    {"title": "Accounting document collection software", "url": "https://vendor.example/pricing",
     "description": "Monthly client document chasing priced per client."},
    {"title": "Thread", "url": "https://forum.example/thread", "description": "We chase documents by email."},
]}}).encode()
TAVILY_BODY = json.dumps({"results": [
    {"title": "Firm pays for portal", "url": "https://other.example/portal", "content": "Paid plan", "score": 0.9},
]}).encode()


class SearchTests(unittest.TestCase):
    def setUp(self):
        # The process environment wins over .env.local; a session's real keys must not leak in.
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in [n for n in os.environ if n.endswith("_API_KEY")]:
            del os.environ[name]
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._get = search.sources.http_get
        self._post = search.sources.http_post_json
        (self.root / ".env.local").write_text(
            "BRAVE_API_KEY=brave-test\nMONEY_FIRST_BRAVE_MIN_INTERVAL_SECONDS=0\n"
            "MONEY_FIRST_TAVILY_MIN_INTERVAL_SECONDS=0\n", encoding="utf-8")

    def tearDown(self):
        search.sources.http_get = self._get
        search.sources.http_post_json = self._post
        self._tmp.cleanup()

    def test_capabilities_report_missing_key_without_network(self):
        snapshot = search.capabilities(root=self.root)
        self.assertEqual(snapshot["available"], ["brave"])
        self.assertTrue(snapshot["web_search_available"])
        self.assertFalse(snapshot["network_used"])
        tavily = [p for p in snapshot["providers"] if p["name"] == "tavily"][0]
        self.assertFalse(tavily["available"])
        self.assertIn("TAVILY_API_KEY", tavily["reason"])

    def test_missing_key_is_capability_unavailable_not_silent_scrape(self):
        calls = []
        (self.root / ".env.local").write_text("MONEY_FIRST_TAVILY_MIN_INTERVAL_SECONDS=0\n", encoding="utf-8")
        search.sources.http_get = lambda *a, **k: calls.append(a) or (200, "application/json", b"{}")
        search.sources.http_post_json = lambda *a, **k: calls.append(a) or (200, "application/json", b"{}")
        result = search.search("search-run", "bookkeeping document chasing", provider="tavily", root=self.root)
        self.assertEqual(result["retrieval_status"], "capability_unavailable")
        self.assertEqual(result["results"], [])
        self.assertIsNone(result["result_count"])
        self.assertEqual(calls, [])
        log = search.search_log("search-run", root=self.root)
        self.assertEqual(log["query_count"], 0)

    def test_brave_results_are_normalised_archived_and_logged(self):
        def fake_get(url, headers=None):
            self.assertIn("api.search.brave.com", url)
            self.assertIn("q=bookkeeping", url)
            self.assertEqual(headers["X-Subscription-Token"], "brave-test")
            return 200, "application/json", BRAVE_BODY

        search.sources.http_get = fake_get
        result = search.search("search-run", "bookkeeping", country="ES", search_lang="en", root=self.root)
        self.assertEqual(result["retrieval_status"], "collected")
        self.assertEqual(result["provider"], "brave")
        self.assertEqual(result["result_count"], 2)
        self.assertEqual(result["results"][0]["rank"], 1)
        self.assertEqual(result["results"][0]["url"], "https://vendor.example/pricing")
        self.assertIsNone(result["results"][0]["score"])
        archived = self.root / "runs" / "search-run" / "sources" / f"{result['source_id']}.json"
        self.assertTrue(archived.is_file())
        log = search.search_log("search-run", root=self.root)
        self.assertEqual(log["query_count"], 1)
        self.assertEqual(log["searches"][0]["country"], "ES")
        self.assertEqual(log["searches"][0]["result_count"], 2)

    def test_calling_agent_is_recorded_next_to_the_search_without_changing_the_budget(self):
        search.sources.http_get = lambda url, headers=None: (200, "application/json", BRAVE_BODY)
        first = search.search("attributed-run", "bookkeeping", assignment_id="cand-001-discovery",
                              role="discovery", worker_id="/root/scout_a", root=self.root)
        log = search.search_log("attributed-run", root=self.root)
        entry = log["searches"][0]
        self.assertEqual(entry["assignment_id"], "cand-001-discovery")
        self.assertEqual(entry["role"], "discovery")
        self.assertEqual(entry["worker_id"], "/root/scout_a")
        self.assertEqual(log["query_count"], 1)
        usage = [json.loads(line) for line in
                 (self.root / "runs" / "attributed-run" / "source_usage.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["source_id"], first["source_id"])
        self.assertEqual(usage[0]["assignment_id"], "cand-001-discovery")
        self.assertEqual(usage[0]["tool"], "brave")
        meta = json.loads((self.root / "runs" / "attributed-run" / "sources" /
                           f"{first['source_id']}.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["first_assignment_id"], "cand-001-discovery")
        second = search.search("attributed-run", "bookkeeping", assignment_id="cand-002-money",
                               role="money", worker_id="/root/facets_b", root=self.root)
        usage = [json.loads(line) for line in
                 (self.root / "runs" / "attributed-run" / "source_usage.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual({row["assignment_id"] for row in usage}, {"cand-001-discovery", "cand-002-money"})
        meta = json.loads((self.root / "runs" / "attributed-run" / "sources" /
                           f"{second['source_id']}.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["first_assignment_id"], "cand-001-discovery")

    def test_source_id_does_not_depend_on_which_agent_fetched_the_page(self):
        search.sources.http_get = lambda url, headers=None: (200, "text/html", b"<html>synthetic</html>")
        anonymous = search.fetch("plain-run", "https://vendor.example/pricing", root=self.root)
        attributed = search.fetch("plain-run", "https://vendor.example/pricing", assignment_id="cand-003-money",
                                  role="money", worker_id="/root/facets_b", root=self.root)
        self.assertEqual(anonymous["source_id"], attributed["source_id"])
        usage = [json.loads(line) for line in
                 (self.root / "runs" / "plain-run" / "source_usage.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual([row["assignment_id"] for row in usage], [None, "cand-003-money"])

    def test_rate_limited_provider_switches_once_instead_of_retrying(self):
        seen = []
        (self.root / ".env.local").write_text("BRAVE_API_KEY=brave-test\nTAVILY_API_KEY=tavily-test\n"
                                              "MONEY_FIRST_BRAVE_MIN_INTERVAL_SECONDS=0\n"
                                              "MONEY_FIRST_TAVILY_MIN_INTERVAL_SECONDS=0\n",
                                              encoding="utf-8")

        def fake_get(url, headers=None):
            seen.append("brave")
            raise http_error(429, url)

        def fake_post(url, payload, headers=None):
            seen.append("tavily")
            self.assertEqual(headers["Authorization"], "Bearer tavily-test")
            return 200, "application/json", TAVILY_BODY

        search.sources.http_get = fake_get
        search.sources.http_post_json = fake_post
        result = search.search("search-run", "bookkeeping", root=self.root)
        self.assertEqual(seen, ["brave", "tavily"])
        self.assertEqual(result["provider"], "tavily")
        self.assertEqual(result["rate_limited"], ["brave"])
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["results"][0]["score"], 0.9)
        self.assertEqual(len(result["attempts"]), 2)
        log = search.search_log("search-run", root=self.root)
        self.assertEqual(log["query_count"], 1)
        self.assertEqual(log["provider_attempt_count"], 2)
        self.assertEqual(log["failure_counts"]["rate_limit"], 1)
        self.assertEqual(log["failure_counts"]["transport"], 0)
        self.assertEqual(len({a["logical_query_id"] for a in result["attempts"]}), 1)

    def test_named_provider_falls_back_when_its_quota_is_exhausted(self):
        seen = []
        (self.root / ".env.local").write_text("BRAVE_API_KEY=brave-test\nTAVILY_API_KEY=tavily-test\n"
                                              "MONEY_FIRST_BRAVE_MIN_INTERVAL_SECONDS=0\n"
                                              "MONEY_FIRST_TAVILY_MIN_INTERVAL_SECONDS=0\n",
                                              encoding="utf-8")

        def fake_get(url, headers=None):
            seen.append("brave")
            raise http_error(429, url)

        search.sources.http_get = fake_get
        search.sources.http_post_json = lambda *a, **k: seen.append("tavily") or (200, "application/json", TAVILY_BODY)
        result = search.search("search-run", "serp check", provider="brave", root=self.root)
        self.assertEqual(seen, ["brave", "tavily"])
        self.assertEqual(result["provider"], "tavily")
        self.assertEqual(result["requested_provider"], "brave")
        self.assertEqual(result["rate_limited"], ["brave"])
        self.assertEqual(result["retrieval_status"], "collected")

    def test_empty_results_do_not_trigger_a_second_provider(self):
        seen = []
        (self.root / ".env.local").write_text("BRAVE_API_KEY=brave-test\nTAVILY_API_KEY=tavily-test\n"
                                              "MONEY_FIRST_BRAVE_MIN_INTERVAL_SECONDS=0\n"
                                              "MONEY_FIRST_TAVILY_MIN_INTERVAL_SECONDS=0\n",
                                              encoding="utf-8")
        search.sources.http_get = lambda url, headers=None: seen.append("brave") or (
            200, "application/json", b'{"web": {"results": []}}')
        search.sources.http_post_json = lambda *a, **k: seen.append("tavily") or (200, "application/json", TAVILY_BODY)
        result = search.search("search-run", "very narrow query", root=self.root)
        self.assertEqual(seen, ["brave"])
        self.assertEqual(result["retrieval_status"], "collected")
        self.assertEqual(result["result_count"], 0)
        self.assertEqual(result["results"], [])

    def test_unrecognised_response_shape_is_an_error_not_zero_results(self):
        search.sources.http_get = lambda url, headers=None: (200, "application/json", b'{"unexpected": true}')
        result = search.search("search-run", "bookkeeping", root=self.root)
        self.assertEqual(result["retrieval_status"], "error")
        self.assertIsNone(result["result_count"])
        self.assertIn("unrecognised", result["error"])

    def test_transport_failure_falls_back_without_counting_another_query(self):
        (self.root / ".env.local").write_text("BRAVE_API_KEY=brave-test\nTAVILY_API_KEY=tavily-test\n"
                                              "MONEY_FIRST_BRAVE_MIN_INTERVAL_SECONDS=0\n"
                                              "MONEY_FIRST_TAVILY_MIN_INTERVAL_SECONDS=0\n")
        def fail(*args, **kwargs):
            raise urllib.error.URLError("synthetic connection failure")
        search.sources.http_get = fail
        search.sources.http_post_json = lambda *a, **k: (200, "application/json", TAVILY_BODY)
        result = search.search("search-run", "synthetic query", root=self.root)
        self.assertEqual(result["retrieval_status"], "collected")
        self.assertEqual(result["transport_failures"], ["brave"])
        self.assertEqual(result["rate_limited"], [])
        log = search.search_log("search-run", root=self.root)
        self.assertEqual(log["query_count"], 1)
        self.assertEqual(log["provider_attempt_count"], 2)
        self.assertEqual(log["failure_counts"]["transport"], 1)
        self.assertEqual(log["failure_counts"]["rate_limit"], 0)

    def test_single_provider_rate_limit_is_recorded(self):
        def fail(*args, **kwargs):
            raise http_error(429)
        search.sources.http_get = fail
        result = search.search("search-run", "synthetic query", provider="brave", root=self.root)
        self.assertEqual(result["resolution"], "rate_limited")
        self.assertEqual(result["rate_limited"], ["brave"])
        self.assertEqual(search.search_log("search-run", root=self.root)["query_count"], 1)

    def test_single_provider_rate_limit_retries_same_logical_query(self):
        responses = [http_error(429), (200, "application/json", BRAVE_BODY)]

        def request(*args, **kwargs):
            value = responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        search.sources.http_get = request
        result = search.search("search-run", "synthetic retry", root=self.root)
        self.assertEqual(result["retrieval_status"], "collected")
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(len({a["logical_query_id"] for a in result["attempts"]}), 1)
        log = search.search_log("search-run", root=self.root)
        self.assertEqual(log["query_count"], 1)
        self.assertEqual(log["provider_attempt_count"], 2)
        self.assertEqual(log["failure_counts"]["rate_limit"], 1)

    def test_repeated_query_retains_attempts_and_separates_geographies(self):
        search.sources.http_get = lambda *a, **k: (200, "application/json", BRAVE_BODY)
        for country, count in (("ES", 5), ("ES", 10), ("US", 10)):
            search.search("search-run", "synthetic query", country=country, count=count, root=self.root)
        log = search.search_log("search-run", root=self.root)
        self.assertEqual(log["query_count"], 2)
        self.assertEqual(log["provider_attempt_count"], 3)
        self.assertEqual(len({r["search_id"] for r in log["searches"]}), 3)

    def test_worker_budget_counts_semantic_queries_not_provider_attempts(self):
        entries = [{"query": "synthetic", "provider": provider, "access_date": "2026-09-11", "status": "failed"}
                   for provider in ("brave", "tavily")]
        self.assertEqual(search.logical_query_count(entries), 1)
        entries.append({**entries[0], "query": "different", "logical_query_id": "pretend-same"})
        entries[0]["logical_query_id"] = "pretend-same"
        self.assertEqual(search.logical_query_count(entries), 2)

    def test_invalid_json_is_archived_and_counted_as_parse_failure(self):
        search.sources.http_get = lambda *a, **k: (200, "application/json", b"\xffinvalid")
        result = search.search("search-run", "synthetic query", root=self.root)
        self.assertEqual(result["retrieval_status"], "error")
        self.assertEqual(result["attempts"][0]["failure_kind"], "parse")
        log = search.search_log("search-run", root=self.root)
        self.assertEqual(log["failure_counts"]["parse"], 1)
        self.assertIsNone(result["result_count"])

    def test_fetch_pdf_retains_original_bytes_and_hash(self):
        body = b"%PDF-1.7\n%\xff\x00\xfe\x80"
        search.sources.http_get = lambda *a, **k: (200, "application/pdf", body)
        result = search.fetch("search-run", "https://example.com/report.pdf", root=self.root, max_bytes=3)
        self.assertEqual(result["content_type"], "application/pdf")
        self.assertTrue(result["archive_path"].endswith(".pdf"))
        self.assertEqual((self.root / "runs/search-run" / result["archive_path"]).read_bytes(), body)
        self.assertEqual(result["sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["byte_length"], len(body))

    def test_fetch_archives_complete_response_even_beyond_preview_threshold(self):
        search.sources.http_get = lambda url, headers=None: (200, "text/html", b"<html>price list</html>")
        result = search.fetch("search-run", "https://vendor.example/pricing", root=self.root)
        self.assertEqual(result["retrieval_status"], "collected")
        self.assertFalse(result["truncated"])
        self.assertTrue((self.root / "runs" / "search-run" / result["archive_path"]).is_file())
        search.sources.http_get = lambda url, headers=None: (200, "text/html", b"x" * 4096)
        big = search.fetch("search-run", "https://vendor.example/huge", max_bytes=100, root=self.root)
        self.assertFalse(big["truncated"])
        self.assertTrue(big["exceeds_preview_limit"])
        self.assertEqual((self.root / "runs/search-run" / big["archive_path"]).read_bytes(), b"x" * 4096)
        self.assertEqual(big["sha256"], hashlib.sha256(b"x" * 4096).hexdigest())
        with self.assertRaises(ValueError):
            search.fetch("search-run", "not-a-url", root=self.root)


if __name__ == "__main__":
    unittest.main()
