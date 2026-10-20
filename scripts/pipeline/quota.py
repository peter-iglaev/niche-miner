"""Дневная квота отчётов Ubersuggest (бесплатный план: 3 отчёта в сутки).

Ledger лежит в backlog/quota.json рядом с бэклогом кластеров. Он ведётся по двум событиям:
- стадия measure «тратит» квоту, когда планирует запросы через мост;
- мост, получив от Ubersuggest ошибку «daily reports limit», помечает день исчерпанным,
  потому что квоту могли потратить и вне пайплайна (в другой сессии или в браузере).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scripts.artifacts import write_json
from scripts.pipeline.run import read_json

DEFAULT_LIMIT = 3
QUOTA_MARKERS = ("daily reports limit", "daily report quota", "quota is exhausted")


def path(root, config=None):
    backlog = Path(root) / ((config or {}).get("backlog") or {}).get("path", "backlog/clusters.jsonl")
    return backlog.parent / "quota.json"


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def limit(config=None):
    return int(((config or {}).get("measure") or {}).get("ubersuggest_daily_reports") or DEFAULT_LIMIT)


def state(root, config=None):
    data = read_json(path(root, config), {}) or {}
    if data.get("date") != today():
        return {"date": today(), "ubersuggest_used": 0, "exhausted": False}
    return {"date": data["date"], "ubersuggest_used": int(data.get("ubersuggest_used") or 0), "exhausted": bool(data.get("exhausted"))}


def left(root, config=None):
    """(осталось, использовано, дата)."""
    current = state(root, config)
    if current["exhausted"]:
        return 0, max(current["ubersuggest_used"], limit(config)), current["date"]
    return max(0, limit(config) - current["ubersuggest_used"]), current["ubersuggest_used"], current["date"]


def spend(root, count, config=None):
    current = state(root, config)
    current["ubersuggest_used"] += int(count)
    write_json(path(root, config), current)
    return current


def mark_exhausted(root, config=None, reason=None):
    current = state(root, config)
    current.update(exhausted=True, ubersuggest_used=max(current["ubersuggest_used"], limit(config)), reason=(reason or "")[:300])
    write_json(path(root, config), current)
    return current


def is_quota_error(text):
    low = (text or "").lower()
    return any(marker in low for marker in QUOTA_MARKERS)
