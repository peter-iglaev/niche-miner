# Agent Operating Manual

**Current pipeline is v2 (demand-first).** Orchestrator flow: `.claude/commands/research.md`; CLI: `scripts/demand.py`; spec: `docs/demand-first-spec.md`. The sections below describe the v1 control plane (`scripts/app.py`, `scripts/worker.py`), kept for the historical runs under `runs/` that lack `kind: demand`.

This repository is a Codex-native application for evidence-first niche research. Its purpose is to investigate concrete buyer workflows and produce traceable commercial judgments. The Codex root coordinates real worker agents; Python manages local state and deterministic checks.

## Language and active scope

- Communicate with the user in Russian, including progress updates, questions, and final responses.
- Write final reports and user-facing deliverables in Russian.
- Internal research and communication between agents may be in English.
- Building or checking this application does not authorize market research. A research run requires a user request with an objective and scope (for v2 it is recorded in the run brief by `scripts/demand.py create --authorization`).
- Read config/, relevant schemas, the assigned role prompt, and the run brief before a job. Use the current user request over obsolete task plans.

## Execution model

Work is executed one of two ways, and both record an actual worker identity. `scripts/worker.py` runs an assignment directly on the provider and model routed to that role: it binds itself, collects evidence through the search and source layers, and writes the immutable result. Alternatively the root delegates through the collaboration tools available in its session, such as spawn_agent, send_input, wait_agent, close_agent and resume_agent, and binds the returned agent identity to the queued job. Creating a directory, queue item, or role label is not an agent launch in either mode. The rest of the Python layer prepares, queues, binds, validates, accepts, scores and assembles the machine part of the report; it never judges evidence.

The executor owns what a model must not be trusted to report about itself: the search log is built from the calls that actually happened, identity and authorship fields are stamped by the harness, the semantic-query budget is refused at the boundary, and a result that fails validation is returned to the model with the exact errors rather than written. Accepting a result stays with the orchestrator, so no worker marks its own work done.

Use available worker slots for independent jobs. There is no fixed worker count, candidate count, industry count, or mandatory sequence of named research stages. A user can request discovery, investigation of an existing candidate, comparison, evidence refresh, or a targeted gap check. The root derives required jobs and a finite budget from that request and records the stopping conditions.

Give every worker its role, objective, candidate IDs, author identities, frozen input packet and SHA-256, allowed tools/actions, budget, immutable output path, and acceptance requirements. Record whether context is fresh or reused. Do not claim blind or statistical independence simply because jobs ran in separate agents.

## Permanent roles and independence

- Discovery combines workflow scouting and pain mining. It proposes sourced candidate records, never commercial ratings.
- Evidence research covers demand, money, competition, distribution, and feasibility. Assign the needed facets separately; one eligible worker may handle several facets, but each has its own result.
- Skeptic is the independent evidence reviewer. It checks claim/source fit, source and buyer independence, counterevidence, inflated conclusions, and fatal risks.
- Judge adjudicates the frozen record and reviewer findings without collecting new supporting facts.
- Root owns planning, job binding, canonical acceptance, and the final Russian report.

A discovery author cannot validate or judge their own candidate. Skeptic must not author the evidence it reviews. Judge must not author the candidate, research findings, or skeptic review under judgment. Root may judge only when it merely coordinated those inputs; otherwise delegate Judge to another eligible agent. Verify actual worker identities and accepted-output provenance, not just role names.

Initial facet findings are frozen before reconciliation. A change after reviewing another worker's findings is a new attributable attempt. Preserve disagreement and explain its resolution. Several workers citing one buyer or one vendor story are not independent market observations.

## Evidence contract

Every important external claim needs a stable ID, URL, source type, access date, precise observation or locator, buyer/job match, directness, and limitations. Numerical claims additionally require units, scope, period, currency where applicable, and calculation inputs/method. Label planning estimates and assumptions. Do not invent prices, TAM, search metrics, customer counts, or workload baselines.

