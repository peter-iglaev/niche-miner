"""Offline checks for the model-driven assignment executor. No network and no real model."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures  # noqa: E402
from scripts import app, worker  # noqa: E402

SEARCH_BODY = json.dumps({"web": {"results": [
    {"title": "Vendor pricing", "url": "https://vendor.example/pricing",
     "description": "Priced per client per month."}]}}).encode()
PAGE_BODY = b"<html><head><style>p{}</style></head><body><h1>Pricing</h1><p>Firms pay 200 EUR monthly.</p></body></html>"


def scripted(*turns):
    """A fake model: each turn is either tool calls or plain content."""
    state = {"i": 0, "seen": []}

    def client(messages, **kwargs):
        state["seen"].append(messages[-1])
        turn = turns[min(state["i"], len(turns) - 1)]
        state["i"] += 1
        message = {"content": turn.get("content", "")}
        if turn.get("calls"):
            message["tool_calls"] = [
                {"id": f"call-{state['i']}-{n}", "type": "function",
                 "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
                for n, (name, args) in enumerate(turn["calls"])]
        return {"message": message, "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "provider": kwargs.get("provider"), "model": kwargs.get("model"), "finish_reason": "stop"}

    client.state = state
    return client


def claim(source_id, url):
    return {"claim": "Firms pay a monthly fee for this workflow.", "source_url": url,
            "source_type": "vendor_page", "source_excerpt": "Firms pay 200 EUR monthly.",
            "locator": "pricing paragraph", "quality": "B", "relationship": "supports",
            "independence_group": "vendor-example", "notes": "Public price establishes an offer only.",
            "source_id": source_id}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".env.local").write_text(
            "BRAVE_API_KEY=brave-test\nDEEPSEEK_API_KEY=model-test\n"
            "MONEY_FIRST_BRAVE_MIN_INTERVAL_SECONDS=0\n", encoding="utf-8")
        self._get = worker.search.sources.http_get
        worker.search.sources.http_get = self.fake_get
        app.create_run("worker-run", "Synthetic executor check", "targeted", "stop after the check",
                       config={"model_routing": {"schema_version": "1.0",
                                                 "default": {"provider": None, "model": None, "reasoning_effort": None},
                                                 "roles": {"discovery": {"provider": "deepseek", "model": "deepseek-chat",
                                                                         "reasoning_effort": "low"}},
                                                 "stages": {}}},
                       root=self.root)
        self.queued = app.queue("worker-run", "worker-cand", "discovery", {"task": "synthetic"}, root=self.root)

    def tearDown(self):
        worker.search.sources.http_get = self._get
        self._tmp.cleanup()

    def fake_get(self, *args, **kwargs):  # bound as a plain function below
        raise AssertionError("unused")

    def run_worker(self, client, **kwargs):
        return worker.run_assignment("worker-run", self.queued["assignment_id"], root=self.root,
                                     client=client, **kwargs)

    def test_evidence_turn_writes_a_valid_result_and_records_the_actual_model(self):
        worker.search.sources.http_get = lambda url, headers=None: (
            (200, "application/json", SEARCH_BODY) if "brave" in url else (200, "text/html", PAGE_BODY))
        holder = {}

        def submit(messages, **kwargs):
            opened = holder.get("source_id")
            if opened is None:
                return scripted({"calls": [("web_search", {"query": "document chasing pricing"})]})(messages, **kwargs)
            return scripted({"calls": [("submit_result", {
                "claims": [claim(opened, "https://vendor.example/pricing")],
                "candidate": {"industry": "accounting_finance_ops", "who": "Small firms",
                              "buyer": "Owner", "trigger": "Monthly close", "pain": "Manual chasing",
                              "current_workaround": "Email", "existing_spend": None,
                              "job_to_be_done": "Collect client documents", "measurable_result": "Days to close",
                              "distribution_channel": None, "frequency": None,
                              "research_gaps": ["spend unverified"],
                              "category_flags": {"ai_agent_related": False, "mcp_related": False,
                                                 "ecommerce": False, "devops_cloud": False,
                                                 "boring_industry": True, "non_technical_buyer": True,
                                                 "unfamiliar_to_founder": None},
                              "classification_rationale": "Synthetic classification."}})]})(messages, **kwargs)

        step = {"n": 0}

        def client(messages, **kwargs):
            step["n"] += 1
            if step["n"] == 1:
                return scripted({"calls": [("web_search", {"query": "document chasing pricing"})]})(messages, **kwargs)
            if step["n"] == 2:
                return scripted({"calls": [("fetch_page", {"url": "https://vendor.example/pricing"})]})(messages, **kwargs)
            if step["n"] == 3:
                holder["source_id"] = json.loads(messages[-1]["content"])["source_id"]
            return submit(messages, **kwargs)

        out = self.run_worker(client)
        self.assertTrue(out["result_path"].endswith("result.json"))
        self.assertEqual(out["provider"], "deepseek")
        self.assertEqual(out["model"], "deepseek-chat")
        self.assertTrue(out["worker_id"].startswith("deepseek:deepseek-chat:"))
        self.assertEqual(out["claims"], 1)
        self.assertFalse(out["accepted"])

        folder = self.root / "runs" / "worker-run"
        result = json.loads((folder / out["result_path"]).read_text())
        self.assertEqual(result["claims"][0]["author_id"], out["worker_id"])
        self.assertEqual(result["candidate"]["discoverer_id"], out["worker_id"])
        # search_log holds semantic queries only: opening a page is archived but spends no budget.
        self.assertEqual([e["provider"] for e in result["search_log"]], ["brave"])
        opened = [json.loads(line) for line in
                  (folder / "source_usage.jsonl").read_text().splitlines() if line.strip()]
        self.assertIn("direct_fetch", {row["tool"] for row in opened})
        self.assertEqual({row["assignment_id"] for row in opened}, {out["assignment_id"]})
        usage = json.loads((folder / out["result_path"]).parent.joinpath("usage.json").read_text())
        self.assertEqual(usage["model"], "deepseek-chat")
        self.assertEqual(usage["queries_spent"], 1)

        manifest = json.loads((folder / "manifest.json").read_text())
        attempt = manifest["assignments"][0]["attempts"][-1]
        self.assertEqual((attempt["provider"], attempt["model"]), ("deepseek", "deepseek-chat"))
        app.accept("worker-run", out["assignment_id"], folder / out["result_path"], root=self.root)

    def test_search_log_comes_from_real_calls_not_from_the_model(self):
        worker.search.sources.http_get = lambda url, headers=None: (200, "application/json", SEARCH_BODY)
        client = scripted({"calls": [("submit_result", {"claims": [], "candidate": {}, "search_log": [
            {"query": "never ran", "provider": "brave", "access_date": "2026-09-15",
             "status": "results", "raw_response_ref": "sources/invented.json", "notes": "fabricated"}]})]})
        with self.assertRaises(ValueError):
            self.run_worker(client, max_steps=2)
        folder = self.root / "runs" / "worker-run"
        self.assertFalse((folder / "work" / self.queued["assignment_id"] / "attempt-1" / "result.json").exists())

    def test_budget_is_refused_at_the_boundary_and_reported_as_a_gap(self):
        worker.search.sources.http_get = lambda url, headers=None: (200, "application/json", SEARCH_BODY)
        seen = []

        def client(messages, **kwargs):
            if messages[-1].get("role") == "tool":
                seen.append(json.loads(messages[-1]["content"]))
            return scripted({"calls": [("web_search", {"query": f"query {len(seen)}"})]})(messages, **kwargs)

        with self.assertRaises(ValueError):
            self.run_worker(client, max_steps=5)
        self.assertEqual(seen[-1]["budget_left"], 0)
        self.assertIn("budget", (seen[-1].get("error") or ""))

    def test_javascript_only_hosts_are_refused_instead_of_archiving_an_empty_shell(self):
        calls = []
        worker.search.sources.http_get = lambda url, headers=None: (calls.append(url), (200, "text/html", PAGE_BODY))[1]
        seen = []

        def client(messages, **kwargs):
            if messages[-1].get("role") == "tool":
                seen.append(json.loads(messages[-1]["content"]))
            return scripted({"calls": [("fetch_page", {"url": "https://www.reddit.com/r/Accounting/comments/x"})]})(
                messages, **kwargs)

        with self.assertRaises(ValueError):
            self.run_worker(client, max_steps=2)
        self.assertEqual(calls, [])
        self.assertTrue(seen[0]["browser_only"])
        self.assertEqual(seen[0]["status"], "not_collected")
        folder = self.root / "runs" / "worker-run"
        self.assertFalse((folder / "source_usage.jsonl").exists())

    def test_missing_route_refuses_to_run_instead_of_picking_a_model(self):
        app.create_run("unrouted-run", "No routing", "targeted", "stop", root=self.root,
                       config={"model_routing": {"schema_version": "1.0",
                                                 "default": {"provider": None, "model": None, "reasoning_effort": None},
                                                 "roles": {}, "stages": {}}})
        queued = app.queue("unrouted-run", "cand-001", "discovery", {"task": "synthetic"}, root=self.root)
        with self.assertRaisesRegex(ValueError, "no provider/model"):
            worker.run_assignment("unrouted-run", queued["assignment_id"], root=self.root,
                                  client=scripted({"content": "unused"}))


if __name__ == "__main__":
    unittest.main()
