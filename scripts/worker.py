#!/usr/bin/env python3
"""Run one assignment with a real model and write its immutable result.

This is the executor the control plane always assumed but never had: it reads the frozen
input packet, runs the role prompt on the provider and model that `config/model-routing.yaml`
assigned to that role, collects evidence through the existing search and source layers, and
writes `result.json` for the orchestrator to accept.

Honesty rules this file enforces rather than trusts:

- the search log is built from the calls that actually happened, never from the model's
  recollection of them;
- identity fields (worker, author, discoverer, input hash) are stamped by this script, so a
  model cannot claim someone else's authorship;
- the semantic-query budget in the packet is counted and refused at the boundary;
- a result that fails schema or contract validation is returned to the model with the exact
  errors, and a run that never produces a valid one exits non-zero instead of writing junk.

Accepting the result stays with the orchestrator: this script never marks its own work done.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import app, search, sources
from scripts.artifacts import write_json
from scripts.founder import familiarity
from scripts.llm import LLMError, complete, provider_config
from scripts.routing import load_routing, resolve as resolve_routing
from scripts.validate import current, read_data, read_jsonl, validate_result, within

# Hosts this executor cannot read: they answer a plain fetch with a JavaScript shell or a block
# page, so archiving the response would store an empty snapshot that no claim can honestly cite.
# They stay valid leads; reaching them needs a browser session, which is a separate capability.
BROWSER_ONLY_HOSTS = ("reddit.com", "redd.it")
MAX_STEPS = 24
MAX_REPAIRS = 3
PAGE_CHARS = 6000
SNIPPET_CHARS = 400
ROLE_PROMPTS = ROOT / "agents"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def browser_only(url: str) -> bool:
    host = urllib.parse.urlparse(url or "").netloc.lower().split(":")[0]
    return any(host == blocked or host.endswith("." + blocked) for blocked in BROWSER_ONLY_HOSTS)


def page_text(raw: bytes, content_type: str) -> str:
    """Readable text from an archived response; the archive itself stays the canonical bytes."""
    if "json" in (content_type or ""):
        return raw[:PAGE_CHARS * 2].decode("utf-8", errors="replace")
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": ("One semantic web query through the configured provider. Returns ranked leads. "
                        "Ranking is not evidence: open a result with fetch_page before citing it. "
                        "Spends one unit of the assignment's query budget, as do tender_search and "
                        "discussion_search. Opening pages does not."),
        "parameters": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "description": "Search query, usually English."},
            "country": {"type": "string", "description": "Two-letter market, e.g. US, ES."},
            "language": {"type": "string", "description": "Two-letter result language, e.g. en."}}}}},
    {"type": "function", "function": {
        "name": "fetch_page",
        "description": ("Open one URL, archive the exact bytes and return readable text. "
                        "Use before citing anything. Does not spend query budget."),
        "parameters": {"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL."}}}}},
    {"type": "function", "function": {
        "name": "tender_search",
        "description": ("Published EU procurement notices (TED). Shows buyer budgets and contract "
                        "subjects; a notice proves public spend, not payment for our exact workflow."),
        "parameters": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "description": "TED expert query, e.g. FT=\"access review\"."}}}}},
    {"type": "function", "function": {
        "name": "discussion_search",
        "description": ("Hacker News discussions through Algolia. Practitioner reports describe how "
                        "work is done today; they are not evidence of prevalence."),
        "parameters": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "archive_lookup",
        "description": "Ask the Wayback Machine which capture of a URL exists near today.",
        "parameters": {"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "submit_result",
        "description": ("Deliver the finished work. Identity fields are stamped by the harness, so omit "
                        "them. Called once when the evidence is collected or the budget is spent."),
        "parameters": {"type": "object", "required": ["claims"], "properties": {
            "claims": {"type": "array", "description": "Evidence claims, each citing one opened source.",
                       "items": {"type": "object"}},
            "candidate": {"type": "object", "description": "discovery only: the canonical niche atom."},
            "role_output": {"type": "object", "description": "non-discovery: findings, counterevidence, research_gaps."},
            "assessment": {"type": "object", "description": "judge only: payment, distribution, feasibility, ratings."}}}}},
]


class Budget:
    """Semantic query budget from the frozen packet. Provider fallbacks stay one query."""

    def __init__(self, limit):
        self.limit = limit
        self.entries = []

    def spent(self):
        return search.logical_query_count(self.entries)

    def left(self):
        return None if self.limit is None else max(0, self.limit - self.spent())

    def exhausted(self):
        return self.limit is not None and self.spent() >= self.limit

    def record(self, entries):
        self.entries.extend(entries or [])


class Session:
    """One assignment's tool surface: every call is attributed and logged where it happened."""

    def __init__(self, run_id, assignment_id, role, worker_id, budget, root=ROOT):
        self.run_id = run_id
        self.who = {"assignment_id": assignment_id, "role": role, "worker_id": worker_id}
        self.budget = budget
        self.root = root
        self.log = []
        self.trace = []
        self.opened = {}

    def _note(self, query, provider, status, source_id, ref, notes, *, is_query=True):
        """Record one call. Only semantic queries enter search_log; opening a page is not a query.

        The budget contract counts semantic queries, and acceptance recounts them from this log, so
        putting page opens here would spend the assignment's budget on reading what it already found.
        Opens stay traceable through sources/ and source_usage.jsonl instead.
        """
        entry = {"query": query, "provider": provider, "access_date": _today(), "status": status,
                 "raw_response_ref": ref, "notes": notes}
        if source_id:
            entry["source_id"] = source_id
        (self.log if is_query else self.trace).append(entry)
        return entry

    def web_search(self, query, country=None, language=None):
        if self.budget.exhausted():
            self._note(query, "none", "not_collected", None, None,
                       "Query budget for this attempt is spent; the gap is reported instead.")
            return {"error": "query budget exhausted", "budget_left": 0}
        result = search.search(self.run_id, query, country=country, search_lang=language,
                               root=self.root, **self.who)
        self.budget.record(result.get("attempts"))
        status = {"collected": "results", "error": "failed"}.get(result["retrieval_status"], "not_collected")
        if status == "results" and not result["results"]:
            status = "no_data"
        self._note(query, result.get("provider") or "none", status, result.get("source_id"),
                   result.get("raw_response_ref") or (f"sources/{result['source_id']}.json" if result.get("source_id") else None),
                   f"{country or 'no country'}/{language or 'no language'}; ranking is a lead, not evidence."
                   + (f" error: {result['error']}" if result.get("error") else ""))
        return {"provider": result.get("provider"), "status": result["retrieval_status"],
                "budget_left": self.budget.left(), "error": result.get("error"),
                "results": [{"rank": r["rank"], "title": r["title"], "url": r["url"],
                             "snippet": (r.get("snippet") or "")[:SNIPPET_CHARS],
                             **({"openable": False} if browser_only(r["url"]) else {})}
                            for r in result.get("results") or []]}

    def fetch_page(self, url):
        if browser_only(url):
            self._note(url, "direct_fetch", "not_collected", None, None,
                       "Host needs a browser session; not opened, so nothing may be cited from it.",
                       is_query=False)
            return {"url": url, "status": "not_collected", "browser_only": True, "error":
                    "This host cannot be read by this executor: it answers automated requests with a "
                    "JavaScript shell. Do not cite it from a search snippet. Record the gap, or find the "
                    "same fact in a source you can open."}
        result = search.fetch(self.run_id, url, root=self.root, **self.who)
        archive = result.get("archive_path")
        body = ""
        if result["retrieval_status"] == "collected" and archive:
            raw = (Path(self.root) / "runs" / self.run_id / archive).read_bytes()
            body = page_text(raw, result.get("content_type") or "")[:PAGE_CHARS]
        self._note(url, "direct_fetch", "results" if result["retrieval_status"] == "collected" else "failed",
                   result.get("source_id"), archive,
                   "Opened and archived before citation." if body else f"fetch failed: {result.get('error')}",
                   is_query=False)
        if result.get("source_id"):
            self.opened[result["source_id"]] = url
        return {"source_id": result.get("source_id"), "url": url, "status": result["retrieval_status"],
                "http_status": result.get("http_status"), "error": result.get("error"),
                "access_date": _today(), "text": body,
                "truncated": bool(body) and len(body) >= PAGE_CHARS}

    def _keyless(self, provider, query, call, *, is_query=True):
        if is_query and self.budget.exhausted():
            self._note(query, provider, "not_collected", None, None,
                       "Query budget for this attempt is spent; the gap is reported instead.")
            return {"error": "query budget exhausted", "budget_left": 0}
        record = call()
        collected = record["retrieval_status"] == "collected"
        body = ""
        if collected and record.get("archive_path"):
            raw = (Path(self.root) / "runs" / self.run_id / record["archive_path"]).read_bytes()
            body = page_text(raw, record.get("content_type") or "")[:PAGE_CHARS]
        self._note(query, provider, "results" if collected else "failed", record.get("source_id"),
                   record.get("archive_path"), record.get("limitations") or "", is_query=is_query)
        if is_query:
            self.budget.record([{"query": query, "searched_at": _now(), "retrieval_status": record["retrieval_status"]}])
        if record.get("source_id"):
            self.opened[record["source_id"]] = record.get("request_url") or query
        return {"source_id": record.get("source_id"), "status": record["retrieval_status"],
                "url": record.get("request_url"), "access_date": _today(),
                "error": record.get("error"), "text": body}

    def tender_search(self, query):
        return self._keyless("ted", query, lambda: sources.ted_search(self.run_id, query, root=self.root, **self.who))

    def discussion_search(self, query):
        return self._keyless("hn_algolia", query, lambda: sources.hn_search(self.run_id, query, root=self.root, **self.who))

    def archive_lookup(self, url):
        return self._keyless("wayback", url, lambda: sources.wayback_snapshot(self.run_id, url, root=self.root, **self.who),
                             is_query=False)


