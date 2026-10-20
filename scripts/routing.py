#!/usr/bin/env python3
"""Per-role model routing: config -> frozen packet -> bind record. No LLM is called here.

The routing file only states which provider/model/reasoning effort each research role should get.
The Codex root passes those values to spawn_agent and records the actually used model at bind time;
the dashboard shows requested vs actual. A null value means "runtime choice".
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.artifacts import atomic_text
from scripts.validate import ROLES, schema_errors

CONFIG_PATH = ROOT / "config" / "model-routing.yaml"
STAGES = ("light_validation", "serious_validation", "deep_investigation")
FIELDS = ("provider", "model", "reasoning_effort")
EFFORTS = ("low", "medium", "high")
CLEAR_VALUES = ("any", "null", "none", "")


def _empty_choice():
    return {field: None for field in FIELDS}


def default_routing():
    return {"schema_version": "1.0", "providers": [], "default": _empty_choice(),
            "roles": {role: _empty_choice() for role in ROLES}, "stages": {}}


def routing_path(root=ROOT):
    return Path(root) / "config" / "model-routing.yaml"


def load_routing(root=ROOT):
    """Read config/model-routing.yaml; a missing or empty file means every role is a runtime choice."""
    path = routing_path(root)
    if not path.is_file():
        return default_routing()
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a mapping")
    routing = default_routing()
    routing.update({k: v for k, v in loaded.items() if k in routing or k == "schema_version"})
    routing["roles"] = {role: {**_empty_choice(), **((loaded.get("roles") or {}).get(role) or {})} for role in ROLES}
    routing["default"] = {**_empty_choice(), **(loaded.get("default") or {})}
    routing["stages"] = loaded.get("stages") or {}
    routing["providers"] = loaded.get("providers") or []
    return routing


def validate_routing(routing):
    """Schema + role vocabulary errors. Unknown providers are allowed (any runtime may be plugged in)."""
    if not isinstance(routing, dict):
        return ["routing: expected a mapping"]
    errors = schema_errors(routing, "model-routing.schema.json", "routing")
    for stage, roles in (routing.get("stages") or {}).items():
        if stage not in STAGES:
            errors.append(f"routing/stages/{stage}: unknown stage")
        for role in (roles or {}):
            if role not in ROLES:
                errors.append(f"routing/stages/{stage}/{role}: unknown role")
    for role in (routing.get("roles") or {}):
        if role not in ROLES:
            errors.append(f"routing/roles/{role}: unknown role")
    return errors


def warnings_for(routing):
    known = {p.get("id") for p in (routing.get("providers") or []) if isinstance(p, dict)}
    notes = []
    for role, choice in (routing.get("roles") or {}).items():
        provider = (choice or {}).get("provider")
        if provider and known and provider not in known:
            notes.append(f"{role}: provider '{provider}' is not in the providers list (allowed, but check the spelling)")
        if (choice or {}).get("model") and not provider:
            notes.append(f"{role}: model is set without a provider")
    return notes


def resolve(routing, role, stage=None):
    """default <- role <- stage override. Tolerates routing=None (old frozen configs)."""
    result = _empty_choice()
    source = "default"
    if not isinstance(routing, dict):
        return {**result, "routing_source": "unset"}
    for field, value in (routing.get("default") or {}).items():
        if field in FIELDS and value is not None:
            result[field] = value
    for field, value in ((routing.get("roles") or {}).get(role) or {}).items():
        if field in FIELDS and value is not None:
            result[field] = value
            source = "role"
    if stage:
        for field, value in (((routing.get("stages") or {}).get(stage) or {}).get(role) or {}).items():
            if field in FIELDS and value is not None:
                result[field] = value
                source = "stage"
    result["routing_source"] = source
    return result


def _dump(routing):
    header = ("# Маршрутизация моделей по ролям агентов. null = выбор во время запуска.\n"
              "# Изменить: python3 scripts/routing.py set --role <role> --provider <p> --model <m> [--reasoning-effort low|medium|high] [--stage <stage>]\n")
    body = yaml.safe_dump(routing, allow_unicode=True, sort_keys=False, default_flow_style=None, width=100)
    return header + body


def set_choice(role, *, provider=None, model=None, reasoning_effort=None, stage=None, root=ROOT):
    """Update one role (or one stage override) and rewrite the YAML atomically."""
    if role not in ROLES:
        raise ValueError("unknown role: " + ", ".join(ROLES))
    if stage is not None and stage not in STAGES:
        raise ValueError("unknown stage: " + ", ".join(STAGES))
    routing = load_routing(root)
    target = routing["roles"][role] if stage is None else routing.setdefault("stages", {}).setdefault(stage, {}).setdefault(role, {})
    changes = {}
    for field, value in (("provider", provider), ("model", model), ("reasoning_effort", reasoning_effort)):
        if value is None:
            continue
        cleared = str(value).strip().lower() in CLEAR_VALUES
        if field == "reasoning_effort" and not cleared and value not in EFFORTS:
            raise ValueError("reasoning_effort must be one of: " + ", ".join(EFFORTS))
        changes[field] = None if cleared else str(value).strip()
    if not changes:
        raise ValueError("nothing to change: pass --provider, --model or --reasoning-effort")
    target.update(changes)
    if stage is not None:
        # keep stage overrides sparse: only fields explicitly set
        for field in list(target):
            if target[field] is None:
                del target[field]
        if not target:
            del routing["stages"][stage][role]
        if not routing["stages"][stage]:
            del routing["stages"][stage]
    problems = validate_routing(routing)
    if problems:
        raise ValueError("; ".join(problems))
    atomic_text(routing_path(root), _dump(routing))
    return {"role": role, "stage": stage, "changes": changes, "effective": resolve(routing, role, stage)}


def show(routing, role=None, stage=None):
    roles = [role] if role else list(ROLES)
    return {r: resolve(routing, r, stage or _default_stage(r)) for r in roles}


def _default_stage(role):
    if role == "discovery":
        return "light_validation"
    if role in ("skeptic", "judge"):
        return "deep_investigation"
    return "serious_validation"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["show", "validate", "set"])
    parser.add_argument("--role", choices=ROLES)
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    try:
        if args.command == "validate":
            routing = load_routing(args.root)
            errors = validate_routing(routing)
            output = {"valid": not errors, "errors": errors, "warnings": warnings_for(routing)}
        elif args.command == "show":
            routing = load_routing(args.root)
            output = {"path": str(routing_path(args.root)), "roles": show(routing, args.role, args.stage),
                      "providers": routing.get("providers"), "warnings": warnings_for(routing)}
        else:
            if not args.role:
                raise ValueError("set requires --role")
            output = set_choice(args.role, provider=args.provider, model=args.model,
                                reasoning_effort=args.reasoning_effort, stage=args.stage, root=args.root)
    except ValueError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1
    if args.json or args.command != "show":
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        for role, choice in output["roles"].items():
            print("%-13s provider=%-10s model=%-28s effort=%-7s (%s)" % (
                role, choice["provider"] or "—", choice["model"] or "—", choice["reasoning_effort"] or "—", choice["routing_source"]))
        for note in output["warnings"]:
            print("warning: " + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