Distinguish a claim's direct support from source reliability. A price page directly establishes an offer; it does not establish a transaction. A review establishes paid use only when its actual contents support payment. A hiring advertisement shows hiring intent; observed paid labor may establish spending on work without proving budget transfer to software.

Retain supporting, contradicting, and qualifying observations. Group syndicated sources and repeated buyer accounts; do not turn multiple URLs into independent corroboration. Preserve unresolved contradictions by claim ID and severity. Missing data, inconclusive data, access failures, and credible negative evidence are different outcomes.

Search volume is query activity and CPC is advertiser activity, not product payment proof. Practitioner posts are reported experiences, not representative prevalence. Templates establish workflows, not necessarily costly pain.

Check tools at run time and record their actual availability and constraints. Collect only permitted requested/researched queries. Save exact query, provider, geography, language, period, units, collection date, raw-result reference, and access limitations. Missing numerical values remain null with the actual no_data/not_collected status and reason. A connected tool need not have data for a particular query.

Store every new raw response byte-for-byte under `runs/<run-id>/sources/`, with a stable `source_id`, original SHA-256, response content type and matching extension. Pass your own assignment id and role to the search and source tools (`--assignment`, `--role`): who fetched a source is provenance recorded in `source_usage.jsonl`, never part of the source identity or the query budget. Never decode a binary response to create its canonical archive, truncate that archive, or use `/tmp` as a canonical evidence reference. One semantic query may have several provider attempts; count it once while recording rate limits, transport failures, HTTP failures and parse failures separately.

## Candidate and commercial judgment

A candidate identifies the user, budget owner where known, job, trigger, workaround, geography/regulatory context when material, and desired measurable outcome. Unknowns remain explicit. Deduplicate by the buying context and workflow before counting or comparing candidates; preserve source IDs and merge reasons.

Payment Confidence uses config/scoring.yaml: an ordinal existing-spend tier, not a probability. A public price alone establishes only an offer. Independent buyer/practitioner reports, observed labor or outsourcing spend, paid competitors and recurrence may converge into meaningful existing-spend evidence without an invoice. Adjacent spending and exact-job spending stay distinct. Transaction Evidence Strength separately records how directly a concrete transaction is observed. The highest payment tier requires independently supported paid buyers and attributable commercial terms; an invoice or contract strengthens it but is not required at earlier tiers.

A request to investigate does not require an existing payment threshold. Investigation Priority allocates the next research budget and may be high while distribution or feasibility is unknown. Commercial Qualification is a separate decision and must pass the configured payment, distribution, feasibility, independence, and contradiction requirements before recommending an MVP or market experiment. Unknown mandatory evidence defers that recommendation; it does not prove that nobody pays. Never lower the criteria to fill a list.

Report problem attractiveness and business attractiveness separately using configured 0–4 anchors. Unknown ratings leave their index unknown. Keep founder fit separate; technical ease cannot rescue failed commercial conditions. Distinguish eligibility, rankability, and a recommendation to perform an experiment. A deterministic weighted ranking may prioritise candidates that already passed every hard gate; it never compensates a failed or unknown gate or an unresolved fatal contradiction, and founder fit is reported separately and never weighted. Do not invent any other blended total.

## Founder context, distribution, and scope

Use the schema-validated `config/founder-profile.yaml`. Do not assume skills, sector knowledge, contacts, languages, budget, or geography. Classify familiarity as `confirmed_familiar`, `confirmed_unfamiliar`, `unknown`, or `outside_confirmed_familiarity`. The last state may guide anti-bias exploration but never means confirmed unfamiliarity. Enforce familiarity quotas only when assessable; disclose classifications and denominators. Apply relevant portfolio constraints to the scope of the actual run. Industry and possible solution technology are separate labels.

Low visibility and avoidance of aggressive outbound constrain acquisition. They do not imply a ban on all buyer conversations. Research authorization does not authorize sending messages, purchases, campaign launches, or publication. Those actions need authorization in the user request.

