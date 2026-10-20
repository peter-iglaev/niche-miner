"""Offline Unicode PDF publishing and hash-bound, explicitly attested visual review."""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from scripts.artifacts import file_hash, write_json

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
CYRILLIC = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"


def report_stem(run_dir):
    """Основа имени отчёта в прогоне: «report-<id прогона>», если такой файл есть.

    Внутри `runs/<id>/` имя `report.pdf` однозначно, но файл уходит из прогона — в Telegram,
    в загрузки, в переписку, — и там три разных прогона выглядят одинаково. Имя с id прогона
    несёт и тему, и дату. Старые прогоны остаются с прежним именем и продолжают читаться.
    """
    run_dir = Path(run_dir)
    stem = f"report-{run_dir.name}"
    if (run_dir / f"{stem}.md").is_file() or not (run_dir / "report.md").is_file():
        return stem
    return "report"


def _text_errors(text):
    errors = []
    if not re.search(r"[А-Яа-яЁё]{3}", text):
        errors.append("report must contain Russian text")
    if "\ufffd" in text or re.search(r"[ÐÑ][\x80-\xbf]|(?:Р[\u0400-\u04ff]){3,}|[□■]", text):
        errors.append("report contains mojibake, replacement characters or missing-glyph squares")
    return errors


def _fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for name, filename in (("NicheSans", "DejaVuSans.ttf"), ("NicheSansBold", "DejaVuSans-Bold.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            font = TTFont(name, str(FONT_DIR / filename))
            missing = [c for c in CYRILLIC if not font.face.charToGlyph.get(ord(c))]
            if missing:
                raise ValueError("PDF font lacks Cyrillic glyphs: " + "".join(missing))
            pdfmetrics.registerFont(font)
    pdfmetrics.registerFontFamily("NicheSans", normal="NicheSans", bold="NicheSansBold", italic="NicheSans", boldItalic="NicheSansBold")


def _plain(text):
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.replace("**", "").replace("`", "").replace("\\|", "|")


def _markup(text):
    # Source strings are always escaped first; Markdown cannot inject ReportLab markup.
    # The only markup we add ourselves is <b> for **bold** spans.
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text).replace("`", "").replace("\\|", "|")
    text = html.escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def _column_widths(rows, total, font_size, padding=12.0):
    """Ширины колонок по содержимому: минимум — самое длинное слово колонки (чтобы слова не рвались),
    остаток делится пропорционально самой длинной строке колонки."""
    from reportlab.pdfbase import pdfmetrics
    count = len(rows[0])
    minimum, preferred = [0.0] * count, [0.0] * count
    for row in rows:
        for i, part in enumerate(row):
            plain = _plain(part)
            for word in plain.split():
                minimum[i] = max(minimum[i], pdfmetrics.stringWidth(word, "NicheSansBold", font_size) + padding)
            preferred[i] = max(preferred[i], pdfmetrics.stringWidth(plain, "NicheSansBold", font_size) + padding)
    minimum = [max(m, padding + 6) for m in minimum]
    if sum(minimum) >= total:
        scale = total / sum(minimum)
        return [m * scale for m in minimum]
    spare = total - sum(minimum)
    wants = [max(0.0, p - m) for p, m in zip(preferred, minimum)]
    if sum(wants) <= 0:
        return [m + spare / count for m in minimum]
    share = min(1.0, spare / sum(wants))
    widths = [m + w * share for m, w in zip(minimum, wants)]
    leftover = total - sum(widths)
    return [w + leftover / count for w in widths]


def _story(text):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    ink = colors.HexColor("#243447")
    navy = colors.HexColor("#173B57")
    accent = colors.HexColor("#287AA9")
    base = ParagraphStyle("body", fontName="NicheSans", fontSize=9.7, leading=14.2, spaceAfter=6,
                          textColor=ink, splitLongWords=True, allowWidows=False, allowOrphans=False)
    styles = {level: ParagraphStyle("h" + str(level), parent=base, fontName="NicheSansBold",
              fontSize={1: 22, 2: 15, 3: 12}.get(level, 11), leading={1: 27, 2: 19, 3: 16}.get(level, 15),
              textColor=navy if level <= 2 else accent, spaceBefore=13, spaceAfter=7, keepWithNext=True) for level in range(1, 7)}
    # Words are never broken mid-word inside cells: a broken word is unreadable and fails text extraction.
    cell = ParagraphStyle("cell", parent=base, fontSize=8, leading=11, spaceAfter=0, splitLongWords=False)
    header_cell = ParagraphStyle("header-cell", parent=cell, fontName="NicheSansBold", textColor=colors.white)
    result, lines, index = [], text.splitlines(), 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("<!--"):
            continue
        if line.startswith("|"):
            rows = [line]
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(lines[index].strip()); index += 1
            parsed = [[part.strip() for part in re.split(r"(?<!\\)\|", row.strip("|"))] for row in rows]
            parsed = [row for row in parsed if not all(re.fullmatch(r"[:\- ]+", part) for part in row)]
            if not parsed:
                continue
            count = max(map(len, parsed))
            parsed = [row + [""] * (count - len(row)) for row in parsed]
            widths = _column_widths(parsed, 483, cell.fontSize)
            data = [[Paragraph(_markup(part), header_cell if row_index == 0 else cell) for part in row]
                    for row_index, row in enumerate(parsed)]
            table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitInRow=1)
            table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "NicheSansBold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F6F8")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), .35, colors.HexColor("#c8d3db")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
            result.extend([table, Spacer(1, 9)])
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading:
            result.append(Paragraph(_markup(heading[2]), styles[len(heading[1])]))
        elif re.match(r"^(?:[-*]|\d+\.)\s+", line):
            content = re.sub(r"^(?:[-*]|\d+\.)\s+", "", line)
            result.append(Paragraph("- " + _markup(content), base))
        else:
            result.append(Paragraph(_markup(line), base))
    return result


