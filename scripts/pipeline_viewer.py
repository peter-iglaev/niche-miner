#!/usr/bin/env python3
"""Собирает docs/pipeline-map.html: встраивает снапшот всех прогонов из runs/.

Страница остаётся автономной: данные, стили и скрипт лежат внутри HTML, сеть и сервер не нужны.
Перегенерация: python3 scripts/pipeline_viewer.py [--open]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.routing import load_routing, resolve as resolve_routing  # noqa: E402
from scripts.validate import ROLES  # noqa: E402

RUNS_DIR = ROOT / "runs"
PAGE = ROOT / "docs" / "pipeline-map.html"
APP_JS = ROOT / "scripts" / "pipeline_viewer.js"
STYLE = ROOT / "scripts" / "pipeline_viewer.css"
TZ = ZoneInfo("Europe/Madrid")

DATA_START = "<!-- RUNS-DATA-START -->"
DATA_END = "<!-- RUNS-DATA-END -->"

# Roles a candidate card treats as "still open" until accepted; not the canonical role list.
GATE_ROLES = ("money", "distribution", "feasibility", "skeptic", "judge")
REVIEW_ROLES = GATE_ROLES
STAGE_ORDER = ("staging", "discovery", "money", "distribution", "feasibility", "skeptic", "judge", "decision")

ROLE_RU = {
    "discovery": "Разведка",
    "demand": "Спрос",
    "money": "Деньги",
    "competition": "Конкуренты",
    "distribution": "Каналы",
    "feasibility": "Реализуемость",
    "skeptic": "Скептик",
    "judge": "Судья",
}
STAGE_PURPOSE = {
    "discovery": "Находит конкретную работу покупателя: кто, когда и каким костылём её делает сейчас.",
    "demand": "Проверяет, насколько часто и остро эта работа возникает.",
    "money": "Ищет доказательства, что за эту работу уже платят деньгами или временем людей.",
    "competition": "Смотрит, кто уже продаёт решение этой работы и на каких условиях.",
    "distribution": "Проверяет, есть ли реальный способ дотянуться до покупателя.",
    "feasibility": "Определяет минимальный кусок, который можно сделать и продать.",
    "skeptic": "Независимо атакует выводы: проверяет, держат ли источники утверждения.",
    "judge": "Выносит итог по замороженным материалам, новых фактов не собирает.",
}
SOURCE_KIND = {
    "brave": ("search", "Поиск"),
    "tavily": ("search", "Поиск"),
    "direct_fetch": ("page", "Страница"),
    "ted": ("tender", "Тендеры ЕС"),
    "hn_algolia": ("discussion", "Обсуждения практиков"),
    "wayback": ("archive", "Веб-архив"),
    "telegram": ("delivery", "Отправка отчёта"),
}
KIND_PURPOSE = {
    "search": "Найти страницы-зацепки по запросу. Выдача поиска — не доказательство.",
    "page": "Открыть и сохранить конкретную страницу, на которую потом ссылается утверждение.",
    "tender": "Публичные закупки ЕС: подтверждают бюджет покупателя, а не оплату нашей задачи.",
    "discussion": "Рассказы практиков: показывают, как работу делают сейчас, но не распространённость.",
    "archive": "Проверить, как страница выглядела на заявленную дату.",
    "delivery": "Отправка готового отчёта наружу; к исследованию не относится.",
}

INDUSTRY_RU = {
    "accounting_finance_ops": "Бухгалтерия и финоперации",
    "construction_trades": "Стройка и подряды",
    "devops_cloud_security": "Devops / облака / безопасность",
    "healthcare_administration": "Клиники и мед-администрация",
    "hospitality_operations": "Общепит и гостеприимство",
    "import_export_logistics": "Логистика и ВЭД",
    "insurance_operations": "Страховые операции",
    "legal_administration": "Юридическая администрация",
    "local_service_businesses": "Локальные сервисные бизнесы",
    "manufacturing_distribution": "Производство и дистрибуция",
    "procurement": "Закупки",
    "real_estate_property_management": "Недвижимость и управление",
    "recruitment_operations": "Рекрутинг и staffing",
    "ecommerce": "Ecommerce",
    "ai_agents_mcp": "AI-агенты / MCP",
}


def industry_label(value: str) -> str:
    return INDUSTRY_RU.get(value, value or "")


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def load_jsonl(path: Path) -> list:
    rows = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except Exception:
        pass
    return rows


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def local_hhmm(dt) -> str:
    return dt.astimezone(TZ).strftime("%H:%M") if dt else ""


def short_agent(agent_id) -> str:
    return "…" + str(agent_id)[-6:] if agent_id else ""


def shorten(text, limit: int = 240) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def classify_verdict(text: str) -> str:
    low = (text or "").lower()
    if "unknown" in low or "defer" in low:
        return "unknown"
    if "fail" in low:
        return "fail"
    if "pass" in low:
        return "pass"
    return ""


def money_level(text: str) -> str:
    m = re.search(r"level\s*[:=]?\s*([0-5])", text or "", re.IGNORECASE)
    return "L" + m.group(1) if m else ""


def candidate_slug(candidate_id: str) -> str:
    return re.sub(r"^(hyp|disc)-", "", candidate_id)


def result_path(run_dir: Path, attempt: dict):
    path = attempt.get("result_path")
    return run_dir / path if path else None


def role_snapshot(run_dir: Path, assignment: dict) -> dict:
    attempts = assignment.get("attempts") or []
    accepted = [a for a in attempts if a.get("status") == "accepted"]
    latest = attempts[-1] if attempts else {}
    payload = {"status": latest.get("status", "none"), "attempts": len(attempts), "agent": short_agent(latest.get("worker_id"))}
    if len(attempts) > 1:
        payload["note"] = "попыток: %d" % len(attempts)
    if accepted:
        path = result_path(run_dir, accepted[-1])
        result = load_json(path) if path else None
        if result:
            ro = result.get("role_output") or {}
            findings = ro.get("findings") or []
            joined = " ".join(str(f) for f in findings)
            payload["excerpt"] = shorten(findings[0] if findings else "", 260)
            role = assignment.get("role")
            if role == "money":
                payload["verdict"] = money_level(joined)
            elif role in ("distribution", "feasibility"):
                payload["verdict"] = classify_verdict(joined)
            assessment = result.get("assessment") or {}
            if assessment:
                payment = (assessment.get("payment") or {}).get("level")
                if payment is not None:
                    payload["verdict"] = "L%s" % payment
                dist = (assessment.get("distribution") or {}).get("status")
                feas = (assessment.get("feasibility") or {}).get("status")
                if dist:
                    payload["distribution"] = dist
                if feas:
                    payload["feasibility"] = feas
                payload["ratings"] = {k: (v or {}).get("value") for k, v in (assessment.get("ratings") or {}).items()}
    return payload


def build_candidates(run_dir: Path, manifest: dict, claims_by_candidate: dict) -> list:
    assignments = manifest.get("assignments") or []
    decisions = {d.get("candidate_id"): d for d in (manifest.get("decisions") or []) if isinstance(d, dict)}
    by_candidate = {}
    for a in assignments:
        by_candidate.setdefault(a.get("candidate_id"), []).append(a)

    staged = {}
    for name in ("hypotheses.jsonl", "hypotheses_selected.jsonl"):
        for row in load_jsonl(run_dir / name):
            cid = row.get("id")
            if cid:
                staged.setdefault(cid, {}).update({k: v for k, v in row.items() if v is not None})

    candidates = []
    candidates_dir = run_dir / "candidates"
    dirs = sorted([p for p in candidates_dir.iterdir() if p.is_dir()]) if candidates_dir.is_dir() else []
    known_ids = [p.name for p in dirs]
    promoted = set(known_ids) | set(by_candidate)
    for cid in sorted(promoted):
        cdir = candidates_dir / cid
        hypothesis = load_json(cdir / "hypothesis.json", {}) if cdir.is_dir() else {}
        hypothesis = {**staged.get(cid, {}), **(hypothesis or {})}
        roles = {}
        for a in by_candidate.get(cid, []):
            roles[a.get("role")] = role_snapshot(run_dir, a)
        judge = roles.get("judge") or {}
        money = roles.get("money") or {}
        tier = judge.get("verdict") or money.get("verdict") or "—"
        missing = [r for r in REVIEW_ROLES if roles.get(r, {}).get("status") != "accepted"]
        decision = decisions.get(cid)
        if decision:
            nxt = "Решение: %s%s" % (decision.get("status", ""), (" — " + str(decision.get("reason"))[:160]) if decision.get("reason") else "")
        elif manifest.get("status") == "complete":
            nxt = "Прогон завершён без решения по этому кандидату."
        elif missing:
            nxt = "Ждём: " + ", ".join(missing)
        else:
            nxt = "Готов к решению root."
        candidates.append({
            "id": cid,
            "title": industry_label(hypothesis.get("buyer_industry") or hypothesis.get("industry") or candidate_slug(cid)) or candidate_slug(cid),
            "subtitle": shorten(hypothesis.get("job_to_be_done") or candidate_slug(cid), 120),
            "industry": hypothesis.get("buyer_industry") or hypothesis.get("industry") or "",
            "who": shorten(hypothesis.get("who") or "", 220),
            "job": shorten(hypothesis.get("job_to_be_done") or "", 300),
            "trigger": shorten(hypothesis.get("trigger") or "", 240),
            "pain": shorten(hypothesis.get("pain") or "", 300),
            "claims": claims_by_candidate.get(cid, 0),
            "roles": roles,
            "tier": tier,
            "next": nxt,
            "decision": {"status": decision.get("status"), "reason": shorten(decision.get("reason") or "", 260)} if decision else None,
        })
    return candidates


def build_timeline(run_dir: Path, manifest: dict):
    events = load_jsonl(run_dir / "events.jsonl")
    stamps = [parse_ts(e.get("at")) for e in events]
    stamps = [s for s in stamps if s]
    if not stamps:
        return {"steps": [], "tasks": []}
    start = min(stamps)
    tracks = {}
    event_minutes = {}
    for e in events:
        ts = parse_ts(e.get("at"))
        if not ts:
            continue
        minute = round((ts - start).total_seconds() / 60, 2)
        event_minutes.setdefault(minute, []).append(e)
        details = e.get("details") or {}
        aid = details.get("assignment_id")
        if not aid:
            continue
        track = tracks.setdefault(aid, {"id": aid, "attempts": set(), "rejected": False})
        track["attempts"].add(details.get("attempt") or 1)
        kind = e.get("event")
        if kind == "queued" and "q" not in track:
            track["q"] = minute
        elif kind == "bound" and "b" not in track:
            track["b"] = minute
            track["worker"] = short_agent(details.get("agent_id"))
        elif kind == "accepted":
            track["a"] = minute
            track["worker"] = short_agent(details.get("agent_id")) or track.get("worker")
        elif kind == "rejected":
            track["rejected"] = True

    assignments = {a.get("id"): a for a in (manifest.get("assignments") or [])}
    tasks = []
    for aid, track in tracks.items():
        assignment = assignments.get(aid) or {}
        task = {
            "id": aid,
            "candidate": assignment.get("candidate_id") or "",
            "role": assignment.get("role") or "",
            "worker": track.get("worker") or "",
            "attempts": len(track["attempts"]),
            "rejected": track["rejected"],
        }
        for key in ("q", "b", "a"):
            if key in track:
                task[key] = track[key]
        if "q" not in task and "b" not in task and "a" not in task:
            continue
        tasks.append(task)

    minutes = sorted(event_minutes)
    if len(minutes) > 14:
        picks = [minutes[0]]
        step = (len(minutes) - 1) / 12.0
        for i in range(1, 12):
            picks.append(minutes[int(round(i * step))])
        picks.append(minutes[-1])
        minutes = sorted(set(picks))

    steps = []
    base_ts = start.astimezone(TZ).timestamp()
    for minute in minutes:
        evs = event_minutes.get(minute) or []
        priority = {"rejected": 4, "accepted": 3, "bound": 2, "queued": 1}
        evs_sorted = sorted(evs, key=lambda e: -priority.get(e.get("event"), 0))
        head = evs_sorted[0]
        names = []
        for e in evs_sorted:
            aid = (e.get("details") or {}).get("assignment_id")
            if aid:
                names.append(candidate_slug(aid))
            if len(names) == 2:
                break
        verbs = {"queued": "поставлено", "bound": "запущено", "accepted": "принято", "rejected": "отклонено"}
        verb = verbs.get(head.get("event"), head.get("event") or "событие")
        note = verb + (": " + ", ".join(names) if names else "")
        if len(evs) > 1:
            note += " (+%d)" % (len(evs) - 1)
        label = datetime.fromtimestamp(base_ts + minute * 60, TZ).strftime("%H:%M")
        steps.append({"m": minute, "label": label, "note": note})
    return {"steps": steps, "tasks": tasks}


def build_stats(run_dir: Path, manifest: dict, candidates: list, decisions: list) -> dict:
    claims = load_jsonl(run_dir / "claims.jsonl")
    searches = load_jsonl(run_dir / "searches.jsonl")
    sources_dir = run_dir / "sources"
    sources = len([p for p in sources_dir.glob("*.meta.json")]) if sources_dir.is_dir() else 0
    assignments = manifest.get("assignments") or []
    attempts = [a for assignment in assignments for a in (assignment.get("attempts") or [])]
    accepted = [a for a in attempts if a.get("status") == "accepted"]
    workers = {a.get("worker_id") for a in attempts if a.get("worker_id")}
    research_workers = {a.get("worker_id") for assignment in assignments for a in (assignment.get("attempts") or [])
                        if a.get("worker_id") and assignment.get("role") != "orchestrator"}
    discovery = [a for a in assignments if a.get("role") == "discovery"]
    discovery_accepted = [a for a in discovery if (a.get("attempts") or [{}])[-1].get("status") == "accepted"]
    review = [a for a in assignments if a.get("role") in REVIEW_ROLES]
    review_accepted = [a for a in review if (a.get("attempts") or [{}])[-1].get("status") == "accepted"]
    limits = (load_json(run_dir / "config.json", {}) or {}).get("limits", {}) if manifest else {}
    industries = {c.get("industry") for c in candidates if c.get("industry")}
    return {
        "candidates": len(candidates),
        "industries": len(industries),
        "discovery_total": len(discovery),
        "discovery_accepted": len(discovery_accepted),
        "review_total": len(review),
        "review_accepted": len(review_accepted),
        "claims": len(claims),
        "searches": len(searches),
        "searches_ok": len([s for s in searches if s.get("retrieval_status") == "collected"]),
        "sources": sources,
        "assignments_used": len(assignments),
        "assignments_accepted": len(accepted),
        "workers": len(research_workers),
        "decisions": len(decisions),
        "assignments_limit": limits.get("max_assignments"),
        "agents_limit": limits.get("max_active_agents"),
    }


def source_stats(sources: dict) -> dict:
    totals = sources.get("totals", {})
    return {"sources_cited": totals.get("cited", 0), "sources_orphan": totals.get("orphan", 0),
            "sources_failed": totals.get("failed", 0),
            "claims_without_source": totals.get("claims_without_source", 0)}


def build_workers(manifest: dict, candidates: list, tasks: list) -> list:
    by_agent = {}
    for assignment in manifest.get("assignments") or []:
        attempts = assignment.get("attempts") or []
        if not attempts:
            continue
        latest = attempts[-1]
        agent = latest.get("worker_id")
        if not agent:
            continue
        by_agent.setdefault(agent, []).append({
            "assignment": assignment,
            "status": latest.get("status"),
            "attempts": len(attempts),
        })
    candidates_by_id = {c["id"]: c for c in candidates}
    titles = {t["id"]: t for t in tasks}
    workers = []
    root_tasks = [a for a in (manifest.get("assignments") or []) if a.get("role") == "orchestrator"]
    workers.append({
        "id": "root",
        "badge": "R",
        "label": "root",
        "sub": "координатор: очередь, привязка агентов, приёмка, решения, отчёт",
        "busy": manifest.get("status") != "complete",
        "order": -1,
        "rows": [
            {"role": "ПРОГОН", "items": [{"candidate": None, "task": None,
                                          "text": "заданий поставлено: %d" % len(manifest.get("assignments") or [])}]},
            {"role": "РЕШЕНИЯ", "items": [{"candidate": None, "task": None,
                                           "text": "decisions: %d" % len(manifest.get("decisions") or [])}]},
        ],
    })
    for agent, items in by_agent.items():
        roles = {}
        for item in items:
            role = item["assignment"].get("role")
            cid = item["assignment"].get("candidate_id")
            ctitle = (candidates_by_id.get(cid) or {}).get("title") or candidate_slug(cid or "")
            label = (ctitle.split(":")[0] if ctitle else candidate_slug(cid or ""))[:28]
            task = titles.get(item["assignment"].get("id"))
            roles.setdefault(role, []).append({
                "candidate": cid,
                "task": item["assignment"].get("id"),
                "text": label + (" (попытка %d)" % item["attempts"] if item["attempts"] > 1 else ""),
                "status": item["status"],
            })
        rows = [{"role": role.upper(), "items": vals} for role, vals in roles.items()]
        busy = any(item["status"] in ("bound", "prepared") for item in items)
        workers.append({
            "id": agent,
            "badge": short_agent(agent)[-4:].upper(),
            "label": "воркер " + short_agent(agent),
            "sub": "ролей: %d, заданий: %d" % (len(roles), len(items)),
            "busy": busy,
            "order": len(items),
            "rows": rows,
        })
    workers[1:] = sorted(workers[1:], key=lambda w: -w["order"])
    return workers


def build_notes(run_dir: Path, manifest: dict, tasks: list) -> list:
    notes = []
    if not manifest:
        notes.append("Манифеста нет: это технический прогон (проверка доступности поиска и источников), а не исследование.")
    retries = [t for t in tasks if t.get("rejected")]
    for t in retries:
        notes.append("Задание <code>%s</code> сначала было отклонено (attempt 1) и принято только со второй попытки." % t["id"])
    phase = manifest.get("phase")
    decisions = manifest.get("decisions") or []
    if phase in ("decision", "review") and not decisions:
        notes.append("Фаза <code>%s</code> выставлена, но решений ещё нет и отчёта нет — фаза опережает факты." % phase)
    if phase == "complete" and not manifest.get("report_path"):
        notes.append("Прогон помечен завершённым, но <code>report.md</code> в манифесте не указан.")
    decisions_by_status = {}
    for d in decisions:
        status = (d or {}).get("status") if isinstance(d, dict) else None
        if status:
            decisions_by_status[status] = decisions_by_status.get(status, 0) + 1
    if decisions_by_status:
        notes.append("Решения: " + ", ".join("%s — %d" % (k, v) for k, v in sorted(decisions_by_status.items())) + ".")
    claims = load_jsonl(run_dir / "claims.jsonl")
    no_source = [c for c in claims if not (c.get("source_id") or c.get("source_url"))]
    if no_source:
        notes.append("У %d claim нет привязки к источнику — это то, что ищет skeptic." % len(no_source))
    searches = load_jsonl(run_dir / "searches.jsonl")
    errors = [s for s in searches if s.get("retrieval_status") != "collected"]
    if errors:
        notes.append("Поиски: %d ошибок из %d (429/DNS) — они записаны в журнал, а не замаскированы." % (len(errors), len(searches)))
    return notes


def _attempt_paths(run_dir: Path):
    """Every attempt folder in the run, newest attempt last."""
    work = run_dir / "work"
    if not work.is_dir():
        return []
    return sorted(p for p in work.glob("*/attempt-*") if p.is_dir())


def read_attempts(run_dir: Path) -> list:
    """Frozen inputs and returned results per attempt; missing files are simply absent."""
    records = []
    for folder in _attempt_paths(run_dir):
        packet = load_json(folder / "input.json", {}) or {}
        delivered = True
        result = load_json(folder / "result.json", None)
        if result is None:
            delivered = False
            result = load_json(folder / "rejected.json", {}) or {}
        usage = load_json(folder / "usage.json", {}) or {}
        records.append({"usage": usage,
                        "delivered": delivered,
                        "result_path": str((folder / "result.json").relative_to(run_dir)),
                        "assignment": packet.get("assignment_id") or folder.parent.name,
                        "attempt": packet.get("attempt") or 1,
                        "role": packet.get("role") or result.get("role") or "",
                        "candidate": packet.get("candidate_id") or "",
                        "stage": packet.get("stage") or "",
                        "worker": result.get("worker_id") or "",
                        "objective": (packet.get("packet") or {}).get("research_focus")
                                     or (packet.get("packet") or {}).get("task")
                                     or packet.get("objective") or "",
                        "execution_policy": packet.get("execution_policy") or {},
                        "search_log": result.get("search_log") or [],
                        "claims": result.get("claims") or []})
    return records


def source_id_from_ref(ref) -> str:
    """Older runs referenced an archive path instead of a source_id; the file stem is that id."""
    name = Path(str(ref or "")).name
    stem = name.split(".")[0]
    return stem if stem.startswith("src-") else ""


def claim_source_ids(claim: dict, by_url: dict) -> list:
    """Source ids a claim points at, including the older archive-path and plain-URL references."""
    ids = []
    for value in [claim.get("source_id")] + [source_id_from_ref(claim.get("locator"))] + \
            [source_id_from_ref((m or {}).get("raw_response_ref")) for m in (claim.get("metrics") or [])]:
        if value and value not in ids:
            ids.append(value)
    url = claim.get("source_url")
    if not ids and url and by_url.get(url):
        ids.append(by_url[url])
    return ids


def build_sources(run_dir: Path, manifest: dict, attempts: list) -> dict:
    """Join every archived source to the agent that pulled it and to the claims that cite it.

    New runs stamp the caller directly (source_usage.jsonl, searches.jsonl, meta sidecar). Older runs
    predate that, so attribution is reconstructed backwards from result.json and claims.jsonl. The
    row always says which of those paths was used, and a source nobody references stays an orphan
    instead of being attached to a plausible agent.
    """
    sources_dir = run_dir / "sources"
    metas = []
    if sources_dir.is_dir():
        for path in sorted(sources_dir.glob("*.meta.json")):
            record = load_json(path)
            if record:
                metas.append(record)

    assignments = {a.get("id"): a for a in (manifest.get("assignments") or [])}
    by_attempt = {record["assignment"]: record for record in attempts}
    by_url = {}
    for record in metas:
        url = record.get("request_url")
        if url:
            by_url.setdefault(url, record.get("source_id"))

    # candidate + role + worker for an assignment id, even when its input.json is gone
    def about(assignment_id):
        record = by_attempt.get(assignment_id) or {}
        assignment = assignments.get(assignment_id) or {}
        return {"assignment": assignment_id or "",
                "role": record.get("role") or assignment.get("role") or "",
                "worker": record.get("worker") or "",
                "stage": record.get("stage") or "",
                "candidate": record.get("candidate") or assignment.get("candidate_id") or "",
                "objective": record.get("objective") or ""}

    stamped, reasons, cited_by = {}, {}, {}
    for row in load_jsonl(run_dir / "source_usage.jsonl"):
        sid = row.get("source_id")
        if sid and row.get("assignment_id"):
            stamped.setdefault(sid, ("журнал использования", row["assignment_id"]))
    for row in load_jsonl(run_dir / "searches.jsonl"):
        sid, aid = row.get("source_id"), row.get("assignment_id")
        if sid and aid:
            stamped.setdefault(sid, ("журнал поисков", aid))
    for record in metas:
        sid, aid = record.get("source_id"), record.get("first_assignment_id")
        if sid and aid:
            stamped.setdefault(sid, ("метка при скачивании", aid))

    for record in attempts:
        for entry in record["search_log"]:
            sid = entry.get("source_id") or source_id_from_ref(entry.get("raw_response_ref"))
            if not sid:
                continue
            stamped.setdefault(sid, ("по отчёту агента", record["assignment"]))
            note = entry.get("notes") or entry.get("query")
            if note and sid not in reasons:
                reasons[sid] = shorten(note, 320)
        for claim in record["claims"]:
            for sid in claim_source_ids(claim, by_url):
                stamped.setdefault(sid, ("по отчёту агента", record["assignment"]))

    claim_authors = {}
    for claim in load_jsonl(run_dir / "claims.jsonl"):
        for sid in claim_source_ids(claim, by_url):
            cited_by.setdefault(sid, []).append({"id": claim.get("id"), "candidate": claim.get("candidate_id"),
                                                 "author": claim.get("author_id"),
                                                 "text": shorten(claim.get("claim") or "", 220)})
            claim_authors.setdefault(sid, (claim.get("author_id"), claim.get("candidate_id")))

    rows, by_kind, by_agent, by_role, by_candidate, by_attribution = [], {}, {}, {}, {}, {}
    for record in metas:
        sid = record.get("source_id") or ""
        provider = record.get("provider") or "—"
        kind, kind_label = SOURCE_KIND.get(provider, ("other", provider))
        origin, assignment_id = stamped.get(sid, (None, None))
        info = about(assignment_id) if assignment_id else {"assignment": "", "role": "", "worker": "",
                                                           "stage": "", "candidate": "", "objective": ""}
        if not info["worker"] and sid in claim_authors:
            info["worker"] = claim_authors[sid][0] or ""
            info["candidate"] = info["candidate"] or (claim_authors[sid][1] or "")
            origin = origin or "по утверждениям"
        claims = cited_by.get(sid, [])
        row = {
            "id": sid,
            "kind": kind,
            "kind_label": kind_label,
            "provider": provider,
            "url": shorten(record.get("request_url") or "", 200),
            "query": shorten(record.get("query") or "", 160),
            "fetched_at": local_hhmm(parse_ts(record.get("fetched_at"))),
            "date": (record.get("fetched_at") or "")[:10],
            "status": record.get("retrieval_status") or "",
            "http": record.get("http_status"),
            "error": shorten(record.get("error") or "", 160),
            "assignment": info["assignment"],
            "role": info["role"],
            "role_label": ROLE_RU.get(info["role"], info["role"]),
            "worker": short_agent(info["worker"]),
            "stage": info["stage"],
            "candidate": info["candidate"],
            "purpose": reasons.get(sid) or shorten(info["objective"], 320),
            "claims": len(claims),
            "claim_list": claims[:6],
            "attribution": origin or "не атрибутирован",
            "orphan": not assignment_id and not claims,
        }
        rows.append(row)
        kind_bucket = by_kind.setdefault(kind, {"key": kind, "label": kind_label, "total": 0, "failed": 0,
                                                "cited": 0, "orphan": 0, "purpose": KIND_PURPOSE.get(kind, "")})
        kind_bucket["total"] += 1
        kind_bucket["failed"] += row["status"] != "collected"
        kind_bucket["cited"] += bool(row["claims"])
        kind_bucket["orphan"] += row["orphan"]
        agent_key = row["worker"] or "не атрибутировано"
        agent_bucket = by_agent.setdefault(agent_key, {"agent": agent_key, "total": 0, "cited": 0,
                                                       "roles": [], "kinds": {}})
        agent_bucket["total"] += 1
        agent_bucket["cited"] += bool(row["claims"])
        if row["role"] and row["role"] not in agent_bucket["roles"]:
            agent_bucket["roles"].append(row["role"])
        agent_bucket["kinds"][kind] = agent_bucket["kinds"].get(kind, 0) + 1
        if row["role"]:
            role_bucket = by_role.setdefault(row["role"], {"role": row["role"], "label": ROLE_RU.get(row["role"], row["role"]),
                                                           "total": 0, "cited": 0})
            role_bucket["total"] += 1
            role_bucket["cited"] += bool(row["claims"])
        if row["candidate"]:
            by_candidate[row["candidate"]] = by_candidate.get(row["candidate"], 0) + 1
        by_attribution[row["attribution"]] = by_attribution.get(row["attribution"], 0) + 1

    claims = load_jsonl(run_dir / "claims.jsonl")
    orphans = [r for r in rows if r["orphan"]]
    uncited_pages = [r for r in rows if r["kind"] == "page" and not r["claims"] and not r["orphan"]]
    failed = [r for r in rows if r["status"] != "collected"]
    no_source = [c for c in claims if not claim_source_ids(c, by_url)]
    snippet_only = [c for c in claims if (c.get("source_type") or "") in ("search_result", "serp", "search_snippet")]
    diagnostics = []
    if orphans:
        diagnostics.append({"level": "warn", "count": len(orphans),
                            "text": "источников скачано, но ни один агент на них не сослался — потраченный бюджет без следа в выводах"})
    if uncited_pages:
        diagnostics.append({"level": "info", "count": len(uncited_pages),
                            "text": "страниц открыто агентом, но ни одно утверждение на них не ссылается"})
    if failed:
        diagnostics.append({"level": "warn", "count": len(failed),
                            "text": "обращений завершились ошибкой (429, DNS, HTTP) — они сохранены как ошибки, а не как данные"})
    if no_source:
        diagnostics.append({"level": "error", "count": len(no_source),
                            "text": "утверждений не удалось связать ни с одним сохранённым источником"})
    if snippet_only:
        diagnostics.append({"level": "error", "count": len(snippet_only),
                            "text": "утверждений опираются только на сниппет поисковой выдачи, а не на открытую страницу"})
    unattributed = by_attribution.get("не атрибутирован", 0)
    if unattributed:
        diagnostics.append({"level": "info", "count": unattributed,
                            "text": "источников не привязаны к заданию: агент не передал --assignment при вызове"})

    rows.sort(key=lambda r: (r["date"], r["fetched_at"], r["id"]))
    return {"rows": rows, "by_kind": sorted(by_kind.values(), key=lambda b: -b["total"]),
            "by_agent": sorted(by_agent.values(), key=lambda b: -b["total"]),
            "by_role": sorted(by_role.values(), key=lambda b: -b["total"]),
            "by_candidate": by_candidate, "by_attribution": by_attribution,
            "diagnostics": diagnostics,
            "totals": {"sources": len(rows), "cited": len([r for r in rows if r["claims"]]),
                       "orphan": len(orphans), "failed": len(failed),
                       "claims": len(claims), "claims_without_source": len(no_source)}}


def build_agents(run_dir: Path, manifest: dict, config: dict, attempts: list, sources: dict) -> list:
    """Per research role: which model was requested, which was actually recorded, and what it produced."""
    frozen = config.get("model_routing")
    # Claims are counted per role from that role's own results: one worker often covers several
    # roles, so counting by worker id would credit the same claim to every role it played.
    tokens_by_role, cost_calls = {}, {}
    for record in attempts:
        usage = (record.get("usage") or {}).get("usage") or {}
        if record["role"] and usage:
            bucket = tokens_by_role.setdefault(record["role"], {"prompt": 0, "completion": 0, "total": 0})
            bucket["prompt"] += usage.get("prompt_tokens") or 0
            bucket["completion"] += usage.get("completion_tokens") or 0
            bucket["total"] += usage.get("total_tokens") or 0
            cost_calls[record["role"]] = cost_calls.get(record["role"], 0) + (usage.get("calls") or 0)
    accepted_results = {a.get("result_path") for assignment in (manifest.get("assignments") or [])
                        for a in (assignment.get("attempts") or []) if a.get("status") == "accepted" and a.get("result_path")}
    claims_by_role = {}
    for record in attempts:
        if record["role"] and record["delivered"] and record["result_path"] in accepted_results:
            claims_by_role[record["role"]] = claims_by_role.get(record["role"], 0) + len(record["claims"])
    sources_by_role = {b["role"]: b for b in sources.get("by_role", [])}
    assignments = manifest.get("assignments") or []
    policy_by_role = {}
    for record in attempts:
        if record["role"] and record["role"] not in policy_by_role and record["execution_policy"]:
            policy_by_role[record["role"]] = record["execution_policy"]

    agents = []
    for role in ROLES:
        role_assignments = [a for a in assignments if a.get("role") == role]
        attempts_of_role = [att for a in role_assignments for att in (a.get("attempts") or [])]
        accepted = [a for a in role_assignments if (a.get("attempts") or [{}])[-1].get("status") == "accepted"]
        workers = sorted({short_agent(a.get("worker_id")) for a in attempts_of_role if a.get("worker_id")})
        actual = sorted({"%s / %s" % (a.get("provider") or "—", a.get("model") or "—")
                         for a in attempts_of_role if a.get("provider") or a.get("model")})
        stage = policy_by_role.get(role, {}).get("stage") or _default_stage(role)
        requested = resolve_routing(frozen, role, stage) if frozen else None
        frozen_policy = policy_by_role.get(role) or {}
        if frozen_policy.get("provider") or frozen_policy.get("model"):
            requested = {"provider": frozen_policy.get("provider"), "model": frozen_policy.get("model"),
                         "reasoning_effort": frozen_policy.get("reasoning_effort"),
                         "routing_source": frozen_policy.get("routing_source") or "packet"}
        agents.append({
            "role": role,
            "label": ROLE_RU.get(role, role),
            "purpose": (STAGE_PURPOSE.get(role) or ""),
            "assignments": len(role_assignments),
            "accepted": len(accepted),
            "workers": workers,
            "model_class": frozen_policy.get("model_class"),
            "requested": requested,
            "actual": actual,
            "claims": claims_by_role.get(role, 0),
            "tokens": tokens_by_role.get(role),
            "model_calls": cost_calls.get(role, 0),
            "sources": (sources_by_role.get(role) or {}).get("total", 0),
        })
    return agents


def _default_stage(role: str) -> str:
    if role == "discovery":
        return "light_validation"
    if role in ("skeptic", "judge"):
        return "deep_investigation"
    return "serious_validation"


def build_run(run_dir: Path) -> dict:
    manifest = load_json(run_dir / "manifest.json", {}) or {}
    config = load_json(run_dir / "config.json", {}) or {}
    claims = load_jsonl(run_dir / "claims.jsonl")
    claims_by_candidate = {}
    for c in claims:
        cid = c.get("candidate_id")
        claims_by_candidate[cid] = claims_by_candidate.get(cid, 0) + 1
    candidates = build_candidates(run_dir, manifest, claims_by_candidate) if manifest else []
    attempts = read_attempts(run_dir)
    sources = build_sources(run_dir, manifest, attempts)
    agents = build_agents(run_dir, manifest, config, attempts, sources) if manifest else []
    timeline = build_timeline(run_dir, manifest)
    decisions = manifest.get("decisions") or []
    stats = build_stats(run_dir, manifest, candidates, decisions)
    stats.update(source_stats(sources))
    workers = build_workers(manifest, candidates, timeline["tasks"]) if manifest else []
    stages = []
    for key in STAGE_ORDER:
        if key == "staging":
            staged = len(load_jsonl(run_dir / "hypotheses.jsonl"))
            selected = len(load_jsonl(run_dir / "hypotheses_selected.jsonl"))
            stages.append({"key": key, "total": staged, "accepted": selected, "bound": 0, "prepared": 0,
                           "staging_label": ("%d → %d + %d" % (staged, selected, max(0, stats["candidates"] - selected))) if staged else ""})
        elif key == "decision":
            stages.append({"key": key, "total": stats["candidates"], "accepted": len(decisions), "bound": 0, "prepared": 0})
        else:
            total = len([a for a in (manifest.get("assignments") or []) if a.get("role") == key])
            accepted = len([a for a in (manifest.get("assignments") or []) if a.get("role") == key
                            and (a.get("attempts") or [{}])[-1].get("status") == "accepted"])
            bound = len([a for a in (manifest.get("assignments") or []) if a.get("role") == key
                         and (a.get("attempts") or [{}])[-1].get("status") == "bound"])
            prepared = len([a for a in (manifest.get("assignments") or []) if a.get("role") == key
                            and (a.get("attempts") or [{}])[-1].get("status") == "prepared"])
            stages.append({"key": key, "total": total, "accepted": accepted, "bound": bound, "prepared": prepared})
    report_path = manifest.get("report_path")
    return {
        "id": run_dir.name,
        "path": str(run_dir.relative_to(ROOT)),
        "manifest_path": str((run_dir / "manifest.json").relative_to(ROOT)) if manifest else None,
        "has_manifest": bool(manifest),
        "phase": manifest.get("phase") or "без манифеста",
        "status": manifest.get("status") or "частичные данные",
        "revision": manifest.get("revision"),
        "objective": manifest.get("objective"),
        "scope": manifest.get("scope"),
        "stopping_conditions": manifest.get("stopping_conditions"),
        "brief": manifest.get("brief"),
        "config_hash": (manifest.get("config_hash") or "")[:12],
        "capabilities": manifest.get("capabilities"),
        "limits": config.get("limits", {}),
        "portfolio": config.get("portfolio"),
        "report_path": (str((run_dir / report_path).relative_to(ROOT)) if report_path else None),
        "decisions": decisions,
        "candidates": candidates,
        "sources": sources,
        "agents": agents,
        "routing_frozen": bool(config.get("model_routing")),
        "workers": workers,
        "stages": stages,
        "timeline": timeline,
        "stats": stats,
        "notes": build_notes(run_dir, manifest, timeline["tasks"]),
        "updated_at": local_hhmm(max([parse_ts(e.get("at")) for e in load_jsonl(run_dir / "events.jsonl")] or [None]) ) if load_jsonl(run_dir / "events.jsonl") else "",
    }


def collect_runs() -> list:
    runs = []
    for run_dir in sorted(RUNS_DIR.iterdir(), reverse=True):
        if not run_dir.is_dir() or run_dir.name.startswith("."):
            continue
        runs.append(build_run(run_dir))
    def group_of(run):
        status = run.get("status")
        if status == "running":
            return 0
        if status == "complete":
            return 1
        return 2
    runs.sort(key=lambda r: r.get("id") or "", reverse=True)
    runs.sort(key=group_of)
    return runs


PAGE_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Панель исследования money-first-niche-miner</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
__STYLE__
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <div class="hero-top">
      <div class="eyebrow" id="runMeta">—</div>
      <h1>Что мы исследуем:<br><span class="accent">от гипотезы до решения</span></h1>
      <p class="muted" id="runObjective" style="max-width: 780px"></p>
      <div class="hero-controls">
        <div class="picker">
          <label class="small muted" for="runPicker">Исследование</label>
          <select id="runPicker" aria-label="Выбор прогона"></select>
          <span class="hint">Данные всех прогонов встроены в страницу; переключение мгновенное.</span>
        </div>
        <div class="live"><span class="dot"></span><span id="runLiveText"></span></div>
      </div>
    </div>
    <div class="stats" id="stats"></div>
  </header>

  <nav class="tabs" id="tabs" role="tablist" aria-label="Разделы"></nav>

  <section class="tabpanel" id="tab-overview" role="tabpanel" aria-labelledby="tabbtn-overview">
    <div class="sec-head">
      <div>
        <h2>Что здесь происходит</h2>
        <p class="lead small">Коротко и без технических терминов: зачем этот проект, что уже сделано и что мешает.</p>
      </div>
    </div>
    <div class="explain" id="overview"></div>
    <div class="sec-head" style="margin-top: 26px">
      <div>
        <h2>Кто что делает и чем пользуется</h2>
        <p class="lead small">Роли агентов и типы источников простым языком.</p>
      </div>
    </div>
    <div class="explain" id="glossary"></div>
  </section>

  <section class="tabpanel" id="tab-sources" role="tabpanel" aria-labelledby="tabbtn-sources" hidden>
    <div class="sec-head">
      <div>
        <h2>Источники</h2>
        <p class="lead small">Каждое сохранённое обращение наружу: кто из агентов его сделал, зачем и попало ли оно в выводы.</p>
      </div>
    </div>
    <div class="chips" id="sourceChips"></div>
    <div class="diag" id="sourceDiag"></div>
    <div class="filters" id="sourceFilters" style="margin-top: 16px"></div>
    <div class="tablewrap"><table class="src" id="sourceTable"><thead></thead><tbody></tbody></table></div>
    <div class="more" id="sourceMore"></div>
  </section>

  <section class="tabpanel" id="tab-agents" role="tabpanel" aria-labelledby="tabbtn-agents" hidden>
    <div class="sec-head">
      <div>
        <h2>Агенты и модели</h2>
        <p class="lead small">Какая модель назначена роли, какая реально записана в прогоне и что роль произвела.</p>
      </div>
    </div>
    <div class="explain" id="routingBox" style="margin-bottom: 18px"></div>
    <div class="agents" id="agents"></div>
  </section>

  <section class="tabpanel" id="tab-candidates" role="tabpanel" aria-labelledby="tabbtn-candidates" hidden>
    <div class="sec-head">
      <div>
        <h2>Кандидаты</h2>
        <p class="lead small">Найденные рабочие задачи покупателей. «Подробнее» открывает покупателя, задачу и вердикты проверок.</p>
      </div>
    </div>
    <div class="cands" id="cands"></div>
  </section>

  <section class="tabpanel" id="tab-tech" role="tabpanel" aria-labelledby="tabbtn-tech" hidden>
    <div class="sec-head">
      <div>
        <h2>Стадии пайплайна</h2>
        <p class="lead small">Нажмите на стадию, чтобы увидеть её роль, продукт и правило.</p>
      </div>
      <div class="legend">
        <span class="lg"><span class="swatch sw-ok"></span>принято</span>
        <span class="lg"><span class="swatch sw-run"></span>в работе</span>
        <span class="lg"><span class="swatch sw-prep"></span>подготовлено</span>
        <span class="lg"><span class="swatch sw-none"></span>задания нет</span>
      </div>
    </div>
    <div class="flow">
      <div class="flow-track" id="flowTrack"></div>
      <div class="stage-note" id="stageNote" hidden>
        <div class="t" id="stageNoteTitle"></div>
        <div class="d" id="stageNoteBody"></div>
      </div>
    </div>

    <div class="sec-head" style="margin-top: 26px">
      <div>
        <h2>Прокрутка времени</h2>
        <p class="lead small">Хронология прогона по событиям: постановка, привязка агентов, приёмка и отклонения.</p>
      </div>
    </div>
    <div class="timeline">
      <div class="timeline-top">
        <button class="play" id="playBtn" type="button" aria-label="Проиграть хронологию">▶</button>
        <span class="tl-clock" id="clock">—</span>
        <span class="small muted" id="clockNote"></span>
      </div>
      <label class="small muted" for="timeRange">Время среза</label>
      <input id="timeRange" type="range" min="0" max="1" step="1" value="1">
    </div>

    <div class="sec-head" style="margin-top: 26px">
      <div>
        <h2>Кто какие задания вёл</h2>
        <p class="lead small">Реальные агенты и их задания по стадиям. Клик по чипу открывает карточку кандидата.</p>
      </div>
    </div>
    <div class="workers" id="workers"></div>

    <div class="sec-head" style="margin-top: 26px">
      <div>
        <h2>Параметры запуска</h2>
        <p class="lead small">Всё, чем ограничен выбранный прогон, и как эти ограничения реально расходуются.</p>
      </div>
    </div>
    <div class="params" id="params"></div>
  </section>

  <footer id="footer"></footer>
</div>

__DATA_ISLAND__
<script>
__APP_JS__
</script>
</body>
</html>
"""


