"""Стадия 5 — гейты: числовые пороги из конфига и режим CPC. Неизвестное не проходит и не проваливается.

Каждый отказывающий порог откалиброван на положительном контроле (config/calibration.yaml,
scripts/calibrate.py): правило, отбраковывающее заведомо живые self-serve ниши, здесь не отказ,
а пометка в `flags`. Пометка не отсекает кластер, но едет с ним в проверку и в карточку отчёта.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.pipeline import backlog
from scripts.pipeline.cluster import tool_shaped_cluster
from scripts.pipeline.config import cpc_band
from scripts.pipeline.run import finish_stage, read_stage_jsonl, write_stage_jsonl

BASELINE_PATH = Path(__file__).resolve().parents[2] / "config" / "calibration-baseline.json"


def baseline(path=BASELINE_PATH):
    """Фон поиска: медиана год-к-году по контрольной выборке, снятая scripts/calibrate.py.

    Судить тренд ниши абсолютным числом нельзя: на контроле из десяти заведомо живых ниш
    восемь падают, а медиана — минус 22% за год. Это состояние поиска в целом, а не ниш.
    Без файла фон считается нулевым, и кластер получает пометку: порог тогда строже, чем надо.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("trend_yoy_median")
    return {"trend_yoy_median": value, "at": data.get("at"), "run": data.get("run")} if isinstance(value, (int, float)) else None


def services_share(cluster, words):
    """Доля фраз кластера, в которых покупают не продукт, а человекочасы.

    Ставка за клик услугу от инструмента не отличает: на контроле внедрение ERP шло по $23
    за клик, а self-serve-биллинг — по $36. Отличает формулировка: у живых ниш контроля доля
    таких слов 0.00–0.08, у внедрения ERP — 1.00. Слово «service» в список не входит намеренно:
    «email verification service» и «form submission service» — это продукты.
    """
    phrases = cluster.get("phrases") or []
    if not phrases or not words:
        return None
    hits = sum(1 for p in phrases if _sells_labour(str(p).lower(), words))
    return round(hits / len(phrases), 3)


def _sells_labour(phrase, words):
    """Слово после «for» называет покупателя, а не товар.

    «invoicing software for freelancers» и «scheduling tool for agencies» — это продукты,
    которые продают агентствам и фрилансерам; «marketing agency» и «erp implementation
    consultant» — продажа человекочасов. Без этой оговорки отсечка услуг снимала бы целые
    темы, где агентство — покупатель.
    """
    for word in words:
        start = phrase.find(word)
        while start >= 0:
            if not phrase[:start].rstrip().endswith("for"):
                return True
            start = phrase.find(word, start + 1)
    return False


def cluster_volume(numbers):
    """Объём, по которому судим: средний месячный за последний год, если ряд его даёт."""
    recent = numbers.get("volume_recent")
    return recent if isinstance(recent, (int, float)) else (numbers.get("volume_sum") or 0)