def _pdf_checks(path, markdown):
    from pypdf import PdfReader
    reader = PdfReader(path)
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    errors = _text_errors(extracted)
    # Every Russian word must survive extraction: font mapping alone is insufficient.
    words = set(re.findall(r"[А-Яа-яЁё]+", markdown))
    missing = words - set(re.findall(r"[А-Яа-яЁё]+", extracted))
    if missing:
        errors.append("PDF lost Russian words: " + ", ".join(sorted(missing)[:8]))
    embedded = False
    for page in reader.pages:
        for reference in page["/Resources"].get("/Font", {}).values():
            font = reference.get_object()
            if "DejaVuSans" in str(font.get("/BaseFont", "")):
                descriptor = font.get("/FontDescriptor")
                if not descriptor or not descriptor.get_object().get("/FontFile2") or not font.get("/ToUnicode"):
                    errors.append("PDF Cyrillic font must be embedded with a Unicode map")
                else:
                    embedded = True
    if not embedded:
        errors.append("PDF has no embedded Cyrillic font")
    return errors, len(reader.pages)


def render_report_pdf(run_dir, *, stem=None):
    """Generate PDF and PNG previews. Never records a visual-review attestation."""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate
    run_dir = Path(run_dir)
    stem = stem or report_stem(run_dir)
    markdown = (run_dir / f"{stem}.md").read_text(encoding="utf-8")
    errors = _text_errors(markdown)
    if errors:
        raise ValueError("; ".join(errors))
    executable = shutil.which("pdftoppm")
    if not executable:
        raise ValueError("pdftoppm (Poppler) is required for report PDF visual verification")
    _fonts()
    # Шрифт покрывает латиницу и кириллицу. Модель изредка вставляет в русский текст иероглиф
    # (DeepSeek написал «один长-форматный исходник» вместо «длинноформатный»), и раньше весь
    # отчёт из-за одного символа не собирался. Молча подставлять квадрат тоже нельзя, поэтому
    # такие символы заменяются в рендере на «?», исходный Markdown остаётся нетронутым,
    # а факт замены попадает в метаданные PDF и в проверку.
    from reportlab.pdfbase import pdfmetrics
    glyphs = pdfmetrics.getFont("NicheSans").face.charToGlyph
    missing = sorted({c for c in markdown if not c.isspace() and ord(c) >= 32 and not glyphs.get(ord(c))})
    substitutions = {}
    if missing:
        substitutions = {c: markdown.count(c) for c in missing}
        markdown = markdown.translate({ord(c): "?" for c in missing})
    with tempfile.TemporaryDirectory(prefix=".pdf-build-", dir=run_dir) as temporary:
        temp = Path(temporary)
        path = temp / f"{stem}.pdf"
        def footer(canvas, document):
            canvas.setStrokeColorRGB(0.78, 0.84, 0.88)
            canvas.line(56, 39, A4[0] - 56, 39)
            canvas.setFont("NicheSans", 8)
            canvas.setFillColorRGB(0.25, 0.33, 0.39)
            canvas.drawString(56, 28, "Money-First Niche Miner")
            canvas.drawRightString(A4[0] - 56, 28, "Страница " + str(document.page))
        document = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=56, rightMargin=56,
            topMargin=40, bottomMargin=48, title="Отчёт по исследованию ниш", author="Money-First Niche Miner")
        document.build(_story(markdown), onFirstPage=footer, onLaterPages=footer)
        errors, pages = _pdf_checks(path, markdown)
        if errors:
            raise ValueError("; ".join(errors))
        subprocess.run([executable, "-r", "100", "-png", str(path), str(temp / "page")],
                       check=True, capture_output=True, timeout=60)
        previews = sorted(temp.glob("page-*.png"))
        if len(previews) != pages or any(p.stat().st_size < 100 for p in previews):
            raise ValueError("PDF page rendering incomplete")
        target = run_dir / f"{stem}-preview"
        target.mkdir(exist_ok=True)
        for old in target.glob("page-*.png"):
            old.unlink()
        for preview in previews:
            shutil.copyfile(preview, target / preview.name)
        path.replace(run_dir / f"{stem}.pdf")
    metadata = {"version": 1, "stem": stem, "markdown_sha256": file_hash(run_dir / f"{stem}.md"),
                "pdf_sha256": file_hash(run_dir / f"{stem}.pdf"), "page_count": pages,
                "font": "embedded DejaVu Sans", "automated_checks": "passed",
                "glyph_substitutions": substitutions or None,
                "previews": [{"path": str(p.relative_to(run_dir)), "sha256": file_hash(p)}
                             for p in sorted(target.glob("page-*.png"))]}
    write_json(run_dir / f"{stem}.pdf.meta.json", metadata)
    return metadata


