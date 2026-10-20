"""Бэклог кластеров между прогонами: один JSONL в репозитории, дописывается каждой стадией."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from scripts.artifacts import atomic_text

ROOT = Path(__file__).resolve().parents[2]
STATUSES = ("new", "screened", "measured", "gated_out", "gated_in", "reviewed", "build_test", "investigate",
            "dropped", "duplicate")
# Версия определения метрик стадии цифр. Поднимается, когда меняется смысл сохранённых чисел —
# например, CPC перестал быть верхней границей ставки, а окно выросло с 12 месяцев до 24.
# Числа, снятые более ранней версией, не переиспользуются: см. fresh_numbers.
METRICS_VERSION = 2
# Поля, по которым судит стадия гейтов: без них переиспользовать числа нельзя.
GATE_FIELDS = ("volume_recent", "trend_yoy", "cpc_median")


def key_for(name):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug[:60] or "cluster"


def _path(config, root=ROOT):
    return Path(root) / ((config.get("backlog") or {}).get("path") or "backlog/clusters.jsonl")


def load(config, root=ROOT):
    path = _path(config, root)
    if not path.is_file():
        return {}
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["key"]: r for r in records}


def save(config, records, root=ROOT):
    path = _path(config, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = [records[k] for k in sorted(records)]
    atomic_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ordered))


def touch(config, cluster, run_id, *, status=None, numbers=None, decision=None, note=None, root=ROOT):
    """Обновить или создать запись кластера. Возвращает запись и флаг «был ли уже»."""
    records = load(config, root)
    key = cluster.get("key") or key_for(cluster.get("name"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    record = records.get(key)
    # «Уже виден» — только если запись пришла из другого прогона; повтор стадии в том же прогоне не в счёт.
    existed = record is not None and record.get("first_seen_run") != run_id
    if record is None:
        record = {"backlog_id": f"bl-{len(records) + 1:06d}", "key": key, "name": cluster.get("name"),
                  "theme": cluster.get("theme"), "first_seen_run": run_id, "first_seen": today,
                  "status": "new", "runs": [], "last_numbers": None, "decision": None, "notes": []}
        records[key] = record
    record["last_seen_run"] = run_id
    record["last_seen"] = today
    if run_id not in record["runs"]:
        record["runs"].append(run_id)
    if status:
        if status not in STATUSES:
            raise ValueError(f"unknown backlog status: {status}")
        record["status"] = status
    if numbers is not None:
        record["last_numbers"] = {**numbers, "at": today, "metrics_version": METRICS_VERSION}
    if decision is not None:
        record["decision"] = decision
    if note:
        record["notes"].append({"at": today, "run": run_id, "text": note})
    save(config, records, root)
    return record, existed


def fresh_numbers(config, key, root=ROOT):
    """Числа из бэклога, если они моложе backlog.refresh_days и считаны текущей метрикой.

    Возраст — не единственный способ протухнуть. Прогон devops-ai-2026-09-22 взял из бэклога
    22 кластера из 46, измеренных сентябрьскими прогонами: там CPC был 80-м перцентилем ставки,
    окно — 12 месяцев, а год-к-году не считался вовсе. Гейт судил эти кластеры по испорченному
    кэшу и пропускал их с неизвестным трендом. Номер версии метрики закрывает эту дыру:
    числа, снятые до изменения определения, просто не переиспользуются.
    """
    record = load(config, root).get(key)
    if not record or not record.get("last_numbers"):
        return None
    if int(record["last_numbers"].get("metrics_version") or 0) < METRICS_VERSION:
        return None
    # Запись бэклога хранит только скалярные числа, без помесячного ряда, поэтому
    # переиспользованный кластер приходил без volume_recent и trend_yoy: гейт судил его
    # по volume_sum и ставил пометку «тренд неизвестен». В прогоне creator-marketing-2026-09-24
    # так прошли шесть кластеров из двадцати трёх. Запрос к Keyword Planner бесплатен —
    # дешевле перемерить, чем судить по обрезку.
    if any(record["last_numbers"].get(field) is None for field in GATE_FIELDS):
        return None
    days = (config.get("backlog") or {}).get("refresh_days", 60)
    try:
        measured = datetime.strptime(record["last_numbers"]["at"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None
    if (datetime.now(timezone.utc) - measured).days > days:
        return None
    return record["last_numbers"]
