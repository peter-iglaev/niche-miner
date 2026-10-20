"""Стадия 3 — отсев: три бинарные проверки по наблюдаемым сигналам, без модели.

money     — кто-то платит человеку за эту работу (Upwork через MCP-мост, Freelancer напрямую);
software  — кто-то платит за софт в этой среде (маркетплейсы: платные листинги с пользователями);
reachable — можно ли туда попасть (Brave: сколько брендов в топ-5 выдачи).
Плюс hn — голос практиков (не гейт). Неизвестное остаётся неизвестным: если мост не исполнен,
кластер помечается pending, а не проваленным.
"""
from __future__ import annotations

import re
import statistics
from datetime import datetime, timedelta, timezone

from scripts.env import load_env
from scripts.pipeline import bridge
from scripts.pipeline.connectors import freelancer, hn, marketplaces, serp
from scripts.pipeline.run import finish_stage, read_stage_jsonl, write_stage_jsonl

STOP = {"the", "and", "for", "with", "how", "what", "best", "free", "tool", "tools", "software", "app", "apps",
        "online", "service", "services", "to", "of", "in", "a", "an", "vs", "alternative", "alternatives"}


def key_words(phrase, limit=3):
    words = [w for w in re.findall(r"[a-z0-9+#.]+", (phrase or "").lower()) if w not in STOP and len(w) > 1]
    words.sort(key=lambda w: (-len(w), w))
    ordered = [w for w in re.findall(r"[a-z0-9+#.]+", (phrase or "").lower()) if w in words[:limit]]
    return ordered[:limit]