def role_prompt(role: str) -> str:
    parts = []
    for name in (f"{role}.md", "sourcing.md"):
        path = ROLE_PROMPTS / name
        if path.is_file():
            parts.append(f"# {name}\n\n{path.read_text(encoding='utf-8')}")
    return "\n\n---\n\n".join(parts)


def result_shape(role: str) -> str:
    if role == "discovery":
        return ("submit_result takes `claims` and `candidate`. The candidate needs industry, who, buyer, "
                "trigger, pain, current_workaround, existing_spend, job_to_be_done, measurable_result, "
                "distribution_channel, frequency, research_gaps, category_flags "
                "(ai_agent_related, mcp_related, ecommerce, devops_cloud, boring_industry, non_technical_buyer, "
                "unfamiliar_to_founder), classification_rationale and claim_ids. Unknown stays null, never zero. "
                "Founder familiarity is derived by the harness from the frozen profile: do not state it.")
    if role == "judge":
        return ("submit_result takes `claims`, `role_output` (findings, counterevidence, research_gaps) and "
                "`assessment` with payment {level, claim_ids, rationale}, distribution and feasibility "
                "{status: pass|fail|unknown, claim_ids, rationale}, ratings for impact_per_event, "
                "recurring_burden, monetization_economics, accessible_distribution and switching_opportunity "
                "as {value 0-4 or null, claim_ids, rationale}, critical_unknowns and "
                "unresolved_fatal_contradictions. Judge collects no new supporting facts.")
    return ("submit_result takes `claims` and `role_output` with findings, counterevidence and research_gaps. "
            "Counterevidence is required: state what you looked for and did not find, without turning "
            "absence into proof.")


