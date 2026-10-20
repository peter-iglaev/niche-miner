#!/bin/bash
# Install run-time dependencies inside a cloud session.
#
# A cloud environment's setup script starts in an unspecified working directory, so
# `pip install -r requirements.txt` there fails before the session opens. A SessionStart
# hook runs after Claude Code launches and gets $CLAUDE_PROJECT_DIR, which always points at
# the repository root. Locally the script exits before touching anything.
set -u

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}" || exit 0

# Ubuntu 24.04 marks the system interpreter externally managed; retry with the override
# rather than failing the session over PEP 668. A Debian-installed PyYAML has no RECORD file and
# cannot be upgraded in place, so the last attempt installs over it.
python3 -m pip install --quiet -r requirements.txt \
  || python3 -m pip install --quiet --break-system-packages -r requirements.txt \
  || python3 -m pip install --quiet --break-system-packages --ignore-installed -r requirements.txt \
  || echo "install_pkgs: dependency install failed; preflight will report what is missing" >&2

# Refuse commits that carry private paths, secrets or owner identifiers (scripts/workspace.py check).
git config core.hooksPath scripts/githooks 2>/dev/null || true

# Owner data (founder profile, runs, backlog) comes from the private data repository named by
# NICHE_MINER_DATA; see docs/workspace.md. Without it the application runs on its own files.
if [ -n "${NICHE_MINER_DATA:-}" ]; then
  python3 -B scripts/workspace.py link --quiet \
    || echo "install_pkgs: workspace link failed; run python3 scripts/workspace.py status" >&2
fi

exit 0