def main() -> int:
    global RUNS_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open", action="store_true", help="открыть собранную страницу в браузере")
    parser.add_argument("--runs-dir", default=str(RUNS_DIR))
    args = parser.parse_args()
    RUNS_DIR = Path(args.runs_dir).resolve()
    runs = collect_runs()
    current = load_routing()
    payload = {
        "generated_at": datetime.now(TZ).strftime("%d.%m.%Y %H:%M"),
        "routing": {"providers": current.get("providers") or [],
                    "roles": {role: {**resolve_routing(current, role, _default_stage(role)),
                                     "label": ROLE_RU.get(role, role),
                                     "purpose": STAGE_PURPOSE.get(role, "")} for role in ROLES}},
        "runs": runs,
    }
    data_json = json.dumps(payload, ensure_ascii=False, indent=1).replace("</", "<\\/")
    island = DATA_START + '\n<script id="runs-data" type="application/json">\n' + data_json + "\n</script>\n" + DATA_END
    page = (PAGE_TEMPLATE
            .replace("__STYLE__", STYLE.read_text().rstrip("\n"))
            .replace("__DATA_ISLAND__", island)
            .replace("__APP_JS__", APP_JS.read_text().strip("\n")))
    PAGE.parent.mkdir(parents=True, exist_ok=True)
    PAGE.write_text(page)
    print("обновлено: %s" % PAGE)
    print("прогонов: %d → %s" % (len(runs), ", ".join(r["id"] for r in runs)))
    for run in runs:
        s = run["stats"]
        print("  %-22s %-12s кандидатов %2d · заданий %2d · claims %3d · источников %3d (без ссылок %2d) · решений %2d"
              % (run["id"], run["phase"], s["candidates"], s["assignments_used"], s["claims"],
                 s["sources"], s["sources_orphan"], s["decisions"]))
    if args.open:
        import webbrowser
        webbrowser.open(PAGE.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
