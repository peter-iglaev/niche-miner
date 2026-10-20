"""Single-writer artifact helpers for research runs (no network or agent dispatch)."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_text(path: str | Path, text: str) -> None:
    path = Path(path)
    if path.is_symlink():
        # Owner data may be symlinked in from the private data repository (scripts/workspace.py);
        # replacing the link itself would silently move the file out of that repository.
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".pending-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path: str | Path, record: dict) -> None:
    atomic_text(path, json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_jsonl(path: str | Path, records: list[dict]) -> None:
    atomic_text(path, "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in records))


def seal_attempt(path: str | Path, payload: dict) -> str:
    """Create an immutable attempt; identical retries are safe, changed retries fail."""
    path = Path(path)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError(f"immutable attempt differs: {path}")
    else:
        atomic_text(path, encoded)
    return file_hash(path)


def merge_records(batches: list[list[dict]], key: str = "id") -> list[dict]:
    """Collapse byte-equivalent logical records, reject conflicting identities."""
    merged = {}
    for records in batches:
        for record in records:
            identity = record[key]
            if identity in merged and merged[identity] != record:
                raise ValueError(f"conflicting {key}: {identity}")
            merged[identity] = record
    return [merged[k] for k in sorted(merged)]
