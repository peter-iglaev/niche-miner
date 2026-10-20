"""Synthetic end-to-end control-plane checks. No market research is executed."""
import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures
from scripts import app


class CycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_targeted_single_candidate_completes_without_portfolio_quotas(self):
        candidate_id = fixtures.start_run(self.root)
        folder = self.root / "runs" / "synthetic-run"
        state = app.status("synthetic-run", root=self.root)
        self.assertEqual(state["scope"], "targeted")
        self.assertEqual(state["portfolio"]["status"], "not_assessed")
        app.decide("synthetic-run", candidate_id, "deferred", "synthetic evidence insufficient", "root-agent", root=self.root)
        app.report("synthetic-run", root=self.root)
        fixtures.write_narrative(folder)
        result = app.finalize("synthetic-run", root=self.root)
        self.assertTrue(result["valid"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["qualified_count"], 0)

    def test_full_qualified_cycle(self):
        candidate_id = fixtures.start_run(self.root)
        folder = self.root / "runs" / "synthetic-run"
        fixtures.finish_facets(self.root)
        fixtures.finish_judge(self.root)
        app.decide("synthetic-run", candidate_id, "qualified", "synthetic gates passed", "root-agent",
                   [f"{candidate_id}-money-c1"], root=self.root)
        app.report("synthetic-run", root=self.root)
        fixtures.write_narrative(folder)
        result = app.finalize("synthetic-run", root=self.root)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["qualified_count"], 1)
        self.assertEqual(app.status("synthetic-run", root=self.root)["investigation_priorities"][candidate_id]["tier"], "high")

    def test_mining_quota_blocks_finalize_until_recorded_override(self):
        candidate_id = fixtures.start_run(self.root, run_id="synthetic-mining", scope="mining")
        folder = self.root / "runs" / "synthetic-mining"
        app.decide("synthetic-mining", candidate_id, "deferred", "synthetic evidence insufficient", "root-agent", root=self.root)
        app.report("synthetic-mining", root=self.root)
        fixtures.write_narrative(folder)
        with self.assertRaisesRegex(ValueError, "industries < minimum"):
            app.finalize("synthetic-mining", root=self.root)
        app.report("synthetic-mining", override_quota="synthetic single-candidate mining check", root=self.root)
        fixtures.write_narrative(folder)
        result = app.finalize("synthetic-mining", root=self.root)
        self.assertTrue(result["valid"])
        state = app.status("synthetic-mining", root=self.root)
        self.assertIsNotNone(state["quota_override"])

    def test_create_run_requires_scope_and_stopping_condition(self):
        with self.assertRaisesRegex(ValueError, "scope must be"):
            app.create_run("needs-scope", "objective", None, "stop", root=self.root)
        with self.assertRaisesRegex(ValueError, "stopping_conditions must be"):
            app.create_run("needs-stop", "objective", "targeted", "  ", root=self.root)

    def test_accept_requires_bound_agent_and_respects_retry(self):
        candidate_id = fixtures.start_run(self.root)
        queued = app.queue("synthetic-run", candidate_id, "money", {}, root=self.root)
        with self.assertRaisesRegex(ValueError, "bound actual agent"):
            app.accept("synthetic-run", queued["assignment_id"], {"schema_version": "1.0"}, root=self.root)
        with self.assertRaisesRegex(ValueError, "self-validate"):
            app.bind("synthetic-run", queued["assignment_id"], fixtures.WORKERS["discovery"], root=self.root)
        app.bind("synthetic-run", queued["assignment_id"], fixtures.WORKERS["money"], root=self.root)
        with self.assertRaisesRegex(ValueError, "already exists"):
            app.queue("synthetic-run", candidate_id, "money", {}, root=self.root)
        app.cancel("synthetic-run", queued["assignment_id"], "synthetic worker runtime ended", "execution", root=self.root)
        retried = app.queue("synthetic-run", candidate_id, "money", {}, retry=True, root=self.root)
        self.assertEqual(retried["attempt"], 2)

    def test_execution_failure_blocks_finalize_and_does_not_spend_research_retry(self):
        app.create_run("health-run", "Synthetic contract check", "targeted", "stop after check",
                       config={"limits": {"max_attempts_per_assignment": 1}}, root=self.root)
        fixtures.discover(self.root, "health-run", "health-cand")
        queued = app.queue("health-run", "health-cand", "money", {}, root=self.root)
        app.bind("health-run", queued["assignment_id"], fixtures.WORKERS["money"], root=self.root)
        app.cancel("health-run", queued["assignment_id"], "worker disappeared", "execution", root=self.root)
        retried = app.queue("health-run", "health-cand", "money", {}, retry=True, root=self.root)
        self.assertEqual(retried["attempt"], 2)
        app.bind("health-run", retried["assignment_id"], fixtures.WORKERS["money"], root=self.root)
        app.cancel("health-run", retried["assignment_id"], "worker disappeared again", "execution", root=self.root)
        app.decide("health-run", "health-cand", "deferred", "execution incomplete", "root-agent", root=self.root)
        app.report("health-run", root=self.root)
        folder = self.root / "runs" / "health-run"
        fixtures.write_narrative(folder)
        with self.assertRaisesRegex(ValueError, "incomplete run"):
            app.finalize("health-run", root=self.root)
        health = app.status("health-run", root=self.root)["execution_health"]
        self.assertEqual(health["state"], "blocked")
        self.assertEqual(health["blocking_assignments"][0]["failure_kind"], "execution")

    def test_validation_failure_preserves_attempt_and_does_not_spend_research_retry(self):
        app.create_run("validation-retry", "Synthetic contract check", "targeted", "stop after check",
                       config={"limits": {"max_attempts_per_assignment": 1}}, root=self.root)
        fixtures.discover(self.root, "validation-retry", "validation-cand")
        queued = app.queue("validation-retry", "validation-cand", "money", {}, root=self.root)
        app.bind("validation-retry", queued["assignment_id"], fixtures.WORKERS["money"], root=self.root)
        with self.assertRaises(ValueError):
            app.accept("validation-retry", queued["assignment_id"], {"schema_version": "1.0"}, root=self.root)
        folder = self.root / "runs" / "validation-retry"
        manifest = json.loads((folder / "manifest.json").read_text())
        attempt = manifest["assignments"][-1]["attempts"][0]
        self.assertEqual(attempt["failure_kind"], "validation")
        self.assertTrue((folder / attempt["result_path"]).is_file())
        retried = app.queue("validation-retry", "validation-cand", "money", {}, retry=True, root=self.root)
        self.assertEqual(retried["attempt"], 2)

    def test_incomplete_worker_result_cannot_be_accepted(self):
        candidate_id = fixtures.start_run(self.root)
        queued = app.queue("synthetic-run", candidate_id, "money", {}, root=self.root)
        app.bind("synthetic-run", queued["assignment_id"], fixtures.WORKERS["money"], root=self.root)
        claimed = fixtures.claim(f"{candidate_id}-money-incomplete", candidate_id, fixtures.WORKERS["money"], payment=True)
        output = fixtures.role_output(candidate_id, "money", fixtures.WORKERS["money"], fixtures.WORKERS["discovery"], queued["input_hash"], [claimed["id"]])
        output["completed"] = False
        record = {"schema_version": "1.0", "run_id": "synthetic-run", "assignment_id": queued["assignment_id"],
                  "attempt": 1, "role": "money", "worker_id": fixtures.WORKERS["money"], "input_hash": queued["input_hash"],
                  "claims": [claimed], "search_log": [], "role_output": output}
        with self.assertRaisesRegex(ValueError, "completed must be true"):
            app.accept("synthetic-run", queued["assignment_id"], record, root=self.root)

    def test_accepted_attempt_is_immutable(self):
        candidate_id = fixtures.start_run(self.root)
        from scripts.validate import validate_run
        status = app.status("synthetic-run", root=self.root)
        assignment = next(a for a in status["assignments"] if a["role"] == "discovery")
        accepted = self.root / "runs" / "synthetic-run" / "work" / assignment["assignment_id"] / "attempt-1" / "result.json"
        original = accepted.read_text(encoding="utf-8")
        accepted.write_text(original.replace("Synthetic buyer group", "Tampered buyer"), encoding="utf-8")
        checked = validate_run(self.root / "runs" / "synthetic-run")
        self.assertFalse(checked["valid"])
        self.assertTrue(any("hash mismatch" in error for error in checked["errors"]), checked["errors"])

    def test_input_packet_is_validated(self):
        app.create_run("packet-run", "objective", "targeted", "stop", root=self.root)
        with self.assertRaisesRegex(ValueError, "allowed_actions must be a nonempty list"):
            app.queue("packet-run", "synthetic-cand", "discovery",
                      {"allowed_actions": []}, root=self.root)

    def test_worker_serialisation_is_accepted_and_canonicalised(self):
        app.create_run("format-run", "objective", "targeted", "stop", root=self.root)
        queued = app.queue("format-run", "fmt-cand", "discovery", {"task": "synthetic"}, root=self.root)
        app.bind("format-run", queued["assignment_id"], "agent-scout", root=self.root)
        claimed = fixtures.claim("fmt-cand-discovery-c1", "fmt-cand", "agent-scout")
        record = {"schema_version": "1.0", "run_id": "format-run", "assignment_id": queued["assignment_id"],
                  "attempt": 1, "role": "discovery", "worker_id": "agent-scout", "input_hash": queued["input_hash"],
                  "claims": [claimed], "search_log": [],
                  "candidate": fixtures.candidate("fmt-cand", "format-run", "agent-scout", claim_ids=[claimed["id"]])}
        path = self.root / "runs" / "format-run" / "work" / queued["assignment_id"] / "attempt-1" / "result.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=4), encoding="utf-8")
        accepted = app.accept("format-run", queued["assignment_id"], path, root=self.root)
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), record)

    def test_new_workers_see_all_accepted_claims_and_previous_packets_do_not_change(self):
        cid = fixtures.start_run(self.root)
        old = app.queue("synthetic-run", cid, "distribution", {}, root=self.root)
        old_path = Path(old["input_path"])
        old_bytes = old_path.read_bytes()
        fixtures.finish_facets(self.root, roles=("money",))
        new = app.queue("synthetic-run", cid, "feasibility", {}, root=self.root)
        packet = json.loads(Path(new["input_path"]).read_text())
        self.assertEqual({c["id"] for c in packet["claims"]}, {cid+"-discovery-c1",cid+"-money-c1"})
        self.assertEqual(set(packet["accepted_outputs"]), {"discovery","money"})
        self.assertEqual(old_path.read_bytes(), old_bytes)
        from scripts.validate import digest
        self.assertEqual(digest(old_path),old["input_hash"])

    def test_light_stage_is_one_cheap_worker_and_later_review_is_independent(self):
        cid=fixtures.start_run(self.root)
        packet=json.loads((self.root/"runs/synthetic-run/work"/(cid+"-discovery")/"attempt-1/input.json").read_text())
        self.assertEqual(packet["stage"],"light_validation")
        self.assertEqual(packet["limits"]["max_search_queries_per_attempt"],2)
        # The packet carries whatever the repository routes this role to, null included.
        from scripts.routing import load_routing, resolve
        expected=resolve(load_routing(),"discovery","light_validation")
        for field in ("provider","model","reasoning_effort"):
            self.assertEqual(packet["execution_policy"][field],expected[field])
        self.assertEqual(packet["execution_policy"]["model_class"],"economy")
        with self.assertRaisesRegex(ValueError,"not allowed"):
            app.queue("synthetic-run",cid,"money",{"stage":"light_validation"},root=self.root)
        fixtures.finish_facets(self.root,roles=("money",))
        queued=app.queue("synthetic-run",cid,"skeptic",{},root=self.root)
        with self.assertRaisesRegex(ValueError,"fresh review context"):
            app.bind("synthetic-run",queued["assignment_id"],"new-skeptic",reused_context=True,root=self.root)
        with self.assertRaisesRegex(ValueError,"independent"):
            app.bind("synthetic-run",queued["assignment_id"],fixtures.WORKERS["money"],root=self.root)
        self.assertIn(cid,app.status("synthetic-run",root=self.root)["investigation_priorities"])

    def test_model_routing_reaches_the_packet_and_bind_records_the_actual_model(self):
        app.create_run("routed-run","Synthetic routing check; no market research","targeted","stop after the synthetic check",
                       config={"model_routing":{"schema_version":"1.0","default":{"provider":None,"model":None,"reasoning_effort":None},
                                                "roles":{"discovery":{"provider":"deepseek","model":"deepseek-chat","reasoning_effort":"low"}},
                                                "stages":{}}},root=self.root)
        queued=app.queue("routed-run","routed-cand","discovery",{"task":"synthetic"},root=self.root)
        packet=json.loads((self.root/"runs/routed-run"/queued["input_path"].split("runs/routed-run/")[-1]).read_text())
        self.assertEqual(packet["execution_policy"]["provider"],"deepseek")
        self.assertEqual(packet["execution_policy"]["model"],"deepseek-chat")
        self.assertEqual(packet["execution_policy"]["reasoning_effort"],"low")
        self.assertEqual(packet["execution_policy"]["routing_source"],"role")
        self.assertEqual(packet["execution_policy"]["model_class"],"economy")
        bound=app.bind("routed-run",queued["assignment_id"],"agent-scout",provider="deepseek",model="deepseek-flash",root=self.root)
        self.assertEqual(bound["requested_model"],"deepseek-chat")
        self.assertEqual(bound["model"],"deepseek-flash")
        manifest=json.loads((self.root/"runs/routed-run/manifest.json").read_text())
        attempt=manifest["assignments"][0]["attempts"][-1]
        self.assertEqual((attempt["provider"],attempt["model"]),("deepseek","deepseek-flash"))
        events=[json.loads(l) for l in (self.root/"runs/routed-run/events.jsonl").read_text().splitlines() if l.strip()]
        bound_event=[e for e in events if e["event"]=="bound"][-1]
        self.assertEqual(bound_event["details"]["model"],"deepseek-flash")
        self.assertEqual(bound_event["details"]["requested_model"],"deepseek-chat")
        app.bind("routed-run",queued["assignment_id"],"agent-scout",provider="deepseek",model="deepseek-flash",root=self.root)
        with self.assertRaisesRegex(ValueError,"only prepared attempts may bind"):
            app.bind("routed-run",queued["assignment_id"],"agent-scout",model="other-model",root=self.root)

    def test_bind_without_a_recorded_model_stays_valid_and_null(self):
        cid=fixtures.start_run(self.root)
        manifest=json.loads((self.root/"runs/synthetic-run/manifest.json").read_text())
        attempt=manifest["assignments"][0]["attempts"][-1]
        self.assertIsNone(attempt["provider"])
        self.assertIsNone(attempt["model"])
        from scripts.validate import validate_run
        self.assertEqual(validate_run(self.root/"runs/synthetic-run")["errors"],[])

    def test_review_detects_accepted_evidence_after_its_snapshot(self):
        cid=fixtures.start_run(self.root)
        fixtures.finish_facets(self.root,roles=("money",))
        queued=app.queue("synthetic-run",cid,"skeptic",{},root=self.root)
        fixtures.finish_facets(self.root,roles=("distribution",))
        from scripts.validate import read_data, review_snapshot_errors
        folder=self.root/"runs/synthetic-run"
        manifest=read_data(folder/"manifest.json")
        assignment=next(a for a in manifest["assignments"] if a["id"]==queued["assignment_id"])
        self.assertTrue(review_snapshot_errors(read_data(queued["input_path"]),manifest,assignment))


if __name__ == "__main__":
    unittest.main()
