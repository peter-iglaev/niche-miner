#!/usr/bin/env python3
"""Minimal OpenAI-compatible chat client for the research workers.

The provider, its endpoint and its key environment variable come from
`config/model-routing.yaml`; nothing about a vendor is hard-coded here. Only the
chat-completions protocol is implemented, which is what DeepSeek, GLM, Kimi and most
gateways speak. A provider that speaks a different protocol is refused loudly instead of
being addressed as if it were OpenAI.

This module talks to a model. It makes no research judgments, stores no evidence and
knows nothing about runs.
"""
from __future__ import annotations
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.env import load_env
from scripts.routing import load_routing

SUPPORTED_API = ("openai-completions",)
TIMEOUT = 180
RETRY_STATUS = (408, 409, 429, 500, 502, 503, 504)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2, 8)


class LLMError(RuntimeError):
    """A model call that did not produce a usable answer. Never silently downgraded."""

    def __init__(self, message, *, kind="request", status=None):
        super().__init__(message)
        self.kind = kind
        self.status = status


def provider_config(provider_id, routing=None, root=ROOT):
    routing = routing or load_routing(root)
    for entry in routing.get("providers") or []:
        if isinstance(entry, dict) and entry.get("id") == provider_id:
            return entry
    raise LLMError(f"provider {provider_id!r} is not described in config/model-routing.yaml",
                   kind="configuration")


def credentials(provider_id, routing=None, root=ROOT):
    """Offline check: endpoint and key availability for one provider. No network calls."""
    try:
        entry = provider_config(provider_id, routing, root)
    except LLMError as error:
        return {"provider": provider_id, "available": False, "reason": str(error)}
    key_env = entry.get("api_key_env") or ""
    key = (load_env(root).get(key_env) or "").strip() if key_env else ""
    return {"provider": provider_id, "base_url": entry.get("base_url"), "api": entry.get("api"),
            "key_env": key_env, "available": bool(key and entry.get("base_url")),
            "reason": None if key else f"{key_env or 'api_key_env'} is not set"}


def _request(url, payload, key, timeout):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "Authorization": f"Bearer {key}", "User-Agent": "money-first-niche-miner/1.0 (research)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def complete(messages, *, provider, model, tools=None, reasoning_effort=None, temperature=None,
             max_tokens=None, routing=None, root=ROOT, timeout=TIMEOUT):
    """One chat turn. Returns the assistant message plus token usage and the provider used."""
    entry = provider_config(provider, routing, root)
    api = entry.get("api") or "openai-completions"
    if api not in SUPPORTED_API:
        raise LLMError(f"provider {provider!r} speaks {api!r}; this client implements "
                       + ", ".join(SUPPORTED_API), kind="configuration")
    key = (load_env(root).get(entry.get("api_key_env") or "") or "").strip()
    if not key:
        raise LLMError(f"{entry.get('api_key_env')} is not set: provider {provider!r} is unavailable",
                       kind="capability_unavailable")
    base = str(entry.get("base_url") or "").rstrip("/")
    if not base:
        raise LLMError(f"provider {provider!r} has no base_url", kind="configuration")
    payload = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if reasoning_effort and entry.get("send_reasoning_effort"):
        payload["reasoning_effort"] = reasoning_effort
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    url = f"{base}/chat/completions"
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            status, raw = _request(url, payload, key, timeout)
        except urllib.error.HTTPError as error:
            detail = error.read()[:400].decode("utf-8", errors="replace")
            last_error = LLMError(f"HTTP {error.code} from {provider}: {detail}",
                                  kind="rate_limit" if error.code == 429 else "http", status=error.code)
            if error.code not in RETRY_STATUS or attempt == MAX_ATTEMPTS:
                raise last_error
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            last_error = LLMError(f"{type(error).__name__}: {error}", kind="transport")
            if attempt == MAX_ATTEMPTS:
                raise last_error
        else:
            try:
                answer = json.loads(raw.decode("utf-8"))
                choice = answer["choices"][0]["message"]
            except (ValueError, KeyError, IndexError, TypeError) as error:
                raise LLMError(f"unrecognised response from {provider}: {type(error).__name__}: {error}",
                               kind="parse", status=status) from error
            usage = answer.get("usage") or {}
            return {"message": choice, "usage": {
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "total_tokens": usage.get("total_tokens")},
                    "provider": provider, "model": answer.get("model") or model,
                    "finish_reason": answer["choices"][0].get("finish_reason")}
        time.sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
    raise last_error or LLMError("model call failed without a recorded reason")


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["providers", "ping"])
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model")
    args = parser.parse_args()
    routing = load_routing()
    if args.command == "providers":
        out = {"providers": [credentials(p.get("id"), routing) for p in routing.get("providers") or []]}
    else:
        entry = provider_config(args.provider, routing)
        model = args.model or (entry.get("models") or [None])[0]
        if not model:
            raise SystemExit(json.dumps({"error": "no model to ping; pass --model"}, ensure_ascii=False))
        try:
            answer = complete([{"role": "user", "content": "Reply with the single word: ready"}],
                              provider=args.provider, model=model, routing=routing)
            out = {"provider": args.provider, "model": answer["model"], "usage": answer["usage"],
                   "reply": (answer["message"].get("content") or "")[:200]}
        except LLMError as error:
            print(json.dumps({"error": str(error), "kind": error.kind}, ensure_ascii=False, indent=2))
            return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
