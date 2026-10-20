#!/usr/bin/env python3
"""Codex-native research control plane. Codex dispatches agents; Python enforces state."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.artifacts import atomic_text, seal_attempt, write_json, write_jsonl, merge_records
from scripts.report import write_report
from scripts.routing import load_routing, resolve as resolve_routing
from scripts.report_pdf import render_report_pdf, record_pdf_review
from scripts.validate import (ROLES, EVIDENCE_ROLES, REVIEW_ROLES, SCOPES, accepted_workers, current,
    digest, execution_health, independence_errors, qualification_errors, queue_prerequisite_errors, read_data, read_jsonl, schema_errors,
    validate_corpus, validate_input, validate_result, validate_run, within)

LIMITS = ("max_candidates", "max_assignments", "max_attempts_per_assignment", "max_search_queries_per_attempt", "max_active_agents")
PHASES = ("discovery", "evidence", "review", "decision", "complete")
DELIVERIES = ("direct", "relayed")
ALLOWED_ACTIONS = ("search_and_cite", "read_public_sources")
ACCEPTANCE_REQUIREMENTS = (
    "result conforms to agent-result.schema.json",
    "identity fields and input_hash match the frozen assignment",
    "every claim carries source_url, locator and access_date",
    "no buyer contact, purchase or campaign without explicit authorization",
)
def _now(): return datetime.now(timezone.utc).isoformat()

def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*",value):
        raise ValueError("IDs must use lowercase letters/digits separated by hyphens")
    return value

def run_path(root,run_id): return Path(root).resolve()/"runs"/identifier(run_id)

def _require_text(value,label):
    if not isinstance(value,str) or not value.strip(): raise ValueError(f"{label} must be nonempty text")

def _config(override=None):
    from scripts.founder import load_profile, validate_profile
    result = {"schema_version":"1.0", "limits":{}, "portfolio":read_data(ROOT/"config/portfolio.yaml"),
              "scoring":read_data(ROOT/"config/scoring.yaml"), "research_policy":read_data(ROOT/"config/research-policy.yaml"),
              "founder_profile":load_profile(), "model_routing":load_routing()}
    if override is not None:
        if not isinstance(override,dict) or set(override)-set(result): raise ValueError("unsupported run configuration field")
        result.update(override)
    profile_errors=validate_profile(result["founder_profile"])
    if profile_errors: raise ValueError("; ".join(profile_errors))
    limits=result["limits"]
    if not isinstance(limits,dict) or set(limits)-set(LIMITS): raise ValueError("unsupported run limit")
    for key,value in limits.items():
        if value is not None and (type(value) is not int or value<0): raise ValueError(f"{key} must be a nonnegative integer or null")
    defaults=result["research_policy"].get("assignment_defaults",{})
    default=defaults.get("search_queries_max")
    if "max_search_queries_per_attempt" not in limits: limits["max_search_queries_per_attempt"]=default
    for key in LIMITS: limits.setdefault(key,None)
    from scripts.score import _validate_config
    _validate_config(result["scoring"])
    return result

def _load(folder):
    manifest=read_data(folder/"manifest.json")
    errors=schema_errors(manifest,"run-manifest.schema.json","manifest")
    if errors: raise ValueError("; ".join(errors))
    config_path=within(folder,manifest["config_path"])
    if digest(config_path)!=manifest["config_hash"]: raise ValueError("frozen config hash mismatch")
    if digest(folder/"claims.jsonl")!=manifest["claims_hash"]: raise ValueError("canonical claims hash mismatch")
    return manifest,read_data(config_path)

@contextmanager
def _locked(folder):
    if not (folder/"manifest.json").exists(): raise ValueError("run does not exist")
    with (folder/".lock").open("a") as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        try: yield _load(folder)
        finally: fcntl.flock(stream,fcntl.LOCK_UN)

def _save(folder,manifest,event,details):
    manifest["revision"]+=1
    record={"revision":manifest["revision"],"at":datetime.now(timezone.utc).isoformat(),"event":event,"details":details}
    # Canonical writes are atomic; the manifest is the authoritative commit marker.
    write_json(folder/"manifest.json",manifest)
    with (folder/"events.jsonl").open("a") as stream: stream.write(json.dumps(record,allow_nan=False)+"\n")

def _running(manifest):
    if manifest["status"]!="running": raise ValueError("completed runs are immutable; create a new run")

def _assignment(manifest,identity):
    values=[a for a in manifest["assignments"] if a["id"]==identity]
    if len(values)!=1: raise ValueError(f"unknown or duplicated assignment {identity}")
    return values[0]

def _limit(config,key,count):
    cap=config["limits"].get(key)
    if cap is not None and count>cap: raise ValueError(f"{key} limit {cap} exceeded by {count}")

def _candidates(folder,manifest):
    records=[]
    for identity,expected in manifest["candidate_hashes"].items():
        path=folder/"candidates"/identity/"hypothesis.json"
        if digest(path)!=expected: raise ValueError(f"{identity}: canonical hypothesis hash mismatch")
        records.append(read_data(path))
    return records

def create_run(run_id,objective,scope,stopping_conditions,brief=None,config=None,capabilities=None,*,root=ROOT):
    _require_text(objective,"objective")
    if scope not in SCOPES: raise ValueError("scope must be one of: "+", ".join(SCOPES))
    _require_text(stopping_conditions,"stopping_conditions")
    if capabilities is None: capabilities={"recorded_at":_now(),"tools":None,"note":"not recorded"}
    if not isinstance(capabilities,dict): raise ValueError("capabilities must be a JSON object")
    capabilities.setdefault("recorded_at",_now());capabilities.setdefault("tools",None);capabilities.setdefault("note","")
    folder=run_path(root,run_id)
    effective=_config(config)
    if folder.exists(): raise ValueError("run already exists; use status or a new run ID")
    folder.mkdir(parents=True)
    seal_attempt(folder/"config.json",effective)
    if brief is not None:
        _require_text(brief,"brief path")
        source=Path(brief)
        if not source.is_file(): raise ValueError(f"brief file does not exist: {brief}")
        atomic_text(folder/"brief.md",source.read_text(encoding="utf-8"))
    write_jsonl(folder/"claims.jsonl",[])
    manifest={"schema_version":"1.0","run_id":run_id,"objective":objective,"scope":scope,
              "stopping_conditions":stopping_conditions,"brief":"brief.md" if brief is not None else None,
              "capabilities":capabilities,"phase":"discovery","status":"running","revision":0,
              "config_path":"config.json","config_hash":digest(folder/"config.json"),"assignments":[],"decisions":[],
              "candidate_hashes":{},"claims_hash":digest(folder/"claims.jsonl"),"quota_override":None,
              "report_path":None,"report_hash":None}
    write_json(folder/"manifest.json",manifest)
    return {"run_id":run_id,"run_dir":str(folder),"scope":scope,"status":"running","research_executed":False}

def queue(run_id,candidate_id,role,packet=None,*,retry=False,root=ROOT):
    identifier(candidate_id)
    if role not in ROLES: raise ValueError("unknown role")
    if packet is None: packet={}
    if not isinstance(packet,dict): raise ValueError("input packet must be a JSON object")
    folder=run_path(root,run_id)
    with _locked(folder) as (manifest,config):
        _running(manifest)
        if any(d["candidate_id"]==candidate_id for d in manifest["decisions"]): raise ValueError("candidate already has a terminal decision")
        identity=f"{candidate_id}-{role}"
        previous=next((a for a in manifest["assignments"] if a["id"]==identity),None)
        if previous:
            if not retry: raise ValueError("assignment already exists; explicit retry required")
            if current(previous)["status"]=="accepted": raise ValueError("accepted assignments are immutable")
            if current(previous)["status"] not in ("rejected", "cancelled"):
                raise ValueError("retry requires a rejected or cancelled current attempt; cancel active work first")
            number=previous["current_attempt"]+1
            # Runtime and contract/serialization repairs do not consume the research budget.
            # They preserve their immutable failed attempts, but only an actual research outcome
            # (or a user-directed stop) counts toward max_attempts_per_assignment.
            research_attempts=sum(
                a.get("failure_kind") not in ("execution", "validation")
                for a in previous["attempts"]
            )+1
            _limit(config,"max_attempts_per_assignment",research_attempts)
        else:
            number=1
            _limit(config,"max_assignments",len(manifest["assignments"])+1)
            _limit(config,"max_attempts_per_assignment",1)
        candidates={c["id"]:c for c in _candidates(folder,manifest)}
        if role=="discovery":
            if candidate_id in candidates: raise ValueError("candidate already discovered")
            reserved={a["candidate_id"] for a in manifest["assignments"] if a["role"]=="discovery"}
            _limit(config,"max_candidates",len(reserved|{candidate_id}))
        else:
            if candidate_id not in candidates: raise ValueError("non-discovery work requires an accepted candidate")
            problems=queue_prerequisite_errors(manifest,candidate_id,role)
            if problems: raise ValueError("; ".join(problems))
        phase="discovery" if role=="discovery" else "evidence" if role in EVIDENCE_ROLES else "review" if role in REVIEW_ROLES else "decision"
        prior=accepted_workers(manifest,candidate_id)
        claims=[c for c in read_jsonl(folder/"claims.jsonl") if c["candidate_id"]==candidate_id]
        packet=dict(packet)
        stage=packet.pop("stage", "light_validation" if role=="discovery" else "deep_investigation" if role in ("skeptic","judge") else "serious_validation")
        stages=config["research_policy"].get("funnel",{}).get("stages",{})
        if stage not in stages: raise ValueError("unknown or unconfigured funnel stage")
        stage_policy=stages[stage]
        if role not in stage_policy["allowed_roles"]: raise ValueError(f"{role} is not allowed in stage {stage}")
        routing=resolve_routing(config.get("model_routing"),role,stage)
        stage_policy={**stage_policy,**routing}
        effective_limits=dict(config["limits"])
        stage_cap=stage_policy.get("search_queries_max")
        cap=effective_limits.get("max_search_queries_per_attempt")
        if stage_cap is not None: effective_limits["max_search_queries_per_attempt"]=min(cap,stage_cap) if cap is not None else stage_cap
        allowed=packet.pop("allowed_actions",list(ALLOWED_ACTIONS))
        requirements=packet.pop("acceptance_requirements",list(ACCEPTANCE_REQUIREMENTS))
        for value,label in ((allowed,"allowed_actions"),(requirements,"acceptance_requirements")):
            if not isinstance(value,list) or not value or not all(isinstance(v,str) and v.strip() for v in value):
                raise ValueError(f"{label} must be a nonempty list of nonempty strings")
        payload={"schema_version":"1.0","run_id":run_id,"assignment_id":identity,"attempt":number,"candidate_id":candidate_id,
                 "role":role,"objective":manifest["objective"],"scope":manifest["scope"],"config_hash":manifest["config_hash"],
                 "limits":effective_limits,"stage":stage,"execution_policy":stage_policy,"allowed_actions":allowed,
                 "output_path":f"work/{identity}/attempt-{number}/result.json","acceptance_requirements":requirements,
                 "discoverer_id":prior.get("discovery"),"prior_workers":prior,
                 "packet":packet,"hypothesis":candidates.get(candidate_id),"claims":claims,
                 "founder_profile":config.get("founder_profile"),
                 "accepted_outputs":{a["role"]:{"worker_id":current(a)["worker_id"],"result_path":current(a)["result_path"],"result_hash":current(a)["result_hash"],"result":read_data(within(folder,current(a)["result_path"]))}
                    for a in manifest["assignments"] if a["candidate_id"]==candidate_id and current(a)["status"]=="accepted"}}
        problems=validate_input(payload)
        if problems: raise ValueError("input packet: "+"; ".join(problems))
        path=folder/"work"/identity/f"attempt-{number}"/"input.json"
        input_hash=seal_attempt(path,payload)
        attempt={"number":number,"status":"prepared","worker_id":None,"reused_context":None,"input_path":str(path.relative_to(folder)),
                 "input_hash":input_hash,"result_path":None,"result_hash":None,"delivery":None,"error":None,
                 "failure_kind":None}
        if previous:
            current(previous)["status"]="superseded"
            previous["attempts"].append(attempt);previous["current_attempt"]=number
        else: manifest["assignments"].append({"id":identity,"candidate_id":candidate_id,"role":role,"current_attempt":number,"attempts":[attempt]})
        manifest["phase"]=PHASES[max(PHASES.index(manifest["phase"]),PHASES.index(phase))]
        _save(folder,manifest,"queued",{"assignment_id":identity,"attempt":number})
        return {"assignment_id":identity,"attempt":number,"input_path":str(path),"input_hash":input_hash,"dispatch_required":True}

def bind(run_id,assignment_id,agent_id,*,reused_context=False,provider=None,model=None,root=ROOT):
    _require_text(agent_id,"actual runtime agent ID")
    if type(reused_context) is not bool: raise ValueError("reused_context must be boolean")
    for value,label in ((provider,"provider"),(model,"model")):
        if value is not None: _require_text(value,f"actual runtime {label}")
    folder=run_path(root,run_id)
    with _locked(folder) as (manifest,config):
        _running(manifest);assignment=_assignment(manifest,assignment_id);attempt=current(assignment)
        if (attempt["status"]=="bound" and attempt["worker_id"]==agent_id and attempt["reused_context"]==reused_context
            and attempt.get("provider")==provider and attempt.get("model")==model):
            return {"assignment_id":assignment_id,"agent_id":agent_id,"status":"bound","provider":provider,"model":model}
        if attempt["status"]!="prepared": raise ValueError("only prepared attempts may bind")
        if reused_context and assignment["role"] in ("skeptic","judge"):
            raise ValueError("skeptic and judge require fresh review context")
        errors=independence_errors(manifest,assignment["candidate_id"],assignment["role"],agent_id)
        if errors: raise ValueError("; ".join(errors))
        active={current(a)["worker_id"] for a in manifest["assignments"] if current(a)["status"]=="bound"}
        _limit(config,"max_active_agents",len(active|{agent_id}))
        requested=(read_data(within(folder,attempt["input_path"])).get("execution_policy") or {})
        attempt.update(status="bound",worker_id=agent_id,reused_context=reused_context,provider=provider,model=model)
        _save(folder,manifest,"bound",{"assignment_id":assignment_id,"agent_id":agent_id,"reused_context":reused_context,
                                       "provider":provider,"model":model,
                                       "requested_provider":requested.get("provider"),"requested_model":requested.get("model")})
        return {"assignment_id":assignment_id,"agent_id":agent_id,"status":"bound","provider":provider,"model":model,
                "requested_provider":requested.get("provider"),"requested_model":requested.get("model")}

def accept(run_id,assignment_id,result,delivery="direct",*,root=ROOT):
    if delivery not in DELIVERIES: raise ValueError("delivery must be one of: "+", ".join(DELIVERIES))
    folder=run_path(root,run_id)
    record=read_data(result) if isinstance(result,(str,Path)) else result
    with _locked(folder) as (manifest,config):
        _running(manifest);assignment=_assignment(manifest,assignment_id);attempt=current(assignment)
        if attempt["status"]=="accepted":
            if read_data(within(folder,attempt["result_path"]))!=record: raise ValueError("accepted result is immutable")
            if attempt["delivery"]!=delivery: raise ValueError("accepted result is immutable: delivery mismatch")
            return {"assignment_id":assignment_id,"status":"accepted","idempotent":True}
        if attempt["status"]!="bound": raise ValueError("result requires a bound actual agent")
        input_path=within(folder,attempt["input_path"])
        if digest(input_path)!=attempt["input_hash"]: raise ValueError("immutable input hash mismatch")
        claims=read_jsonl(folder/"claims.jsonl");em={c["id"]:c for c in claims}
        errors=validate_result(record,manifest,assignment,attempt,em)
        if assignment["role"] != "discovery" and record.get("role_output", {}).get("completed") is not True:
            errors.append("role_output.completed must be true before an assignment can be accepted")
        from scripts.validate import source_reference_errors, review_snapshot_errors
        if config.get("scoring", {}).get("version") != "2.2":
            errors.extend(source_reference_errors(folder, record, strict=True))
        errors.extend(review_snapshot_errors(read_data(input_path),manifest,assignment))
        if not errors:
            try:
                from scripts.search import logical_query_count
                _limit({"limits":read_data(input_path)["limits"]},"max_search_queries_per_attempt",logical_query_count(record["search_log"]))
                merged=merge_records([claims,record["claims"]])
                if assignment["role"]=="discovery":
                    candidate=record["candidate"]
                    if "founder_profile" in config:
                        from scripts.founder import candidate_errors
                        errors.extend(candidate_errors(candidate, config["founder_profile"], require_explicit=True))
                    # Structural/source checks first; portfolio coverage is assessed when evidence work begins.
                    check=validate_corpus([candidate],record["claims"],config["portfolio"])
                    errors.extend(e for e in check["errors"] if not e.startswith("portfolio:"))
                if assignment["role"]=="judge":
                    from scripts.score import score
                    computed=score(record["assessment"],config=config["scoring"],claims=merged)
            except (ValueError,TypeError) as exc: errors.append(str(exc))
        if errors:
            rejected=input_path.with_name("rejected.json")
            seal_attempt(rejected,record)
            attempt.update(status="rejected",error="; ".join(errors),failure_kind="validation",
                           result_path=str(rejected.relative_to(folder)),result_hash=digest(rejected))
            _save(folder,manifest,"rejected",{"assignment_id":assignment_id,"errors":errors})
            raise ValueError("; ".join(errors))
        target=input_path.with_name("result.json")
        # Workers may serialise the same result differently; compare content, then store the canonical form.
        if target.exists() and read_data(target)!=record: raise ValueError(f"immutable attempt differs: {target}")
        atomic_text(target,json.dumps(record,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+"\n")
        cid,role=assignment["candidate_id"],assignment["role"]
        canonical=folder/"candidates"/cid
        if role=="discovery":
            seal_attempt(canonical/"hypothesis.json",record["candidate"])
            manifest["candidate_hashes"][cid]=digest(canonical/"hypothesis.json")
        else:
            seal_attempt(canonical/"research"/(role+".json"),record["role_output"])
            if role=="judge":
                seal_attempt(canonical/"assessment.json",record["assessment"])
                write_json(canonical/"score.json",computed)
        write_jsonl(folder/"claims.jsonl",merged)
        manifest["claims_hash"]=digest(folder/"claims.jsonl")
        attempt.update(status="accepted",result_path=str(target.relative_to(folder)),result_hash=digest(target),delivery=delivery,error=None,
                       failure_kind=None)
        _save(folder,manifest,"accepted",{"assignment_id":assignment_id,"attempt":attempt["number"]})
        return {"assignment_id":assignment_id,"candidate_id":cid,"role":role,"status":"accepted"}

def decide(run_id,candidate_id,status,reason,author_id,claim_ids=None,duplicate_of=None,*,root=ROOT):
    _require_text(reason,"decision reason");_require_text(author_id,"decision author")
    if status not in ("rejected","deferred","duplicate","qualified"): raise ValueError("unknown decision status")
    folder=run_path(root,run_id)
    with _locked(folder) as (manifest,config):
        _running(manifest)
        if candidate_id not in manifest["candidate_hashes"]: raise ValueError("decision requires an accepted candidate")
        decision={"candidate_id":candidate_id,"status":status,"reason":reason,"author_id":author_id,"claim_ids":claim_ids or [],"duplicate_of":duplicate_of}
        existing=next((d for d in manifest["decisions"] if d["candidate_id"]==candidate_id),None)
        if existing:
            if existing==decision:return decision
            raise ValueError("terminal decision is immutable; create a new run to reconsider")
        active=[a for a in manifest["assignments"] if a["candidate_id"]==candidate_id and current(a)["status"] in ("prepared","bound")]
        if active: raise ValueError("resolve active assignments before deciding")
        em={c["id"]:c for c in read_jsonl(folder/"claims.jsonl")}
        if any(i not in em or em[i]["candidate_id"]!=candidate_id for i in decision["claim_ids"]): raise ValueError("decision claim reference invalid")
        if status=="rejected" and not decision["claim_ids"]: raise ValueError("rejection requires cited evidence; use deferred for missing evidence")
        if status=="duplicate" and (duplicate_of not in manifest["candidate_hashes"] or duplicate_of==candidate_id): raise ValueError("duplicate decision requires another accepted candidate")
        if status=="qualified":
            candidate=folder/"candidates"/candidate_id
            assessment=read_data(candidate/"assessment.json")
            from scripts.score import score
            computed=score(assessment,config=config["scoring"],claims=em)
            problems=qualification_errors(manifest,candidate_id,assessment,list(em.values()))
            if problems: raise ValueError("; ".join(problems))
            if not computed.get("commercial_qualification",{}).get("qualified",computed["eligible"]) or assessment["decision_context"]!="commercial_validation": raise ValueError("commercial validation gates not passed")
        manifest["decisions"].append(decision)
        manifest["phase"]="decision"
        _save(folder,manifest,"decided",decision)
        return decision

def cancel(run_id,assignment_id,reason,failure_kind="execution",*,root=ROOT):
    _require_text(reason,"cancellation reason");folder=run_path(root,run_id)
    if failure_kind not in ("execution", "research", "user"):
        raise ValueError("failure_kind must be execution, research or user")
    with _locked(folder) as (manifest,_):
        _running(manifest);attempt=current(_assignment(manifest,assignment_id))
        if attempt["status"] not in ("prepared","bound"):raise ValueError("only active assignments may cancel")
        attempt.update(status="cancelled",error=reason,failure_kind=failure_kind)
        _save(folder,manifest,"cancelled",{"assignment_id":assignment_id,"reason":reason,"failure_kind":failure_kind})
        return {"assignment_id":assignment_id,"status":"cancelled","runtime_interrupt_required":bool(attempt["worker_id"])}

def status(run_id,*,root=ROOT):
    folder=run_path(root,run_id);manifest,config=_load(folder)
    checked=validate_run(folder)
    from scripts.score import investigation_priority_from_claims
    claims=read_jsonl(folder/"claims.jsonl")
    priorities={}
    for cid in manifest["candidate_hashes"]:
        score_path=folder/"candidates"/cid/"score.json"
        scored=read_data(score_path).get("investigation_priority") if score_path.exists() else None
        priorities[cid]=scored or investigation_priority_from_claims(cid,claims,config["scoring"])
    return {"run_id":run_id,"scope":manifest["scope"],"phase":manifest["phase"],"status":manifest["status"],"revision":manifest["revision"],
            "stopping_conditions":manifest["stopping_conditions"],"quota_override":manifest["quota_override"],
            "limits":config["limits"],"report_written":(folder/"report.md").exists(),"candidate_count":len(manifest["candidate_hashes"]),
            "portfolio":checked["coverage"],"execution_health":execution_health(manifest,folder),
            "investigation_priorities":priorities,"decisions":manifest["decisions"],"assignments":[{"assignment_id":a["id"],"candidate_id":a["candidate_id"],"role":a["role"],
             "attempt":a["current_attempt"],"status":current(a)["status"],"agent_id":current(a)["worker_id"],"reused_context":current(a)["reused_context"]} for a in manifest["assignments"]]}

def next_work(run_id,*,root=ROOT):
    folder=run_path(root,run_id);manifest,config=_load(folder)
    active={current(a)["worker_id"] for a in manifest["assignments"] if current(a)["status"]=="bound"}
    cap=config["limits"]["max_active_agents"]
    ready=[{"assignment_id":a["id"],"candidate_id":a["candidate_id"],"role":a["role"],"attempt":a["current_attempt"],
            "input_path":str(within(folder,current(a)["input_path"])),"input_hash":current(a)["input_hash"]} for a in manifest["assignments"] if current(a)["status"]=="prepared"]
    if cap is not None:ready=ready[:max(0,cap-len(active))]
    return {"run_id":run_id,"scope":manifest["scope"],"ready":ready,"execution_health":execution_health(manifest,folder),
            "dispatch":"Root must call Codex collaboration tools, then bind the returned actual agent ID.",
            "reconcile":[{"assignment_id":a["id"],"agent_id":current(a)["worker_id"]} for a in manifest["assignments"] if current(a)["status"]=="bound"]}

def report(run_id,override_quota=None,*,root=ROOT):
    folder=run_path(root,run_id)
    with _locked(folder) as (manifest,config):
        _running(manifest)
        if override_quota is not None:
            _require_text(override_quota,"quota override reason")
            manifest["quota_override"]={"reason":override_quota,"author_id":"root","at":_now()}
            _save(folder,manifest,"quota-overridden",{"reason":override_quota})
        claims=read_jsonl(folder/"claims.jsonl")
        coverage=validate_run(folder)["coverage"]
        path=write_report(folder,manifest,config,claims,coverage)
        pdf=render_report_pdf(folder)
        return {"run_id":run_id,"report_path":str(path.relative_to(folder)),"report_hash":digest(path),
                "narrative_required":True,"quota_override":manifest["quota_override"],
                "pdf_path":"report.pdf","pdf_hash":pdf["pdf_sha256"],"visual_review_required":True}

def pdf_review(run_id,author_id,reason,*,root=ROOT):
    _require_text(author_id,"actual PDF reviewer ID")
    _require_text(reason,"visual inspection notes")
    folder=run_path(root,run_id)
    with _locked(folder) as (manifest,_):
        _running(manifest)
        receipt=record_pdf_review(folder,author_id,reason)
        _save(folder,manifest,"pdf-visually-reviewed",receipt)
        return receipt


def finalize(run_id,*,root=ROOT):
    folder=run_path(root,run_id)
    with _locked(folder) as (manifest,_):
        if manifest["status"]=="complete":return validate_run(folder,final=True)
        checked=validate_run(folder,final=True)
        if not checked["valid"]:raise ValueError("; ".join(checked["errors"]))
        manifest.update(status="complete",phase="complete",report_path="report.md",report_hash=digest(folder/"report.md"),
                        report_pdf_path="report.pdf",report_pdf_hash=digest(folder/"report.pdf"))
        _save(folder,manifest,"finalized",{"candidate_count":checked["candidate_count"],"qualified_count":checked["qualified_count"],"report_hash":manifest["report_hash"]})
        summary={**checked,"status":"complete","report_path":manifest["report_path"],"report_hash":manifest["report_hash"],
                 "primary_deliverable":"report.pdf","report_pdf_path":manifest["report_pdf_path"],"report_pdf_hash":manifest["report_pdf_hash"]}
        write_json(folder/"summary.json",summary)
        return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["create","create-run","queue","bind","accept","status","next","decide","cancel","validate","report","pdf-review","finalize"])
    parser.add_argument("--run",required=True);parser.add_argument("--objective");parser.add_argument("--config");parser.add_argument("--candidate");parser.add_argument("--role",choices=ROLES)
    parser.add_argument("--scope",choices=SCOPES);parser.add_argument("--stopping-condition");parser.add_argument("--brief");parser.add_argument("--capabilities")
    parser.add_argument("--delivery",choices=DELIVERIES,default="direct");parser.add_argument("--override-quota")
    parser.add_argument("--input");parser.add_argument("--retry",action="store_true");parser.add_argument("--assignment");parser.add_argument("--agent-id");parser.add_argument("--reused-context",action="store_true")
    parser.add_argument("--provider");parser.add_argument("--model")
    parser.add_argument("--result");parser.add_argument("--status");parser.add_argument("--reason");parser.add_argument("--failure-kind",choices=("execution","research","user"),default="execution");parser.add_argument("--author-id");parser.add_argument("--claim-id",action="append",default=[]);parser.add_argument("--duplicate-of")
    args=parser.parse_args()
    try:
        if args.command in ("create","create-run"):
            out=create_run(args.run,args.objective,args.scope,args.stopping_condition,brief=args.brief,
                           config=read_data(args.config) if args.config else None,
                           capabilities=read_data(args.capabilities) if args.capabilities else None)
        elif args.command=="queue":out=queue(args.run,args.candidate,args.role,read_data(args.input) if args.input else {},retry=args.retry)
        elif args.command=="bind":out=bind(args.run,args.assignment,args.agent_id,reused_context=args.reused_context,provider=args.provider,model=args.model)
        elif args.command=="accept":
            if not args.result:raise ValueError("accept requires --result")
            out=accept(args.run,args.assignment,args.result,args.delivery)
        elif args.command=="status":out=status(args.run)
        elif args.command=="next":out=next_work(args.run)
        elif args.command=="decide":out=decide(args.run,args.candidate,args.status,args.reason,args.author_id,args.claim_id,args.duplicate_of)
        elif args.command=="cancel":out=cancel(args.run,args.assignment,args.reason,args.failure_kind)
        elif args.command=="validate":out=validate_run(run_path(ROOT,args.run))
        elif args.command=="report":out=report(args.run,args.override_quota)
        elif args.command=="pdf-review":out=pdf_review(args.run,args.author_id,args.reason)
        else:out=finalize(args.run)
        print(json.dumps(out,indent=2,allow_nan=False));return 0 if out.get("valid",True) else 1
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({"valid":False,"errors":[str(exc)]},indent=2));return 1

if __name__=="__main__":raise SystemExit(main())
