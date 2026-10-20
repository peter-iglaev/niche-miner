"""Стадия 1 — разворот: из seed-фраз темы получить сотни реальных формулировок спроса.

Без модели и без денег: autocomplete Google/YouTube/Amazon/Shopify, три оператора порождения
узких ниш (пары «из A в B», «X alternative», формы инструмента) и, через MCP-мост, разворот
Ubersuggest `google_suggestions`. Каждая фраза помнит, откуда пришла.
"""
from __future__ import annotations

import itertools
import re

from scripts.pipeline import bridge
from scripts.pipeline.connectors import autocomplete
from scripts.pipeline.run import finish_stage, read_stage_jsonl, write_stage_jsonl

ROOT = None  # root передаётся явно


def normalize(phrase):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9а-яё+#./\- ]", " ", (phrase or "").lower())).strip()


def operator_phrases(theme, config):
    """Детерминированные фразы от трёх операторов. Возвращает список (phrase, operator, seed)."""
    ops = set((config.get("expand") or {}).get("operators") or [])
    shapes = (config.get("expand") or {}).get("tool_shapes") or []
    out = []
    platforms = [p.strip() for p in theme.get("platforms") or [] if p and p.strip()]
    if "pairs" in ops:
        for a, b in itertools.permutations(platforms, 2):
            out.append((f"{a} to {b} migration", "pairs", a))
            out.append((f"migrate from {a} to {b}", "pairs", a))
    if "alternatives" in ops:
        for p in platforms:
            for suffix in ("alternative", "alternatives", "vs", "pricing", "replacement"):
                out.append((f"{p} {suffix}", "alternatives", p))
    if "tool_shaped" in ops:
        for seed in theme.get("seeds") or []:
            for shape in shapes:
                out.append((f"{seed} {shape}", "tool_shaped", seed))
    for seed in theme.get("seeds") or []:
        for vertical in theme.get("verticals") or []:
            out.append((f"{seed} for {vertical}", "vertical", seed))
    return out


def _walk_strings(node, keys=("keyword", "phrase", "suggestion", "query", "text")):
    """Собрать строки-подсказки из ответа неизвестной формы (например, Ubersuggest MCP)."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_walk_strings(value, keys))
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, str):
                found.append(item)
            else:
                found.extend(_walk_strings(item, keys))
    return found


def run(folder, config, themes, *, root):
    settings = config.get("expand") or {}
    run_id = folder.name
    sources_enabled = [s for s in settings.get("sources") or [] if s in autocomplete.PROVIDERS]
    use_ubersuggest = "ubersuggest_suggestions" in (settings.get("sources") or [])
    cap = settings.get("max_phrases_per_seed", 250)
    existing = {p["normalized"]: p for p in read_stage_jsonl(folder, "expand", "phrases.jsonl")}
    records = dict(existing)
    # Журнал уже сделанных autocomplete-запросов: повторный запуск стадии не дёргает сеть заново.
    done_queries = {(q["source"], q["query"]) for q in read_stage_jsonl(folder, "expand", "queries.jsonl")}
    query_log = list(read_stage_jsonl(folder, "expand", "queries.jsonl"))
    calls, per_source, bridge_pending = 0, {}, []

    def add(phrase, theme_id, seed, operator, source, source_id=None):
        norm = normalize(phrase)
        if len(norm) < 3 or norm in records:
            return False
        records[norm] = {"phrase": phrase.strip(), "normalized": norm, "theme": theme_id, "seed": seed,
                         "operator": operator, "source": source, "source_id": source_id}
        per_source[source] = per_source.get(source, 0) + 1
        return True

    for theme in themes:
        theme_id = theme["id"]
        seeds = [s.strip() for s in theme.get("seeds") or [] if s and s.strip()]
        for seed in seeds:
            add(seed, theme_id, seed, "seed", "config")
        for phrase, operator, seed in operator_phrases(theme, config):
            add(phrase, theme_id, seed, operator, "operator")
        # autocomplete: seed сам по себе во всех источниках; seed+модификатор и «платформа alternative» — только Google
        queries = [(seed, seed, "autocomplete") for seed in seeds]
        for seed in seeds:
            for modifier in settings.get("modifiers") or []:
                queries.append((f"{seed} {modifier}", seed, "autocomplete_modifier"))
        for platform in theme.get("platforms") or []:
            queries.append((f"{platform} alternative", platform, "alternatives"))
        seen_queries = set()
        for query, seed, operator in queries:
            if query in seen_queries:
                continue
            seen_queries.add(query)
            for name in (sources_enabled if operator == "autocomplete" else [s for s in sources_enabled if s == "google"]):
                seed_count = sum(1 for r in records.values() if r["seed"] == seed and r["theme"] == theme_id)
                if seed_count >= cap:
                    break
                if (name, query) in done_queries:
                    continue
                out = autocomplete.PROVIDERS[name](run_id, query, settings, root, None)
                calls += 1
                done_queries.add((name, query))
                query_log.append({"source": name, "query": query, "status": out["status"], "phrases": len(out["phrases"]), "source_id": out["source_id"]})
                for suggestion in out["phrases"]:
                    add(suggestion, theme_id, seed, operator, name, out["source_id"])
        if use_ubersuggest:
            for start in range(0, len(seeds), 10):
                chunk = seeds[start:start + 10]
                request = bridge.plan(folder, "ubersuggest.google_suggestions",
                                      {"keywords": chunk, "country": settings.get("country", "us"), "language": settings.get("language", "en")},
                                      purpose=f"разворот темы {theme_id}", stage="expand")
                payload = bridge.response(folder, request)
                if payload is None:
                    if request["status"] == "pending":
                        bridge_pending.append(request["id"])
                    continue
                for suggestion in _walk_strings(payload):
                    seed = next((s for s in chunk if s.lower() in suggestion.lower()), chunk[0])
                    add(suggestion, theme_id, seed, "autocomplete", "ubersuggest_suggestions", request["source_id"])
    ordered = sorted(records.values(), key=lambda r: (r["theme"], r["seed"], r["normalized"]))
    write_stage_jsonl(folder, "expand", "phrases.jsonl", ordered)
    write_stage_jsonl(folder, "expand", "queries.jsonl", query_log)
    per_theme = {t["id"]: sum(1 for r in ordered if r["theme"] == t["id"]) for t in themes}
    summary = {"phrases": len(ordered), "new_this_pass": len(ordered) - len(existing), "per_theme": per_theme,
               "per_source": per_source, "autocomplete_calls": calls, "bridge_pending": bridge_pending}
    status = "pending_bridge" if bridge_pending else "done"
    finish_stage(folder, "expand", summary, status=status)
    return summary


def summary_text(summary):
    themes = ", ".join(f"{k} {v}" for k, v in (summary.get("per_theme") or {}).items())
    extra = f"; ждёт моста: {len(summary['bridge_pending'])}" if summary.get("bridge_pending") else ""
    return f"{summary['phrases']} фраз ({themes}); вызовов autocomplete {summary['autocomplete_calls']}{extra}"