## Durable state and acceptance

Use run-scoped paths and actual IDs; never hard-code a named run into application behavior. The exact machine contract is defined by current schemas and CLI help. The root is the only canonical writer. Cheap hypotheses may be staged in runs/<run-id>/hypotheses.jsonl before the control plane: a staged record carries no claims and is not a candidate artifact, and only selections promoted from it become assignments.

Every run declares an explicit scope: `targeted` to investigate an existing hypothesis or refresh evidence, `mining` for a broad search. Portfolio anti-bias quotas are enforced only for `mining`; `targeted` runs report the audit as not assessed. A mining run may finish below quota only through an explicitly recorded override reason, which appears in the manifest, the event log and the report.

Generate `report.md` from accepted artifacts, then write the Russian narrative outside the machine markers and regenerate it. Publish `report.pdf` as the primary user deliverable with an embedded Cyrillic font. Finalization requires a current machine section, valid accepted references, a PDF generated from the current Markdown, automated Unicode/font/page checks, rendered previews for every page, and a hash-bound record that root visually inspected all previews.

Keep the objective, configuration/capability snapshots, manifest, frozen inputs, immutable worker attempts, searches, claims, and decisions under runs/<run-id>/. Inputs/results live at work/<assignment-id>/attempt-<n>/input.json and result.json. Accepted dossiers live at candidates/<candidate-id>/ with hypothesis.json, research/<role>.json, assessment.json, and generated score.json. The run's claims.jsonl is canonical. Optional readable evidence.md and the Russian report link back to accepted attempts and claim IDs.

The immutable Niche Atom includes run_id and discovery provenance, not a mutable status or rejection reason. Record rejected/deferred/duplicate/qualified dispositions separately through the manifest decision command. Assessments use decision_context: investigate or commercial_validation. These contexts gate evidence-backed recommendations; they do not prevent initial evidence collection requested by the user.

Workers write only assigned attempts. A completed attempt is immutable; retries use a new attempt ID and preserve the previous result and reason. Root validates schema, input/output hashes, author independence, source/claim references, required dependencies, and any score before accepting. Changing accepted inputs makes dependent judgments stale until reviewed again.

Run the application preflight before research, and use deterministic validation at acceptance and completion. A passing offline test suite is evidence of local behavior, not proof of a live agent dispatch or of a market opportunity.

A run finishes when its stated objective and acceptance conditions are met, or is explicitly reported as deferred/incomplete with its gaps. Empty results and no recommended candidates are valid. Do not claim completion while required jobs, evidence, or checks are missing. Always report the actual work performed.

A queued assignment whose current attempt is rejected or cancelled is an execution blocker, not a market result. Do not finalize the run as complete around it by writing a deferred candidate decision. Recover the assignment or publish an explicitly incomplete recovery report. Worker/runtime failures do not consume the research-attempt budget; evidence validation failures do. Keep provider transport, rate-limit, HTTP and parse failures separate from a successful `no_data` result.

The main report is a decision brief, not an execution log. Lead with what was learned, whether the run is technically complete, and what deserves the next budget. For each shortlisted candidate explain the hypothetical business in plain language: buyer and user, trigger, paid workflow, proposed product boundary, delivery model, charging basis, inbound path, decisive risk and cheapest next test. Keep worker identities, retries, hashes and detailed failure traces in the machine section or technical appendix unless they change the conclusion.

For broad mining, use the configured budget funnel as a scale target: many cheap hypotheses, one-worker light validation, a smaller serious-validation set, and only then deep investigation. A light candidate does not need one expensive worker per facet. Deep validation, Skeptic and Judge use frozen accepted snapshots and stricter author/context independence. Provider and model names are chosen per role in `config/model-routing.yaml` rather than written into prompts or policy constants. The resolved choice is frozen into each assignment packet as `execution_policy.provider/model/reasoning_effort`, and the model actually used is recorded at bind time; requested and actual stay separate so a runtime substitution is visible rather than hidden.
