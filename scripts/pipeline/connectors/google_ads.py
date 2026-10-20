"""Google Ads API → Keyword Planner: первоисточник объёмов и ставок. Бесплатно, нужна настройка.

Ключи: GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET, GOOGLE_ADS_REFRESH_TOKEN, GOOGLE_ADS_CUSTOMER_ID,
GOOGLE_ADS_LOGIN_CUSTOMER_ID (опц.), GOOGLE_ADS_DEVELOPER_TOKEN (опц., старая схема).
Библиотека: pip install google-ads. См. docs/sources-setup.md §4.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from scripts import sources
from scripts.env import load_env

REQUIRED = ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN", "GOOGLE_ADS_CUSTOMER_ID")
NOTE = ("Keyword Planner volumes are rounded and merge close variants; cpc_low/cpc_high are the 20th and 80th "
        "percentile top-of-page bids, and cpc is their midpoint — advertiser bidding, not buyer payment.")
LANGUAGE_IDS = {"en": 1000, "es": 1003, "de": 1001, "fr": 1002, "ru": 1031}
COMPETITION = {0: None, 1: None, 2: 0.2, 3: 0.5, 4: 0.9}  # UNSPECIFIED, UNKNOWN, LOW, MEDIUM, HIGH


def availability(root):
    env = load_env(root)
    missing = [k for k in REQUIRED if not env.get(k, "").strip()]
    if missing:
        return {"available": False, "reason": "not set: " + ", ".join(missing)}
    try:
        import google.ads.googleads.client  # noqa: F401
    except ImportError:
        return {"available": False, "reason": "python package google-ads is not installed (pip install google-ads)"}
    return {"available": True, "reason": None}


def _client(env):
    from google.ads.googleads.client import GoogleAdsClient
    settings = {"client_id": env["GOOGLE_ADS_CLIENT_ID"], "client_secret": env["GOOGLE_ADS_CLIENT_SECRET"],
                "refresh_token": env["GOOGLE_ADS_REFRESH_TOKEN"], "use_proto_plus": True}
    if env.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip():
        settings["developer_token"] = env["GOOGLE_ADS_DEVELOPER_TOKEN"].strip()
    login = env.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").strip().replace("-", "")
    if login:
        settings["login_customer_id"] = login
    return GoogleAdsClient.load_from_dict(settings)


def _month(value):
    """MonthOfYearEnum: UNSPECIFIED=0, UNKNOWN=1, JANUARY=2 … DECEMBER=13 — календарный месяц на единицу меньше."""
    number = int(value) - 1
    return number if 1 <= number <= 12 else 0


def _year_month_range(client, request, months):
    """Окно помесячных объёмов: по умолчанию API отдаёт 12 месяцев, и на них сезонность
    неотличима от тренда. Двадцать четыре месяца дают сравнение год к году.

    Последний завершённый месяц — предыдущий календарный: текущий ещё идёт и занижен.
    """
    end = date.today().replace(day=1) - timedelta(days=1)
    start_index = (end.year * 12 + end.month - 1) - (months - 1)
    start_year, start_month = divmod(start_index, 12)
    start_month += 1
    options = request.historical_metrics_options
    options.year_month_range.start.year = start_year
    options.year_month_range.start.month = start_month + 1  # MonthOfYearEnum: JANUARY = 2
    options.year_month_range.end.year = end.year
    options.year_month_range.end.month = end.month + 1
    return {"start": f"{start_year}{start_month:02d}", "end": f"{end.year}{end.month:02d}"}


def historical_metrics(run_id, phrases, *, location_id=2840, language="en", months=24, root=None, who=None):
    """Точные метрики по списку фраз (до 500 за вызов). Ответ сериализуется и архивируется.

    months — глубина помесячного ряда; 24 нужны стадии цифр, чтобы считать год к году.
    """
    root = root or sources.ROOT
    env = load_env(root)
    check = availability(root)
    if not check["available"]:
        return {"status": "capability_unavailable", "error": check["reason"], "rows": [], "source_ids": []}
    client = _client(env)
    service = client.get_service("KeywordPlanIdeaService")
    customer_id = env["GOOGLE_ADS_CUSTOMER_ID"].replace("-", "")
    rows, source_ids = [], []
    for start in range(0, len(phrases), 500):
        chunk = list(phrases[start:start + 500])
        request = client.get_type("GenerateKeywordHistoricalMetricsRequest")
        request.customer_id = customer_id
        request.keywords.extend(chunk)
        request.geo_target_constants.append(f"geoTargetConstants/{location_id}")
        request.language = f"languageConstants/{LANGUAGE_IDS.get(language, 1000)}"
        request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
        request.include_adult_keywords = False
        window = _year_month_range(client, request, months) if months else None
        error = None
        try:
            response = service.generate_keyword_historical_metrics(request=request)
            payload = []
            for item in response.results:
                metrics = item.keyword_metrics
                payload.append({"phrase": item.text, "volume": int(metrics.avg_monthly_searches or 0),
                                "competition": COMPETITION.get(int(metrics.competition), None),
                                "cpc_low": round((metrics.low_top_of_page_bid_micros or 0) / 1e6, 2),
                                "cpc_high": round((metrics.high_top_of_page_bid_micros or 0) / 1e6, 2),
                                "monthly": [{"period": f"{m.year}{_month(m.month):02d}", "volume": int(m.monthly_searches or 0)}
                                            for m in metrics.monthly_search_volumes]})
        except Exception as exc:  # noqa: BLE001 - provider errors are recorded, never hidden
            payload, error = [], f"{type(exc).__name__}: {str(exc)[:400]}"
        body = json.dumps({"request": {"keywords": chunk, "location_id": location_id, "language": language, "months": window},
                           "results": payload, "error": error}, ensure_ascii=False).encode("utf-8")
        record = sources.archive(run_id, "google_ads_keyword_planner", "googleads://KeywordPlanIdeaService/GenerateKeywordHistoricalMetrics",
                                 query=json.dumps(chunk[:5]) + ("…" if len(chunk) > 5 else ""), status=None if error else 200,
                                 content_type="application/json", body=body, error=error, limitations=NOTE, root=root, **(who or {}))
        source_ids.append(record["source_id"])
        for row in payload:
            # Keyword Planner отдаёт не цену клика, а границы ставок вверху страницы: cpc_low —
            # 20-й перцентиль, cpc_high — 80-й. Брать верхнюю границу за «CPC фразы» значит
            # завышать ставку втрое: на контрольной выборке из десяти живых self-serve ниш
            # медиана по верхней границе давала 8–57 долларов при 2.6–9.6 по нижней. Типичная
            # ставка — середина диапазона; обе границы остаются в строке и видны в карточке.
            bounds = [b for b in (row["cpc_low"], row["cpc_high"]) if b]
            row["cpc"] = round(sum(bounds) / len(bounds), 2) if bounds else None
            row["source_id"] = record["source_id"]
            rows.append(row)
        if error:
            return {"status": "error", "error": error, "rows": rows, "source_ids": source_ids}
    return {"status": "collected", "error": None, "rows": rows, "source_ids": source_ids}
