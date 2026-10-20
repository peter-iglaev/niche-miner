"""MCP-мост: инструменты, доступные только внутри сессии Claude Code (Ubersuggest, Upwork).

Скрипт не может их вызвать сам, поэтому:
1. стадия пишет запросы в runs/<id>/bridge/requests.jsonl (id, tool, args, purpose);
2. оркестратор исполняет их ровно с этими аргументами и кладёт ответ как есть в
   runs/<id>/bridge/responses/<id>.json;
3. `bridge-ingest` сверяет ответы с запросами, архивирует их в sources/ (с хэшем и sidecar) и
   помечает запросы выполненными. Стадия при повторном запуске читает уже архивированные ответы.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import sources
from scripts.artifacts import atomic_text, write_json
from scripts.pipeline import quota
from scripts.pipeline.run import now, read_json, read_jsonl

TOOLS = {
    "ubersuggest.google_suggestions": {"mcp": "mcp__Ubersuggest__google_suggestions", "quota": False,
                                       "note": "fan-out ~60 modifiers per keyword; does not spend the daily report quota"},
    "ubersuggest.keyword_suggestions": {"mcp": "mcp__Ubersuggest__keyword_suggestions", "quota": True,
                                        "note": "spends one daily report; returns up to ~250 keywords with volume and CPC"},
    "ubersuggest.keyword_overview": {"mcp": "mcp__Ubersuggest__keyword_overview", "quota": True,
                                     "note": "spends one daily report for a single keyword"},
    "upwork.find_jobs": {"mcp": "mcp__Upwork__upwork__find_jobs", "quota": False,
                         "note": "title filter, fixed jobs, budget_min; org_uid comes from list_accounts"},
}
NOTE = "Response returned by an MCP tool inside the orchestrator session and archived byte-for-byte."


def _dir(folder):
    path = Path(folder) / "bridge"
    (path / "responses").mkdir(parents=True, exist_ok=True)
    return path


def request_id(tool, args):
    digest = hashlib.sha256(json.dumps([tool, args], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return f"{tool.replace('.', '-')}-{digest}"


def load_requests(folder):
    return read_jsonl(_dir(folder) / "requests.jsonl")


def _save(folder, requests):
    atomic_text(_dir(folder) / "requests.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests))


def plan(folder, tool, args, *, purpose, stage):
    """Добавить запрос (идемпотентно). Возвращает запись запроса с её текущим статусом."""
    if tool not in TOOLS:
        raise ValueError(f"unknown bridge tool: {tool}")
    requests = load_requests(folder)
    identity = request_id(tool, args)
    for existing in requests:
        if existing["id"] == identity:
            return existing
    record = {"id": identity, "tool": tool, "mcp_tool": TOOLS[tool]["mcp"], "args": args, "purpose": purpose,
              "stage": stage, "status": "pending", "planned_at": now(), "source_id": None,
              "response_path": f"bridge/responses/{identity}.json"}
    requests.append(record)
    _save(folder, requests)
    return record


def pending(folder, stage=None):
    return [r for r in load_requests(folder) if r["status"] == "pending" and (stage is None or r["stage"] == stage)]


def response(folder, request):
    """Архивированный ответ по запросу или None, если он ещё не выполнен."""
    if request.get("status") != "done" or not request.get("source_id"):
        return None
    meta = read_json(Path(folder) / "sources" / f"{request['source_id']}.meta.json")
    if not meta or not meta.get("archive_path"):
        return None
    try:
        return json.loads((Path(folder) / meta["archive_path"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def ingest(folder, *, root=None):
    """Принять ответы из bridge/responses/, архивировать в sources/, обновить статусы."""
    folder = Path(folder)
    run_id = folder.name
    requests = load_requests(folder)
    done, missing, errors, exhausted = [], [], [], []
    for request in requests:
        if request["status"] == "done":
            continue
        path = folder / request["response_path"]
        if not path.is_file():
            missing.append(request["id"]); continue
        raw = path.read_bytes()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            errors.append(f"{request['id']}: response is not valid JSON ({exc})"); continue
        error = None
        if isinstance(parsed, dict) and parsed.get("error") and not parsed.get("results") and not parsed.get("jobs"):
            error = str(parsed.get("error"))[:400]
        record = sources.archive(run_id, "mcp:" + request["tool"].split(".")[0], "mcp://" + request["tool"].replace(".", "/"),
                                 query=json.dumps(request["args"], ensure_ascii=False, sort_keys=True), status=200 if not error else None,
                                 content_type="application/json", body=raw, error=error, limitations=NOTE, root=root or sources.ROOT)
        status = "done" if not error else "error"
        if error and TOOLS[request["tool"]]["quota"] and quota.is_quota_error(error):
            # квота потрачена вне ledger'а (другая сессия, браузер) — фиксируем день как исчерпанный,
            # а сам запрос оставляем в очереди на завтра
            status = "deferred_quota"
            quota.mark_exhausted(root or Path(folder).parent.parent, reason=error)
            exhausted.append(request["id"])
            path.unlink(missing_ok=True)
        request.update(status=status, source_id=record["source_id"], ingested_at=now(), error=error)
        done.append(request["id"])
    _save(folder, requests)
    return {"ingested": done, "missing": missing, "errors": errors, "quota_exhausted": exhausted,
            "pending": [r["id"] for r in requests if r["status"] == "pending"]}


def cancel_unneeded(folder, stage, keep_ids):
    """Снять запросы стадии, которые больше никому не нужны.

    Кластер могли отсеять по другому сигналу уже после того, как запрос попал в очередь;
    оставлять его «ожидающим» — значит просить оркестратора выполнить бессмысленный вызов.
    """
    requests = load_requests(folder)
    cancelled = []
    for request in requests:
        if request["stage"] == stage and request["status"] == "pending" and request["id"] not in keep_ids:
            request.update(status="not_needed", cancelled_at=now())
            cancelled.append(request["id"])
    if cancelled:
        _save(folder, requests)
    return cancelled


def instructions(folder):
    """Человеко-читаемый список того, что оркестратор должен выполнить."""
    items = pending(folder)
    lines = []
    for r in items:
        lines.append(f"- {r['id']}: инструмент `{r['mcp_tool']}` с аргументами {json.dumps(r['args'], ensure_ascii=False)}"
                     f" → сохранить ответ как есть в `{r['response_path']}` ({r['purpose']})")
    return {"count": len(items), "requests": items, "text": "\n".join(lines)}
