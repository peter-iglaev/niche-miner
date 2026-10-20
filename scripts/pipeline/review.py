"""Стадия 6 — проверка: interpret → skeptic → judge для лучших кластеров, на DeepSeek, чистый контекст на роль."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.pipeline import backlog, gate, screen
from scripts.pipeline.run import finish_stage, read_json, read_stage_jsonl, write_stage_jsonl
from scripts.pipeline.worker import run_role

ROLES = ("interpret", "skeptic", "judge")


def _strength(cluster):
    """Порядок разбора кластеров без чисел: сначала объём, потом остальные сигналы.

    Заказы на бирже стояли здесь первыми и тянули наверх то, что похоже на услугу, а не на
    продукт; малое число брендов в топ-5 — признак ниши, за трафик которой никто не платит.
    Оба сигнала остались, но как последние доводы при равном объёме.
    """
    money = cluster.get("signals", {}).get("money", {})
    reach = cluster.get("signals", {}).get("reachable", {})
    jobs = (money.get("upwork", {}).get("jobs") or 0) + (money.get("freelancer", {}).get("jobs") or 0)
    brands = reach.get("brands_top5_max")
    return (gate.cluster_volume(cluster.get("numbers") or {}), len(cluster.get("phrases") or []),
            jobs, -(brands if brands is not None else 5))


def bands(config):
    """Границы полос объёма, по которым раскладывается отбор в проверку."""
    edges = (config.get("review") or {}).get("volume_bands") or [20000, 2000]
    return sorted((float(e) for e in edges), reverse=True)


def band_of(cluster, edges):
    volume = gate.cluster_volume(cluster.get("numbers") or {})
    for index, edge in enumerate(edges):
        if volume >= edge:
            return index
    return len(edges)


def select(gated, config):
    """Поровну из каждой полосы объёма, а не шесть самых крупных.

    Отбор строго по убыванию объёма — это отбор самых конкурентных категорий, какие есть:
    в прогоне creator-marketing-2026-09-21 проверка разобрала шедулинг соцсетей, выбор
    email-платформы и трекинг позиций, где инкумбенты — Buffer, Mailchimp и Ahrefs, а узкие
    кластеры вроде тестирования превью или проверки доставляемости до неё не дошли вовсе.
    Объём внутри полосы по-прежнему решает, но полосы делят бюджет проверки между собой.
    """
    settings = config.get("review") or {}
    limit = int(settings.get("max_clusters") or 6)
    edges = bands(config)
    passed = sorted([c for c in gated if c["gate"]["status"] == "passed"],
                    key=lambda c: -gate.cluster_volume(c.get("numbers") or {}))
    queues = {}
    for cluster in passed:
        queues.setdefault(band_of(cluster, edges), []).append(cluster)
    chosen = []
    while len(chosen) < limit and any(queues.values()):
        for index in sorted(queues):
            if len(chosen) >= limit:
                break
            if queues[index]:
                chosen.append(queues[index].pop(0))
    if len(chosen) < limit and settings.get("include_unknown", True):
        unknown = sorted([c for c in gated if c["gate"]["status"] == "unknown"], key=_strength, reverse=True)
        chosen += unknown[: limit - len(chosen)]
    return chosen


def review_cluster(folder, config, cluster, *, root, roles=ROLES):
    out_dir = Path(folder) / "6-review" / cluster["cluster_id"]
    results, errors = {}, {}
    for role in roles:
        existing = read_json(out_dir / f"{role}.json")
        if existing and existing.get("result"):
            results[role] = existing; continue
        try:
            results[role] = run_role(folder, config, {**cluster, **{r: results[r]["result"] for r in results if r in ("interpret", "skeptic")}}, role, root=root)
        except Exception as error:  # noqa: BLE001 - записываем, не прячем
            errors[role] = f"{type(error).__name__}: {str(error)[:600]}"
            break
    return results, errors


def ask_money(folder, config, clusters, *, root):
    """Заказы на бирже по отобранным кластерам: один вызов моста на кластер.

    Раньше Upwork спрашивался на отсеве по всем шестидесяти кластерам и там же снимал их
    за отсутствие следа услуги. Из обязательных проверок он убран, но спрашивать его совсем
    перестали — и судья отметил «деньги не наблюдаемы» в каждой карточке прогона
    creator-marketing-2026-09-21. Здесь ответ ещё может изменить вердикт, а кластеров шесть,
    а не шестьдесят.
    """
    run_id = Path(folder).name
    pending = []
    for cluster in clusters:
        signals = cluster.setdefault("signals", {})
        money = signals.get("money") or {}
        if (money.get("upwork") or {}).get("status") == "collected":
            continue
        fresh = screen._money(cluster, folder, config, root, run_id, use_upwork=True)
        signals["money"] = fresh
        if fresh["upwork"].get("status") == "pending_bridge":
            pending.append(cluster["cluster_id"])
    return pending


def run(folder, config, *, root, only=None):
    run_id = Path(folder).name
    gated = read_stage_jsonl(folder, "gate", "gated.jsonl")
    if not gated:
        raise ValueError("stage gate produced nothing; run gate first")
    # Явный список кластеров задаёт отбор целиком, а не сужает автоматический: пересечение
    # молча выбрасывало названные кластеры, если автоотбор их не выбрал, и стадия разбирала
    # не то, что просили.
    chosen = [c for c in gated if c["cluster_id"] in only] if only else select(gated, config)
    if only:
        missing = [i for i in only if i not in {c["cluster_id"] for c in gated}]
        if missing:
            raise ValueError("no such clusters in the gate stage: " + ", ".join(missing))
    if (config.get("review") or {}).get("ask_upwork", True):
        waiting = ask_money(folder, config, chosen, root=root)
        if waiting:
            summary = {"selected": len(chosen), "reviewed": 0, "errors": 0, "by_decision": {},
                       "tokens": 0, "decisions_superseded": 0, "bridge_pending": waiting}
            finish_stage(folder, "review", summary, status="pending_bridge")
            return summary
    workers = int((config.get("limits") or {}).get("max_active_agents") or 4)
    reviewed = []

    def one(cluster):
        results, errors = review_cluster(folder, config, cluster, root=root)
        return cluster, results, errors

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(chosen) or 1))) as pool:
        for cluster, results, errors in pool.map(one, chosen):
            judge = (results.get("judge") or {}).get("result") or {}
            record = {**cluster, "review": {"status": "done" if not errors else "error", "errors": errors,
                                            "worker_ids": {r: results[r]["worker_id"] for r in results},
                                            "usage": {r: results[r]["usage"] for r in results}},
                      "interpret": (results.get("interpret") or {}).get("result"),
                      "skeptic": (results.get("skeptic") or {}).get("result"),
                      "judge": judge or None}
            reviewed.append(record)
            if judge:
                backlog.touch(config, cluster, run_id, status="reviewed", note=f"judge: {judge.get('decision_context')}", root=root)
    write_stage_jsonl(folder, "review", "reviewed.jsonl", reviewed)
    superseded = drop_stale_decisions(folder, {r["cluster_id"] for r in reviewed})
    by_decision = {}
    for r in reviewed:
        key = (r.get("judge") or {}).get("decision_context") or "error"
        by_decision[key] = by_decision.get(key, 0) + 1
    tokens = sum(u.get("total_tokens", 0) for r in reviewed for u in r["review"]["usage"].values())
    summary = {"selected": len(chosen), "reviewed": sum(r["review"]["status"] == "done" for r in reviewed),
               "errors": sum(r["review"]["status"] == "error" for r in reviewed), "by_decision": by_decision, "tokens": tokens,
               "decisions_superseded": len(superseded)}
    finish_stage(folder, "review", summary, status="done" if not summary["errors"] else "blocked")
    return summary


def summary_text(summary):
    dec = ", ".join(f"{k} {v}" for k, v in summary["by_decision"].items()) or "—"
    if summary.get("bridge_pending"):
        return f"ждём биржу по {len(summary['bridge_pending'])} кластерам из {summary['selected']}"
    text = f"проверено {summary['reviewed']} из {summary['selected']} ({dec}); токенов {summary['tokens']}"
    if summary.get("errors"):
        text += f"; с ошибкой {summary['errors']}"
    return text


DECISION_STATUSES = ("build_test", "investigate", "dropped", "duplicate")


def drop_stale_decisions(folder, reviewed_ids):
    """Снять решения по кластерам, которых больше нет в результате стадии «проверка».

    Решение выводится из вердикта судьи, поэтому пережить перезапуск проверки оно не может:
    иначе в сводке висит решение по кластеру, который в этом прогоне уже не рассматривался.
    История остаётся в events.jsonl.
    """
    from scripts.pipeline.run import load_manifest, record_event, save_manifest
    manifest = load_manifest(folder)
    stale = [cid for cid in (manifest.get("decisions") or {}) if cid not in reviewed_ids]
    if not stale:
        return []
    for cluster_id in stale:
        manifest["decisions"].pop(cluster_id, None)
    save_manifest(folder, manifest)
    record_event(folder, "decisions_superseded", {"cluster_ids": stale, "reason": "стадия «проверка» перезапущена"})
    return stale


def decide(folder, config, cluster_id, status, reason, *, author="root", root):
    """Записать решение по кластеру. build_test только при вердикте судьи build_test и пройденных гейтах."""
    if status not in DECISION_STATUSES:
        raise ValueError("status must be one of: " + ", ".join(DECISION_STATUSES))
    if not (reason or "").strip():
        raise ValueError("reason is required")
    from scripts.pipeline.run import load_manifest, now, record_event, save_manifest
    reviewed = {r["cluster_id"]: r for r in read_stage_jsonl(folder, "review", "reviewed.jsonl")}
    cluster = reviewed.get(cluster_id)
    if cluster is None:
        raise ValueError(f"cluster {cluster_id} was not reviewed")
    if status == "build_test":
        if ((cluster.get("judge") or {}).get("decision_context") != "build_test") or not (cluster.get("gate") or {}).get("passed"):
            raise ValueError("build_test requires a judge verdict build_test and passed gates")
    manifest = load_manifest(folder)
    manifest["decisions"][cluster_id] = {"status": status, "reason": reason.strip(), "author": author, "at": now(),
                                         "judge": (cluster.get("judge") or {}).get("decision_context")}
    save_manifest(folder, manifest)
    record_event(folder, "decided", {"cluster_id": cluster_id, "status": status})
    backlog.touch(config, cluster, Path(folder).name, status=status, decision={"status": status, "run": Path(folder).name, "reason": reason.strip()[:300]}, root=root)
    return manifest["decisions"][cluster_id]


def decide_auto(folder, config, *, root):
    """Решения по вердиктам судьи: build_test→build_test, investigate→investigate, drop→dropped. Ошибка ревью → investigate с пометкой."""
    out = {}
    for cluster in read_stage_jsonl(folder, "review", "reviewed.jsonl"):
        judge = cluster.get("judge") or {}
        verdict = judge.get("decision_context")
        status = {"build_test": "build_test", "investigate": "investigate", "drop": "dropped"}.get(verdict, "investigate")
        if status == "build_test" and not (cluster.get("gate") or {}).get("passed"):
            status = "investigate"
        reason = (judge.get("rationale") or "; ".join(cluster["review"].get("errors", {}).values()) or "вердикт судьи отсутствует")[:1500]
        out[cluster["cluster_id"]] = decide(folder, config, cluster["cluster_id"], status, reason, author="root(auto-from-judge)", root=root)
    return out