def build_messages(packet: dict, budget: Budget) -> list:
    role = packet["role"]
    system = "\n\n".join([
        role_prompt(role),
        "# How this turn works\n\n"
        "You are one worker with one assignment. Collect evidence with the tools, then call submit_result once.\n"
        "Every claim must cite a source you actually opened in this turn: give its source_url, an exact "
        "source_excerpt, a locator, the access_date and source_id returned by the tool. A search snippet is "
        "not a source. A public price establishes an offer, never a payment. Unknown values stay null and are "
        "reported as gaps. A result marked openable: false cannot be read by this executor and must never be "
        "cited from its snippet: treat it as a gap, or find the same fact in a source you can open. "
        "Identity fields (ids, worker, author, hashes) are stamped by the harness: omit them.\n\n"
        + result_shape(role),
    ])
    frozen = {k: packet.get(k) for k in ("objective", "scope", "stage", "role", "candidate_id",
                                         "acceptance_requirements", "allowed_actions")}
    frozen["query_budget"] = budget.limit
    context = {"assignment": frozen, "task": packet.get("packet") or {},
               "hypothesis": packet.get("hypothesis"), "known_claims": packet.get("claims") or [],
               "accepted_outputs": {role_name: value.get("result")
                                    for role_name, value in (packet.get("accepted_outputs") or {}).items()},
               "founder_profile": packet.get("founder_profile")}
    return [{"role": "system", "content": system},
            {"role": "user", "content": "Frozen assignment packet:\n\n"
                                        + json.dumps(context, ensure_ascii=False, indent=1)}]


