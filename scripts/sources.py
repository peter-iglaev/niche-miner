#!/usr/bin/env python3
"""Free public-source fetchers that archive raw responses inside a run. No agent dispatch."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import mimetypes
import os
import tempfile
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.artifacts import atomic_text, write_json

USER_AGENT = "money-first-niche-miner/1.0 (research)"
TIMEOUT = 30
RUN_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
TED_URL = "https://api.ted.europa.eu/v3/notices/search"
WAYBACK_URL = "https://archive.org/wayback/available"
HN_URL = "https://hn.algolia.com/api/v1/search"
ARCHIVE_NOTE = "Raw public response stored at fetch time; it is a snapshot, not proof of any transaction."
# Verified against the API's own accepted-value list; the endpoint rejects an empty field list.
TED_FIELDS = ["publication-number", "notice-title", "publication-date", "buyer-name",
              "organisation-country-buyer", "notice-type", "links"]


def _now():
    return datetime.now(timezone.utc).isoformat()


def http_get(url, headers=None):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read()


def http_post_json(url, payload, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={
        "User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read()


def sources_dir(run_id, root=ROOT):
    if not RUN_PATTERN.fullmatch(run_id or ""):
        raise ValueError("run id must use lowercase letters, digits and hyphens")
    base = (Path(root).resolve() / "runs").resolve()
    folder = (base / run_id / "sources").resolve()
    if not folder.is_relative_to(base):
        raise ValueError("run id escapes runs/")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _media_type(content_type, payload):
    """Use response MIME, with magic detection for commonly mislabelled binary data."""
    signatures = ((b"%PDF-", "application/pdf"), (b"\x89PNG\r\n\x1a\n", "image/png"),
                  (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif"))
    for signature, detected in signatures:
        if payload.startswith(signature):
            return detected
    return content_type.strip() if content_type else "application/octet-stream"


def _extension(content_type):
    mime = content_type.split(";", 1)[0].strip().lower()
    fixed = {"application/pdf": ".pdf", "application/json": ".json", "text/html": ".html",
             "application/xhtml+xml": ".html", "text/plain": ".txt", "text/csv": ".csv",
             "application/octet-stream": ".bin", "application/xml": ".xml", "text/xml": ".xml",
             "image/jpeg": ".jpg"}
    return fixed.get(mime) or (".json" if mime.endswith("+json") else None) or mimetypes.guess_extension(mime) or ".bin"


def _atomic_bytes(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".pending-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def archive(run_id, provider, url, *, query=None, status=None, content_type="", body=b"",
            error=None, limitations=None, method="GET", assignment_id=None, role=None, worker_id=None, root=ROOT):
    """Archive original bytes; request+content identity preserves distinct source origins.

    Repeated identical captures reuse an immutable snapshot. HTTP failure bodies are retained
    when available, but their retrieval_status remains error and cannot substantiate claims.

    Who fetched a source is provenance, never identity: assignment/role/worker are stamped on the
    first capture only and stay out of the source_id hash, so the same bytes keep the same ID no
    matter which agent asked for them. Every later use is appended to source_usage.jsonl instead.
    """
    if not isinstance(body, (bytes, bytearray, memoryview)):
        raise TypeError("raw source body must be bytes, never decoded text")
    payload = bytes(body)
    payload_hash = hashlib.sha256(payload).hexdigest()
    media_type = _media_type(content_type, payload)
    identity = json.dumps([provider, url, query, method, status, media_type, payload_hash, error],
                          ensure_ascii=False, separators=(",", ":"))
    source_id = "src-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    suffix = _extension(media_type)
    folder = sources_dir(run_id, root)
    has_body = not error or bool(payload)
    record = {"source_id": source_id, "run_id": run_id, "provider": provider, "query": query,
              "request_url": url, "method": method, "http_status": status, "content_type": media_type,
              "response_content_type": content_type or None,
              "fetched_at": _now(), "sha256": payload_hash, "byte_length": len(payload),
              "archive_path": f"sources/{source_id}{suffix}" if has_body else None,
              "retrieval_status": "error" if error else "collected",
              "error": error, "limitations": limitations or ARCHIVE_NOTE,
              "first_assignment_id": assignment_id, "first_role": role, "first_worker_id": worker_id}
    meta_path = folder / f"{source_id}.meta.json"
    if meta_path.exists():
        existing = json.loads(meta_path.read_text(encoding="utf-8"))
        if existing["sha256"] != payload_hash or existing["archive_path"] != record["archive_path"]:
            raise ValueError(f"conflicting immutable source: {source_id}")
        if has_body and (folder / f"{source_id}{suffix}").read_bytes() != payload:
            raise ValueError(f"corrupt immutable source: {source_id}")
        return existing
    if has_body:
        _atomic_bytes(folder / f"{source_id}{suffix}", payload)
    write_json(meta_path, record)
    return record


def usage_path(run_id, root=ROOT):
    folder = sources_dir(run_id, root).parent
    return folder / "source_usage.jsonl"


def record_usage(run_id, source_id, *, tool, assignment_id=None, role=None, worker_id=None, root=ROOT):
    """Append one 'this agent used this source' row. Missing attribution is recorded as null, not invented."""
    if not source_id:
        raise ValueError("source_id is required to record usage")
    entry = {"source_id": source_id, "assignment_id": assignment_id, "role": role, "worker_id": worker_id,
             "tool": tool, "at": _now()}
    path = usage_path(run_id, root)
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = []
        if path.is_file():
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        identity = {k: entry[k] for k in ("source_id", "assignment_id", "role", "worker_id", "tool")}
        if not any({k: row.get(k) for k in identity} == identity for row in rows):
            rows.append(entry)
            atomic_text(path, "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows))
        fcntl.flock(lock, fcntl.LOCK_UN)
    return entry


def _capture(run_id, provider, url, query, call, *, limitations=None, method="GET",
             assignment_id=None, role=None, worker_id=None, root=ROOT):
    attribution = {"assignment_id": assignment_id, "role": role, "worker_id": worker_id}
    try:
        status, content_type, body = call()
    except urllib.error.HTTPError as error:
        body = error.read()
        detail = body[:400].decode("utf-8", errors="replace")
        record = archive(run_id, provider, url, query=query, status=error.code, error=f"HTTP {error.code}: {detail}",
                         body=body, content_type=(error.headers or {}).get("Content-Type", ""),
                         limitations=limitations, method=method, root=root, **attribution)
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        record = archive(run_id, provider, url, query=query, error=f"{type(error).__name__}: {error}",
                         limitations=limitations, method=method, root=root, **attribution)
    else:
        record = archive(run_id, provider, url, query=query, status=status, content_type=content_type, body=body,
                         limitations=limitations, method=method, root=root, **attribution)
    record_usage(run_id, record["source_id"], tool=provider, root=root, **attribution)
    return record


def ted_search(run_id, query, *, limit=10, fields=None, assignment_id=None, role=None, worker_id=None, root=ROOT):
    """Anonymous access is permitted for published notices; no API key is used."""
    payload = {"query": query, "fields": list(fields or TED_FIELDS), "limit": limit, "page": 1}
    url = f"{TED_URL}?query={urllib.parse.quote(query)}"
    return _capture(run_id, "ted", url, query, lambda: http_post_json(TED_URL, payload),
                    limitations="Anonymous TED search over published notices; absent values mean not published, not absent in reality.",
                    method="POST", assignment_id=assignment_id, role=role, worker_id=worker_id, root=root)


def wayback_snapshot(run_id, target, *, assignment_id=None, role=None, worker_id=None, root=ROOT):
    url = f"{WAYBACK_URL}?url={urllib.parse.quote(target, safe='')}"
    return _capture(run_id, "wayback", url, target, lambda: http_get(url),
                    limitations="Availability API reports the closest archived capture, not necessarily this date's version.",
                    assignment_id=assignment_id, role=role, worker_id=worker_id, root=root)


def hn_search(run_id, query, *, tags="story", limit=20, assignment_id=None, role=None, worker_id=None, root=ROOT):
    url = f"{HN_URL}?query={urllib.parse.quote(query)}&tags={tags}&hitsPerPage={limit}"
    return _capture(run_id, "hn_algolia", url, query, lambda: http_get(url),
                    limitations="Practitioner discussion is a reported experience, not representative prevalence.",
                    assignment_id=assignment_id, role=role, worker_id=worker_id, root=root)


def list_sources(run_id, *, root=ROOT):
    folder = sources_dir(run_id, root)
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.meta.json"))]
    return {"run_id": run_id, "count": len(records),
            "collected": sum(1 for r in records if r["retrieval_status"] == "collected"),
            "failed": sum(1 for r in records if r["retrieval_status"] == "error"), "sources": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("command", choices=["ted", "wayback", "hn", "list"])
    parser.add_argument("--query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--assignment", help="assignment id of the agent making this call (records who pulled the source)")
    parser.add_argument("--role", help="research role of the calling agent")
    parser.add_argument("--worker", help="actual runtime worker id of the calling agent")
    args = parser.parse_args()
    who = {"assignment_id": args.assignment, "role": args.role, "worker_id": args.worker}
    try:
        if args.command == "list":
            out = list_sources(args.run)
        elif not args.query:
            raise ValueError(f"{args.command} requires --query")
        elif args.command == "ted":
            out = ted_search(args.run, args.query, limit=args.limit, **who)
        elif args.command == "wayback":
            out = wayback_snapshot(args.run, args.query, **who)
        else:
            out = hn_search(args.run, args.query, limit=args.limit, **who)
        print(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
