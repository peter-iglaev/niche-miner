"""Synthetic validator contract checks; these never run market research."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures
from scripts import app
from scripts.validate import (FACET_ROLES, QUALIFICATION_ROLES, ROLES, SCOPES, read_data, schema_errors,
                              validate_corpus, validate_input, validate_run)

ROOT = Path(__file__).resolve().parents[1]


def portfolio():
    return read_data(ROOT / "config/portfolio.yaml")


def pieces(candidate_id="synthetic-cand", industry="accounting_finance_ops"):
    claimed = fixtures.claim(f"{candidate_id}-discovery-c1", candidate_id, "agent-scout")
    candidate = fixtures.candidate(candidate_id, "synthetic-run", "agent-scout", industry, [claimed["id"]])
    return [candidate], [claimed]


class ValidatorTests(unittest.TestCase):
    def test_role_model_is_canonical(self):
        self.assertEqual(len(ROLES), 8)
        self.assertNotIn("orchestrator", ROLES)
        self.assertEqual(set(QUALIFICATION_ROLES), {"discovery", "money", "distribution", "feasibility", "skeptic", "judge"})
        self.assertEqual(set(FACET_ROLES), {"demand", "money", "competition", "distribution", "feasibility"})
        self.assertEqual(tuple(SCOPES), ("targeted", "mining"))

    def test_candidate_schema_rejects_empty_and_unknown_fields(self):
        candidates, _ = pieces()
        broken = copy.deepcopy(candidates[0]); broken["who"] = "   "
        self.assertTrue(schema_errors(broken, "niche-atom.schema.json", "candidate"))
        extra = copy.deepcopy(candidates[0]); extra["decision"] = "qualified"
        self.assertTrue(schema_errors(extra, "niche-atom.schema.json", "candidate"))

    def test_unknown_metric_requires_a_recorded_gap(self):
        candidates, _ = pieces()
        broken = copy.deepcopy(candidates[0]); broken["research_gaps"] = []
        self.assertTrue(any("research_gaps" in error for error in schema_errors(broken, "niche-atom.schema.json", "candidate")))

    def test_claim_references_must_resolve(self):
        candidates, claims = pieces()
        candidates[0]["claim_ids"] = ["missing-claim"]
        result = validate_corpus(candidates, claims, portfolio())
        self.assertFalse(result["valid"])
        self.assertTrue(any("missing claim" in error for error in result["errors"]))

    def test_targeted_corpus_reports_quotas_not_assessed(self):
        candidates, claims = pieces()
        result = validate_corpus(candidates, claims, portfolio())
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["coverage"]["status"], "not_assessed")
        self.assertEqual(result["quota_errors"], [])

    def test_mining_corpus_enforces_quotas_without_blocking_structure(self):
        candidates, claims = pieces()
        result = validate_corpus(candidates, claims, portfolio(), enforce_portfolio=True)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["coverage"]["status"], "fail")
        self.assertTrue(any("industries < minimum" in error for error in result["quota_errors"]))

    def test_scope_specific_industry_is_preserved_outside_preferred_buckets(self):
        candidates, claims = pieces(industry="saas")
        result = validate_corpus(candidates, claims, portfolio(), enforce_portfolio=True)
        self.assertTrue(result["valid"], result["errors"])
        self.assertIn("saas", result["coverage"]["industries"])
        self.assertTrue(any("outside preferred buckets" in warning for warning in result["warnings"]))

    def test_duplicate_workflow_is_rejected(self):
        candidates, claims = pieces()
        twin = copy.deepcopy(candidates[0]); twin["id"] = "synthetic-twin"
        twin["claim_ids"] = ["synthetic-twin-c1"]
        claims.append(fixtures.claim("synthetic-twin-c1", "synthetic-twin", "agent-scout"))
        result = validate_corpus(candidates + [twin], claims, portfolio())
        self.assertTrue(any("duplicate" in error for error in result["errors"]), result["errors"])

    def test_input_packet_is_checked_against_schema(self):
        self.assertTrue(validate_input({}))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app.create_run("packet-run", "objective", "targeted", "stop", root=root)
            queued = app.queue("packet-run", "synthetic-cand", "discovery", {}, root=root)
            self.assertEqual(validate_input(read_data(Path(queued["input_path"]))), [])

    def test_validate_run_accepts_a_targeted_run_with_deferred_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_id = fixtures.start_run(root)
            folder = root / "runs" / "synthetic-run"
            app.decide("synthetic-run", candidate_id, "deferred", "synthetic evidence insufficient", "root-agent", root=root)
            app.report("synthetic-run", root=root)
            fixtures.write_narrative(folder)
            checked = validate_run(folder, final=True)
            self.assertTrue(checked["valid"], checked["errors"])


if __name__ == "__main__":
    unittest.main()
