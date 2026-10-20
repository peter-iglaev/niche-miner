#!/usr/bin/env python3
"""Keep the owner's data out of the public code and within reach of every session.

The application is public; the owner's founder profile, runs, backlog and journal are not. The
paths that hold such data are listed in `.private-paths`. They live in a separate private data
repository whose location is NICHE_MINER_DATA (environment or .env.local, relative paths are
resolved against the repository root), and `link` symlinks them into this checkout, so every
script keeps using `runs/`, `backlog/` and `config/founder-profile.yaml` unchanged.

    python3 scripts/workspace.py status               # data location and the state of every path
    python3 scripts/workspace.py link                 # symlink private paths from NICHE_MINER_DATA
    python3 scripts/workspace.py check --staged       # pre-commit: no private paths, secrets or PII
    python3 scripts/workspace.py check --all          # the same over every tracked file
    python3 scripts/workspace.py export --out DIR     # clean public snapshot with a fresh git history

A repository is public-mode when its .gitignore ignores `runs`: there a private path in a commit
is an error. In the original all-in-one repository private paths are tracked on purpose, so check
skips them and scans only the application files. Content is scanned for well-known secret
formats, for the values of secret-named environment variables, and for the owner's identifiers in
`.privacy-denylist` (one literal per line, `re:` prefix for a regular expression), which is itself
a private path. Findings name the file, line and rule, never the matched value.
"""
from __future__ import annotations
import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.env import load_env

