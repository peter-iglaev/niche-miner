#!/usr/bin/env python3
"""Cheap hypothesis staging for a mining run, before the control plane.

Reasoning and code are separate layers: cheap models propose canonical niche atoms, this module
validates them, rejects duplicates, reports portfolio coverage and writes a deterministic
selection. A staged hypothesis carries no claims and is not a candidate artifact; only promoted
hypotheses become assignments with a frozen input and an accepted result.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.artifacts import atomic_text, write_jsonl
from scripts.score import load_config

RUN_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SPEND_STATUS = ("observed", "adjacent", "unknown", "not_collected")
TEXT_FIELDS = ("who", "trigger", "pain", "current_workaround", "job_to_be_done",
               "measurable_result", "distribution_channel", "origin")
FILE_FIELDS = ("id", "buyer_industry") + TEXT_FIELDS + ("existing_spend", "existing_spend_status")
PORTFOLIO = ROOT / "config/portfolio.yaml"


def _industries():
    from scripts.score import load_yaml
    buckets = load_yaml(PORTFOLIO)["preferred_industry_buckets"]
    return tuple(buckets)


def run_dir(run_id, root=ROOT):
    if not RUN_PATTERN.fullmatch(run_id or ""):
        raise ValueError("run id must use lowercase letters, digits and hyphens")
    base = (Path(root).resolve() / "runs").resolve()
    folder = (base / run_id).resolve()
    if not folder.is_relative_to(base) or not folder.is_dir():
        raise ValueError(f"run does not exist: {run_id}")
    return folder


def validate_hypothesis(record, industries=None):
    """Structural checks only. Payment evidence is not required at this stage."""
    errors = []
    if not isinstance(record, dict):
        return ["hypothesis must be an object"]
    unknown = sorted(set(record) - set(FILE_FIELDS))
    if unknown:
        errors.append("unknown fields: " + ", ".join(unknown))
    missing = [name for name in FILE_FIELDS if name not in record]
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    identifier = record.get("id")
    if not isinstance(identifier, str) or not RUN_PATTERN.fullmatch(identifier):
        errors.append("id must use lowercase letters, digits and hyphens")
    if industries is not None and record.get("buyer_industry") not in industries:
        errors.append("buyer_industry must be a configured portfolio bucket")
    for name in TEXT_FIELDS:
        value = record.get(name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{name} must be nonempty text")
    status = record.get("existing_spend_status")
    if status not in SPEND_STATUS:
        errors.append("existing_spend_status must be one of: " + ", ".join(SPEND_STATUS))
    spend = record.get("existing_spend")
    if status in ("observed", "adjacent"):
        if not isinstance(spend, str) or not spend.strip():
            errors.append("existing_spend must describe the observed spend for that status")
    elif spend is not None:
        errors.append("unknown or uncollected spend must be null, not a placeholder string")
    return errors


def load_staged(run_id, root=ROOT):
    path = run_dir(run_id, root) / "hypotheses.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def add(run_id, records, *, root=ROOT):
    """Merge validated hypotheses into the staging file. Identical retries collapse, conflicts fail."""
    industries = _industries()
    if not isinstance(records, list) or not records:
        raise ValueError("records must be a nonempty list")
    errors = []
    for index, record in enumerate(records):
        errors.extend(f"record {index}: {message}" for message in validate_hypothesis(record, industries))
    if errors:
        raise ValueError("; ".join(errors))
    merged = {record["id"]: record for record in load_staged(run_id, root)}
    added = 0
    for record in records:
        current = merged.get(record["id"])
        if current is not None and current != record:
            raise ValueError(f"conflicting hypothesis: {record['id']}")
        added += current is None
        merged[record["id"]] = record
    write_jsonl(run_dir(run_id, root) / "hypotheses.jsonl", [merged[key] for key in sorted(merged)])
    return {"run_id": run_id, "staged": len(merged), "added": added, "duplicates_collapsed": len(records) - added}


def audit(run_id, *, root=ROOT):
    records = load_staged(run_id, root)
    industries = _industries()
    by_industry = Counter(record["buyer_industry"] for record in records)
    spend = Counter(record["existing_spend_status"] for record in records)
    return {"run_id": run_id, "staged": len(records), "by_industry": dict(sorted(by_industry.items())),
            "industries_covered": len(by_industry), "industries_configured": len(industries),
            "missing_industries": [name for name in industries if name not in by_industry],
            "by_existing_spend_status": dict(sorted(spend.items())),
            "with_observed_spend": spend.get("observed", 0) + spend.get("adjacent", 0),
            "payment_evidence_required_at_this_stage": False}


def select(run_id, limit, *, root=ROOT):
    """Deterministic round-robin across buyer industries, so one industry cannot fill the stage."""
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    records = sorted(load_staged(run_id, root), key=lambda record: (record["buyer_industry"], record["id"]))
    grouped = {}
    for record in records:
        grouped.setdefault(record["buyer_industry"], []).append(record)
    picked, queues = [], [grouped[name] for name in sorted(grouped)]
    while queues and len(picked) < limit:
        remaining = []
        for queue in queues:
            if len(picked) >= limit:
                break
            picked.append(queue.pop(0))
            if queue:
                remaining.append(queue)
        queues = remaining
    path = run_dir(run_id, root) / "hypotheses_selected.jsonl"
    write_jsonl(path, picked)
    return {"run_id": run_id, "selected": len(picked), "requested": limit,
            "industries_in_selection": len({record["buyer_industry"] for record in picked}),
            "path": "hypotheses_selected.jsonl"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["add", "list", "audit", "select"])
    parser.add_argument("--run")
    parser.add_argument("--input")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    try:
        if args.command == "add":
            if not args.input:
                raise ValueError("add requires --input")
            records = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines()
                       if line.strip()]
            out = add(args.run, records)
        elif args.command == "select":
            if args.limit is None:
                raise ValueError("select requires --limit")
            out = select(args.run, args.limit)
        elif args.command == "audit":
            out = audit(args.run)
        else:
            records = load_staged(args.run)
            out = {"run_id": args.run, "staged": len(records), "hypotheses": records}
        print(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
