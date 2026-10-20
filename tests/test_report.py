"""Synthetic report integrity checks; no market research."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures
from scripts import app
from scripts.validate import validate_run


class ReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def prepared_run(self, run_id="synthetic-run"):
        candidate_id = fixtures.start_run(self.root, run_id=run_id)
        app.decide(run_id, candidate_id, "deferred", "synthetic evidence insufficient", "root-agent", root=self.root)
        app.report(run_id, root=self.root)
        return candidate_id, self.root / "runs" / run_id

    def test_report_requires_written_narrative(self):
        _, folder = self.prepared_run()
        errors = validate_run(folder, final=True)["errors"]
        self.assertTrue(any("narrative" in error for error in errors), errors)
        fixtures.write_narrative(folder)
        checked = validate_run(folder, final=True)
        self.assertTrue(checked["valid"], checked["errors"])

    def test_unknown_claim_and_attempt_references_are_caught(self):
        _, folder = self.prepared_run("synthetic-refs")
        fixtures.write_narrative(folder, "Ссылка на claim:ghost-claim и attempt:work/ghost/attempt-1/result.json")
        errors = validate_run(folder, final=True)["errors"]
        self.assertTrue(any("unknown claim" in error for error in errors), errors)
        self.assertTrue(any("unknown attempt" in error for error in errors), errors)

    def test_stale_machine_section_is_caught(self):
        _, folder = self.prepared_run("synthetic-stale")
        fixtures.write_narrative(folder)
        self.assertTrue(validate_run(folder, final=True)["valid"])
        fixtures.discover(self.root, "synthetic-stale", "synthetic-cand-2")
        errors = validate_run(folder, final=True)["errors"]
        self.assertTrue(any("stale" in error for error in errors), errors)

    def test_report_preserves_narrative_when_regenerated(self):
        _, folder = self.prepared_run("synthetic-keep")
        fixtures.write_narrative(folder, "Сохранённый текст нарратива для проверки.")
        app.report("synthetic-keep", root=self.root)
        self.assertIn("Сохранённый текст нарратива", (folder / "report.md").read_text(encoding="utf-8"))

    def test_human_readable_sections_and_compact_evidence_summary(self):
        _, folder = self.prepared_run("synthetic-readable")
        text = (folder / "report.md").read_text(encoding="utf-8")
        for section in ("Что получили", "Состояние исследования", "Лучшие кандидаты", "Как выглядит бизнес",
                        "Покупатель и момент запуска", "Почему интересен", "Существующие расходы",
                        "Боль и частота", "Конкуренция", "Дистрибуция", "Реализуемость",
                        "Главная неизвестная", "Приоритет исследования", "Коммерческий статус",
                        "Самая дешёвая решающая проверка"):
            self.assertIn(section, text)
        self.assertIn("[реестр доказательств](claims.jsonl)", text)
        self.assertNotIn("| ID | Кандидат | Автор |", text)

    def test_report_separates_execution_blockers_from_no_data(self):
        candidate_id = fixtures.start_run(self.root, run_id="synthetic-health")
        queued = app.queue("synthetic-health", candidate_id, "money", {}, root=self.root)
        app.bind("synthetic-health", queued["assignment_id"], fixtures.WORKERS["money"], root=self.root)
        app.cancel("synthetic-health", queued["assignment_id"], "synthetic worker failure", "execution", root=self.root)
        app.decide("synthetic-health", candidate_id, "deferred", "execution incomplete", "root-agent", root=self.root)
        app.report("synthetic-health", root=self.root)
        text = (self.root / "runs" / "synthetic-health" / "report.md").read_text(encoding="utf-8")
        self.assertIn("не завершён из-за технических сбоев", text)
        self.assertIn("Технические блокеры", text)
        self.assertIn(queued["assignment_id"], text)


if __name__ == "__main__":
    unittest.main()