MANIFEST = ".private-paths"
DENYLIST = ".privacy-denylist"
DATA_ENV = "NICHE_MINER_DATA"
DENYLIST_ENV = "NICHE_MINER_DENYLIST"
ALLOW_MARKER = "privacy-check: allow"
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CHAT_ID)$")
SECRET_PATTERNS = {
    "telegram bot token": re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}"),
    "sk- API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "Tavily API key": re.compile(r"\btvly-[A-Za-z0-9_-]{16,}"),
    "Brave API key": re.compile(r"\bBSA[A-Za-z0-9_-]{24,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    "Google OAuth client secret": re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{10,}"),
    "Google OAuth refresh token": re.compile(r"\b1//0[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
SCAN_LIMIT = 5_000_000


def entries(root=ROOT):
    """(pattern, is_directory) pairs from .private-paths."""
    path = Path(root) / MANIFEST
    if not path.is_file():
        raise ValueError(f"{MANIFEST} not found in {root}")
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append((line.rstrip("/"), line.endswith("/")))
    return out


def is_private(relative, patterns):
    for pattern, directory in patterns:
        if directory and (relative == pattern or relative.startswith(pattern + "/")):
            return True
        if not directory and fnmatch.fnmatchcase(relative, pattern):
            return True
    return False


def data_dir(root=ROOT, env=None):
    value = (env if env is not None else load_env(root)).get(DATA_ENV, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return (path if path.is_absolute() else Path(root) / path).resolve()


def _git(root, *args, check=True):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if check and result.returncode != 0:
        raise ValueError(f"git {' '.join(args)}: {result.stderr.decode(errors='replace').strip()}")
    return result


def public_mode(root=ROOT):
    return _git(root, "check-ignore", "-q", "--no-index", "runs", check=False).returncode == 0


# --- link -------------------------------------------------------------------------------------

def _link_one(source, target, dry_run):
    """Symlink source -> target. Local data is never moved or overwritten."""
    if source.is_symlink():
        if source.resolve() == target.resolve():
            return "linked"
        if not dry_run:
            source.unlink()
            source.symlink_to(target)
        return "relinked"
    if source.is_dir() and not any(source.iterdir()):
        if not dry_run:
            source.rmdir()
    elif source.exists():
        return "conflict: local data present, left untouched"
    if not dry_run:
        source.parent.mkdir(parents=True, exist_ok=True)
        source.symlink_to(target)
    return "created"


def link(root=ROOT, data=None, dry_run=False):
    root = Path(root).resolve()
    data = data or data_dir(root)
    if data is None:
        raise ValueError(f"{DATA_ENV} is not set: point it at the private data repository")
    data = Path(data).resolve()
    if data == root:
        raise ValueError(f"{DATA_ENV} points at this repository itself; nothing to link")
    if not data.is_dir():
        raise ValueError(f"{DATA_ENV} directory does not exist: {data}")
    results = []
    for pattern, directory in entries(root):
        if directory:
            target = data / pattern
            if not target.exists() and not dry_run:
                target.mkdir(parents=True)
            results.append({"path": pattern + "/", "state": _link_one(root / pattern, target, dry_run)})
            continue
        matches = sorted(data.glob(pattern)) if any(c in pattern for c in "*?[") else [data / pattern]
        matches = [m for m in matches if m.is_file()]
        if not matches:
            results.append({"path": pattern, "state": "missing in data repository"})
        for target in matches:
            relative = target.relative_to(data).as_posix()
            results.append({"path": relative, "state": _link_one(root / relative, target, dry_run)})
    return {"data": str(data), "dry_run": dry_run, "paths": results,
            "conflicts": [r["path"] for r in results if r["state"].startswith("conflict")]}


def status(root=ROOT):
    root = Path(root).resolve()
    data = data_dir(root)
    report = {"root": str(root), "data": str(data) if data else None, "public_mode": public_mode(root),
              "hooks_path": _git(root, "config", "core.hooksPath", check=False).stdout.decode().strip() or None}
    if data and data != root and data.is_dir():
        report["paths"] = link(root, data, dry_run=True)["paths"]
    else:
        report["paths"] = [{"path": p + ("/" if d else ""),
                            "state": "symlink" if (root / p).is_symlink() else "local" if (root / p).exists() else "absent"}
                           for p, d in entries(root)]
    return report


# --- check ------------------------------------------------------------------------------------

def _denylist(root, env):
    texts = []
    for folder in {Path(root).resolve(), data_dir(root, env)} - {None}:
        path = folder / DENYLIST
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8"))
    texts.append(env.get(DENYLIST_ENV, "").replace(";", "\n"))
    rules = []
    for text in texts:
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            pattern = line[3:] if line.startswith("re:") else re.escape(line)
            rules.append(re.compile(pattern, re.IGNORECASE))
    return rules


def _env_secrets(env):
    return {name: value for name, value in env.items()
            if SECRET_NAME.search(name) and len(value) >= 8 and value.strip() == value}


def scan_text(text, *, denylist, secrets):
    """[(line number, rule)] for one file. The matched value never leaves this function."""
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(line):
                findings.append((number, name))
        for name, value in secrets.items():
            if value in line:
                findings.append((number, f"value of ${name}"))
        for index, pattern in enumerate(denylist, 1):
            if pattern.search(line):
                findings.append((number, f"{DENYLIST} entry {index}"))
    return findings


def check(root=ROOT, *, staged=False, denylist_root=None):
    root = Path(root).resolve()
    env = load_env(denylist_root or root)
    patterns = entries(root)
    public = public_mode(root)
    if staged:
        names = _git(root, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR").stdout.decode().split("\0")
    else:
        names = _git(root, "ls-files", "-z").stdout.decode().split("\0")
    names = [n for n in names if n]
    denylist = _denylist(denylist_root or root, env)
    secrets = _env_secrets(env)
    errors, skipped = [], 0
    for name in names:
        if is_private(name, patterns):
            if public:
                errors.append({"file": name, "rule": f"private path listed in {MANIFEST}"})
            else:
                skipped += 1
            continue
        if staged:
            blob = _git(root, "show", f":{name}", check=False).stdout
        else:
            path = root / name
            blob = path.read_bytes() if path.is_file() and not path.is_symlink() else b""
        if len(blob) > SCAN_LIMIT:
            continue
        text = blob.decode("utf-8", errors="replace")
        errors.extend({"file": name, "line": line, "rule": rule}
                      for line, rule in scan_text(text, denylist=denylist, secrets=secrets))
    if public:
        email = _git(root, "config", "user.email", check=False).stdout.decode().strip()
        noreply = email.endswith("@users.noreply.github.com")  # carries the login, which is public anyway
        if email and not noreply and any(p.search(email) for p in denylist):
            errors.append({"file": "git config user.email", "rule": f"matches {DENYLIST}; use a noreply address"})
    return {"root": str(root), "public_mode": public, "scanned": len(names) - skipped,
            "skipped_private": skipped, "denylist_rules": len(denylist), "errors": errors,
            "valid": not errors}


# --- export -----------------------------------------------------------------------------------

def export(out, *, root=ROOT, commit=False, author=None, message="Initial public release"):
    """Copy tracked application files into a fresh repository; private paths never enter it."""
    root = Path(root).resolve()
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"output directory is not empty: {out}")
    if out == root or out.is_relative_to(root):
        raise ValueError("export outside the repository, not into it")
    patterns = entries(root)
    names = [n for n in _git(root, "ls-files", "-z").stdout.decode().split("\0") if n]
    copied = 0
    for name in names:
        source = root / name
        if is_private(name, patterns) or source.is_symlink() or not source.is_file():
            continue
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    ignore = out / ".gitignore"
    block = "\n# Owner data (.private-paths): lives in the private data repository, see docs/workspace.md\n"
    block += "".join("/" + p + "\n" for p, _ in patterns)
    ignore.write_text((ignore.read_text(encoding="utf-8") if ignore.exists() else "") + block, encoding="utf-8")
    _git(out, "init", "-q", "-b", "main")
    _git(out, "config", "core.hooksPath", "scripts/githooks")
    _git(out, "add", "-A")
    report = check(out, staged=True, denylist_root=root)
    result = {"out": str(out), "files": copied, "check": report, "committed": False}
    if report["valid"] and commit:
        args = ["commit", "-q", "-m", message] + (["--author", author] if author else [])
        _git(out, *args)
        result["committed"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["status", "link", "check", "export"])
    parser.add_argument("--data", help=f"data repository (default: ${DATA_ENV})")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--staged", action="store_true", help="check the index instead of every tracked file")
    parser.add_argument("--all", action="store_true", help="check every tracked file (default)")
    parser.add_argument("--out", help="export: empty directory outside this repository")
    parser.add_argument("--commit", action="store_true", help="export: make the single initial commit")
    parser.add_argument("--author", help="export: commit author, e.g. 'Name <id+user@users.noreply.github.com>'")
    parser.add_argument("--quiet", action="store_true", help="print only problems")
    args = parser.parse_args()
    try:
        if args.command == "status":
            out = status()
        elif args.command == "link":
            out = link(data=Path(args.data) if args.data else None, dry_run=args.dry_run)
        elif args.command == "check":
            out = check(staged=args.staged)
        else:
            if not args.out:
                raise ValueError("export requires --out")
            out = export(args.out, commit=args.commit, author=args.author)
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    failed = (out.get("valid") is False or bool(out.get("conflicts"))
              or (args.command == "export" and not out["check"]["valid"]))
    if not (args.quiet and not failed):
        print(json.dumps(out, ensure_ascii=False, indent=2), file=sys.stderr if failed else sys.stdout)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