def _title_matches(title, words):
    haystack = (title or "").lower()
    hits = sum(1 for w in words if w in haystack)
    return words and hits >= max(1, (len(words) + 1) // 2)


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("amount", "value", "max", "maximum", "fixed", "budget", "rawValue"):
            if key in value:
                got = _number(value[key])
                if got is not None:
                    return got
    if isinstance(value, str):
        match = re.search(r"\d[\d,]*(?:\.\d+)?", value)
        if match:
            try:
                return float(match.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


def parse_upwork(payload, *, window_days=90):
    """Терпимый разбор ответа Upwork MCP: список заказов с бюджетами, откликами и историей клиента."""
    jobs = []
    items = None
    if isinstance(payload, dict):
        for key in ("jobs", "results", "items", "data"):
            if isinstance(payload.get(key), list):
                items = payload[key]; break
            if isinstance(payload.get(key), dict):
                for inner in ("jobs", "results", "items"):
                    if isinstance(payload[key].get(inner), list):
                        items = payload[key][inner]; break
            if items is not None:
                break
    elif isinstance(payload, list):
        items = payload
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    for item in items or []:
        if not isinstance(item, dict):
            continue
        budget = None
        for key in ("budget", "fixed_budget", "amount", "budget_amount", "price"):
            budget = _number(item.get(key))
            if budget is not None:
                break
        if budget is None and isinstance(item.get("hourly_rate"), (dict, str, int, float)):
            budget = None
        posted = None
        for key in ("published_date", "created_date", "posted_on", "posted_at", "created_at", "published_at", "created", "date", "posted"):
            raw = item.get(key)
            if isinstance(raw, str) and len(raw) >= 10:
                try:
                    posted = datetime.fromisoformat(raw.replace("Z", "+00:00")[:25]).astimezone(timezone.utc)
                except ValueError:
                    posted = None
                if posted:
                    break
        client = item.get("client") if isinstance(item.get("client"), dict) else {}
        jobs.append({"title": item.get("title"), "budget_usd": budget, "type": item.get("job_type") or item.get("type"),
                     "proposals": item.get("proposals_tier") or item.get("proposals"), "posted": posted.strftime("%Y-%m-%d") if posted else None,
                     "in_window": (posted >= cutoff) if posted else None,
                     "client_spent": _number(client.get("total_spent")), "client_jobs": _number(client.get("total_posted_jobs")),
                     "url": item.get("url") or item.get("link")})
    return jobs


def _money(cluster, folder, config, root, run_id, *, use_upwork=True):
    """use_upwork=False — не ставить запрос в мост: Upwork дорог (ручной вызов MCP),
    и спрашивать его стоит только когда ответ ещё может изменить вердикт."""
    settings = config.get("screen") or {}
    up = settings.get("upwork") or {}
    result = {"upwork": {"status": "disabled" if up.get("enabled", True) else "disabled"},
              "freelancer": {"status": "disabled"}}
    words = key_words(cluster["head_phrase"])
    if up.get("enabled", True) and not use_upwork:
        result["upwork"] = {"status": "not_requested"}
    if up.get("enabled", True) and use_upwork:
        env = load_env(root)
        org = env.get("UPWORK_ORG_UID", "").strip()
        args = {"action": "search", "params": {"title": " ".join(words), "job_type": "fixed",
                                               "budget_min": int(up.get("min_budget") or 100), "sort": "recency"}}
        if org:
            args["org_uid"] = org
        request = bridge.plan(folder, "upwork.find_jobs", args, purpose=f"оплата за работу: {cluster['cluster_id']}", stage="screen")
        payload = bridge.response(folder, request)
        if payload is None:
            result["upwork"] = {"status": "pending_bridge" if request["status"] == "pending" else "error", "request_id": request["id"]}
        else:
            jobs = parse_upwork(payload, window_days=int(up.get("window_days") or 90))
            counted = [j for j in jobs if j["in_window"] is not False and (j["budget_usd"] or 0) >= (up.get("min_budget") or 0)]
            budgets = [j["budget_usd"] for j in counted if j["budget_usd"]]
            result["upwork"] = {"status": "collected", "jobs": len(counted), "jobs_total": len(jobs),
                                "median_budget": statistics.median(budgets) if budgets else None,
                                "max_budget": max(budgets) if budgets else None,
                                "clients_spent_5k": sum(1 for j in counted if (j["client_spent"] or 0) >= 5000),
                                "sample": counted[:5], "source_id": request["source_id"]}
    if (settings.get("freelancer") or {}).get("enabled", True):
        out = freelancer.search(run_id, cluster["head_phrase"], root=root)
        matched = [j for j in out["jobs"] if _title_matches(j["title"], words)]
        budgets = [j["budget_max_usd"] or j["budget_min_usd"] for j in matched if (j["budget_max_usd"] or j["budget_min_usd"])]
        result["freelancer"] = {"status": out["status"], "jobs": len(matched), "jobs_total": len(out["jobs"]),
                                "median_budget": statistics.median(budgets) if budgets else None,
                                "max_budget": max(budgets) if budgets else None, "sample": matched[:5], "source_id": out["source_id"]}
    return result


def _software(cluster, config, root, run_id):
    settings = (config.get("screen") or {}).get("marketplaces") or {}
    out = {"status": "disabled", "sources": {}}
    if not settings.get("enabled", True):
        return out
    listings, source_ids = [], []
    for name in settings.get("sources") or []:
        fn = marketplaces.PROVIDERS.get(name)
        if not fn:
            continue
        got = fn(run_id, cluster["head_phrase"], root=root)
        words = key_words(cluster["head_phrase"])
        matched = [l for l in got["listings"] if _title_matches(l["name"], words)]
        out["sources"][name] = {"status": got["status"], "total": got.get("total"), "matched": len(matched),
                                "source_id": got.get("source_id")}
        listings.extend(matched)
        source_ids.extend(got.get("source_ids") or [got.get("source_id")])
    two_years = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y-%m-%d")
    paid = [l for l in listings if l.get("paid") is True and (l.get("users") or 0) > 0]
    active = [l for l in listings if (l.get("users") or 0) > 0 and str(l.get("updated") or "9999") >= two_years]
    out.update(status="collected", listings=len(listings), paid_with_users=len(paid), active_recent=len(active),
               gap=len(active) < 3, top=sorted(listings, key=lambda l: -(l.get("users") or 0))[:8], source_ids=[s for s in source_ids if s])
    return out


def _reachable(cluster, config, root, run_id):
    settings = (config.get("screen") or {}).get("serp") or {}
    if not settings.get("enabled", True):
        return {"status": "disabled"}
    phrases = [cluster["head_phrase"]]
    for phrase in cluster["phrases"]:
        if len(phrases) >= int(settings.get("phrases_per_cluster") or 2):
            break
        if phrase != cluster["head_phrase"] and any(w in phrase.lower() for w in ("tool", "software", "alternative", "checker", "calculator", "template")):
            phrases.append(phrase)
    if len(phrases) < int(settings.get("phrases_per_cluster") or 2) and len(cluster["phrases"]) > 1:
        phrases.append(next(p for p in cluster["phrases"] if p != cluster["head_phrase"]))
    rows, source_ids = [], []
    for phrase in phrases:
        got = serp.top(run_id, phrase, provider=settings.get("provider", "brave"),
                       country=(config.get("measure") or {}).get("country", "US"), language=(config.get("measure") or {}).get("language", "en"), root=root)
        rows.append({"phrase": phrase, "status": got["status"], "brands_top5": got["brands_top5"], "kinds_top5": got["kinds_top5"],
                     "top": [{"rank": r["rank"], "domain": r["domain"], "kind": r["kind"], "url": r["url"]} for r in got["results"][:10]],
                     "source_id": got["source_id"], "error": got.get("error")})
        if got["source_id"]:
            source_ids.append(got["source_id"])
    collected = [r for r in rows if r["status"] == "collected"]
    return {"status": "collected" if collected else "error", "phrases": rows,
            "brands_top5_max": max((r["brands_top5"] for r in collected), default=None),
            "forums_present": any(k in ("forum", "video") for r in collected for k in r["kinds_top5"]),
            "source_ids": source_ids}


def evaluate(signals, config):
    """Бинарные проверки и итог. Неизвестное (мост не исполнен) → pending, а не fail."""
    settings = config.get("screen") or {}
    up, fl = settings.get("upwork") or {}, {}
    checks = {}
    upwork, freelance = signals["money"]["upwork"], signals["money"]["freelancer"]
    u_ok = upwork.get("status") == "collected" and upwork.get("jobs", 0) >= int(up.get("min_jobs") or 3)
    f_ok = freelance.get("status") == "collected" and freelance.get("jobs", 0) >= int(up.get("min_jobs") or 3) \
        and (freelance.get("max_budget") or 0) >= (up.get("min_budget") or 0)
    if u_ok or f_ok:
        # достаточно одной площадки: обе показывают одно и то же — за работу платят
        checks["money"] = True
    elif upwork.get("status") in ("pending_bridge", "not_requested"):
        checks["money"] = None
    else:
        checks["money"] = False
    software = signals["software"]
    checks["software"] = None if software.get("status") not in ("collected",) else bool(software.get("paid_with_users") or software.get("gap"))
    reach = signals["reachable"]
    threshold = int(((settings.get("serp") or {}).get("max_brands_top5")) or 2)
    checks["reachable"] = None if reach.get("status") != "collected" else bool((reach.get("brands_top5_max") or 0) <= threshold)
    required = settings.get("require") or ["money", "reachable"]
    verdicts = [checks.get(name) for name in required]
    if any(v is False for v in verdicts):
        # проваленная обязательная проверка решает исход: ждать остальные сигналы незачем
        status = "failed"
    elif any(v is None for v in verdicts):
        status = "pending"
    else:
        status = "passed"
    failed = [name for name in required if checks.get(name) is False]
    return {"status": status, "checks": checks, "required": required, "failed": failed}


def run(folder, config, *, root, refresh=False):
    """refresh=True — пересобрать сигналы для всех кластеров (после смены парсера или конфига); иначе
    кластеры с готовым вердиктом переиспользуются, а у ожидающих мост досчитываются только деньги."""
    run_id = folder.name
    clusters = read_stage_jsonl(folder, "cluster", "clusters.jsonl")
    if not clusters:
        raise ValueError("stage cluster produced no clusters; run cluster first")
    previous = {} if refresh else {c["cluster_id"]: c for c in read_stage_jsonl(folder, "screen", "screened.jsonl")}
    out, pending = [], []
    for cluster in clusters:
        prior = previous.get(cluster["cluster_id"])
        if prior and prior["screen"]["status"] in ("passed", "failed"):
            out.append(prior); continue
        signals = {"money": _money(cluster, folder, config, root, run_id, use_upwork=False)}
        if prior and prior.get("signals", {}).get("software", {}).get("status") == "collected":
            signals["software"] = prior["signals"]["software"]
            signals["reachable"] = prior["signals"]["reachable"]
            signals["hn"] = prior["signals"].get("hn")
        else:
            signals["software"] = _software(cluster, config, root, run_id)
            signals["reachable"] = _reachable(cluster, config, root, run_id)
            signals["hn"] = hn.practitioners(run_id, cluster["head_phrase"], root=root) if (config.get("screen") or {}).get("hn", {}).get("enabled", True) else {"status": "disabled"}
        verdict = evaluate(signals, config)
        if verdict["status"] == "pending" and verdict["checks"].get("money") is None:
            # только здесь Upwork ещё может изменить исход — ставим запрос в мост
            signals["money"] = _money(cluster, folder, config, root, run_id)
            verdict = evaluate(signals, config)
        if verdict["status"] == "pending":
            pending.append(cluster["cluster_id"])
        out.append({**cluster, "signals": signals, "screen": verdict})
    write_stage_jsonl(folder, "screen", "screened.jsonl", out)
    wanted = {(c.get("signals", {}).get("money", {}).get("upwork") or {}).get("request_id")
              for c in out if c["screen"]["status"] == "pending"}
    cancelled = bridge.cancel_unneeded(folder, "screen", {i for i in wanted if i})
    counts = {s: sum(1 for c in out if c["screen"]["status"] == s) for s in ("passed", "failed", "pending")}
    summary = {"clusters": len(out), **counts, "bridge_cancelled": len(cancelled),
               "bridge_pending": [r["id"] for r in bridge.pending(folder, "screen")],
               "failed_by": {name: sum(1 for c in out if name in c["screen"]["failed"]) for name in ("money", "software", "reachable")}}
    finish_stage(folder, "screen", summary, status="pending_bridge" if pending else "done")
    return summary


def summary_text(summary):
    text = f"прошли {summary['passed']}, отпали {summary['failed']} из {summary['clusters']}"
    if summary.get("pending"):
        text += f"; ждут моста Upwork: {summary['pending']}"
    return text
