#!/usr/bin/env python3
"""Offline contracts for arbitrary Codex research runs; no agent dispatch/network."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EVIDENCE_ROLES = ("demand", "money", "competition", "distribution")
REVIEW_ROLES = ("feasibility", "skeptic")
ROLES = ("discovery",) + EVIDENCE_ROLES + REVIEW_ROLES + ("judge",)
FACET_ROLES = EVIDENCE_ROLES + ("feasibility",)
GATE_ROLES = ("money", "distribution", "feasibility")
QUALIFICATION_ROLES = ("discovery",) + GATE_ROLES + ("skeptic", "judge")
SCOPES = ("targeted", "mining")
TECHNICAL_SEARCH_FAILURES = ("transport", "rate_limit", "http", "parse", "capability_unavailable")
QUOTAS = {"max_ai_agent_related_pct": ("ai_agent_related", "max"), "max_mcp_related_pct": ("mcp_related", "max"),
          "max_ecommerce_pct": ("ecommerce", "max"), "max_devops_cloud_pct": ("devops_cloud", "max"),
          "min_boring_industries_pct": ("boring_industry", "min"), "min_non_technical_buyers_pct": ("non_technical_buyer", "min"),
          "min_unfamiliar_to_founder_pct": ("unfamiliar_to_founder", "min")}

def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result

def parse_json(text):
    def invalid(value): raise ValueError(f"non-finite JSON number: {value}")
    return json.loads(text, object_pairs_hook=_unique, parse_constant=invalid)

def read_data(path):
    path = Path(path)
    if path.suffix in (".yaml", ".yml"):
        from scripts.score import load_yaml
        return load_yaml(path)
    return parse_json(path.read_text())

def read_jsonl(path):
    return [parse_json(line) for line in Path(path).read_text().splitlines() if line.strip()]

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def schema_errors(record, name, label):
    validator = Draft202012Validator(read_data(ROOT / "schemas" / name), format_checker=FormatChecker())
    errors = [f"{label}/{'/'.join(map(str,e.absolute_path)) or '$'}: {e.message}" for e in validator.iter_errors(record)]
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value): errors.append(f"{label}: non-finite number")
        elif isinstance(value, dict):
            for v in value.values(): finite(v)
        elif isinstance(value, list):
            for v in value: finite(v)
    finite(record)
    return errors

def index_records(records, schema, label, errors):
    result = {}
    if not isinstance(records, list):
        errors.append(f"{label}: expected list"); return result
    for index, record in enumerate(records):
        problems = schema_errors(record, schema, f"{label}[{index}]")
        errors.extend(problems)
        if problems: continue
        if record["id"] in result: errors.append(f"{label}: duplicate id {record['id']}")
        result[record["id"]] = record
    return result

def check_refs(ids, candidate_id, claims, label, errors):
    for identity in ids:
        if identity not in claims: errors.append(f"{label}: missing claim {identity}")
        elif claims[identity]["candidate_id"] != candidate_id: errors.append(f"{label}: claim {identity} belongs to another candidate")

def check_portfolio(candidates, config, errors, warnings, *, enforce=False):
    n = len(candidates)
    coverage = {"denominator": n, "scope": "unique_mining_corpus", "mode": "enforced" if enforce else "not_assessed",
                "status": "not_assessed", "industries": sorted({c["industry"] for c in candidates}), "quotas": {}, "fully_assessed": False}
    if not enforce:
        warnings.append("Portfolio quotas NOT ASSESSED: run scope is targeted." if n else "No candidates: portfolio NOT ASSESSED.")
        return coverage
    if not n:
        warnings.append("No candidates: portfolio NOT ASSESSED."); return coverage
    policy = config.get("enforcement", {})
    for key, expected in {"scope":"unique_mining_corpus", "rounding_maximum":"floor", "rounding_minimum":"ceil", "overlapping_flags":"count_each_independently", "unknown_classification_policy":"reject"}.items():
        if policy.get(key) != expected: errors.append(f"portfolio: {key} must be {expected}")
    industries = {c["industry"] for c in candidates}
    minimum = policy.get("minimum_industries", 1)
    if type(minimum) is not int or minimum < 1: errors.append("portfolio: invalid minimum_industries")
    elif len(industries) < minimum: coverage.setdefault("gaps", []).append(f"portfolio: {len(industries)} industries < minimum {minimum}")
    for industry in industries:
        if industry not in config.get("preferred_industry_buckets", []):
            warnings.append(f"portfolio: industry {industry} is outside preferred buckets; keep the explicit run-scope label")
    for key, (flag, direction) in QUOTAS.items():
        percentage = config.get("portfolio_rules", {}).get(key)
        if type(percentage) not in (int, float) or not math.isfinite(percentage) or not 0 <= percentage <= 100:
            errors.append(f"portfolio: invalid percentage {key}"); continue
        threshold = (math.floor if direction == "max" else math.ceil)(n * percentage / 100)
        observed = sum(c["category_flags"][flag] is True for c in candidates)
        unknown = sum(c["category_flags"][flag] is None for c in candidates)
        entry = {"observed": observed, "unknown": unknown, "threshold": threshold, "direction": direction}
        coverage["quotas"][flag] = entry
        if unknown and flag == "unfamiliar_to_founder" and policy.get("unfamiliar_to_founder_policy") == "enforce_when_known":
            entry["status"] = "not_assessed"; warnings.append(f"unfamiliar_to_founder quota NOT ASSESSED: {unknown}/{n} unknown; do not report full portfolio as passed."); continue
        if unknown:
            entry["status"] = "invalid"; continue
        passed = observed <= threshold if direction == "max" else observed >= threshold
        entry["status"] = "pass" if passed else "fail"
    coverage["fully_assessed"] = len(coverage["quotas"]) == len(QUOTAS) and all(q["status"] == "pass" for q in coverage["quotas"].values())
    failing = [flag for flag,q in coverage["quotas"].items() if q["status"] in ("fail","invalid")]
    coverage["status"] = "pass" if not failing and not coverage.get("gaps") else "fail"
    return coverage

def quota_errors(coverage):
    if coverage.get("mode") != "enforced": return []
    errors = list(coverage.get("gaps", []))
    for flag, entry in sorted(coverage.get("quotas", {}).items()):
        if entry["status"] == "fail":
            errors.append(f"portfolio: {flag} count {entry['observed']} violates {entry['direction']} {entry['threshold']} at N={coverage['denominator']}")
        elif entry["status"] == "invalid":
            errors.append(f"portfolio: unknown classifications for {flag}")
    return errors

def validate_corpus(candidates, claims, portfolio, *, enforce_portfolio=False):
    errors, warnings = [], []
    cm = index_records(candidates, "niche-atom.schema.json", "candidates", errors)
    em = index_records(claims, "claim.schema.json", "claims", errors)
    for claim in em.values():
        if claim["candidate_id"] not in cm: errors.append(f"claim {claim['id']}: unknown candidate")
    seen = {}
    for c in cm.values():
        check_refs(c["claim_ids"], c["id"], em, c["id"], errors)
        if not any(em[i]["quality"] in ("A","B","C") and em[i]["relationship"] in ("supports","qualifies") for i in c["claim_ids"] if i in em):
            errors.append(f"{c['id']}: needs nonspeculative workflow evidence")
        key = tuple(" ".join(c[k].casefold().split()) for k in ("who", "buyer", "trigger", "job_to_be_done"))
        if key in seen: errors.append(f"{c['id']}: exact normalized workflow duplicate of {seen[key]}")
        seen[key] = c["id"]
    coverage = check_portfolio(list(cm.values()), portfolio, errors, warnings, enforce=enforce_portfolio)
    failures = quota_errors(coverage) if enforce_portfolio else []
    return {"valid": not errors, "errors": errors, "warnings": warnings, "coverage": coverage, "quota_errors": failures}

def within(root, relative):
    root = Path(root).resolve(); path = Path(relative)
    if path.is_absolute(): raise ValueError(f"path must be run-relative: {relative}")
    result = (root / path).resolve()
    if not result.is_relative_to(root): raise ValueError(f"path escapes run: {relative}")
    return result

def current(assignment):
    matches = [a for a in assignment["attempts"] if a["number"] == assignment["current_attempt"]]
    if len(matches) != 1: raise ValueError(f"assignment {assignment['id']}: invalid current attempt")
    return matches[0]

def execution_health(manifest, run_dir=None):
    """Separate control-plane failures from legitimate no-data research outcomes."""
    current_attempts = [(assignment, current(assignment)) for assignment in manifest.get("assignments", [])]
    active = [assignment["id"] for assignment, attempt in current_attempts
              if attempt["status"] in ("prepared", "bound")]
    blockers = [{"assignment_id": assignment["id"], "candidate_id": assignment["candidate_id"],
                 "role": assignment["role"], "status": attempt["status"],
                 "failure_kind": attempt.get("failure_kind") or "execution",
                 "reason": attempt.get("error")}
                for assignment, attempt in current_attempts if attempt["status"] in ("rejected", "cancelled")]
    accepted = sum(attempt["status"] == "accepted" for _, attempt in current_attempts)
    failures = {kind: 0 for kind in TECHNICAL_SEARCH_FAILURES}
    no_data = 0
    if run_dir is not None:
        folder = Path(run_dir)
        for _, attempt in current_attempts:
            if attempt["status"] != "accepted" or not attempt.get("result_path"):
                continue
            try:
                record = read_data(within(folder, attempt["result_path"]))
            except (OSError, ValueError, TypeError, KeyError):
                continue
            for entry in record.get("search_log", []):
                status = entry.get("status") or entry.get("retrieval_status")
                kind = entry.get("failure_kind")
                if status == "no_data":
                    no_data += 1
                if kind in failures:
                    failures[kind] += 1
    state = "blocked" if blockers else "running" if active else "healthy"
    return {"state": state, "accepted_assignments": accepted, "active_assignments": active,
            "blocking_assignments": blockers, "technical_search_failures": failures,
            "no_data_queries": no_data}

def accepted_workers(manifest, candidate_id):
    return {a["role"]: current(a)["worker_id"] for a in manifest["assignments"] if a["candidate_id"] == candidate_id and current(a)["status"] == "accepted"}

def independence_errors(manifest, candidate_id, role, worker_id):
    prior = accepted_workers(manifest, candidate_id)
    errors = []
    if role != "discovery" and worker_id == prior.get("discovery"): errors.append("discovery worker cannot self-validate")
    audited = FACET_ROLES if role == "skeptic" else tuple(r for r in ROLES if r != "judge") if role == "judge" else ()
    if any(prior.get(r) == worker_id for r in audited): errors.append(f"{role} worker must be independent of audited authors")
    return errors

def queue_prerequisite_errors(manifest, candidate_id, role):
    if role == "discovery": return []
    prior = accepted_workers(manifest, candidate_id)
    errors = []
    if "discovery" not in prior: errors.append("non-discovery work requires an accepted discovery result")
    if role == "skeptic" and not (set(FACET_ROLES) & set(prior)): errors.append("skeptic requires at least one accepted facet result to audit")
    if role == "judge" and "skeptic" not in prior: errors.append("judge requires the accepted skeptic review")
    return errors

def cited_claim_ids(assessment):
    cited = set(assessment.get("unresolved_fatal_contradictions", []))
    for section in [assessment.get("payment", {}), assessment.get("distribution", {}), assessment.get("feasibility", {}),
                    *assessment.get("ratings", {}).values()]:
        cited.update(section.get("claim_ids", []))
    return cited

def qualification_errors(manifest, candidate_id, assessment, claims):
    prior = accepted_workers(manifest, candidate_id)
    errors = [f"qualification requires an accepted {role} result" for role in QUALIFICATION_ROLES if role not in prior]
    evidence = {c["id"]: c for c in claims}
    for identifier in sorted(cited_claim_ids(assessment)):
        claim = evidence.get(identifier)
        if claim is None:
            errors.append(f"assessment cites unknown claim {identifier}")
        elif claim["candidate_id"] != candidate_id:
            errors.append(f"assessment cites claim {identifier} from another candidate")
    return errors

def source_reference_errors(run_dir, record, *, strict=False):
    """Validate every supplied canonical raw reference against run-scoped bytes."""
    errors = []
    folder = Path(run_dir)
    references = []
    for claim in record.get("claims", []):
        if strict and claim.get("source_type") != "synthetic_fixture" and not claim.get("source_id"):
            errors.append(f"claim {claim.get('id', '<unknown>')}: source_id is required for new evidence")
        if claim.get("source_id"):
            references.append((claim["source_id"], True))
        for metric in claim.get("metrics", []):
            if metric.get("raw_response_ref"):
                references.append((metric["raw_response_ref"], metric.get("collection_status") == "collected"))
    for search in record.get("search_log", []):
        status = search.get("status")
        if strict and status != "not_collected" and not search.get("source_id"):
            errors.append(f"search {search.get('query', '<unknown>')}: source_id is required for a provider attempt")
        if search.get("source_id"):
            references.append((search["source_id"], status in ("results", "no_data")))
        if search.get("raw_response_ref"):
            references.append((search["raw_response_ref"], search.get("status") in ("results", "no_data")))
    for ref, require_body in references:
        try:
            if not isinstance(ref, str): raise ValueError("source reference must be text")
            if ref.startswith("src-") and "/" not in ref:
                meta_path = within(folder, f"sources/{ref}.meta.json")
            else:
                path = within(folder, ref)
                if not path.is_relative_to((folder / "sources").resolve()):
                    raise ValueError("raw source reference must be inside sources/")
                meta_path = path if path.name.endswith(".meta.json") else path.with_suffix(".meta.json")
            meta = read_data(meta_path)
            if meta.get("run_id") != folder.name or meta_path.name != str(meta.get("source_id")) + ".meta.json":
                raise ValueError("source identity does not match its run or sidecar")
            archive = meta.get("archive_path")
            if not archive:
                if require_body: raise ValueError("collected evidence requires archived source bytes")
                continue
            raw = within(folder, archive)
            if not raw.is_relative_to((folder / "sources").resolve()):
                raise ValueError("source archive must be inside sources/")
            if digest(raw) != meta.get("sha256") or raw.stat().st_size != meta.get("byte_length"):
                raise ValueError("source bytes SHA/length mismatch")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(f"raw source {ref}: {exc}")
    return errors


def review_snapshot_errors(packet, manifest, assignment):
    # Old immutable packets predate snapshot provenance. New reviews must see all
    # accepted upstream findings and cannot silently review a later changed record.
    if assignment["role"] not in ("skeptic", "judge") or "accepted_outputs" not in packet:
        return []
    roles = FACET_ROLES + ("discovery",) if assignment["role"] == "skeptic" else FACET_ROLES + ("discovery", "skeptic")
    expected = {a["role"]: current(a)["result_hash"] for a in manifest["assignments"]
                if a["candidate_id"] == assignment["candidate_id"] and a["role"] in roles and current(a)["status"] == "accepted"}
    frozen = {role: output["result_hash"] for role, output in packet["accepted_outputs"].items() if role in roles}
    return [] if frozen == expected else ["review snapshot is stale; queue a new attempt after freezing upstream findings"]


def validate_input(packet):
    return schema_errors(packet, "assignment-input.schema.json", "input")

def validate_result(record, manifest, assignment, attempt, existing_claims):
    errors = schema_errors(record, "agent-result.schema.json", "result")
    if errors: return errors
    cid, role = assignment["candidate_id"], assignment["role"]
    for key, expected in {"run_id":manifest["run_id"], "assignment_id":assignment["id"], "attempt":attempt["number"], "role":role, "worker_id":attempt["worker_id"], "input_hash":attempt["input_hash"]}.items():
        if record[key] != expected: errors.append(f"result: {key} identity mismatch")
    new_claims = index_records(record["claims"], "claim.schema.json", "result claims", errors)
    merged = dict(existing_claims)
    for identity, claim in new_claims.items():
        if claim["candidate_id"] != cid or claim["author_id"] != attempt["worker_id"]: errors.append(f"claim {identity}: candidate/author mismatch")
        if identity in merged and merged[identity] != claim: errors.append(f"claim {identity}: conflicting immutable evidence")
        merged[identity] = claim
    errors.extend(independence_errors(manifest, cid, role, attempt["worker_id"]))
    if role == "discovery":
        c = record["candidate"]
        problems = schema_errors(c, "niche-atom.schema.json", "candidate"); errors.extend(problems)
        if not problems:
            if c["id"] != cid or c["run_id"] != manifest["run_id"] or c["discoverer_id"] != attempt["worker_id"]: errors.append("candidate: identity mismatch")
            check_refs(c["claim_ids"], cid, merged, cid, errors)
    else:
        output = record["role_output"]
        problems = schema_errors(output, "role-output.schema.json", "role output"); errors.extend(problems)
        if not problems:
            expected = {"candidate_id":cid,"role":role,"worker_id":attempt["worker_id"],"discoverer_id":accepted_workers(manifest,cid).get("discovery"),"input_hash":attempt["input_hash"],"completed":True}
            for key,value in expected.items():
                if output[key] != value: errors.append(f"role output: {key} mismatch")
            check_refs(output["claim_ids"], cid, merged, role, errors)
        if role == "judge":
            from scripts.score import score
            assessment = record["assessment"]
            try:
                if assessment.get("candidate_id") != cid or assessment.get("author_id") != attempt["worker_id"] or assessment.get("discoverer_id") != accepted_workers(manifest,cid).get("discovery"): errors.append("assessment: identity mismatch")
                # Full scoring happens with the frozen run policy during ingestion.
                errors.extend(schema_errors(assessment,"assessment.schema.json","assessment"))
            except (ValueError,TypeError) as exc: errors.append(str(exc))
    return errors

def validate_run(run_dir, final=False):
    run_dir = Path(run_dir).resolve(); errors, warnings = [], []
    try:
        manifest = read_data(run_dir / "manifest.json")
        errors.extend(schema_errors(manifest,"run-manifest.schema.json","manifest"))
        if errors: return {"valid":False,"errors":errors,"warnings":warnings}
        config_path = within(run_dir,manifest["config_path"])
        if digest(config_path) != manifest["config_hash"]: errors.append("frozen config hash mismatch")
        config = read_data(config_path)
        if "founder_profile" in config:
            from scripts.founder import validate_profile
            errors.extend(validate_profile(config["founder_profile"]))
        claims = read_jsonl(run_dir / "claims.jsonl")
        if digest(run_dir / "claims.jsonl") != manifest["claims_hash"]: errors.append("canonical claims hash mismatch")
        cm = {identity:read_data(run_dir/"candidates"/identity/"hypothesis.json") for identity in manifest["candidate_hashes"]}
        for identity,expected in manifest["candidate_hashes"].items():
            if digest(run_dir/"candidates"/identity/"hypothesis.json") != expected: errors.append(f"{identity}: canonical hypothesis hash mismatch")
        corpus = validate_corpus(list(cm.values()),claims,config["portfolio"],enforce_portfolio=manifest["scope"]=="mining")
        errors.extend(corpus["errors"])
        if "founder_profile" in config:
            from scripts.founder import audit, candidate_errors
            corpus["coverage"]["founder_familiarity"] = audit(list(cm.values()), config["founder_profile"])
            for candidate in cm.values():
                errors.extend(f"{candidate['id']}: {error}" for error in
                              candidate_errors(candidate, config["founder_profile"], require_explicit=True))
        warnings.extend(corpus["warnings"])
        if corpus["quota_errors"]:
            if final and manifest.get("quota_override") is None: errors.extend(corpus["quota_errors"])
            else: warnings.extend(corpus["quota_errors"])
        em = {c["id"]:c for c in claims}
        ids, paths, seen_claims = set(), set(), set()
        for assignment in manifest["assignments"]:
            if assignment["id"] in ids: errors.append("duplicate assignment id")
            ids.add(assignment["id"])
            now = current(assignment)
            if final and now["status"] in ("prepared","bound"): errors.append(f"{assignment['id']}: active assignment")
            numbers = set()
            for attempt in assignment["attempts"]:
                if attempt["number"] in numbers: errors.append("duplicate attempt number")
                numbers.add(attempt["number"])
                path = within(run_dir,attempt["input_path"])
                if str(path) in paths: errors.append("shared assignment input path")
                paths.add(str(path))
                errors.extend(validate_input(read_data(path)))
                if digest(path) != attempt["input_hash"]: errors.append(f"{assignment['id']}: immutable input hash mismatch")
                if attempt["status"] == "accepted":
                    output = within(run_dir,attempt["result_path"])
                    if digest(output) != attempt["result_hash"]: errors.append(f"{assignment['id']}: immutable output hash mismatch")
                    record = read_data(output)
                    errors.extend(validate_result(record,manifest,assignment,attempt,em))
                    if config.get("scoring", {}).get("version") != "2.2":
                        errors.extend(source_reference_errors(run_dir, record, strict=True))
                    errors.extend(review_snapshot_errors(read_data(path),manifest,assignment))
                    seen_claims.update(c["id"] for c in record["claims"])
                    cid,role = assignment["candidate_id"],assignment["role"]
                    canonical = run_dir/"candidates"/cid
                    if role == "discovery":
                        if cm.get(cid) != record["candidate"]: errors.append(f"{cid}: canonical discovery mismatch")
                    else:
                        if read_data(canonical/"research"/(role+".json")) != record["role_output"]: errors.append(f"{cid}/{role}: canonical role mismatch")
                    if role == "judge":
                        from scripts.score import score
                        assessment = read_data(canonical/"assessment.json")
                        if assessment != record["assessment"]: errors.append(f"{cid}: assessment mismatch")
                        if read_data(canonical/"score.json") != score(assessment,config=config["scoring"],claims=em): errors.append(f"{cid}: deterministic score mismatch")
        health = execution_health(manifest, run_dir)
        if final or manifest["status"] == "complete":
            for blocker in health["blocking_assignments"]:
                errors.append(f"incomplete run: {blocker['assignment_id']} ended {blocker['status']} "
                              f"({blocker['failure_kind']}); recover it or start a follow-up run")
        if seen_claims != set(em): errors.append("canonical claims are not exactly attributable to accepted results")
        decisions = {}
        for d in manifest["decisions"]:
            cid = d["candidate_id"]
            if cid in decisions: errors.append(f"{cid}: duplicate decision")
            decisions[cid] = d
            if cid not in cm: errors.append(f"{cid}: decision for missing candidate")
            check_refs(d["claim_ids"],cid,em,"decision",errors)
            if d["status"] == "qualified":
                score_record = read_data(run_dir/"candidates"/cid/"score.json")
                assessment = read_data(run_dir/"candidates"/cid/"assessment.json")
                if not score_record.get("commercial_qualification",{}).get("qualified",score_record["eligible"]) or assessment.get("decision_context") != "commercial_validation": errors.append(f"{cid}: qualified decision lacks commercial validation gates")
                missing = [role for role in QUALIFICATION_ROLES if role not in accepted_workers(manifest,cid)]
                if missing: errors.append(f"{cid}: qualified candidate missing roles: {', '.join(missing)}")
            if d["status"] == "duplicate" and (d["duplicate_of"] not in cm or d["duplicate_of"] == cid): errors.append(f"{cid}: invalid duplicate target")
            if d["status"] == "rejected" and not d["claim_ids"]: errors.append(f"{cid}: rejection requires cited evidence; missing evidence is deferral")
        if final or manifest["status"] == "complete":
            if set(decisions) != set(cm): errors.append("every accepted candidate requires a terminal decision")
        if manifest["status"] == "complete" and manifest["phase"] != "complete": errors.append("completed run has wrong phase")
        if manifest.get("report_path"):
            report_file = within(run_dir, manifest["report_path"])
            if not report_file.exists(): errors.append("report file is missing")
            elif manifest.get("report_hash") and digest(report_file) != manifest["report_hash"]: errors.append("report hash mismatch")
        current_policy = config.get("scoring", {}).get("version") != "2.2"
        if (final or manifest["status"] == "complete") and current_policy:
            from scripts.report_pdf import validate_report_pdf
            errors.extend(validate_report_pdf(run_dir))
            if manifest.get("report_pdf_hash") and digest(run_dir/"report.pdf") != manifest["report_pdf_hash"]:
                errors.append("finalized report.pdf hash mismatch")
        if final and current_policy:
            from scripts.report import validate_report
            errors.extend(validate_report(run_dir,manifest,claims,config,corpus["coverage"]))
        return {"valid":not errors,"errors":errors,"warnings":warnings,"coverage":corpus["coverage"],
                "execution_health":health,"candidate_count":len(cm),
                "qualified_count":sum(d["status"]=="qualified" for d in decisions.values())}
    except (OSError,ValueError,TypeError,KeyError) as exc:
        errors.append(str(exc)); return {"valid":False,"errors":errors,"warnings":warnings}

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("command",choices=["corpus","run"])
    p.add_argument("--candidates");p.add_argument("--claims");p.add_argument("--portfolio",default=str(ROOT/"config/portfolio.yaml"));p.add_argument("--run-dir");p.add_argument("--final",action="store_true")
    a=p.parse_args()
    try:
        if a.command=="run":
            if not a.run_dir: raise ValueError("run requires --run-dir")
            result=validate_run(a.run_dir,a.final)
        else:
            if not a.candidates or not a.claims: raise ValueError("corpus requires --candidates and --claims")
            result=validate_corpus(read_jsonl(a.candidates),read_jsonl(a.claims),read_data(a.portfolio))
    except (OSError,ValueError,TypeError) as exc: result={"valid":False,"errors":[str(exc)],"warnings":[]}
    print(json.dumps(result,indent=2,allow_nan=False));return 0 if result["valid"] else 1

if __name__=="__main__":raise SystemExit(main())
