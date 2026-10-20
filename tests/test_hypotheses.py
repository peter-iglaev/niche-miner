"""Offline checks for the cheap hypothesis staging layer. No network calls are made."""
import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scripts import hypotheses


def atom(identifier, industry="accounting_finance_ops", status="observed", spend="staff time plus a paid portal"):
    return {"id": identifier, "buyer_industry": industry, "who": "small accounting firms",
            "trigger": "monthly client document collection", "pain": "chasing missing documents",
            "current_workaround": "spreadsheets and email reminders", "existing_spend": spend,
            "existing_spend_status": status, "job_to_be_done": "collect and flag missing documents",
            "measurable_result": "fewer staff hours at month end",
            "distribution_channel": "search and accounting software marketplace",
            "origin": "cheap_model:deepseek-flash"}


class HypothesisStagingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "runs" / "stage-run").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_staging_add_dedupes_and_audits_coverage(self):
        result = hypotheses.add("stage-run", [atom("hyp-1"), atom("hyp-2", industry="procurement")], root=self.root)
        self.assertEqual((result["staged"], result["added"]), (2, 2))
        again = hypotheses.add("stage-run", [atom("hyp-1")], root=self.root)
        self.assertEqual((again["added"], again["duplicates_collapsed"]), (0, 1))
        report = hypotheses.audit("stage-run", root=self.root)
        self.assertEqual(report["staged"], 2)
        self.assertEqual(report["industries_covered"], 2)
        self.assertIn("legal_administration", report["missing_industries"])
        self.assertEqual(report["with_observed_spend"], 2)
        self.assertFalse(report["payment_evidence_required_at_this_stage"])

    def test_conflicting_hypothesis_is_rejected(self):
        hypotheses.add("stage-run", [atom("hyp-1")], root=self.root)
        with self.assertRaises(ValueError):
            hypotheses.add("stage-run", [atom("hyp-1", industry="procurement")], root=self.root)

    def test_structural_defects_are_rejected_before_staging(self):
        cases = {
            "spend with no evidence": atom("hyp-1", status="observed", spend=None),
            "unknown status": atom("hyp-1", status="probably"),
            "placeholder spend": atom("hyp-1", status="unknown", spend="unknown"),
            "unknown industry": atom("hyp-1", industry="crypto_gambling"),
        }
        for label, record in cases.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    hypotheses.add("stage-run", [record], root=self.root)
        missing = atom("hyp-1")
        missing["trigger"] = "   "
        with self.assertRaises(ValueError):
            hypotheses.add("stage-run", [missing], root=self.root)
        absent = atom("hyp-1")
        absent["unexpected"] = "value"
        with self.assertRaises(ValueError):
            hypotheses.add("stage-run", [absent], root=self.root)

    def test_selection_is_deterministic_and_spreads_across_industries(self):
        records = ([atom(f"hyp-a{i}") for i in range(1, 4)]
                   + [atom(f"hyp-b{i}", industry="procurement") for i in range(1, 4)]
                   + [atom(f"hyp-c{i}", industry="legal_administration") for i in range(1, 4)])
        hypotheses.add("stage-run", records, root=self.root)
        first = hypotheses.select("stage-run", 4, root=self.root)
        self.assertEqual(first["selected"], 4)
        self.assertEqual(first["industries_in_selection"], 3)
        picked = json.loads((self.root / "runs/stage-run/hypotheses_selected.jsonl")
                            .read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(picked["buyer_industry"], "accounting_finance_ops")
        self.assertEqual(hypotheses.select("stage-run", 4, root=self.root)["selected"], 4)

    def test_staging_requires_an_existing_run(self):
        with self.assertRaises(ValueError):
            hypotheses.add("missing-run", [atom("hyp-1")], root=self.root)
        with self.assertRaises(ValueError):
            hypotheses.select("stage-run", 0, root=self.root)


if __name__ == "__main__":
    unittest.main()
