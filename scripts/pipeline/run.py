"""Каркас прогона v2: папка, манифест, события, итоги стадий, уведомления.

Манифест намеренно простой: что исследуем, какой режим, какие стадии закрыты и с каким итогом.
Всё тяжёлое (фразы, кластеры, сигналы) лежит в папках стадий, каждая со своим summary.json.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from scripts.artifacts import atomic_text, write_json, write_jsonl
from scripts.pipeline.config import config_hash

ROOT = Path(__file__).resolve().parents[2]
RUN_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
STAGE_DIRS = {"expand": "1-expand", "cluster": "2-cluster", "screen": "3-screen", "measure": "4-measure",
              "gate": "5-gate", "review": "6-review", "report": "7-report"}
STAGE_RU = {"expand": "Разворот", "cluster": "Кластеры", "screen": "Отсев", "measure": "Цифры",
            "gate": "Гейты", "review": "Проверка", "report": "Отчёт"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def run_dir(root, run_id, *, must_exist=True):
    if not RUN_PATTERN.fullmatch(run_id or ""):
        raise ValueError("run id must use lowercase letters, digits and hyphens")
    folder = (Path(root).resolve() / "runs" / run_id)
    if must_exist and not (folder / "manifest.json").is_file():
        raise ValueError(f"run does not exist: {run_id}")
    return folder


def read_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path):
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stage_dir(folder, stage):
    if stage not in STAGE_DIRS:
        raise ValueError(f"unknown stage: {stage}")
    path = Path(folder) / STAGE_DIRS[stage]
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_run(root, run_id, config, themes, *, mode, objective, authorization):
    folder = run_dir(root, run_id, must_exist=False)
    if (folder / "manifest.json").exists():
        raise ValueError("run already exists; pick a new run id")
    folder.mkdir(parents=True, exist_ok=True)
    frozen = dict(config, mode=mode, themes=themes)
    write_json(folder / "config.json", frozen)
    manifest = {"schema_version": "2.0", "kind": "demand", "run_id": run_id, "created_at": now(),
                "mode": mode, "themes": [t["id"] for t in themes], "objective": objective,
                "authorization": authorization, "config_hash": config_hash(frozen),
                "status": "running", "stages": {}, "decisions": {}, "report": None}
    write_json(folder / "manifest.json", manifest)
    brief = [f"# Прогон {run_id}", "", f"Цель: {objective}", "", f"Режим: {mode}",
             "Темы: " + ", ".join(f"{t['id']} ({t.get('title', '')})" for t in themes), "",
             "## Разрешение", "", authorization, ""]
    atomic_text(folder / "brief.md", "\n".join(brief))
    record_event(folder, "created", {"mode": mode, "themes": manifest["themes"]})
    return manifest


def load_manifest(folder):
    manifest = read_json(Path(folder) / "manifest.json")
    if not manifest:
        raise ValueError(f"run has no manifest: {folder}")
    return manifest


def save_manifest(folder, manifest):
    write_json(Path(folder) / "manifest.json", manifest)


def load_config(folder):
    return read_json(Path(folder) / "config.json") or {}


def record_event(folder, event, details=None):
    path = Path(folder) / "events.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": now(), "event": event, "details": details or {}}, ensure_ascii=False) + "\n")


def finish_stage(folder, stage, summary, *, status="done"):
    """Записать итог стадии в её папку и в манифест. status: done | pending_bridge | blocked."""
    folder = Path(folder)
    write_json(stage_dir(folder, stage) / "summary.json", {"stage": stage, "status": status, "at": now(), **summary})
    manifest = load_manifest(folder)
    manifest["stages"][stage] = {"status": status, "at": now(), "summary": summary}
    save_manifest(folder, manifest)
    record_event(folder, f"stage:{stage}:{status}", summary)
    return manifest["stages"][stage]


def stage_status(folder, stage):
    return (load_manifest(folder).get("stages") or {}).get(stage, {}).get("status")


def write_stage_jsonl(folder, stage, name, records):
    write_jsonl(stage_dir(folder, stage) / name, records)


def read_stage_jsonl(folder, stage, name):
    return read_jsonl(Path(folder) / STAGE_DIRS[stage] / name)


def notify(folder, config, stage, text):
    """Одно короткое сообщение в Telegram после стадии, если это включено в конфиге."""
    settings = config.get("notify") or {}
    if not settings.get("telegram") or stage not in (settings.get("stages") or []):
        return {"skipped": True}
    from scripts import telegram
    run_id = Path(folder).name
    try:
        outcome = telegram.send_message(run_id, f"[{run_id}] {STAGE_RU.get(stage, stage)}: {text}", root=ROOT)
    except ValueError as error:
        outcome = {"telegram_status": "error", "error": str(error)}
    record_event(folder, f"notify:{stage}", {"status": outcome.get("telegram_status"), "error": outcome.get("error")})
    return outcome
