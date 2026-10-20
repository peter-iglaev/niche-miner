#!/usr/bin/env python3
"""Калибровка гейтов на положительном контроле.

Прогон, который никого не пропустил, сам по себе ничего не доказывает: он одинаково выглядит
и когда на рынке пусто, и когда пороги отрезают живое. Различить два случая можно только
эталоном — набором ниш, про которые заранее известно, что там платят (config/calibration.yaml).

Скрипт гонит эталон через тот же код, что и настоящий прогон: google_ads.historical_metrics →
measure.aggregate → gate.evaluate. Никакой отдельной арифметики, иначе калибруется не тот прибор.
Проверки отсева подставляются как пройденные: здесь измеряются числовые гейты, а SERP-правило
проверяется отдельно (--serp), потому что оно спорно ровно для этих ниш.

Вывод: runs/<run>/calibration.json и таблица в консоли — кто прошёл, кто нет, какое правило
отрезало и при каком пороге контроль прошёл бы целиком.

    python3 -B scripts/calibrate.py --run calib-2026-09-21
    python3 -B scripts/calibrate.py --run calib-2026-09-21 --serp   # ещё и проверка достижимости
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.pipeline import gate, measure  # noqa: E402
from scripts.pipeline.config import cpc_band, load_config  # noqa: E402
from scripts.pipeline.connectors import google_ads, serp  # noqa: E402
from scripts.pipeline.run import now  # noqa: E402

CALIBRATION_PATH = ROOT / "config" / "calibration.yaml"
BASELINE_PATH = gate.BASELINE_PATH
# Гейты, у которых есть числовой порог: правило → (ключ метрики, ключ конфига, сторона отказа).
# Сторона нужна, чтобы предложить порог: min_* поднимают снизу, cpc_band сжимает с двух сторон.
NUMERIC_RULES = ("min_volume_sum", "min_trend", "cpc_band")


def load_calibration(path=CALIBRATION_PATH):
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not data.get("niches"):
        raise ValueError(f"{path} does not list any niches")
    for side in ("niches", "negative"):
        for niche in data.get(side) or []:
            for field in ("id", "name", "head_phrase", "phrases", "intent", "product_shape"):
                if not niche.get(field):
                    raise ValueError(f"{side}: {niche.get('id', '?')} is missing {field}")
            if niche["head_phrase"] not in niche["phrases"]:
                raise ValueError(f"{side}: {niche['id']}: head_phrase is not among its phrases")
    return data


def as_cluster(niche, numbers):
    """Эталонная ниша в виде кластера — ровно такой формы, какую видит gate.evaluate.

    Проверки отсева подставлены пройденными намеренно: отсев на эталоне не измеряется,
    иначе непонятно, какой именно гейт отрезал, — а нас интересуют числовые пороги.
    """
    return {"cluster_id": f"cal-{niche['id']}", "name": niche["name"], "intent": niche["intent"],
            "product_shape": niche["product_shape"], "head_phrase": niche["head_phrase"],
            "phrases": niche["phrases"], "numbers": numbers,
            "screen": {"status": "passed", "checks": {"money": True, "software": True, "reachable": True},
                       "required": [], "failed": []}}


def collect(niche, config, run_id, *, root):
    settings = config.get("measure") or {}
    got = google_ads.historical_metrics(run_id, niche["phrases"], location_id=settings.get("location_id", 2840),
                                        language=settings.get("language", "en"), months=settings.get("months", 24), root=root)
    if got["status"] != "collected":
        return {"status": "not_collected", "provider": None, "reason": got.get("error") or got["status"],
                "volume_sum": None, "cpc_median": None, "trend_slope": None, "source_ids": []}
    return measure.aggregate(got["rows"], niche["head_phrase"], "google_ads", got["source_ids"],
                             trend_min_mean=settings.get("trend_min_mean_volume", 20))


def reachable(niche, config, run_id, *, root):
    """Правило достижимости на эталоне: сколько брендов в топ-5 у ниши, где заведомо платят."""
    settings = (config.get("screen") or {}).get("serp") or {}
    got = serp.top(run_id, niche["head_phrase"], provider=settings.get("provider", "brave"),
                   country=(config.get("measure") or {}).get("country", "US"),
                   language=(config.get("measure") or {}).get("language", "en"), root=root)
    threshold = int(settings.get("max_brands_top5") or 2)
    if got["status"] != "collected":
        return {"status": got["status"], "error": got.get("error")}
    return {"status": "collected", "brands_top5": got["brands_top5"], "threshold": threshold,
            "passes": got["brands_top5"] <= threshold,
            "top": [{"rank": r["rank"], "domain": r["domain"], "kind": r["kind"]} for r in got["results"][:5]],
            "source_id": got["source_id"]}


def rule_name(rule):
    return rule.split(":")[0].split("[")[0]


def write_baseline(summary, results, path=BASELINE_PATH):
    """Фон поиска для гейта тренда: медиана год-к-году по контролю на момент замера.

    Файл коммитится: без него гейт судит падение по нулю, то есть считает нормой рост,
    которого сейчас нет ни у одной живой ниши из десяти.
    """
    yoy = sorted(r["numbers"]["trend_yoy"] for r in results
                 if isinstance((r["numbers"] or {}).get("trend_yoy"), (int, float)))
    if not yoy:
        return None
    median = round(yoy[len(yoy) // 2] if len(yoy) % 2 else (yoy[len(yoy) // 2 - 1] + yoy[len(yoy) // 2]) / 2, 3)
    payload = {"at": summary["at"], "run": summary["run"], "niches": len(yoy),
               "trend_yoy_median": median, "trend_yoy_range": [yoy[0], yoy[-1]],
               "note": "Медиана падения спроса по контрольной выборке живых self-serve ниш. "
                       "Гейт тренда судит кластер относительно этого числа, а не нуля."}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def thresholds(results, config):
    """Какой порог пропустил бы весь контроль — и сколько эталонных ниш режет нынешний.

    Считается по собранным числам, а не по отказам: ниша может провалиться по двум правилам
    сразу, и тогда отказ одного скрывает запас другого.
    """
    numbers = [r["numbers"] for r in results if r["numbers"].get("status") == "collected"]
    gate_cfg = config.get("gate") or {}
    band = cpc_band(config)
    out = {}

    volumes = [gate.cluster_volume(n) for n in numbers]
    volumes = [v for v in volumes if isinstance(v, (int, float))]
    if volumes:
        out["min_volume_sum"] = {"current": gate_cfg.get("min_volume_sum"), "control_min": min(volumes),
                                 "rejects_now": sum(1 for v in volumes if v < float(gate_cfg.get("min_volume_sum") or 0)),
                                 "admits_all_at": min(volumes)}
    yoy = [n["trend_yoy"] for n in numbers if isinstance(n.get("trend_yoy"), (int, float))]
    if yoy:
        level = (gate.baseline() or {}).get("trend_yoy_median") or 0.0
        cutoff = level + float(gate_cfg.get("min_trend_yoy_vs_baseline", -0.3))
        out["min_trend_yoy_vs_baseline"] = {"current": gate_cfg.get("min_trend_yoy_vs_baseline"), "baseline": level,
                                            "cutoff": round(cutoff, 3), "control_min": round(min(yoy), 3),
                                            "rejects_now": sum(1 for s in yoy if s < cutoff),
                                            "admits_all_at": round(min(yoy) - level, 3),
                                            "unknown": sum(1 for n in numbers if n.get("trend_yoy") is None)}
    slopes = [n["trend_slope"] for n in numbers if isinstance(n.get("trend_slope"), (int, float))]
    if slopes:
        # Наклон больше не отказывает; его разброс на контроле показывает, почему.
        out["trend_slope_not_a_gate"] = {"control_range": [round(min(slopes), 3), round(max(slopes), 3)],
                                         "would_reject_at_-0.2": sum(1 for s in slopes if s < -0.2)}
    cpcs = [n["cpc_median"] for n in numbers if isinstance(n.get("cpc_median"), (int, float))]
    if cpcs and band:
        out["cpc_band"] = {"current": list(band), "control_range": [round(min(cpcs), 2), round(max(cpcs), 2)],
                           "rejects_now": sum(1 for c in cpcs if not (band[0] <= c <= band[1])),
                           "rejects_above": sum(1 for c in cpcs if c > band[1]),
                           "rejects_below": sum(1 for c in cpcs if c < band[0]),
                           "admits_all_at": [round(min(cpcs), 2), round(max(cpcs), 2)],
                           "unknown": sum(1 for n in numbers if n.get("cpc_median") is None)}
    intents = [r["niche"]["intent"] for r in results]
    allowed = gate_cfg.get("allowed_intents") or []
    out["intent"] = {"current": allowed, "rejects_now": sum(1 for i in intents if i not in allowed),
                     "control_intents": sorted(set(intents))}
    shares = [n["volume_top_share"] for n in numbers if isinstance(n.get("volume_top_share"), (int, float))]
    if shares:
        # Порога на концентрацию сейчас нет; контроль показывает, какая доля нормальна для живой ниши
        # и с какого значения «объём кластера» — это на самом деле одна фраза.
        out["volume_top_share"] = {"current": None, "control_max": round(max(shares), 3),
                                   "control_median": round(sorted(shares)[len(shares) // 2], 3)}
    return out


def run(run_id, *, root=ROOT, with_serp=False, config_path=None):
    config = load_config(config_path) if config_path else load_config()
    calibration = load_calibration()
    folder = root / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)

    availability = google_ads.availability(root)
    if not availability["available"]:
        raise SystemExit(f"google ads is not available: {availability['reason']}")

    def measure_side(niches, label):
        rows = []
        for niche in niches:
            numbers = collect(niche, config, run_id, root=root)
            row = {"niche": niche, "numbers": numbers, "side": label}
            if with_serp and label == "positive":
                row["reachable"] = reachable(niche, config, run_id, root=root)
            rows.append(row)
            print(f"{label[:3]} {niche['id']:<28} vol={gate.cluster_volume(numbers)} cpc={numbers.get('cpc_median')} "
                  f"yoy={numbers.get('trend_yoy')}", flush=True)
        return rows

    results = measure_side(calibration["niches"], "positive")
    negative = measure_side(calibration.get("negative") or [], "negative")

    # Фон снимается только по живым нишам и до вердиктов: гейт тренда судит относительно него,
    # а мёртвые ниши в медиане сдвинули бы фон вниз и оправдали бы падение, которое ловим.
    at = now()
    fon = write_baseline({"at": at, "run": run_id}, results)
    print(f"\nфон поиска (медиана год-к-году по живым нишам): {fon and fon['trend_yoy_median']} → {BASELINE_PATH.name}\n")

    for row in results + negative:
        verdict = gate.evaluate(as_cluster(row["niche"], row["numbers"]), config, fon=gate.baseline())
        row["gate"] = verdict
        mark = "OK" if verdict["passed"] == (row["side"] == "positive") else "ПРОМАХ"
        print(f"{row['side'][:3]} {row['niche']['id']:<28} {verdict['status']:<8} {mark:<7} "
              f"{', '.join(verdict['failed_rules']) or '—'}", flush=True)

    passed = [r for r in results if r["gate"]["passed"]]
    held = [r for r in negative if not r["gate"]["passed"]]
    wrong_rule = [{"id": r["niche"]["id"], "expected": r["niche"].get("expect_rule"),
                   "got": [rule_name(x) for x in r["gate"]["failed_rules"]]}
                  for r in held if r["niche"].get("expect_rule")
                  and r["niche"]["expect_rule"] not in [rule_name(x) for x in r["gate"]["failed_rules"]]]
    failed_by = {}
    for r in results:
        for rule in r["gate"]["failed_rules"]:
            failed_by[rule_name(rule)] = failed_by.get(rule_name(rule), 0) + 1
    summary = {"run": run_id, "at": at, "mode": config.get("mode"), "cpc_band": cpc_band(config), "baseline": fon,
               "niches": len(results), "passed": len(passed), "failed": len(results) - len(passed),
               "failed_by": failed_by,
               "negative": {"niches": len(negative), "held": len(held), "leaked": len(negative) - len(held),
                            "leaked_ids": [r["niche"]["id"] for r in negative if r["gate"]["passed"]],
                            "held_by_other_rule": wrong_rule},
               "thresholds": thresholds(results, config)}
    if with_serp:
        reach = [r["reachable"] for r in results if r.get("reachable", {}).get("status") == "collected"]
        summary["reachable"] = {"checked": len(reach), "passes": sum(1 for r in reach if r["passes"]),
                                "brands_top5": sorted(r["brands_top5"] for r in reach)}
    payload = {"summary": summary, "results": results, "negative": negative,
               "config_gate": config.get("gate"), "source": str(CALIBRATION_PATH.relative_to(ROOT))}
    (folder / "calibration.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n→ {folder / 'calibration.json'}")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, help="идентификатор прогона калибровки, например calib-2026-09-21")
    parser.add_argument("--serp", action="store_true", help="ещё и проверить правило достижимости (Brave, один запрос на нишу)")
    parser.add_argument("--config", help="путь к config/demand.yaml, если нужен не основной")
    args = parser.parse_args()
    run(args.run, with_serp=args.serp, config_path=Path(args.config) if args.config else None)


if __name__ == "__main__":
    main()
