import json
import tempfile
import unittest
from pathlib import Path

from scripts.artifacts import file_hash, merge_records, seal_attempt, write_json


class ArtifactTests(unittest.TestCase):
    def test_retry_is_idempotent_but_cannot_rewrite_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work" / "attempt-1.json"
            first = seal_attempt(path, {"claim": "original observation"})
            self.assertEqual(first, seal_attempt(path, {"claim": "original observation"}))
            with self.assertRaisesRegex(ValueError, "immutable"):
                seal_attempt(path, {"claim": "changed observation"})
            self.assertEqual(file_hash(path), first)

    def test_identity_conflicts_do_not_silently_overwrite(self):
        self.assertEqual(merge_records([[{"id": "c1"}], [{"id": "c1"}]]), [{"id": "c1"}])
        with self.assertRaisesRegex(ValueError, "conflicting"):
            merge_records([[{"id": "c1", "v": 1}], [{"id": "c1", "v": 2}]])

    def test_write_through_a_symlink_keeps_the_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "data" / "dashboard.json"
            target.parent.mkdir()
            target.write_text("{}", encoding="utf-8")
            link = Path(tmp) / "code" / "dashboard.json"
            link.parent.mkdir()
            link.symlink_to(target)
            write_json(link, {"runs": 2})
            self.assertTrue(link.is_symlink())
            self.assertEqual(json.loads(target.read_text()), {"runs": 2})

    def test_invalid_json_preserves_previous_canonical_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.json"
            write_json(path, {"status": "complete"})
            with self.assertRaises(ValueError):
                write_json(path, {"value": float("nan")})
            self.assertEqual(json.loads(path.read_text()), {"status": "complete"})


if __name__ == "__main__":
    unittest.main()
