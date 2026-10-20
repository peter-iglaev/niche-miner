"""Shared environment loading for local tooling. Secret values are never printed or logged."""
from __future__ import annotations

import os
from pathlib import Path

ENV_FILES = (".env.local", ".env")


def load_env(root):
    """Environment values for key lookup; the process environment wins over the env files."""
    values = dict(os.environ)
    for name in ENV_FILES:
        path = Path(root) / name
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return values
