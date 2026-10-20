"""Бесплатные autocomplete-эндпоинты: реальные формулировки спроса без чисел и без ключей."""
from __future__ import annotations

from scripts.pipeline.connectors.http import get_json, quote

NOTE = "Autocomplete suggestions show phrasing people type; they carry no volume and no payment signal."


def google(run_id, phrase, *, country="us", language="en", youtube=False, pause_seconds=1.2, root=None, who=None):
    url = ("https://suggestqueries.google.com/complete/search?client=firefox"
           f"&hl={quote(language)}&gl={quote(country)}&q={quote(phrase)}" + ("&ds=yt" if youtube else ""))
    record, parsed = get_json(run_id, "youtube_suggest" if youtube else "google_suggest", url, query=phrase,
                              limitations=NOTE, pause_seconds=pause_seconds, root=root, who=who)
    phrases = []
    if isinstance(parsed, list) and len(parsed) > 1 and isinstance(parsed[1], list):
        phrases = [str(item) for item in parsed[1] if isinstance(item, str)]
    return {"source_id": record["source_id"], "status": record["retrieval_status"], "phrases": phrases}


def amazon(run_id, phrase, *, pause_seconds=1.2, root=None, who=None):
    url = f"https://completion.amazon.com/api/2017/suggestions?mid=ATVPDKIKX0DER&alias=aps&prefix={quote(phrase)}"
    record, parsed = get_json(run_id, "amazon_suggest", url, query=phrase, limitations=NOTE,
                              pause_seconds=pause_seconds, root=root, who=who)
    phrases = []
    if isinstance(parsed, dict):
        phrases = [s.get("value") for s in parsed.get("suggestions") or [] if isinstance(s, dict) and s.get("value")]
    return {"source_id": record["source_id"], "status": record["retrieval_status"], "phrases": phrases}


def shopify(run_id, phrase, *, pause_seconds=1.2, root=None, who=None):
    url = f"https://apps.shopify.com/search/autocomplete?q={quote(phrase)}"
    record, parsed = get_json(run_id, "shopify_suggest", url, query=phrase, limitations=NOTE,
                              headers={"Accept": "application/json"}, pause_seconds=pause_seconds, root=root, who=who)
    phrases = []
    if isinstance(parsed, dict):
        for key in ("searches", "apps", "category_features", "collections", "taxonomy"):
            for item in parsed.get(key) or []:
                name = item.get("name") if isinstance(item, dict) else None
                if name:
                    phrases.append(str(name))
    return {"source_id": record["source_id"], "status": record["retrieval_status"], "phrases": phrases}


PROVIDERS = {
    "google": lambda run_id, phrase, cfg, root, who: google(run_id, phrase, country=cfg.get("country", "us"),
                                                             language=cfg.get("language", "en"),
                                                             pause_seconds=cfg.get("pause_seconds", 1.2), root=root, who=who),
    "youtube": lambda run_id, phrase, cfg, root, who: google(run_id, phrase, country=cfg.get("country", "us"),
                                                              language=cfg.get("language", "en"), youtube=True,
                                                              pause_seconds=cfg.get("pause_seconds", 1.2), root=root, who=who),
    "amazon": lambda run_id, phrase, cfg, root, who: amazon(run_id, phrase, pause_seconds=cfg.get("pause_seconds", 1.2), root=root, who=who),
    "shopify": lambda run_id, phrase, cfg, root, who: shopify(run_id, phrase, pause_seconds=cfg.get("pause_seconds", 1.2), root=root, who=who),
}
