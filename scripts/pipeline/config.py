"""Конфиг пайплайна v2: загрузка, проверка, заморозка в прогон.

Один файл config/demand.yaml описывает всё; здесь он читается, дополняется значениями по
умолчанию и проверяется на очевидные ошибки до того, как стадия что-то запросит из сети.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "demand.yaml"
MODES = ("self_serve", "services", "any")
INTENTS = ("commercial", "tool_shaped", "informational", "navigational")
STAGE_NAMES = ("expand", "cluster", "screen", "measure", "gate", "review", "report")

DEFAULTS = {
    "schema_version": "2.0",
    "mode": "self_serve",
    "cpc": {"self_serve": [2, 40], "services": [12, 60]},
    "themes": [],
    "expand": {"sources": ["google", "youtube", "amazon", "shopify"],
               "modifiers": ["free", "api", "tool", "software", "template"],
               "operators": ["pairs", "alternatives", "tool_shaped"],
               "tool_shapes": ["calculator", "checker", "validator", "generator", "converter", "template"],
               "pause_seconds": 1.2, "max_phrases_per_seed": 250, "country": "us", "language": "en"},
    "cluster": {"min_phrases": 4, "max_clusters": 60, "phrases_per_call": 350, "min_phrases_per_call": 40,
                "split": True, "split_sample": 120, "split_max_groups": 4, "split_passes": 3},
    "screen": {"upwork": {"enabled": True, "min_jobs": 3, "min_budget": 200, "window_days": 90},
               "freelancer": {"enabled": True},
               "marketplaces": {"enabled": True, "sources": ["apify", "wordpress", "atlassian", "vscode", "obsidian", "smithery"]},
               "serp": {"enabled": True, "provider": "brave", "phrases_per_cluster": 2, "max_brands_top5": 2},
               "hn": {"enabled": True},
               "require": ["software"]},
    "measure": {"providers": ["google_ads", "bing", "ubersuggest"], "ubersuggest_daily_reports": 3,
                "country": "US", "language": "en", "location_id": 2840, "months": 24, "trend_min_mean_volume": 20},
    "gate": {"min_volume_sum": 120, "min_trend_yoy_vs_baseline": -0.3, "allowed_intents": ["commercial", "tool_shaped"],
             "cpc_flag_above": 20, "flag_top_share": 0.9, "max_services_share": 0.3,
             "services_words": ["consultant", "consulting", "implementation", "agency", "freelancer",
                                "outsourcing", "staffing", "hire "]},
    "review": {"max_clusters": 12, "volume_bands": [20000, 2000], "ask_upwork": True,
               "interpret_search_queries": 4, "skeptic_search_queries": 6, "max_steps": 24},
    "models": {"cluster": {"provider": "deepseek", "model": "deepseek-chat"},
               "interpret": {"provider": "deepseek", "model": "deepseek-chat"},
               "skeptic": {"provider": "deepseek", "model": "deepseek-v4-pro", "reasoning_effort": "high"},
               "judge": {"provider": "deepseek", "model": "deepseek-v4-pro", "reasoning_effort": "high"},
               "narrative": {"provider": "deepseek", "model": "deepseek-chat"}},
    "notify": {"telegram": True, "stages": list(STAGE_NAMES), "send_pdf": True},
    "backlog": {"path": "backlog/clusters.jsonl", "refresh_days": 60},
    "limits": {"max_active_agents": 8, "max_attempts_per_assignment": 2},
}


def _merge(base, override):
    if not isinstance(override, dict):
        return copy.deepcopy(base) if override is None else override
    result = copy.deepcopy(base)
    for key, value in override.items():
        result[key] = _merge(base[key], value) if isinstance(base.get(key), dict) and isinstance(value, dict) else copy.deepcopy(value)
    return result


def validate_config(config):
    """Ошибки, из-за которых прогон не должен стартовать. Возвращает список строк."""
    errors = []
    if config.get("mode") not in MODES:
        errors.append("mode must be one of: " + ", ".join(MODES))
    for name in ("self_serve", "services"):
        band = (config.get("cpc") or {}).get(name)
        if not (isinstance(band, list) and len(band) == 2 and all(isinstance(v, (int, float)) for v in band) and band[0] <= band[1]):
            errors.append(f"cpc.{name} must be [low, high] with low <= high")
    themes = config.get("themes") or []
    if not themes:
        errors.append("themes must list at least one theme")
    seen = set()
    for index, theme in enumerate(themes):
        if not isinstance(theme, dict) or not theme.get("id"):
            errors.append(f"themes[{index}] needs an id"); continue
        if theme["id"] in seen:
            errors.append(f"themes: duplicate id {theme['id']}")
        seen.add(theme["id"])
        if not theme.get("seeds"):
            errors.append(f"themes[{theme['id']}] needs at least one seed")
    gate = config.get("gate") or {}
    if not isinstance(gate.get("min_volume_sum"), (int, float)) or gate["min_volume_sum"] < 0:
        errors.append("gate.min_volume_sum must be a nonnegative number")
    bad = [i for i in gate.get("allowed_intents", []) if i not in INTENTS]
    if bad:
        errors.append("gate.allowed_intents has unknown values: " + ", ".join(bad))
    if not isinstance(gate.get("min_trend_yoy_vs_baseline"), (int, float)):
        errors.append("gate.min_trend_yoy_vs_baseline must be a number")
    for stale in ("min_trend", "min_trend_yoy"):
        # Старые пороги судили тренд по наклону одного окна и по абсолютному падению.
        # Молча игнорировать их нельзя: конфиг выглядел бы работающим, а гейт судил бы иначе.
        if stale in gate:
            errors.append(f"gate.{stale} is gone: the trend gate is gate.min_trend_yoy_vs_baseline "
                          "(year over year, against config/calibration-baseline.json)")
    bad = [name for name in (config.get("screen") or {}).get("require", []) if name not in ("money", "software", "reachable")]
    if bad:
        errors.append("screen.require has unknown checks: " + ", ".join(bad))
    for role, choice in (config.get("models") or {}).items():
        if not isinstance(choice, dict) or not choice.get("provider") or not choice.get("model"):
            errors.append(f"models.{role} needs provider and model")
    for stage in (config.get("notify") or {}).get("stages", []):
        if stage not in STAGE_NAMES:
            errors.append(f"notify.stages: unknown stage {stage}")
    return errors


def load_config(path=CONFIG_PATH):
    """config/demand.yaml с дефолтами. Бросает ValueError с понятным списком ошибок."""
    path = Path(path)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a mapping")
    config = _merge(DEFAULTS, loaded)
    errors = validate_config(config)
    if errors:
        raise ValueError("config/demand.yaml: " + "; ".join(errors))
    return config


def select_themes(config, ids=None):
    """Темы прогона по списку id; пустой список = все темы из конфига."""
    themes = config.get("themes") or []
    if not ids:
        return themes
    wanted = [i.strip() for i in ids if i and i.strip()]
    by_id = {t["id"]: t for t in themes}
    missing = [i for i in wanted if i not in by_id]
    if missing:
        raise ValueError("unknown themes: " + ", ".join(missing) + "; known: " + ", ".join(by_id))
    return [by_id[i] for i in wanted]


def cpc_band(config, mode=None):
    mode = mode or config["mode"]
    if mode == "any":
        return None
    return list(config["cpc"][mode])


def config_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