def stamp(payload: dict, packet: dict, worker_id: str, discoverer_id, log: list, input_hash: str) -> dict:
    """Assemble the result envelope; the harness owns identity and the search log."""
    role, cid = packet["role"], packet["candidate_id"]
    claims = []
    for index, claim in enumerate(payload.get("claims") or [], start=1):
        claim = dict(claim)
        claim["candidate_id"] = cid
        claim["author_id"] = worker_id
        claim.setdefault("id", f"{cid}-{role}-c{index:02d}")
        claim.setdefault("access_date", _today())
        claims.append(claim)
    claim_ids = [c["id"] for c in claims]
    result = {"schema_version": "1.0", "run_id": packet["run_id"], "assignment_id": packet["assignment_id"],
              "attempt": packet["attempt"], "role": role, "worker_id": worker_id,
              "input_hash": input_hash, "claims": claims, "search_log": list(log)}
    if role == "discovery":
        candidate = dict(payload.get("candidate") or {})
        candidate.update({"schema_version": "1.0", "id": cid, "run_id": packet["run_id"],
                          "discoverer_id": worker_id})
        candidate["claim_ids"] = [i for i in (candidate.get("claim_ids") or claim_ids) if i in claim_ids] or claim_ids
        # Founder familiarity follows from the industry and the frozen profile, so the harness
        # derives it instead of letting a model guess a label the control plane recomputes.
        derived = familiarity(candidate.get("industry"), packet.get("founder_profile") or {})
        candidate["founder_familiarity"] = derived
        flags = dict(candidate.get("category_flags") or {})
        flags["unfamiliar_to_founder"] = (True if derived == "confirmed_unfamiliar"
                                          else False if derived == "confirmed_familiar" else None)
        candidate["category_flags"] = flags
        result["candidate"] = candidate
    else:
        output = dict(payload.get("role_output") or {})
        output.update({"schema_version": "1.0", "candidate_id": cid, "role": role, "worker_id": worker_id,
                       "discoverer_id": discoverer_id, "input_hash": input_hash, "completed": True})
        output["claim_ids"] = [i for i in (output.get("claim_ids") or claim_ids) if i in claim_ids]
        result["role_output"] = output
        if role == "judge":
            assessment = dict(payload.get("assessment") or {})
            assessment.update({"schema_version": "2.0", "candidate_id": cid, "author_id": worker_id,
                               "discoverer_id": discoverer_id})
            assessment.setdefault("decision_context", "commercial_validation")
            result["assessment"] = assessment
    return result


