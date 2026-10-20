"""Bing Webmaster Tools → Keyword Research: точные объёмы Bing. Бесплатно, нужен верифицированный сайт.

Ключи: BING_WEBMASTER_API_KEY. Формат ответа Bing документирован скупо; разбор защитный, сырой ответ
всегда архивируется. См. docs/sources-setup.md §5.
"""
from __future__ import annotations

from datetime import date, timedelta

from scripts import sources
from scripts.env import load_env
from scripts.pipeline.connectors.http import quote

BASE = "https://ssl.bing.com/webmaster/api.svc/json"
NOTE = "Bing volumes are exact but cover a 3-8% share of search; use proportions, not absolute market size."
VOLUME_KEYS = ("Broad", "Phrase", "Exact", "Impressions", "Volume", "Count", "SearchVolume")


def availability(root):
    env = load_env(root)
    if not env.get("BING_WEBMASTER_API_KEY", "").strip():
        return {"available": False, "reason": "not set: BING_WEBMASTER_API_KEY"}
    return {"available": True, "reason": None}


def _volume(item):
    if not isinstance(item, dict):
        return None
    for key in VOLUME_KEYS:
        value = item.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def keyword(run_id, phrase, *, country="us", language="en-US", root=None, who=None):
    root = root or sources.ROOT
    env = load_env(root)
    key = env.get("BING_WEBMASTER_API_KEY", "").strip()
    if not key:
        return {"status": "capability_unavailable", "error": "not set: BING_WEBMASTER_API_KEY", "volume": None, "related": [], "source_ids": []}
    end, start = date.today(), date.today() - timedelta(days=90)
    common = f"&country={quote(country)}&language={quote(language)}&startDate={start.isoformat()}&endDate={end.isoformat()}&apikey={quote(key)}"
    url = f"{BASE}/GetKeyword?q={quote(phrase)}{common}"
    record, parsed = _fetch(run_id, url, url.replace(quote(key), "<redacted>"), phrase, root, who)
    volume = _volume((parsed or {}).get("d") if isinstance(parsed, dict) else None)
    related_url = f"{BASE}/GetRelatedKeywords?q={quote(phrase)}{common}"
    record2, parsed2 = _fetch(run_id, related_url, related_url.replace(quote(key), "<redacted>"), phrase, root, who)
    related = []
    for item in ((parsed2 or {}).get("d") or []) if isinstance(parsed2, dict) else []:
        if isinstance(item, dict) and item.get("Query"):
            related.append({"phrase": item["Query"], "volume": _volume(item)})
    status = "collected" if record["retrieval_status"] == "collected" else "error"
    return {"status": status, "error": record.get("error"), "volume": volume, "related": related,
            "source_ids": [record["source_id"], record2["source_id"]], "note": "experimental parser; raw responses archived"}


def _fetch(run_id, real_url, safe_url, phrase, root, who):
    """Ключ уходит в запрос, но в архив попадает только URL с заглушкой вместо ключа."""
    from scripts.pipeline.connectors.http import _parse
    record = sources._capture(run_id, "bing_webmaster", safe_url, phrase, lambda: sources.http_get(real_url, {}),
                              limitations=NOTE, root=root, **(who or {}))
    return record, _parse(record, root)