def evaluate(cluster, config, *, fon=None):
    settings = config.get("gate") or {}
    fon = fon if fon is not None else baseline()
    mode = config.get("mode", "self_serve")
    numbers = cluster.get("numbers") or {}
    screen = cluster.get("screen") or {}
    failed, unknown, flags = [], [], []
    # Интент ставит модель, и на подкластерах она регулярно ошибается: кластер с головой
    # «email deliverability check» и фразами tester/tools/checker/platform приходил
    # informational и снимался гейтом за разметку, а не за нишу. Проверка детерминированная
    # и смотрит на сами фразы, поэтому стоит здесь, а не только на стадии кластеров.
    intent = cluster.get("intent")
    if intent == "informational" and tool_shaped_cluster(cluster.get("head_phrase"), cluster.get("phrases")):
        intent = "tool_shaped"
        flags.append("intent_relabelled: фразы описывают инструмент, разметка модели не принята")
    if intent not in (settings.get("allowed_intents") or ["commercial", "tool_shaped"]):
        failed.append(f"intent:{intent}")
    if numbers.get("status") != "collected":
        unknown.append("numbers_not_collected")
    else:
        volume = cluster_volume(numbers)
        if volume < float(settings.get("min_volume_sum") or 0):
            failed.append(f"min_volume_sum:{volume}<{settings.get('min_volume_sum')}")
        band = cpc_band(config, mode)
        cpc = numbers.get("cpc_median")
        if band is not None:
            if cpc is None:
                unknown.append("cpc_unknown")
            elif not (band[0] <= cpc <= band[1]):
                failed.append(f"cpc_band[{mode}]:{cpc} not in {band}")
            elif settings.get("cpc_flag_above") and cpc >= float(settings["cpc_flag_above"]):
                flags.append(f"cpc_high:{cpc}>={settings['cpc_flag_above']} — ставка на уровне услуги, проверить покупателя")
        # Тренд решает только год к году и только относительно общего фона поиска.
        # Наклон по одному 12-месячному окну — это сезонность; абсолютное падение — это среда,
        # в которой падает почти всё. Отказ — только тем, кто падает заметно быстрее фона.
        yoy = numbers.get("trend_yoy")
        if yoy is not None:
            level = (fon or {}).get("trend_yoy_median") or 0.0
            cutoff = round(level + float(settings.get("min_trend_yoy_vs_baseline", -0.3)), 3)
            if not fon:
                flags.append("baseline_missing: фон поиска не снят, тренд судится строже, чем надо")
            if yoy < cutoff:
                failed.append(f"min_trend_yoy_vs_baseline:{yoy}<{cutoff} (фон {level})")
        else:
            flags.append("trend_unknown: ряд короче 24 месяцев, сезонность не вычтена")
        # Сигналы отсева — маркетплейсы, выдача, биржа — собраны по головной фразе. Если её
        # никто не ищет, они собраны не про эту работу, и вердикт отсева ничего не значит.
        if numbers.get("volume_head") == 0 and volume > 0:
            flags.append(f"head_no_volume: «{cluster.get('head_phrase')}» — 0 запросов, сигналы отсева собраны по ней")
        share = numbers.get("volume_top_share")
        limit = settings.get("flag_top_share")
        if limit and isinstance(share, (int, float)) and share >= float(limit):
            flags.append(f"one_phrase:{share} — объём кластера держит одна фраза, проверить, та ли она")
    checks = screen.get("checks") or {}
    if mode == "self_serve":
        share = services_share(cluster, settings.get("services_words") or [])
        limit = settings.get("max_services_share")
        if limit is not None and share is not None and share > float(limit):
            failed.append(f"services_shaped:{share}>{limit} — покупают человекочасы, а не продукт")
        if checks.get("software") is False:
            failed.append("screen:software")
        if checks.get("reachable") is False:
            # На контроле у семи из десяти живых ниш в топ-5 больше двух брендов: плотность
            # брендов — признак того, что за трафик платят, а не причина не заходить.
            flags.append("crowded_serp: топ-5 занят брендами, проверить, чем заходить")
    elif mode == "services":
        if checks.get("money") is False:
            failed.append("screen:money")
    status = "failed" if failed else "unknown" if unknown else "passed"
    return {"status": status, "passed": status == "passed", "failed_rules": failed, "unknown": unknown,
            "flags": flags, "mode": mode}


def run(folder, config, *, root):
    run_id = folder.name
    measured = read_stage_jsonl(folder, "measure", "measured.jsonl")
    if not measured:
        raise ValueError("stage measure produced nothing; run measure first")
    fon = baseline()
    out = []
    for cluster in measured:
        verdict = evaluate(cluster, config, fon=fon)
        out.append({**cluster, "gate": verdict})
        if verdict["status"] in ("passed", "failed"):
            backlog.touch(config, cluster, run_id, status="gated_in" if verdict["passed"] else "gated_out",
                          note=None if verdict["passed"] else "gate: " + ", ".join(verdict["failed_rules"]), root=root)
    out.sort(key=lambda c: (c["gate"]["status"] != "passed", -cluster_volume(c.get("numbers") or {})))
    write_stage_jsonl(folder, "gate", "gated.jsonl", out)
    summary = {"clusters": len(out), "passed": sum(c["gate"]["passed"] for c in out),
               "failed": sum(c["gate"]["status"] == "failed" for c in out), "unknown": sum(c["gate"]["status"] == "unknown" for c in out),
               "mode": config.get("mode"), "cpc_band": cpc_band(config), "baseline": fon,
               "failed_rules": {rule.split(":")[0]: 0 for c in out for rule in c["gate"]["failed_rules"]},
               "flags": {flag.split(":")[0]: 0 for c in out for flag in c["gate"].get("flags") or []}}
    for c in out:
        for rule in c["gate"]["failed_rules"]:
            summary["failed_rules"][rule.split(":")[0]] += 1
        for flag in c["gate"].get("flags") or []:
            summary["flags"][flag.split(":")[0]] += 1
    finish_stage(folder, "gate", summary)
    return summary


def summary_text(summary):
    return (f"режим {summary['mode']}: прошли {summary['passed']}, не прошли {summary['failed']}, "
            f"без чисел {summary['unknown']} из {summary['clusters']}")
