"""Воркер v2: одна роль (interpret | skeptic | judge) на DeepSeek с инструментами и проверкой контракта.

Что здесь принудительно, а не на доверии к модели:
- журнал поиска строится из реально сделанных вызовов;
- каждое утверждение обязано ссылаться на source_id, который есть в архиве прогона;
- бюджет запросов считается на границе; результат, не прошедший контракт, возвращается модели с
  точным списком ошибок (до 3 починок), иначе роль завершается ошибкой, а не мусором;
- личность воркера и время стоят в результате печатью харнесса.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from scripts import search, sources
from scripts.artifacts import write_json
from scripts.llm import LLMError, complete
from scripts.pipeline.run import now, read_json, today
from scripts.worker import page_text, browser_only

ROLE_PROMPTS = Path(__file__).resolve().parents[2] / "agents" / "v2"
PAGE_CHARS = 6000
MAX_REPAIRS = 3
SEVERITIES = ("fatal", "material", "speculative")
DECISIONS = ("build_test", "investigate", "drop")
SCORE_KEYS = ("demand", "money", "reachability", "pain", "trend")

TOOLS = [
    {"type": "function", "function": {"name": "web_search", "description": "Один поисковый запрос (Brave). Выдача — зацепка, не доказательство: перед цитированием открой страницу fetch_page. Тратит одну единицу бюджета.",
                                      "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "fetch_page", "description": "Открыть URL, заархивировать байты, вернуть текст и source_id. Не тратит бюджет.",
                                      "parameters": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "hn_search", "description": "Поиск обсуждений практиков в Hacker News (Algolia). Тратит одну единицу бюджета.",
                                      "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "read_source", "description": "Прочитать уже архивированный источник по source_id (сырой ответ API или страницу). Не тратит бюджет.",
                                      "parameters": {"type": "object", "required": ["source_id"], "properties": {"source_id": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "submit_result", "description": "Сдать результат роли. Вызывается один раз. Поля по контракту роли; claims — список утверждений со ссылками на source_id.",
                                      "parameters": {"type": "object", "required": ["result"], "properties": {"result": {"type": "object"}}}}},
]


def role_prompt(role):
    path = ROLE_PROMPTS / f"{role}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def contract(role):
    common = ("Каждый элемент claims: {claim, source_id, source_url, excerpt, locator, kind: supports|contradicts|qualifies}. "
              "source_id — только из открытых в этом ходе источников или из архива (read_source). Числа с единицами.")
    if role == "interpret":
        return ("submit_result.result = {job, buyer, trigger, product_shape, payment_unit, price_point:{low, high, unit, basis}, "
                "incumbents:[{name, url, source_id, note}], differentiator_hypothesis, cheapest_test, gaps:[...], claims:[...]}. " + common)
    if role == "skeptic":
        return ("submit_result.result = {findings:[{severity: fatal|material|speculative, about, text, source_ids:[...]}], "
                "counterevidence:[...], gaps:[...], claims:[...]}. " + common)
    return ("submit_result.result = {decision_context: build_test|investigate|drop, scorecard:{demand, money, reachability, pain, trend: "
            "{value: 0-4|null, rationale}}, resolved_objections:[{finding, resolution}], unresolved_objections:[...], "
            "critical_unknowns:[...], rationale, claims:[]}. Судья новых источников не открывает.")


def _packet(cluster, role, prior):
    keep = {k: cluster.get(k) for k in ("cluster_id", "theme", "name", "job", "product_shape", "intent", "head_phrase", "phrases",
                                        "backlog_status", "backlog_previous")}
    keep["signals"] = cluster.get("signals")
    keep["numbers"] = cluster.get("numbers")
    keep["gate"] = cluster.get("gate")
    keep["screen"] = cluster.get("screen")
    if role in ("skeptic", "judge"):
        keep["interpret"] = prior.get("interpret")
    if role == "judge":
        keep["skeptic"] = prior.get("skeptic")
    return keep


class Session:
    def __init__(self, run_id, cluster_id, role, worker_id, budget, root):
        self.run_id, self.cluster_id, self.role, self.worker_id = run_id, cluster_id, role, worker_id
        self.budget, self.root = budget, root
        self.log, self.opened = [], set()
        self.who = {"assignment_id": f"{cluster_id}-{role}", "role": role, "worker_id": worker_id}

    def spent(self):
        return sum(1 for e in self.log if e["counts"])

    def _budget_left(self):
        return self.budget is None or self.spent() < self.budget

    def web_search(self, query):
        if not self._budget_left():
            self.log.append({"tool": "web_search", "query": query, "status": "not_collected", "counts": False, "at": now()})
            return {"error": "бюджет запросов исчерпан; зафиксируй пробел"}
        result = search.search(self.run_id, query, count=10, root=self.root, **self.who)
        self.log.append({"tool": "web_search", "query": query, "status": result["retrieval_status"], "source_id": result.get("source_id"),
                         "counts": True, "at": now()})
        return {"status": result["retrieval_status"], "error": result.get("error"), "budget_left": None if self.budget is None else self.budget - self.spent(),
                "results": [{"rank": r["rank"], "title": r["title"], "url": r["url"], "snippet": (r.get("snippet") or "")[:300],
                             **({"openable": False} if browser_only(r["url"]) else {})} for r in result.get("results") or []]}

    def fetch_page(self, url):
        if browser_only(url):
            return {"url": url, "status": "not_collected", "error": "хост отдаёт JS-оболочку; цитировать нельзя"}
        result = search.fetch(self.run_id, url, root=self.root, **self.who)
        body = ""
        if result["retrieval_status"] == "collected" and result.get("archive_path"):
            raw = (Path(self.root) / "runs" / self.run_id / result["archive_path"]).read_bytes()
            body = page_text(raw, result.get("content_type") or "")[:PAGE_CHARS]
        if result.get("source_id"):
            self.opened.add(result["source_id"])
        self.log.append({"tool": "fetch_page", "query": url, "status": result["retrieval_status"], "source_id": result.get("source_id"),
                         "counts": False, "at": now()})
        return {"source_id": result.get("source_id"), "url": url, "status": result["retrieval_status"], "error": result.get("error"),
                "access_date": today(), "text": body}

    def hn_search(self, query):
        if not self._budget_left():
            return {"error": "бюджет запросов исчерпан; зафиксируй пробел"}
        record = sources.hn_search(self.run_id, query, tags="comment", limit=20, root=self.root, **self.who)
        self.log.append({"tool": "hn_search", "query": query, "status": record["retrieval_status"], "source_id": record["source_id"],
                         "counts": True, "at": now()})
        if record["retrieval_status"] != "collected":
            return {"status": "error", "error": record.get("error")}
        self.opened.add(record["source_id"])
        raw = (Path(self.root) / "runs" / self.run_id / record["archive_path"]).read_bytes()
        return {"source_id": record["source_id"], "status": "collected", "text": raw.decode("utf-8", errors="replace")[:PAGE_CHARS]}

    def read_source(self, source_id):
        folder = Path(self.root) / "runs" / self.run_id
        meta = read_json(folder / "sources" / f"{source_id}.meta.json")
        if not meta:
            return {"error": f"нет такого источника: {source_id}"}
        if not meta.get("archive_path"):
            return {"source_id": source_id, "status": meta.get("retrieval_status"), "error": meta.get("error"), "text": ""}
        raw = (folder / meta["archive_path"]).read_bytes()
        self.opened.add(source_id)
        self.log.append({"tool": "read_source", "query": source_id, "status": "collected", "source_id": source_id, "counts": False, "at": now()})
        return {"source_id": source_id, "url": meta.get("request_url"), "provider": meta.get("provider"),
                "text": page_text(raw, meta.get("content_type") or "")[:PAGE_CHARS]}


def validate(role, result, session, cluster, run_dir):
    errors = []
    if not isinstance(result, dict):
        return ["result must be an object"]
    claims = result.get("claims")
    if not isinstance(claims, list):
        errors.append("claims must be a list (may be empty for judge)")
        claims = []
    known_meta = {p.name[:-len(".meta.json")] for p in (Path(run_dir) / "sources").glob("*.meta.json")}
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(f"claims[{index}] must be an object"); continue
        for key in ("claim", "source_id"):
            if not claim.get(key):
                errors.append(f"claims[{index}].{key} is required")
        sid = claim.get("source_id")
        if sid and sid not in known_meta:
            errors.append(f"claims[{index}].source_id {sid} is not in the run archive (open the page with fetch_page or use read_source)")
        if claim.get("kind") not in (None, "supports", "contradicts", "qualifies"):
            errors.append(f"claims[{index}].kind must be supports|contradicts|qualifies")
    if role == "interpret":
        for key in ("job", "buyer", "product_shape", "payment_unit", "price_point", "incumbents", "cheapest_test"):
            if result.get(key) in (None, "", []):
                errors.append(f"{key} is required (use null only inside price_point when unknown)")
        if not isinstance(result.get("incumbents"), list):
            errors.append("incumbents must be a list (may be empty if none were found)")
    elif role == "skeptic":
        findings = result.get("findings")
        if not isinstance(findings, list):
            errors.append("findings must be a list")
        else:
            for i, f in enumerate(findings):
                if not isinstance(f, dict) or f.get("severity") not in SEVERITIES or not f.get("text"):
                    errors.append(f"findings[{i}] needs severity fatal|material|speculative and text")
        if not isinstance(result.get("counterevidence"), list) or not result.get("counterevidence"):
            errors.append("counterevidence must list what you looked for and did not find")
    else:
        if result.get("decision_context") not in DECISIONS:
            errors.append("decision_context must be build_test|investigate|drop")
        card = result.get("scorecard")
        if not isinstance(card, dict) or any(k not in card for k in SCORE_KEYS):
            errors.append("scorecard must contain demand, money, reachability, pain, trend")
        else:
            for key in SCORE_KEYS:
                entry = card[key]
                value = entry.get("value") if isinstance(entry, dict) else entry
                if value is not None and (not isinstance(value, int) or not 0 <= value <= 4):
                    errors.append(f"scorecard.{key}.value must be an integer 0-4 or null")
        if not result.get("rationale"):
            errors.append("rationale is required")
        if result.get("decision_context") == "build_test":
            if not (cluster.get("gate") or {}).get("passed"):
                errors.append("build_test is not allowed: gates did not pass (use investigate or drop)")
            fatal = [f for f in ((cluster.get("skeptic") or {}).get("findings") or []) if f.get("severity") == "fatal"]
            resolved = {r.get("finding") for r in result.get("resolved_objections") or [] if isinstance(r, dict)}
            if fatal and not all(f.get("text") in resolved or any(str(f.get("text", ""))[:40] in str(r) for r in resolved) for f in fatal):
                errors.append("build_test is not allowed while a fatal skeptic finding is unresolved; resolve it explicitly or choose investigate/drop")
    return errors


def run_role(folder, config, cluster, role, *, root, client=complete, max_steps=None):
    """Выполнить одну роль для кластера; результат пишется в 6-review/<cluster>/<role>.json."""
    folder = Path(folder)
    run_id = folder.name
    choice = (config.get("models") or {}).get(role)
    if not choice:
        raise ValueError(f"models.{role} is not configured in config/demand.yaml")
    settings = config.get("review") or {}
    budget = {"interpret": settings.get("interpret_search_queries", 4), "skeptic": settings.get("skeptic_search_queries", 6), "judge": 0}[role]
    max_steps = max_steps or int(settings.get("max_steps") or 24)
    worker_id = f"{choice['provider']}:{choice['model']}:{uuid.uuid4().hex[:8]}"
    out_dir = folder / "6-review" / cluster["cluster_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    prior = {r: read_json(out_dir / f"{r}.json") for r in ("interpret", "skeptic")}
    prior = {k: (v or {}).get("result") for k, v in prior.items() if v}
    cluster_with_prior = dict(cluster, skeptic=prior.get("skeptic"))
    session = Session(run_id, cluster["cluster_id"], role, worker_id, budget, root)
    system = "\n\n".join([role_prompt(role),
                          "# Контракт ответа\n\n" + contract(role) +
                          "\n\nТы один воркер с одним заданием. Соберите факты инструментами, затем один раз вызови submit_result. "
                          "Личность, время и журнал поиска проставляет харнесс. Неизвестное остаётся null и попадает в gaps. "
                          f"Бюджет поисковых запросов: {budget}."])
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": "Замороженный пакет кластера:\n\n" + json.dumps(_packet(cluster, role, prior), ensure_ascii=False, indent=1)}]
    tools = TOOLS if role != "judge" else [t for t in TOOLS if t["function"]["name"] in ("read_source", "submit_result")]
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    dispatch = {"web_search": session.web_search, "fetch_page": session.fetch_page, "hn_search": session.hn_search,
                "read_source": session.read_source}
    repairs, problems = 0, []
    for step in range(1, max_steps + 1):
        answer = client(messages, provider=choice["provider"], model=choice["model"], tools=tools,
                        reasoning_effort=choice.get("reasoning_effort"), root=root)
        usage["calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage[key] += int((answer.get("usage") or {}).get(key) or 0)
        message = answer["message"]
        calls = message.get("tool_calls") or []
        messages.append({"role": "assistant", "content": message.get("content") or "", **({"tool_calls": calls} if calls else {})})
        if not calls:
            messages.append({"role": "user", "content": "Продолжай вызовом инструмента или сдай результат через submit_result."})
            continue
        for call in calls:
            name = (call.get("function") or {}).get("name")
            try:
                arguments = json.loads((call.get("function") or {}).get("arguments") or "{}")
            except ValueError as error:
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": name, "content": json.dumps({"error": f"аргументы не JSON: {error}"})})
                continue
            if name == "submit_result":
                result = arguments.get("result") if isinstance(arguments.get("result"), dict) else arguments
                problems = validate(role, result, session, cluster_with_prior, folder)
                if not problems:
                    envelope = {"schema_version": "2.0", "run_id": run_id, "cluster_id": cluster["cluster_id"], "role": role,
                                "worker_id": worker_id, "provider": choice["provider"], "model": answer.get("model") or choice["model"],
                                "finished_at": now(), "search_log": session.log, "sources_opened": sorted(session.opened),
                                "queries_spent": session.spent(), "queries_allowed": budget, "steps": step, "usage": usage,
                                "result": result}
                    write_json(out_dir / f"{role}.json", envelope)
                    return envelope
                repairs += 1
                if repairs > MAX_REPAIRS:
                    raise ValueError(f"{role}: result failed validation after {MAX_REPAIRS} repairs: " + "; ".join(problems[:6]))
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": name,
                                 "content": json.dumps({"accepted": False, "errors": problems[:12], "instruction": "Исправь ровно это и вызови submit_result снова."}, ensure_ascii=False)})
                continue
            handler = dispatch.get(name)
            output = {"error": f"неизвестный инструмент {name!r}"} if handler is None else None
            if handler is not None:
                try:
                    output = handler(**arguments)
                except (ValueError, TypeError, OSError) as error:
                    output = {"error": f"{type(error).__name__}: {error}"}
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": name, "content": json.dumps(output, ensure_ascii=False)[:20000]})
    raise ValueError(f"{role}: step limit reached without a valid result" + (f"; last errors: {'; '.join(problems[:4])}" if problems else ""))
