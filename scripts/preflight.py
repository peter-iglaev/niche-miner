#!/usr/bin/env python3
"""Offline readiness checks for the control plane: schemas, tests, synthetic run. No agent dispatch."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.artifacts import write_json

REQUIRED = [
    "AGENTS.md", "README.md", "requirements.txt",
    "scripts/app.py", "scripts/artifacts.py", "scripts/env.py", "scripts/hypotheses.py",
    "scripts/preflight.py", "scripts/report.py", "scripts/report_pdf.py", "scripts/founder.py",
    "schemas/founder-profile.schema.json", "assets/fonts/DejaVuSans.ttf", "assets/fonts/DejaVuSans-Bold.ttf",
    "assets/fonts/LICENSE-DejaVu.txt",
    "scripts/install_pkgs.sh", "scripts/llm.py", "scripts/worker.py", "scripts/pipeline_viewer.py", "scripts/pipeline_viewer.js", "scripts/pipeline_viewer.css",
    "scripts/routing.py", "scripts/workspace.py", "scripts/score.py", "scripts/search.py", "scripts/sources.py", "scripts/telegram.py", "scripts/validate.py",
    "config/model-routing.yaml", "config/scoring.yaml", "config/portfolio.yaml", "config/research-policy.yaml", "config/founder-profile.example.yaml",
    "docs/architecture.md", "tasks/README.md", "templates/evidence.md",
    "tests/test_app_cycle.py", "tests/test_artifacts.py", "tests/test_hypotheses.py", "tests/test_report.py",
    "tests/test_score.py", "tests/test_founder.py", "tests/test_report_pdf.py", "tests/test_source_references.py",
    "tests/test_routing.py", "tests/test_worker.py", "tests/test_search.py", "tests/test_sources.py", "tests/test_telegram.py", "tests/test_validate.py", "tests/test_workspace.py",
]


def _synthetic_run():
    from scripts import app
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_id = "preflight-synthetic"
        app.create_run(run_id, "preflight synthetic check", "targeted", "stop after the synthetic check", root=root)
        state = app.status(run_id, root=root)
        if state["scope"] != "targeted" or state["candidate_count"] != 0:
            raise ValueError("synthetic run did not reach the expected empty targeted state")
        shutil.rmtree(root / "runs" / run_id, ignore_errors=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "runs/.preflight/report.json"))
    args = parser.parse_args()
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    errors = [f"missing required preflight file: {name}" for name in missing]
    schemas = sorted((ROOT / "schemas").glob("*.schema.json"))
    prompts = sorted((ROOT / "agents").glob("*.md"))
    if not schemas: errors.append("no schemas found")
    if not prompts: errors.append("no agent prompts found")
    for path in schemas:
        try:
            Draft202012Validator.check_schema(json.loads(path.read_text()))
        except Exception as error:  # noqa: BLE001 - report any schema defect
            errors.append(f"{path.relative_to(ROOT)}: {error}")
    for path in sorted((ROOT / "scripts").glob("*.py")):
        if path.name == "preflight.py": continue
        if re.search(r"pilot", path.read_text(), re.IGNORECASE):
            errors.append(f"{path.relative_to(ROOT)}: stale pilot reference")
    try:
        from scripts.founder import load_profile
        load_profile()
        from scripts.report_pdf import _fonts
        _fonts()
        if not shutil.which("pdftoppm"): errors.append("Poppler pdftoppm is required for PDF rendering")
    except (ImportError, OSError, ValueError) as error:
        errors.append(f"founder/PDF preflight: {error}")
    test_output, tests_run = "", 0
    if not errors:
        result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
                                cwd=ROOT, capture_output=True, text=True)
        test_output = result.stdout + result.stderr
        match = re.search(r"Ran (\d+) tests?", test_output)
        tests_run = int(match.group(1)) if match else 0
        if result.returncode: errors.append("offline test suite failed")
    from scripts.search import capabilities
    from scripts.telegram import credentials
    search_capabilities = capabilities()
    telegram_credentials = credentials()
    warnings = [] if search_capabilities["web_search_available"] else [
        "no search provider key found: set BRAVE_API_KEY or TAVILY_API_KEY in the environment or .env.local; "
        "stages that need web search must be reported blocked instead of falling back to scraping"]
    synthetic = False
    if not errors:
        try:
            synthetic = _synthetic_run()
        except Exception as error:  # noqa: BLE001 - report any control-plane defect
            errors.append(f"synthetic run failed: {error}")
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "search_capabilities": search_capabilities,
        "telegram_credentials": telegram_credentials,
        "tests_run": tests_run,
        "synthetic_run": synthetic,
        "schema_count": len(schemas),
        "prompt_count": len(prompts),
        "test_output": test_output,
        "file_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                        for name in REQUIRED if (ROOT / name).is_file()},
        "research_executed_by_preflight": False,
    }
    write_json(Path(args.output), report)
    print(json.dumps({k: v for k, v in report.items() if k not in {"file_hashes", "test_output"}}, ensure_ascii=False, indent=2))
    if errors and test_output:
        print(test_output, file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
