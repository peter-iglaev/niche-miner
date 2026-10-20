#!/usr/bin/env python3
"""Send a research run's report file to an authorized Telegram chat.

Sending is an external action: it requires an explicit authorization in the user request and a
configured bot. The bot token is read from the environment or .env.local and is never written into
run artifacts, logs or archived URLs. Every send attempt, including failures, is archived in
runs/<run-id>/sources/ so the run records what actually left the machine. The archived response
drops Telegram's chat and sender objects: the run records that a message left, not whose account
received it (runs are meant to be shareable without the owner's identity).
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import sources
from scripts.env import load_env

API = "https://api.telegram.org"
TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ENV = "TELEGRAM_CHAT_ID"
CAPTION_LIMIT = 1024
SAFE_URL = API + "/bot<redacted>/sendDocument"
LIMITATIONS = ("Sent to the chat configured in the environment. Telegram delivery is not evidence "
               "about any buyer and does not replace the archived run artifacts.")
NO_AUTH = "automatic send is not authorized by the run brief"
IDENTITY_FIELDS = ("chat", "from", "sender_chat", "via_bot")
REDACTED_NOTE = (" Recipient and sender identity fields removed before archiving: the archive holds the "
                 "redacted response, not the original bytes.")


def _now():
    return datetime.now(timezone.utc).isoformat()


def credentials(root=ROOT):
    """Offline credential check. Never returns the token or chat id values."""
    env = load_env(root)
    token = env.get(TOKEN_ENV, "").strip()
    chat_id = env.get(CHAT_ENV, "").strip()
    missing = [name for name, value in ((TOKEN_ENV, token), (CHAT_ENV, chat_id)) if not value]
    return {"available": not missing, "bot_token_present": bool(token), "chat_id_present": bool(chat_id),
            "reason": None if not missing else "not set: " + ", ".join(missing), "checked_at": _now(),
            "network_used": False}


def _require_credentials(root=ROOT):
    env = load_env(root)
    token = env.get(TOKEN_ENV, "").strip()
    chat_id = env.get(CHAT_ENV, "").strip()
    missing = [name for name, value in ((TOKEN_ENV, token), (CHAT_ENV, chat_id)) if not value]
    if missing:
        raise ValueError("telegram capability unavailable: not set: " + ", ".join(missing))
    return token, chat_id


def _multipart(fields, files):
    boundary = "----mfnm" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    for name, filename, content in files:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                     f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
                     + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _post(url, body, content_type):
    request = urllib.request.Request(url, data=body, headers={
        "Content-Type": content_type, "User-Agent": sources.USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read()


def _redact(body):
    """(bytes, redacted?) with every chat/sender object dropped from a JSON response."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body, False

    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in IDENTITY_FIELDS}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    cleaned = strip(payload)
    if cleaned == payload:
        return body, False
    return json.dumps(cleaned, ensure_ascii=False).encode("utf-8"), True


def _call(run_id, method, *, fields, files=(), root=ROOT):
    """Perform one Bot API call and archive the response. The token never reaches the archive."""
    token, _ = _require_credentials(root)
    url = f"{API}/bot{token}/{method}"
    body, content_type = _multipart(fields, files)
    safe = f"{API}/bot<redacted>/{method}"
    try:
        status, response_type, response_body = _post(url, body, content_type)
    except urllib.error.HTTPError as error:
        detail = error.read()[:600].decode("utf-8", errors="replace")
        record = sources.archive(run_id, "telegram", safe, query=method, status=error.code,
                                 error=f"HTTP {error.code}: {detail}", limitations=LIMITATIONS, root=root)
        return {"telegram_status": "error", "http_status": error.code, "error": record["error"],
                "source_id": record["source_id"], "sent_at": _now()}
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        record = sources.archive(run_id, "telegram", safe, query=method,
                                 error=f"{type(error).__name__}: {error}", limitations=LIMITATIONS, root=root)
        return {"telegram_status": "error", "http_status": None, "error": record["error"],
                "source_id": record["source_id"], "sent_at": _now()}
    archived, redacted = _redact(response_body)
    record = sources.archive(run_id, "telegram", safe, query=method, status=status,
                             content_type=response_type, body=archived,
                             limitations=LIMITATIONS + (REDACTED_NOTE if redacted else ""), root=root)
    payload = json.loads(response_body.decode("utf-8", errors="replace") or "{}")
    if not payload.get("ok"):
        return {"telegram_status": "error", "http_status": status,
                "error": payload.get("description") or "telegram refused the request",
                "source_id": record["source_id"], "sent_at": _now()}
    result = payload.get("result") or {}
    return {"telegram_status": "sent", "http_status": status, "message_id": result.get("message_id"),
            "source_id": record["source_id"], "error": None, "sent_at": _now()}


def _resolve_file(root, path):
    """(absolute path, path relative to the project). The containment check is lexical on purpose:
    runs/ may be a symlink into the private data repository (scripts/workspace.py link)."""
    base = Path(os.path.abspath(Path(root).resolve()))
    candidate = Path(os.path.abspath(base / path))
    if not candidate.is_relative_to(base):
        raise ValueError("refusing to send a file outside the project")
    if not candidate.is_file():
        raise ValueError(f"file not found: {candidate}")
    return candidate, candidate.relative_to(base)


def send_message(run_id, text, *, root=ROOT):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be nonempty")
    _, chat_id = _require_credentials(root)
    return _call(run_id, "sendMessage", fields={"chat_id": chat_id, "text": text[:4096]}, root=root)


def send_document(run_id, path, *, caption=None, root=ROOT):
    """Send one project file as a Telegram document. Requires explicit send authorization."""
    target, relative = _resolve_file(root, path)
    _, chat_id = _require_credentials(root)
    fields = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:CAPTION_LIMIT]
    outcome = _call(run_id, "sendDocument", fields=fields,
                    files=[("document", target.name, target.read_bytes())], root=root)
    return {**outcome, "file": relative.as_posix(), "byte_length": target.stat().st_size,
            "limitations": LIMITATIONS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "message", "send"])
    parser.add_argument("--run")
    parser.add_argument("--file")
    parser.add_argument("--text")
    parser.add_argument("--caption")
    args = parser.parse_args()
    try:
        if args.command == "check":
            out = credentials()
        elif args.command == "message":
            if not args.text:
                raise ValueError("message requires --text")
            out = send_message(args.run, args.text)
        else:
            if not args.file:
                raise ValueError("send requires --file")
            out = send_document(args.run, args.file, caption=args.caption)
        print(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if out.get("telegram_status", "sent") == "sent" else 1
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
