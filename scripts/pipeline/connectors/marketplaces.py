"""Публичные API маркетплейсов: платят ли за софт и сколько конкурентов в среде, где будет продукт.

Каждая функция возвращает {"source_id", "status", "listings": [...]}, где листинг нормализован:
name, url, users (число или None), paid (True/False/None), updated (дата или None), rating, reviews.
Установки — сигнал уже удовлетворённого спроса; гэп — это много подсказок и мало/старые результаты.
"""
from __future__ import annotations

import re

from scripts.pipeline.connectors.http import get_json, post_json, quote

NOTE = "Marketplace listings show what exists and how many use it; they do not show revenue."


def _date(value):
    """Дата листинга как ISO-строка YYYY-MM-DD; числа считаем epoch (мс или с); иначе None."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        from datetime import datetime, timezone
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)[:10]


def _listing(source, name, url, *, users=None, paid=None, updated=None, rating=None, reviews=None, extra=None):
    try:
        users = int(users) if users is not None and not isinstance(users, bool) else None
    except (TypeError, ValueError):
        users = None
    return {"source": source, "name": name, "url": url, "users": users if users is None or users >= 0 else None, "paid": paid,
            "updated": _date(updated), "rating": rating, "reviews": reviews, **({"extra": extra} if extra else {})}


def apify(run_id, phrase, *, limit=25, pause_seconds=0.8, root=None, who=None):
    url = f"https://api.apify.com/v2/store?search={quote(phrase)}&limit={limit}"
    record, parsed = get_json(run_id, "apify_store", url, query=phrase, limitations=NOTE, pause_seconds=pause_seconds, root=root, who=who)
    listings = []
    for item in ((parsed or {}).get("data") or {}).get("items") or []:
        stats = item.get("stats") or {}
        # магазин отдаёт текущую модель оплаты в currentPricingInfo.pricingModel: FREE | FLAT_PRICE_PER_MONTH | PRICE_PER_DATASET_ITEM | PAY_PER_EVENT
        model = (item.get("currentPricingInfo") or {}).get("pricingModel")
        paid = None if not model else model != "FREE"
        listings.append(_listing("apify", item.get("title") or item.get("name"),
                                 f"https://apify.com/{item.get('username')}/{item.get('name')}",
                                 users=stats.get("totalUsers30Days"), paid=paid, updated=(stats.get("lastRunStartedAt") or "")[:10] or None,
                                 rating=stats.get("actorReviewRating"), reviews=stats.get("actorReviewCount"),
                                 extra={"total_users": stats.get("totalUsers"), "total_runs": stats.get("totalRuns"), "pricing_model": model}))
    return {"source_id": record["source_id"], "status": record["retrieval_status"], "listings": listings,
            "total": ((parsed or {}).get("data") or {}).get("total")}


def wordpress(run_id, phrase, *, limit=25, pause_seconds=0.8, root=None, who=None):
    url = ("https://api.wordpress.org/plugins/info/1.2/?action=query_plugins"
           f"&request[search]={quote(phrase)}&request[per_page]={limit}&request[fields][description]=0&request[fields][sections]=0")
    record, parsed = get_json(run_id, "wordpress_plugins", url, query=phrase, limitations=NOTE, pause_seconds=pause_seconds, root=root, who=who)
    listings = []
    for item in (parsed or {}).get("plugins") or []:
        listings.append(_listing("wordpress", re.sub(r"<[^>]+>|&amp;", "&", str(item.get("name") or "")),
                                 f"https://wordpress.org/plugins/{item.get('slug')}/",
                                 users=item.get("active_installs"), paid=None, updated=(item.get("last_updated") or "")[:10] or None,
                                 rating=item.get("rating"), reviews=item.get("num_ratings")))
    return {"source_id": record["source_id"], "status": record["retrieval_status"], "listings": listings,
            "total": ((parsed or {}).get("info") or {}).get("results")}


def atlassian(run_id, phrase, *, limit=25, pause_seconds=0.8, root=None, who=None):
    url = f"https://marketplace.atlassian.com/rest/2/addons?text={quote(phrase)}&hosting=cloud&limit={limit}"
    record, parsed = get_json(run_id, "atlassian_marketplace", url, query=phrase, limitations=NOTE, pause_seconds=pause_seconds, root=root, who=who)
    listings = []
    for item in ((parsed or {}).get("_embedded") or {}).get("addons") or []:
        embedded = item.get("_embedded") or {}
        dist = embedded.get("distribution") or {}
        reviews = embedded.get("reviews") or {}
        listings.append(_listing("atlassian", item.get("name"), f"https://marketplace.atlassian.com/apps/{item.get('id')}",
                                 users=dist.get("totalInstalls"), paid=None, updated=(embedded.get("lastModified") or "")[:10] or None,
                                 rating=reviews.get("averageStars"), reviews=reviews.get("count")))
    return {"source_id": record["source_id"], "status": record["retrieval_status"], "listings": listings,
            "total": (parsed or {}).get("count")}


def vscode(run_id, phrase, *, limit=25, pause_seconds=0.8, root=None, who=None):
    url = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
    payload = {"filters": [{"criteria": [{"filterType": 8, "value": "Microsoft.VisualStudio.Code"},
                                          {"filterType": 10, "value": phrase}], "pageNumber": 1, "pageSize": limit}],
               "flags": 914}
    record, parsed = post_json(run_id, "vscode_marketplace", url, payload, query=phrase, limitations=NOTE,
                               headers={"Accept": "application/json;api-version=3.0-preview.1"}, pause_seconds=pause_seconds, root=root, who=who)
    listings = []
    for item in (((parsed or {}).get("results") or [{}])[0].get("extensions") or []):
        stats = {s.get("statisticName"): s.get("value") for s in item.get("statistics") or []}
        publisher = (item.get("publisher") or {}).get("publisherName")
        listings.append(_listing("vscode", item.get("displayName"),
                                 f"https://marketplace.visualstudio.com/items?itemName={publisher}.{item.get('extensionName')}",
                                 users=int(stats.get("install") or 0) or None, paid=False, updated=(item.get("lastUpdated") or "")[:10] or None,
                                 rating=stats.get("averagerating"), reviews=int(stats.get("ratingcount") or 0) or None))
    return {"source_id": record["source_id"], "status": record["retrieval_status"], "listings": listings,
            "total": next((m.get("count") for r in (parsed or {}).get("results") or [] for m in (r.get("resultMetadata") or [{}])[0].get("metadataItems", [])
                           if m.get("name") == "TotalCount"), None)}


_OBSIDIAN_CACHE = {}


def obsidian(run_id, phrase, *, limit=25, pause_seconds=0.8, root=None, who=None):
    """Каталог и статистика загрузок лежат в двух JSON-файлах на GitHub; тянем раз на прогон."""
    if run_id not in _OBSIDIAN_CACHE:
        plugins_url = "https://raw.githubusercontent.com/obsidianmd/obsidian-releases/HEAD/community-plugins.json"
        stats_url = "https://raw.githubusercontent.com/obsidianmd/obsidian-releases/HEAD/community-plugin-stats.json"
        rec1, plugins = get_json(run_id, "obsidian_plugins", plugins_url, limitations=NOTE, pause_seconds=pause_seconds, root=root, who=who)
        rec2, stats = get_json(run_id, "obsidian_plugins", stats_url, limitations=NOTE, pause_seconds=pause_seconds, root=root, who=who)
        _OBSIDIAN_CACHE[run_id] = (rec1, rec2, plugins or [], stats or {})
    rec1, rec2, plugins, stats = _OBSIDIAN_CACHE[run_id]
    words = [w for w in re.findall(r"[a-z0-9]+", phrase.lower()) if len(w) > 2]
    listings = []
    for plugin in plugins:
        haystack = f"{plugin.get('name', '')} {plugin.get('description', '')}".lower()
        if words and all(w in haystack for w in words):
            stat = stats.get(plugin.get("id")) or {}
            listings.append(_listing("obsidian", plugin.get("name"), f"https://github.com/{plugin.get('repo')}",
                                     users=stat.get("downloads"), paid=False, updated=stat.get("updated"),
                                     rating=None, reviews=None))
        if len(listings) >= limit:
            break
    status = "collected" if rec1["retrieval_status"] == "collected" and rec2["retrieval_status"] == "collected" else "error"
    return {"source_id": rec2["source_id"], "status": status, "listings": listings, "total": len(listings),
            "source_ids": [rec1["source_id"], rec2["source_id"]]}


def smithery(run_id, phrase, *, limit=25, pause_seconds=0.8, root=None, who=None):
    url = f"https://registry.smithery.ai/servers?q={quote(phrase)}&pageSize={limit}"
    record, parsed = get_json(run_id, "smithery_registry", url, query=phrase, limitations=NOTE, pause_seconds=pause_seconds, root=root, who=who)
    listings = []
    for item in (parsed or {}).get("servers") or []:
        listings.append(_listing("smithery", item.get("displayName") or item.get("qualifiedName"),
                                 f"https://smithery.ai/server/{item.get('qualifiedName')}",
                                 users=item.get("useCount"), paid=False, updated=(item.get("createdAt") or "")[:10] or None,
                                 rating=None, reviews=None, extra={"verified": item.get("verified")}))
    return {"source_id": record["source_id"], "status": record["retrieval_status"], "listings": listings,
            "total": (parsed or {}).get("totalCount")}


PROVIDERS = {"apify": apify, "wordpress": wordpress, "atlassian": atlassian, "vscode": vscode,
             "obsidian": obsidian, "smithery": smithery}