def run_assignment(run_id, assignment_id, *, provider=None, model=None, max_steps=MAX_STEPS,
                   root=ROOT, client=complete):
    folder = app.run_path(root, run_id)
    manifest = read_data(folder / "manifest.json")
    assignment = next((a for a in manifest["assignments"] if a["id"] == assignment_id), None)
    if assignment is None:
        raise ValueError(f"unknown assignment: {assignment_id}")
    attempt = current(assignment)
    if attempt["status"] != "prepared":
        raise ValueError(f"attempt is {attempt['status']}; only a prepared attempt can be executed")
    packet = read_data(within(folder, attempt["input_path"]))
    policy = packet.get("execution_policy") or {}
    # Provider endpoints and key names come from the installation; which model a role gets comes
    # from the routing frozen into this run, so replaying an old run cannot silently re-route it.
    routing = load_routing(ROOT)
    frozen_config = read_data(folder / "config.json") if (folder / "config.json").is_file() else {}
    fallback = resolve_routing(frozen_config.get("model_routing"), packet["role"], packet.get("stage"))
    provider = provider or policy.get("provider") or fallback.get("provider")
    model = model or policy.get("model") or fallback.get("model")
    if not provider or not model:
        raise ValueError("no provider/model for this role: set them in config/model-routing.yaml "
                         "(python3 scripts/routing.py set --role %s --provider ... --model ...)" % packet["role"])
    provider_config(provider, routing, root)
    effort = policy.get("reasoning_effort") or fallback.get("reasoning_effort")

    worker_id = f"{provider}:{model}:{uuid.uuid4().hex[:8]}"
    app.bind(run_id, assignment_id, worker_id, provider=provider, model=model, root=root)

    budget = Budget((packet.get("limits") or {}).get("max_search_queries_per_attempt"))
    session = Session(run_id, assignment_id, packet["role"], worker_id, budget, root=root)
    messages = build_messages(packet, budget)
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    reported_models = []
    discoverer = packet.get("discoverer_id")
    dispatch = {"web_search": session.web_search, "fetch_page": session.fetch_page,
                "tender_search": session.tender_search, "discussion_search": session.discussion_search,
                "archive_lookup": session.archive_lookup}
    repairs, problems = 0, []

    for step in range(1, max_steps + 1):
        answer = client(messages, provider=provider, model=model, tools=TOOLS,
                        reasoning_effort=effort, routing=routing, root=root)
        usage["calls"] += 1
        # A gateway may answer with a different model than the one requested; record what answered.
        if answer.get("model") and answer["model"] not in reported_models:
            reported_models.append(answer["model"])
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage[key] += (answer.get("usage") or {}).get(key) or 0
        message = answer["message"]
        calls = message.get("tool_calls") or []
        messages.append({"role": "assistant", "content": message.get("content") or "",
                         **({"tool_calls": calls} if calls else {})})
        if not calls:
            messages.append({"role": "user", "content":
                             "Continue with a tool call, or call submit_result if the evidence is collected."})
            continue
        for call in calls:
            name = (call.get("function") or {}).get("name")
            try:
                arguments = json.loads((call.get("function") or {}).get("arguments") or "{}")
            except ValueError as error:
                output = {"error": f"arguments were not valid JSON: {error}"}
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": name,
                                 "content": json.dumps(output, ensure_ascii=False)})
                continue
            if name == "submit_result":
                result = stamp(arguments, packet, worker_id, discoverer, session.log, attempt["input_hash"])
                existing = {c["id"]: c for c in read_jsonl(folder / "claims.jsonl")}
                manifest_now = read_data(folder / "manifest.json")
                assignment_now = next(a for a in manifest_now["assignments"] if a["id"] == assignment_id)
                problems = validate_result(result, manifest_now, assignment_now, current(assignment_now), existing)
                if not problems:
                    output_path = within(folder, packet["output_path"])
                    write_json(output_path, result)
                    write_json(output_path.parent / "usage.json",
                               {"worker_id": worker_id, "provider": provider, "model": model,
                                "models_reported_by_provider": reported_models,
                                "reasoning_effort": effort, "steps": step, "usage": usage,
                                "queries_spent": budget.spent(), "queries_allowed": budget.limit,
                                "sources_opened": len(session.opened), "pages_opened": len(session.trace),
                                "finished_at": _now()})
                    return {"assignment_id": assignment_id, "worker_id": worker_id, "provider": provider,
                            "model": model, "models_reported_by_provider": reported_models,
                            "result_path": str(output_path.relative_to(folder)),
                            "claims": len(result["claims"]), "queries_spent": budget.spent(),
                            "steps": step, "usage": usage, "accepted": False}
                repairs += 1
                if repairs > MAX_REPAIRS:
                    raise ValueError("result failed validation after %d repairs: %s"
                                     % (MAX_REPAIRS, "; ".join(problems[:6])))
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": name,
                                 "content": json.dumps({"accepted": False, "errors": problems[:12],
                                                        "instruction": "Fix exactly these and call submit_result again."},
                                                       ensure_ascii=False)})
                continue
            handler = dispatch.get(name)
            if handler is None:
                output = {"error": f"unknown tool {name!r}"}
            else:
                try:
                    output = handler(**arguments)
                except (ValueError, TypeError, OSError) as error:
                    output = {"error": f"{type(error).__name__}: {error}"}
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": name,
                             "content": json.dumps(output, ensure_ascii=False)[:20000]})
    raise ValueError("step limit reached without a valid result"
                     + (f"; last validation errors: {'; '.join(problems[:4])}" if problems else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--assignment", required=True)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    args = parser.parse_args()
    try:
        out = run_assignment(args.run, args.assignment, provider=args.provider, model=args.model,
                             max_steps=args.max_steps)
    except (ValueError, OSError, LLMError) as error:
        print(json.dumps({"assignment_id": args.assignment, "ok": False,
                          "error": str(error), "kind": getattr(error, "kind", "contract")},
                         ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({**out, "ok": True}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
