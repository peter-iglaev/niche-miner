"""Hacker News Algolia: голос практиков. Ноль совпадений — пробел выборки, не отсутствие спроса."""
from __future__ import annotations

from scripts import sources

TEMPLATES = ('"is there a tool" {q}', '"I would pay for" {q}', '{q}')


def practitioners(run_id, phrase, *, root=None, who=None):
    hits, source_ids, errors = 0, [], []
    for template in TEMPLATES:
        query = template.format(q=phrase)
        record = sources.hn_search(run_id, query, tags="comment", limit=20, root=root or sources.ROOT, **(who or {}))
        source_ids.append(record["source_id"])
        if record["retrieval_status"] != "collected":
            errors.append(record.get("error"))
            continue
        path = sources.sources_dir(run_id, root or sources.ROOT).parent / record["archive_path"]
        try:
            import json
            hits += int(json.loads(path.read_text(encoding="utf-8")).get("nbHits") or 0)
        except (ValueError, OSError):
            errors.append("unparsable HN response")
    return {"hits": hits, "source_ids": source_ids, "status": "collected" if not errors else "partial",
            "errors": [e for e in errors if e]}
