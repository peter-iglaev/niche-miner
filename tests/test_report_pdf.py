"""Offline PDF regression tests, synthetic text only."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.report_pdf import render_report_pdf, validate_report_pdf, record_pdf_review


class PdfTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        (self.folder / "report.md").write_text(
            "# Исследование узких рабочих процессов\n\n## Краткий итог\n\n"
            "Русский текст: ёжик, расходы, покупатель, дистрибуция, ещё, ЁЛКА.\n\n"
            "- Первая проверка: неизвестно, кто платит.\n- Вторая проверка: частота работы.\n\n"
            "| Кандидат | Главная неизвестная |\n|---|---|\n"
            "| Сверка накладных | " + "Проверить существующие расходы и доступ к покупателям. " * 20 + "|\n",
            encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_russian_unicode_embedded_font_and_all_pages_render(self):
        from pypdf import PdfReader
        metadata = render_report_pdf(self.folder)
        pdf = PdfReader(self.folder / "report.pdf")
        text = "".join(page.extract_text() for page in pdf.pages)
        for word in ("Исследование", "ёжик", "ЁЛКА", "дистрибуция", "неизвестная"):
            self.assertIn(word, text)
        self.assertEqual(metadata["page_count"], len(metadata["previews"]))
        self.assertEqual([], validate_report_pdf(self.folder, require_review=False))
        self.assertTrue(any("visual review" in e for e in validate_report_pdf(self.folder)))
        record_pdf_review(self.folder, "synthetic-reviewer", "Synthetic review receipt only.")
        self.assertEqual([], validate_report_pdf(self.folder))

    def test_markdown_edit_invalidates_pdf_and_review(self):
        render_report_pdf(self.folder)
        record_pdf_review(self.folder, "synthetic-reviewer", "Synthetic review receipt only.")
        with (self.folder / "report.md").open("a", encoding="utf-8") as stream:
            stream.write("\nИзменение вывода.\n")
        self.assertTrue(any("stale" in e for e in validate_report_pdf(self.folder)))
        render_report_pdf(self.folder)
        self.assertTrue(any("visual review" in e for e in validate_report_pdf(self.folder)))

    def test_missing_or_modified_rendered_page_invalidates_review(self):
        meta = render_report_pdf(self.folder)
        (self.folder / meta["previews"][0]["path"]).write_bytes(b"modified preview")
        self.assertTrue(any("preview" in e for e in validate_report_pdf(self.folder)))
        with self.assertRaises(ValueError):
            record_pdf_review(self.folder, "synthetic-reviewer", "invalid")


    def test_the_receipt_records_the_pages_actually_inspected(self):
        """Поле утверждало намерение, а не факт: оно всегда было True."""
        from scripts.report_pdf import parse_pages
        meta = render_report_pdf(self.folder)
        total = meta["page_count"]
        partial = record_pdf_review(self.folder, "synthetic-reviewer", "смотрел только первую", pages="1")
        self.assertEqual(partial["pages_inspected"], [1])
        self.assertEqual(partial["all_pages_inspected"], total == 1)
        full = record_pdf_review(self.folder, "synthetic-reviewer", "смотрел все", pages=f"1-{total}")
        self.assertTrue(full["all_pages_inspected"])
        self.assertEqual(validate_report_pdf(self.folder), [])
        self.assertEqual(parse_pages("1,3-5", 9), [1, 3, 4, 5])
        with self.assertRaises(ValueError):
            parse_pages("1-99", 3)

    def test_mojibake_is_rejected(self):
        for text in ("Русский отчёт \u00d0\u0178\u00d1\u20ac\u00d0\u00b8\u00d0\u00b2\u00d0\u00b5\u00d1\u201a",
                     "Русский отчёт РџРѕРєСѓ", "Русский отчёт \ufffd", "Русский отчёт \u25a1"):
            with self.subTest(text=text):
                (self.folder / "report.md").write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    render_report_pdf(self.folder)

    def test_a_glyph_the_font_lacks_is_substituted_and_recorded(self):
        """Один иероглиф от модели не должен ронять весь отчёт, но и молчать о нём нельзя."""
        (self.folder / "report.md").write_text("Русский отчёт: один\u957f-форматный исходник", encoding="utf-8")
        meta = render_report_pdf(self.folder)
        self.assertEqual(meta["glyph_substitutions"], {"\u957f": 1})
        # Исходный Markdown не трогается: замена живёт только в отрисовке.
        self.assertIn("\u957f", (self.folder / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