def validate_report_pdf(run_dir, *, require_review=True, stem=None):
    run_dir = Path(run_dir)
    stem = stem or report_stem(run_dir)
    needed = [f"{stem}.md", f"{stem}.pdf", f"{stem}.pdf.meta.json"]
    if any(not (run_dir / name).is_file() for name in needed):
        return ["report.pdf and its verification metadata are required before finalize"]
    try:
        metadata = json.loads((run_dir / f"{stem}.pdf.meta.json").read_text(encoding="utf-8"))
        errors = []
        if metadata.get("pdf_sha256") != file_hash(run_dir / f"{stem}.pdf"):
            errors.append("report.pdf hash mismatch; regenerate PDF")
        if metadata.get("markdown_sha256") != file_hash(run_dir / f"{stem}.md"):
            errors.append("report.pdf is stale relative to report.md; regenerate PDF")
        checked, pages = _pdf_checks(run_dir / f"{stem}.pdf", (run_dir / f"{stem}.md").read_text(encoding="utf-8"))
        errors.extend(checked)
        previews = metadata.get("previews", [])
        if len(previews) != pages or metadata.get("page_count") != pages:
            errors.append("PDF previews do not cover every page")
        for item in previews:
            path = (run_dir / item["path"]).resolve()
            if not path.is_relative_to((run_dir / f"{stem}-preview").resolve()) or not path.is_file() or file_hash(path) != item["sha256"]:
                errors.append("PDF preview missing, outside run, or modified")
        if require_review:
            receipt = run_dir / f"{stem}.pdf.review.json"
            review = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {}
            if (review.get("pdf_sha256") != metadata.get("pdf_sha256") or
                review.get("metadata_sha256") != file_hash(run_dir / f"{stem}.pdf.meta.json") or
                not str(review.get("reviewer_id", "")).strip() or not str(review.get("note", "")).strip() or
                review.get("all_pages_inspected") is not True):
                errors.append("report.pdf requires explicit visual review of all rendered pages for this PDF hash")
        return errors
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [f"invalid report PDF or metadata: {error}"]


def parse_pages(spec, total):
    """«1-17», «1,4,9-11» или пусто (= все) → отсортированный список номеров страниц."""
    if not spec or not str(spec).strip():
        return list(range(1, total + 1))
    pages = set()
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            first, _, last = part.partition("-")
            pages.update(range(int(first), int(last) + 1))
        else:
            pages.add(int(part))
    outside = sorted(p for p in pages if not 1 <= p <= total)
    if outside:
        raise ValueError(f"pages outside the document (1..{total}): " + ", ".join(map(str, outside)))
    return sorted(pages)


def record_pdf_review(run_dir, reviewer_id, note, *, stem=None, pages=None):
    """Root attests actual page inspection; this function cannot perform visual judgment.

    Раньше расписка писала all_pages_inspected=True независимо от того, что просмотрел
    оркестратор: поле утверждало не факт, а намерение. Теперь оно хранит список фактически
    просмотренных страниц, и «все» — это отдельный, явно записанный случай.
    """
    run_dir = Path(run_dir)
    if not reviewer_id.strip() or not note.strip():
        raise ValueError("PDF review requires reviewer_id and actual visual inspection notes")
    stem = stem or report_stem(run_dir)
    errors = validate_report_pdf(run_dir, require_review=False, stem=stem)
    if errors:
        raise ValueError("; ".join(errors))
    total = json.loads((run_dir / f"{stem}.pdf.meta.json").read_text(encoding="utf-8"))["page_count"]
    inspected = parse_pages(pages, total)
    receipt = {"pdf_sha256": file_hash(run_dir / f"{stem}.pdf"),
               "metadata_sha256": file_hash(run_dir / f"{stem}.pdf.meta.json"),
               "reviewer_id": reviewer_id, "note": note,
               "pages_inspected": inspected, "page_count": total,
               "all_pages_inspected": len(inspected) == total,
               "reviewed_at": datetime.now(timezone.utc).isoformat()}
    write_json(run_dir / f"{stem}.pdf.review.json", receipt)
    return receipt
