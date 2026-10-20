"""Synthetic fixtures for offline contract tests. Never market research."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import app, founder  # noqa: E402

# Synthetic runs freeze the public example profile, never the owner's private one, so the suite
# runs the same in a public clone that has no config/founder-profile.yaml.
founder.PROFILE = founder.EXAMPLE

WORKERS = {"discovery": "agent-scout", "money": "agent-money", "distribution": "agent-distribution",
           "feasibility": "agent-feasibility", "skeptic": "agent-skeptic", "judge": "agent-judge"}
PAID_ROLES = ("money",)


def claim(identity, candidate_id, author_id, *, payment=False, quality="A", relationship="supports"):
    record = {"id": identity, "candidate_id": candidate_id, "claim": "Synthetic fixture observation",
              "source_url": "https://example.com/synthetic", "source_type": "synthetic_fixture",
              "access_date": "2026-09-11", "source_excerpt": "Synthetic fixture excerpt.",
              "locator": "fixture paragraph", "quality": quality, "relationship": relationship,
              "independence_group": f"group-{author_id}", "notes": "Synthetic test only.", "author_id": author_id}
    if payment:
        record.update(payment_scope="exact", paid_use_explicit=True, buyer_group="buyer-1")
    return record


def candidate(candidate_id, run_id, discoverer_id, industry="accounting_finance_ops", claim_ids=None):
    return {"schema_version": "1.0", "id": candidate_id, "run_id": run_id, "discoverer_id": discoverer_id,
            "industry": industry, "who": "Synthetic buyer group", "buyer": "Operations owner",
            "trigger": "Monthly close", "pain": "Manual reconciliation", "current_workaround": "Spreadsheet",
            "existing_spend": None, "job_to_be_done": "Close the books", "measurable_result": "Hours to close",
            "distribution_channel": None, "frequency": None,
            "founder_familiarity": "outside_confirmed_familiarity",
            "research_gaps": ["spend, channel and frequency unverified"],
            "category_flags": {"ai_agent_related": False, "mcp_related": False, "ecommerce": False, "devops_cloud": False,
                               "boring_industry": True, "non_technical_buyer": True, "unfamiliar_to_founder": None},
            "classification_rationale": "Synthetic fixture classification.", "claim_ids": claim_ids or [f"{candidate_id}-discovery-c1"]}


def role_output(candidate_id, role, worker_id, discoverer_id, input_hash, claim_ids):
    return {"schema_version": "1.0", "candidate_id": candidate_id, "role": role, "worker_id": worker_id,
            "discoverer_id": discoverer_id, "input_hash": input_hash, "claim_ids": claim_ids,
            "findings": ["Synthetic facet finding."],
            "counterevidence": ["Bounded synthetic search found no contrary source; this does not establish absence."],
            "research_gaps": ["bounded synthetic search only"], "completed": True}


def assessment(candidate_id, worker_id, discoverer_id, discovery_claim, paid_claim, distribution_claim, feasibility_claim,
               context="commercial_validation"):
    rating = {"value": 4, "claim_ids": [discovery_claim], "rationale": "Synthetic evidence supports this rating."}
    return {"schema_version": "2.0", "candidate_id": candidate_id, "author_id": worker_id, "discoverer_id": discoverer_id,
            "decision_context": context,
            "payment": {"level": 4, "claim_ids": [paid_claim], "rationale": "Synthetic exact paid use."},
            "distribution": {"status": "pass", "claim_ids": [distribution_claim], "rationale": "Synthetic access."},
            "feasibility": {"status": "pass", "claim_ids": [feasibility_claim], "rationale": "Synthetic feasibility."},
            "ratings": {name: dict(rating) for name in ("impact_per_event", "recurring_burden", "monetization_economics",
                                                        "accessible_distribution", "switching_opportunity")},
            "critical_unknowns": [], "unresolved_fatal_contradictions": []}


def _dispatch(root, run_id, candidate_id, role, worker, packet=None):
    queued = app.queue(run_id, candidate_id, role, packet or {"task": "synthetic"}, root=root)
    app.bind(run_id, queued["assignment_id"], worker, root=root)
    return queued


def start_run(root, run_id="synthetic-run", scope="targeted", candidate_id="synthetic-cand", industry="accounting_finance_ops"):
    app.create_run(run_id, "Synthetic contract check; no market research", scope, "stop after the synthetic check", root=root)
    discover(root, run_id, candidate_id, industry)
    return candidate_id


def discover(root, run_id, candidate_id, industry="accounting_finance_ops"):
    claimed = claim(f"{candidate_id}-discovery-c1", candidate_id, WORKERS["discovery"])
    queued = _dispatch(root, run_id, candidate_id, "discovery", WORKERS["discovery"])
    app.accept(run_id, queued["assignment_id"],
               {"schema_version": "1.0", "run_id": run_id, "assignment_id": queued["assignment_id"], "attempt": 1,
                "role": "discovery", "worker_id": WORKERS["discovery"], "input_hash": queued["input_hash"],
                "claims": [claimed], "search_log": [],
                "candidate": candidate(candidate_id, run_id, WORKERS["discovery"], industry, [claimed["id"]])}, root=root)
    return claimed


def finish_facets(root, run_id="synthetic-run", candidate_id="synthetic-cand", roles=("money", "distribution", "feasibility", "skeptic")):
    for role in roles:
        claimed = claim(f"{candidate_id}-{role}-c1", candidate_id, WORKERS[role], payment=role in PAID_ROLES)
        queued = _dispatch(root, run_id, candidate_id, role, WORKERS[role])
        app.accept(run_id, queued["assignment_id"],
                   {"schema_version": "1.0", "run_id": run_id, "assignment_id": queued["assignment_id"], "attempt": 1,
                    "role": role, "worker_id": WORKERS[role], "input_hash": queued["input_hash"],
                    "claims": [claimed], "search_log": [],
                    "role_output": role_output(candidate_id, role, WORKERS[role], WORKERS["discovery"], queued["input_hash"], [claimed["id"]])},
                   root=root)


def finish_judge(root, run_id="synthetic-run", candidate_id="synthetic-cand", context="commercial_validation"):
    record = assessment(candidate_id, WORKERS["judge"], WORKERS["discovery"], f"{candidate_id}-discovery-c1",
                        f"{candidate_id}-money-c1", f"{candidate_id}-distribution-c1", f"{candidate_id}-feasibility-c1", context)
    queued = _dispatch(root, run_id, candidate_id, "judge", WORKERS["judge"])
    app.accept(run_id, queued["assignment_id"],
               {"schema_version": "1.0", "run_id": run_id, "assignment_id": queued["assignment_id"], "attempt": 1,
                "role": "judge", "worker_id": WORKERS["judge"], "input_hash": queued["input_hash"], "claims": [],
                "search_log": [],
                "role_output": role_output(candidate_id, "judge", WORKERS["judge"], WORKERS["discovery"], queued["input_hash"], []),
                "assessment": record}, root=root)
    return record


def write_narrative(folder, text="Итог: синтетическая проверка полного цикла успешно завершена."):
    path = folder / "report.md"
    # Synthetic test attestation only: production finalize never acknowledges its own PDF.
    content = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if "Заполните" not in line)
    path.write_text(content + "\n" + text + "\n", encoding="utf-8")
    from scripts.report_pdf import render_report_pdf, record_pdf_review
    render_report_pdf(folder)
    record_pdf_review(folder, "synthetic-test-reviewer", "Синтетическая квитанция для проверки контракта, не реальная визуальная экспертиза.")
