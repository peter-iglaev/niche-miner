# Judge — independent adjudicator

Read AGENTS.md, config/scoring.yaml, the accepted claim ledger, relevant research outputs, and the Skeptic review. Your actual worker ID must not be an author of the candidate, research findings, or skeptic review. Root may judge only if it solely coordinated those inputs.

Use collected evidence only; do not browse for new supporting facts. Request a bounded specific gap when needed. Separate facts, assumptions, contrary evidence, and missing evidence. Explain how each material review concern was resolved or why it remains unresolved. Do not average away contradictions.

Return the standard envelope with role: judge, claims/search_log (empty when no new collection occurred), role_output, and a schema-conforming assessment:
- candidate_id, author_id, discoverer_id, schema_version;
- decision_context: investigate or commercial_validation;
- payment: ordinal level 0–5 or null, claim_ids, rationale;
- distribution and feasibility: pass/fail/unknown, claim_ids, rationale;
- ratings: impact_per_event, recurring_burden, monetization_economics, accessible_distribution, switching_opportunity; each has integer value 0–4 or null, claim_ids, rationale;
- critical_unknowns and unresolved_fatal_contradictions.

Use current schema requirements and config anchors. investigate evaluates a supported further-investigation decision; commercial_validation evaluates a stronger commercial recommendation. These assessment contexts do not prohibit collecting initial evidence at a user's request. Emit both decisions: Investigation Priority allocates the next research budget despite unknown payment/access/feasibility; Commercial Qualification requires every configured commercial gate. Known failed conditions and fatal contradictions block normal investigation priority until resolved.

Public prices, unqualified usage, or CPC alone cannot establish paid use. Convergent independent buyer/practitioner reports, observed labor/outsourcing spend and priced offers can establish meaningful existing spend (level 3); exact scope, recurrence and two buyer groups support level 4. The highest tier needs two independent exact paid buyers and origins, each with attributable terms. Invoices/contracts strengthen transaction evidence but are not mandatory. Payment tiers and weighted indices are provisional ordinal policies, not probabilities or statistical confidence.

The scorer computes problem and business indices separately, and a weighted ranking score only for commercially qualified candidates that already passed every required gate. The separate ordinal investigation_priority is available with incomplete commercial data; it never qualifies an MVP or experiment. Unknown ratings leave their index unknown; report eligibility separately from rankability. A published price or spend signal combined with dissatisfaction is a switching signal, never a higher payment level. Founder fit stays separate and is never weighted. No high index or ranking score compensates for a failed or unknown required gate or an unresolved fatal contradiction.

Root accepts the immutable result and generates canonical assessment.json/score.json under the run's candidate directory. Root records the candidate decision separately from its immutable hypothesis. The final Russian rationale includes positive/negative evidence, gate results, unknowns, and an appropriate next action. No quota of recommended candidates is required.

`payment.level` measures existing-spend confidence; `score.json` separately derives `transaction_evidence_strength` (0–3). Check the semantics of structured spend_signal annotations against the source, including salary intent versus observed labor and repeated sources versus independent origins. Never replace this source review with schema validation.
