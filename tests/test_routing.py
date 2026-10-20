"""Per-role model routing contracts. No LLM call and no market research happen here."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import routing  # noqa: E402


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "config").mkdir()
        shutil.copy(ROOT / "config/model-routing.yaml", self.root / "config/model-routing.yaml")

    def tearDown(self):
        self._tmp.cleanup()

    def test_repository_routing_is_valid_and_every_role_is_present(self):
        current = routing.load_routing()
        self.assertEqual(routing.validate_routing(current), [])
        self.assertEqual(set(current["roles"]), set(routing.ROLES))

    def test_missing_file_means_runtime_choice_rather_than_an_error(self):
        empty = routing.load_routing(self.root / "nowhere")
        self.assertEqual(routing.resolve(empty, "money")["provider"], None)
        self.assertEqual(routing.resolve(None, "money")["routing_source"], "unset")

    def test_stage_override_wins_over_role_and_role_over_default(self):
        record = {"schema_version": "1.0", "default": {"provider": "openai", "model": None, "reasoning_effort": "low"},
                  "roles": {"skeptic": {"provider": "deepseek", "model": "deepseek-reasoner", "reasoning_effort": None}},
                  "stages": {"deep_investigation": {"skeptic": {"model": "deepseek-flash"}}}}
        role_level = routing.resolve(record, "skeptic")
        self.assertEqual((role_level["provider"], role_level["model"], role_level["reasoning_effort"]),
                         ("deepseek", "deepseek-reasoner", "low"))
        self.assertEqual(role_level["routing_source"], "role")
        staged = routing.resolve(record, "skeptic", "deep_investigation")
        self.assertEqual(staged["model"], "deepseek-flash")
        self.assertEqual(staged["routing_source"], "stage")
        untouched = routing.resolve(record, "money")
        self.assertEqual(untouched["provider"], "openai")
        self.assertEqual(untouched["routing_source"], "default")

    def test_unknown_role_and_stage_are_rejected(self):
        broken = {"schema_version": "1.0", "roles": {"scout": {"provider": "deepseek"}}}
        self.assertTrue(routing.validate_routing(broken))
        with self.assertRaisesRegex(ValueError, "unknown role"):
            routing.set_choice("scout", provider="deepseek", root=self.root)
        with self.assertRaisesRegex(ValueError, "unknown stage"):
            routing.set_choice("skeptic", provider="deepseek", stage="round-000", root=self.root)
        with self.assertRaisesRegex(ValueError, "reasoning_effort"):
            routing.set_choice("skeptic", reasoning_effort="maximum", root=self.root)
        with self.assertRaisesRegex(ValueError, "nothing to change"):
            routing.set_choice("skeptic", root=self.root)

    def test_set_round_trip_keeps_other_roles_and_can_clear_a_provider(self):
        money = routing.load_routing(self.root)["roles"]["money"]
        routing.set_choice("skeptic", provider="deepseek", model="deepseek-reasoner", root=self.root)
        stored = routing.load_routing(self.root)
        self.assertEqual(routing.validate_routing(stored), [])
        self.assertEqual(stored["roles"]["skeptic"]["provider"], "deepseek")
        self.assertEqual(stored["roles"]["skeptic"]["reasoning_effort"], "high")
        self.assertEqual(stored["roles"]["money"], money)
        routing.set_choice("skeptic", provider="any", model="any", root=self.root)
        cleared = routing.load_routing(self.root)
        self.assertIsNone(cleared["roles"]["skeptic"]["provider"])
        self.assertIsNone(cleared["roles"]["skeptic"]["model"])

    def test_unknown_provider_is_a_warning_not_an_error(self):
        routing.set_choice("judge", provider="some-new-runtime", model="x1", root=self.root)
        stored = routing.load_routing(self.root)
        self.assertEqual(routing.validate_routing(stored), [])
        self.assertTrue(any("some-new-runtime" in note for note in routing.warnings_for(stored)))

    def test_cli_show_and_validate_emit_json(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/routing.py"), "show", "--json",
                                 "--root", str(self.root)], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload["roles"]), set(routing.ROLES))
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/routing.py"), "validate",
                                 "--root", str(self.root)], capture_output=True, text=True, cwd=ROOT)
        self.assertTrue(json.loads(result.stdout)["valid"])


if __name__ == "__main__":
    unittest.main()
