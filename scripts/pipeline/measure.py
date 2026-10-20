"""Стадия 4 — цифры: объём, CPC, конкуренция рекламодателей, тренд по фразам кластера.

Провайдеры по приоритету из конфига: Google Ads Keyword Planner (бесплатно, нужна настройка),
Bing Webmaster (нужен верифицированный сайт), Ubersuggest через MCP-мост (3 отчёта в день на free).
Свежие числа из бэклога переиспользуются. Отсутствие провайдера — честный статус not_collected.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from scripts.pipeline import backlog, bridge, quota
from scripts.pipeline.connectors import bing, google_ads
from scripts.pipeline.run import finish_stage, read_stage_jsonl, write_stage_jsonl


def quota_left(config, root):
    """(осталось, использовано, дата) — см. scripts/pipeline/quota.py."""
    return quota.left(root, config)


def _spend_quota(config, root, count):
    quota.spend(root, count, config)


def cluster_monthly(rows):
    """Помесячный ряд всего кластера: суммы по периодам всех фраз, у которых он есть.

    Ряд одной головной фразы обманчив: у фразы с объёмом 10 нули и десятки дают
    наклон в разы больше среднего, и кластер с настоящим спросом срезается гейтом.
    """
    totals = {}
    for row in rows or []:
        for point in row.get("monthly") or []:
            period, volume = point.get("period"), point.get("volume")
            if period and isinstance(volume, (int, float)):
                totals[period] = totals.get(period, 0) + volume
    return [{"period": period, "volume": totals[period]} for period in sorted(totals)] or None


def trend_slope(monthly, *, min_mean_volume=0):
    """Наклон помесячного ряда, нормированный на среднее: +0.5 = рост на половину среднего за период.

    На рядах с маленьким средним наклон — шум, поэтому ниже min_mean_volume тренд
    считается неизвестным (None), а не провалом.
    """
    values = [m.get("volume") if isinstance(m, dict) else m for m in monthly or []]
    values = [float(v) for v in values if isinstance(v, (int, float))]
    if len(values) < 3 or not any(values):
        return None
    if sum(values) / len(values) < float(min_mean_volume or 0):
        return None
    n = len(values)
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n)) or 1
    slope = num / den * (n - 1)
    return round(slope / mean_y, 3) if mean_y else None


def trend_yoy(monthly, *, min_mean_volume=0):
    """Год к году: последние 12 месяцев против предыдущих 12. −0.3 = спрос упал на треть.

    Наклон по одному 12-месячному окну не отделяет тренд от сезона: ряд, начинающийся осенью,
    всегда идёт вниз из-за ноябрьско-декабрьского провала. На контрольной выборке из десяти
    заведомо живых ниш такой наклон отбраковывал половину. Сравнение одинаковых месяцев
    сезонность вычитает целиком, поэтому решает гейт именно оно.

    Требуется 24 месяца; на коротком ряде честный ответ — None, а не ноль.
    """
    values = [m.get("volume") if isinstance(m, dict) else m for m in monthly or []]
    values = [float(v) for v in values if isinstance(v, (int, float))]
    if len(values) < 24:
        return None
    recent, earlier = values[-12:], values[-24:-12]
    if sum(recent) / 12 < float(min_mean_volume or 0) or not sum(earlier):
        return None
    return round(sum(recent) / sum(earlier) - 1, 3)


def volume_recent(monthly):
    """Средний месячный объём кластера за последние 12 месяцев ряда.

    `volume_sum` складывает средние по фразам, а провайдер считает своё среднее по всему
    запрошенному окну: на 24 месяцах растущая ниша выглядит меньше, чем она есть сегодня.
    Это число всегда про последний год, каким бы длинным ни было окно.
    """
    values = [m.get("volume") if isinstance(m, dict) else m for m in monthly or []]
    values = [float(v) for v in values if isinstance(v, (int, float))]
    if len(values) < 12:
        return None
    return int(round(sum(values[-12:]) / 12))


def top_share(volumes):
    """Доля самой крупной фразы в сумме кластера.

    Близкая к 1 доля означает, что «спрос кластера» — это одна фраза, и если она посторонняя,
    сумма обманывает. Проверка агентами ловила это вручную; теперь число видно в карточке.
    """
    total = sum(r["volume"] for r in volumes) if volumes else 0
    if not total:
        return None
    return round(max(r["volume"] for r in volumes) / total, 3)


def aggregate(rows, head_phrase, provider, source_ids, *, monthly=None, trend_min_mean=0):
    """Числа кластера из строк {phrase, volume, cpc, competition}."""
    volumes = [r for r in rows if isinstance(r.get("volume"), (int, float))]
    cpcs = [r["cpc"] for r in rows if isinstance(r.get("cpc"), (int, float)) and r["cpc"] > 0]
    lows = [r["cpc_low"] for r in rows if isinstance(r.get("cpc_low"), (int, float)) and r["cpc_low"] > 0]
    highs = [r["cpc_high"] for r in rows if isinstance(r.get("cpc_high"), (int, float)) and r["cpc_high"] > 0]
    comps = [r["competition"] for r in rows if isinstance(r.get("competition"), (int, float))]
    head = next((r for r in rows if r.get("phrase", "").lower() == (head_phrase or "").lower()), None)
    series = monthly or cluster_monthly(rows) or (head or {}).get("monthly")
    return {"status": "collected" if rows else "no_data", "provider": provider,
            "volume_sum": int(sum(r["volume"] for r in volumes)) if volumes else None,
            "volume_head": int(head["volume"]) if head and isinstance(head.get("volume"), (int, float)) else None,
            "cpc_median": round(statistics.median(cpcs), 2) if cpcs else None,
            "cpc_max": round(max(cpcs), 2) if cpcs else None,
            "cpc_low_median": round(statistics.median(lows), 2) if lows else None,
            "cpc_high_median": round(statistics.median(highs), 2) if highs else None,
            "competition_median": round(statistics.median(comps), 2) if comps else None,
            "phrases_measured": len(volumes), "phrases_with_volume": sum(1 for r in volumes if r["volume"] > 0),
            "volume_top_share": top_share(volumes),
            "trend_slope": trend_slope(series, min_mean_volume=trend_min_mean),
            "trend_yoy": trend_yoy(series, min_mean_volume=trend_min_mean),
            "volume_recent": volume_recent(series),
            "months": len(series or []),
            "monthly": series,
            "trend_basis": "cluster" if series is not None and series is not (head or {}).get("monthly") and monthly is None else "head_phrase",
            "top_phrases": sorted(volumes, key=lambda r: -r["volume"])[:8],
            "source_ids": [s for s in source_ids if s]}


def parse_ubersuggest(payload):
    rows = []
    for item in (payload or {}).get("searched_keywords") or []:
        if item.get("keyword"):
            rows.append({"phrase": item["keyword"], "volume": item.get("volume"), "cpc": item.get("cpcDollars", item.get("cpc")),
                         "competition": item.get("competition")})
    for item in (payload or {}).get("results") or []:
        if isinstance(item, dict) and item.get("keyword") and "volume" in item:
            rows.append({"phrase": item["keyword"], "volume": item.get("volume"), "cpc": item.get("cpcDollars", item.get("cpc")),
                         "competition": item.get("competition")})
    monthly = None
    if isinstance(payload, dict) and payload.get("monthly_searches"):
        monthly = [{"period": m.get("period"), "volume": m.get("search_volume")} for m in payload["monthly_searches"]]
        rows.append({"phrase": payload.get("keyword"), "volume": payload.get("search_volume"), "cpc": payload.get("cpc"),
                     "competition": payload.get("competition"), "monthly": monthly})
    return rows, monthly


def run(folder, config, *, root, refresh=False):
    """refresh=True — не брать числа из бэклога, спросить провайдера заново (после правки метрики)."""
    run_id = folder.name
    settings = config.get("measure") or {}
    providers = settings.get("providers") or ["google_ads", "bing", "ubersuggest"]
    screened = read_stage_jsonl(folder, "screen", "screened.jsonl")
    if not screened:
        raise ValueError("stage screen produced nothing; run screen first")
    candidates = [c for c in screened if c["screen"]["status"] == "passed"]
    previous = {} if refresh else {c["cluster_id"]: c for c in read_stage_jsonl(folder, "measure", "measured.jsonl")}
    availability = {"google_ads": google_ads.availability(root), "bing": bing.availability(root)}
    out, used_provider, pending = [], {}, []

    for cluster in candidates:
        prior = previous.get(cluster["cluster_id"])
        if prior and prior["numbers"]["status"] == "collected":
            out.append(prior); continue
        fresh = None if refresh else backlog.fresh_numbers(config, cluster["key"], root)
        if fresh:
            numbers = {**fresh, "status": "collected", "provider": fresh.get("provider", "backlog"), "from_backlog": True}
            out.append({**cluster, "numbers": numbers}); used_provider["backlog"] = used_provider.get("backlog", 0) + 1
            continue
        numbers = None
        bridge_error = None
        for provider in providers:
            if provider == "google_ads" and availability["google_ads"]["available"]:
                got = google_ads.historical_metrics(run_id, cluster["phrases"], location_id=settings.get("location_id", 2840),
                                                    language=settings.get("language", "en"), months=settings.get("months", 24), root=root)
                if got["status"] == "collected":
                    numbers = aggregate(got["rows"], cluster["head_phrase"], "google_ads", got["source_ids"],
                                        trend_min_mean=settings.get("trend_min_mean_volume", 20))
                    break
            elif provider == "bing" and availability["bing"]["available"]:
                got = bing.keyword(run_id, cluster["head_phrase"], root=root)
                if got["status"] == "collected" and got.get("volume") is not None:
                    rows = [{"phrase": cluster["head_phrase"], "volume": got["volume"], "cpc": None, "competition": None}]
                    rows += [{"phrase": r["phrase"], "volume": r["volume"], "cpc": None, "competition": None} for r in got["related"]]
                    numbers = aggregate(rows, cluster["head_phrase"], "bing", got["source_ids"],
                                        trend_min_mean=settings.get("trend_min_mean_volume", 20))
                    numbers["note"] = "Bing volumes only; no CPC"
                    break
            elif provider == "ubersuggest":
                args = {"keywords": [cluster["head_phrase"]], "language": settings.get("language", "en")}
                request = bridge.plan(folder, "ubersuggest.keyword_suggestions", args,
                                      purpose=f"цифры кластера {cluster['cluster_id']}", stage="measure")
                payload = bridge.response(folder, request)
                if payload is not None:
                    rows, monthly = parse_ubersuggest(payload)
                    numbers = aggregate(rows, cluster["head_phrase"], "ubersuggest", [request["source_id"]], monthly=monthly,
                                        trend_min_mean=settings.get("trend_min_mean_volume", 20))
                    break
                if request["status"] in ("pending", "deferred_quota"):
                    pending.append(cluster["cluster_id"])
                elif request["status"] == "error":
                    bridge_error = request.get("error")
        if numbers is None:
            reason = "no keyword provider available: " + "; ".join(f"{k}: {v['reason']}" for k, v in availability.items() if not v["available"])
            if bridge_error:
                reason = f"ubersuggest bridge error: {bridge_error}; " + reason
            numbers = {"status": "pending_bridge" if cluster["cluster_id"] in pending else "not_collected", "provider": None,
                       "volume_sum": None, "volume_head": None, "cpc_median": None, "cpc_max": None, "competition_median": None,
                       "trend_slope": None, "source_ids": [], "reason": reason}
        else:
            used_provider[numbers["provider"]] = used_provider.get(numbers["provider"], 0) + 1
            backlog.touch(config, cluster, run_id, status="measured",
                          numbers={k: numbers.get(k) for k in ("volume_sum", "volume_head", "cpc_median", "cpc_max", "competition_median", "trend_slope", "provider")}, root=root)
        out.append({**cluster, "numbers": numbers})

    # квота Ubersuggest: планируем не больше, чем осталось на сегодня, остальные ждут завтра.
    # Отложенные вчера запросы снова становятся кандидатами — поэтому сначала возвращаем их в очередь.
    left, _, _ = quota_left(config, root)
    requests = bridge.load_requests(folder)
    for r in requests:
        if r["status"] == "deferred_quota" and r["stage"] == "measure":
            r["status"] = "pending"
    planned = [r for r in requests if r["status"] == "pending" and r["stage"] == "measure" and r["tool"].startswith("ubersuggest")]
    order = {c["cluster_id"]: i for i, c in enumerate(candidates)}
    planned.sort(key=lambda r: order.get(r["purpose"].split()[-1], 999))
    deferred = planned[left:]
    for r in deferred:
        r["status"] = "deferred_quota"
    bridge._save(folder, requests)
    deferred_ids = {d["id"] for d in deferred}
    for c in out:
        if c["numbers"]["status"] == "pending_bridge" and all(
                r["id"] in deferred_ids for r in requests if r["stage"] == "measure" and r["purpose"].endswith(c["cluster_id"])):
            c["numbers"]["status"] = "deferred_quota"
            c["numbers"]["reason"] = "Ubersuggest daily report quota is spent; the request is queued for the next day. " + c["numbers"].get("reason", "")
    if planned[:left]:
        _spend_quota(config, root, len(planned[:left]))
    write_stage_jsonl(folder, "measure", "measured.jsonl", out)
    summary = {"clusters": len(out), "collected": sum(1 for c in out if c["numbers"]["status"] == "collected"),
               "pending_bridge": len([c for c in out if c["numbers"]["status"] == "pending_bridge"]),
               "deferred_quota": len([c for c in out if c["numbers"]["status"] == "deferred_quota"]),
               "not_collected": sum(1 for c in out if c["numbers"]["status"] == "not_collected"),
               "providers": used_provider, "ubersuggest_planned_today": len(planned[:left]), "ubersuggest_deferred": len(deferred),
               "availability": availability}
    status = "pending_bridge" if summary["pending_bridge"] else "done"
    finish_stage(folder, "measure", summary, status=status)
    return summary


def summary_text(summary):
    text = f"цифры собраны для {summary['collected']} из {summary['clusters']} ({', '.join(f'{k} {v}' for k, v in summary['providers'].items()) or 'нет провайдера'})"
    if summary.get("pending_bridge"):
        text += f"; ждут моста Ubersuggest: {summary['pending_bridge']}"
    if summary.get("deferred_quota"):
        text += f"; отложено до завтра из-за квоты Ubersuggest: {summary['deferred_quota']}"
    if summary.get("not_collected"):
        text += f"; без провайдера: {summary['not_collected']}"
    return text
