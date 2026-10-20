"""Общий слой для бесплатных API: вызов, архив сырого ответа, разбор JSON, пауза между вызовами."""
from __future__ import annotations

import json
import time
import urllib.parse

from scripts import sources

_LAST_CALL = {}


def pause(provider, seconds):
    """Выдержать интервал между вызовами одного провайдера (в рамках процесса)."""
    previous = _LAST_CALL.get(provider, 0)
    wait = seconds - (time.time() - previous)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL[provider] = time.time()


def get_json(run_id, provider, url, *, query=None, headers=None, limitations=None, pause_seconds=0.0, root=None,
             who=None):
    """GET + архив. Возвращает (record, parsed) — parsed None при ошибке или не-JSON теле."""
    pause(provider, pause_seconds)
    kwargs = dict(limitations=limitations, root=root or sources.ROOT, **(who or {}))
    record = sources._capture(run_id, provider, url, query, lambda: sources.http_get(url, headers or {}), **kwargs)
    return record, _parse(record, root or sources.ROOT)


def post_json(run_id, provider, url, payload, *, query=None, headers=None, limitations=None, pause_seconds=0.0,
              root=None, who=None):
    pause(provider, pause_seconds)
    kwargs = dict(limitations=limitations, method="POST", root=root or sources.ROOT, **(who or {}))
    record = sources._capture(run_id, provider, url, query, lambda: sources.http_post_json(url, payload, headers or {}), **kwargs)
    return record, _parse(record, root or sources.ROOT)


def _parse(record, root):
    if record.get("retrieval_status") != "collected" or not record.get("archive_path"):
        return None
    path = sources.sources_dir(record["run_id"], root).parent / record["archive_path"]
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace").strip()
    if text.startswith(")]}'"):
        text = text[4:].strip()
    try:
        return json.loads(text)
    except ValueError:
        return None


def quote(value):
    return urllib.parse.quote(str(value), safe="")
