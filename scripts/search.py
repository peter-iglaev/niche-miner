#!/usr/bin/env python3
"""Provider-abstracted web search and page fetch for a research run.

Reasoning and data acquisition are separate layers: agents ask for evidence, this module
talks to a swappable search backend. Brave and Tavily are supported; the provider is chosen
by key availability and can be forced with --provider. A missing key marks the provider
unavailable instead of silently falling back to scraping. Raw responses and failures are
archived under runs/<run-id>/sources/ through scripts/sources.py.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import re
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import sources
from scripts.artifacts import atomic_text
from scripts.env import load_env

DEFAULT_ORDER = ("brave", "tavily")
KEY_ENV = {"brave": "BRAVE_API_KEY", "tavily": "TAVILY_API_KEY"}
MIN_INTERVAL_ENV = {"brave": "MONEY_FIRST_BRAVE_MIN_INTERVAL_SECONDS",
                    "tavily": "MONEY_FIRST_TAVILY_MIN_INTERVAL_SECONDS"}
DEFAULT_MIN_INTERVAL = {"brave": 1.05, "tavily": 0.0}
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
TAVILY_URL = "https://api.tavily.com/search"
RUN_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_FETCH_BYTES = 2_000_000
SEARCH_LIMITATIONS = (
    "Search-result ranking is provider ordering, not evidence of payment or prevalence. "
    "Result URLs must be opened and archived before any claim cites them."
)
FETCH_LIMITATIONS = (
    "A fetched page is a snapshot at collection time; page contents prove an offer or statement, "
    "not that a transaction happened."
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def capabilities(root=ROOT):
    """Offline capability snapshot: which search providers have a usable key. No network calls."""
    env = load_env(root)
    providers = []
    for name in DEFAULT_ORDER:
        key = env.get(KEY_ENV[name], "")
        providers.append({"name": name, "key_env": KEY_ENV[name], "available": bool(key.strip()),
                          "reason": None if key.strip() else f"{KEY_ENV[name]} is not set"})
    usable = [p["name"] for p in providers if p["available"]]
    return {"providers": providers, "available": usable, "default_order": list(DEFAULT_ORDER),
            "web_search_available": bool(usable),
            "checked_at": _now(), "network_used": False}


def _require_run(run_id):
    if not RUN_PATTERN.fullmatch(run_id or ""):
        raise ValueError("run id must use lowercase letters, digits and hyphens")
    return run_id


def _min_interval(provider, env):
    raw = env.get(MIN_INTERVAL_ENV[provider])
    if raw is None or not str(raw).strip():
        return DEFAULT_MIN_INTERVAL[provider]
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{MIN_INTERVAL_ENV[provider]} must be a nonnegative number") from error
    if value < 0:
        raise ValueError(f"{MIN_INTERVAL_ENV[provider]} must be a nonnegative number")
    return value


@contextmanager
def _provider_slot(root, provider, interval):
    """Serialize provider starts across worker processes and enforce the plan interval."""
    folder = Path(root).resolve() / "runs" / ".provider-rate-limits"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{provider}.lock"
    with path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        lock.seek(0)
        try:
            previous = float(lock.read().strip() or 0)
        except ValueError:
            previous = 0
        delay = interval - (time.time() - previous)
        if delay > 0:
            time.sleep(delay)
        lock.seek(0)
        lock.truncate()
        lock.write(str(time.time()))
        lock.flush()
        yield
        fcntl.flock(lock, fcntl.LOCK_UN)


def _brave_request(key, query, count, country, search_lang, freshness):
    params = {"q": query, "count": min(max(count, 1), 20)}
    if country:
        params["country"] = country
    if search_lang:
        params["search_lang"] = search_lang
    if freshness:
        params["freshness"] = freshness
    url = f"{BRAVE_URL}?{urllib.parse.urlencode(params)}"
    headers = {"X-Subscription-Token": key, "Accept": "application/json"}
    return url, lambda: sources.http_get(url, headers)


def _tavily_request(key, query, count, country, search_lang, freshness):
    del country, search_lang, freshness  # Tavily exposes its own filters; not mapped to avoid guessing.
    url = TAVILY_URL
    payload = {"query": query, "max_results": min(max(count, 1), 20), "search_depth": "basic"}
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    return url, lambda: sources.http_post_json(url, payload, headers)


REQUESTERS = {"brave": _brave_request, "tavily": _tavily_request}


def _normalize(provider, payload):
    if not isinstance(payload, dict):
        raise ValueError(f"{provider} returned a non-object body")
    if provider == "brave":
        items = (payload.get("web") or {}).get("results")
    else:
        items = payload.get("results")
    if items is None:
        return None  # Unrecognised shape: report as a parse failure rather than as zero results.
    results = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("url"):
            continue
        results.append({"rank": index + 1, "title": item.get("title"), "url": item["url"],
                        "snippet": item.get("content") if provider == "tavily" else item.get("description"),
                        "score": item.get("score"), "published": item.get("age")})
    return results


def _record_search(run_id, entry, root=ROOT):
    """Append one provider attempt; repeated writes of the same attempt are idempotent."""
    path = Path(root).resolve() / "runs" / _require_run(run_id) / "searches.jsonl"
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = []
        if path.is_file():
            existing = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        merged = {record["search_id"]: record for record in existing}
        if merged.get(entry["search_id"], entry) != entry:
            raise ValueError(f"conflicting search record: {entry['search_id']}")
        merged[entry["search_id"]] = entry
        atomic_text(path, "".join(json.dumps(merged[k], ensure_ascii=False, allow_nan=False) + "\n"
                                  for k in sorted(merged)))
        fcntl.flock(lock, fcntl.LOCK_UN)
    return entry


def logical_query_id(entry):
    """Provider-independent semantic query, geography/language/period and collection day.

    Result count and provider retries are acquisition details, not new research questions.
    Derive rather than trust supplied IDs, so an arbitrary reused ID cannot hide queries.
    """
    day = (entry.get("access_date") or entry.get("searched_at") or "")[:10]
    identity = [entry.get("query", "").strip(), entry.get("country") or entry.get("geography"),
                entry.get("search_lang") or entry.get("language"),
                entry.get("freshness") or entry.get("period"), day]
    return "qry-" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]


def logical_query_count(entries):
    return len({logical_query_id(entry) for entry in entries
                if entry.get("retrieval_status") != "capability_unavailable"
                and entry.get("status") != "not_collected"})


def search_log(run_id, *, root=ROOT):
    path = Path(root).resolve() / "runs" / _require_run(run_id) / "searches.jsonl"
    records = []
    if path.is_file():
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    failure_counts = {kind: sum(r.get("failure_kind") == kind or
                               (kind == "rate_limit" and r.get("http_status") == 429) for r in records)
                      for kind in ("rate_limit", "transport", "http", "parse")}
    return {"run_id": run_id, "query_count": logical_query_count(records),
            "provider_attempt_count": len(records), "failure_counts": failure_counts, "searches": records}


def search(run_id, query, *, provider=None, count=10, country=None, search_lang=None, freshness=None,
           assignment_id=None, role=None, worker_id=None, root=ROOT):
    """One semantic research query with separately recorded provider attempts.

    Fall back once per available provider on rate limits or transport failures. A named provider
    is tried first, then the others in DEFAULT_ORDER. Other HTTP failures and malformed responses
    stay explicit errors; empty results are valid results.

    assignment_id/role/worker_id say which agent asked and are recorded next to the attempt; they
    never enter the logical query identity or the source identity, so budgets and archives are
    unchanged whether or not an agent states who it is.
    """
    _require_run(run_id)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be nonempty text")
    query = query.strip()
    env = load_env(root)
    if provider and provider not in REQUESTERS:
        raise ValueError("unknown search provider: " + provider)
    # A named provider goes first, not alone: when its quota or transport fails, the other
    # configured providers still answer the same logical query.
    order = [provider, *(name for name in DEFAULT_ORDER if name != provider)] if provider else list(DEFAULT_ORDER)
    searched_at = _now()
    base = {"run_id": run_id, "query": query, "count": count, "country": country,
            "search_lang": search_lang, "freshness": freshness, "searched_at": searched_at}
    query_id = logical_query_id(base)
    asked_by = {"assignment_id": assignment_id, "role": role, "worker_id": worker_id}
    attempts, rate_limited, transport_failures = [], [], []
    last_entry = None

    def finish(entry=None, results=None):
        actual = entry or last_entry
        success = actual is not None and actual["retrieval_status"] == "collected"
        resolution = "collected" if success else (actual["failure_kind"] if actual else "capability_unavailable")
        if resolution == "rate_limit":
            resolution = "rate_limited"
        return {"run_id": run_id, "query": query, "logical_query_id": query_id,
                "provider": actual["provider"] if actual else None, "requested_provider": provider,
                "resolution": resolution, "result_count": len(results) if success else None,
                "results": results or [], "source_id": actual["source_id"] if actual else None,
                "retrieval_status": "collected" if success else "error" if actual else "capability_unavailable",
                "http_status": actual["http_status"] if actual else None,
                "rate_limited": rate_limited, "transport_failures": transport_failures,
                "attempts": attempts, "error": actual["error"] if actual else "no search provider has a configured key",
                "limitations": SEARCH_LIMITATIONS, "searched_at": searched_at}

    for provider_index, name in enumerate(order):
        key = env.get(KEY_ENV[name], "").strip()
        if not key:
            attempts.append({"provider": name, "retrieval_status": "capability_unavailable",
                             "error": f"{KEY_ENV[name]} is not set", "logical_query_id": query_id, **asked_by})
            continue
        later_provider_available = any(env.get(KEY_ENV[p], "").strip() for p in order[provider_index + 1:])
        max_provider_attempts = 1 if later_provider_available else 2
        for provider_attempt in range(1, max_provider_attempts + 1):
            url, call = REQUESTERS[name](key, query, count, country, search_lang, freshness)
            body, status, content_type, error_text, failure_kind, results = b"", None, "", None, None, None
            try:
                with _provider_slot(root, name, _min_interval(name, env)):
                    status, content_type, body = call()
            except urllib.error.HTTPError as error:
                body = error.read()
                status = error.code
                content_type = (error.headers or {}).get("Content-Type", "")
                error_text = f"HTTP {status}: {body[:400].decode('utf-8', errors='replace')}"
                failure_kind = "rate_limit" if status == 429 else "http"
            except (urllib.error.URLError, OSError, TimeoutError) as error:
                error_text, failure_kind = f"{type(error).__name__}: {error}", "transport"
            record = sources.archive(run_id, name, url, query=query, status=status, content_type=content_type,
                                     body=body, error=error_text, limitations=SEARCH_LIMITATIONS, root=root,
                                     method="POST" if name == "tavily" else "GET", **asked_by)
            if not error_text:
                try:
                    results = _normalize(name, json.loads(body.decode("utf-8")))
                    if results is None:
                        raise ValueError(f"unrecognised {name} response shape")
                except (ValueError, TypeError, AttributeError) as error:
                    error_text, failure_kind = f"{type(error).__name__}: {error}", "parse"
            entry = {**base, **asked_by, "search_id": "srch-" + uuid.uuid4().hex,
                     "logical_query_id": query_id, "attempt_id": f"{query_id}-{name}-{provider_attempt}",
                     "provider": name, "http_status": status,
                     "retrieval_status": "error" if error_text else "collected",
                     "failure_kind": failure_kind, "result_count": None if error_text else len(results),
                     "source_id": record["source_id"], "raw_response_ref": record["archive_path"],
                     "error": error_text,
                     "applied_filters": {"country": country if name == "brave" else None,
                                         "search_lang": search_lang if name == "brave" else None,
                                         "freshness": freshness if name == "brave" else None},
                     "limitations": SEARCH_LIMITATIONS + (" Tavily does not apply country, language or freshness filters."
                                                           if name == "tavily" and any((country, search_lang, freshness)) else "")}
            _record_search(run_id, entry, root)
            sources.record_usage(run_id, record["source_id"], tool=name, root=root, **asked_by)
            attempts.append(entry)
            last_entry = entry
            if failure_kind == "rate_limit":
                if name not in rate_limited:
                    rate_limited.append(name)
                if provider_attempt < max_provider_attempts:
                    continue
                break
            if failure_kind == "transport":
                if name not in transport_failures:
                    transport_failures.append(name)
                break
            return finish(entry, results)
    return finish()


def fetch(run_id, url, *, assignment_id=None, role=None, worker_id=None, root=ROOT, max_bytes=MAX_FETCH_BYTES):
    """Fetch one page directly and archive it. Used after a search returns a specific URL."""
    _require_run(run_id)
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("fetch requires an absolute http(s) URL")
    headers = {"Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
               "Accept-Language": "en,ru;q=0.8"}
    asked_by = {"assignment_id": assignment_id, "role": role, "worker_id": worker_id}
    try:
        status, content_type, body = sources.http_get(url, headers)
    except urllib.error.HTTPError as error:
        body = error.read()
        detail = body[:400].decode("utf-8", errors="replace")
        record = sources.archive(run_id, "direct_fetch", url, status=error.code,
                                 error=f"HTTP {error.code}: {detail}", body=body,
                                 content_type=(error.headers or {}).get("Content-Type", ""), limitations=FETCH_LIMITATIONS,
                                 root=root, **asked_by)
        sources.record_usage(run_id, record["source_id"], tool="direct_fetch", root=root, **asked_by)
        return {"run_id": run_id, "url": url, "source_id": record["source_id"], "retrieval_status": "error",
                "http_status": error.code, "error": record["error"], "archive_path": record["archive_path"],
                "limitations": FETCH_LIMITATIONS, "fetched_at": _now()}
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        record = sources.archive(run_id, "direct_fetch", url, error=f"{type(error).__name__}: {error}",
                                 limitations=FETCH_LIMITATIONS, root=root, **asked_by)
        sources.record_usage(run_id, record["source_id"], tool="direct_fetch", root=root, **asked_by)
        return {"run_id": run_id, "url": url, "source_id": record["source_id"], "retrieval_status": "error",
                "http_status": None, "error": record["error"], "archive_path": record["archive_path"],
                "limitations": FETCH_LIMITATIONS, "fetched_at": _now()}
    # max_bytes is a preview threshold only. The canonical raw response must never be truncated.
    exceeds_preview_limit = len(body) > max_bytes
    record = sources.archive(run_id, "direct_fetch", url, status=status, content_type=content_type,
                             body=body, limitations=FETCH_LIMITATIONS, root=root, **asked_by)
    sources.record_usage(run_id, record["source_id"], tool="direct_fetch", root=root, **asked_by)
    return {"run_id": run_id, "url": url, "source_id": record["source_id"], "retrieval_status": "collected",
            "http_status": status, "content_type": record["content_type"], "byte_length": len(body),
            "sha256": record["sha256"], "truncated": False, "exceeds_preview_limit": exceeds_preview_limit,
            "error": None, "archive_path": record["archive_path"],
            "limitations": FETCH_LIMITATIONS, "fetched_at": record["fetched_at"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["search", "fetch", "log", "providers"])
    parser.add_argument("--run")
    parser.add_argument("--query")
    parser.add_argument("--url")
    parser.add_argument("--provider", choices=list(DEFAULT_ORDER))
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--country")
    parser.add_argument("--lang")
    parser.add_argument("--freshness")
    parser.add_argument("--assignment", help="assignment id of the agent making this call (records who pulled the source)")
    parser.add_argument("--role", help="research role of the calling agent")
    parser.add_argument("--worker", help="actual runtime worker id of the calling agent")
    args = parser.parse_args()
    who = {"assignment_id": args.assignment, "role": args.role, "worker_id": args.worker}
    try:
        if args.command == "providers":
            out = capabilities()
        elif args.command == "log":
            out = search_log(args.run)
        elif args.command == "fetch":
            if not args.url:
                raise ValueError("fetch requires --url")
            out = fetch(args.run, args.url, **who)
        else:
            if not args.query:
                raise ValueError("search requires --query")
            out = search(args.run, args.query, provider=args.provider, count=args.count,
                         country=args.country, search_lang=args.lang, freshness=args.freshness, **who)
        print(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if out.get("retrieval_status") in (None, "collected") else 1
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
