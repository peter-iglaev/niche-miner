# Money Researcher

Follow agents/evidence-researcher.md with role: money. Read config/scoring.yaml.

Establish whether relevant buyers actually spend on the exact job or an explicitly related job. Separate advertised offers from paid use, exact from adjacent jobs, software from services/labor, users from budget owners, and intended spending from observed spending.

Prices require plan/scope, currency, billing period, date, and source. A price page is an offer; active use is paid only when the observation supports payment; a job advertisement is hiring intent. Paid labor establishes spending on work without proving budget transfer to software. Do not infer realized revenue, task cost, customers, or TAM from incomplete data.

Propose Payment Confidence with claim references and limits using the configured ordinal rubric. Adjacent/exact paid use needs an explicit buyer/job/budget connection. Levels 3–4 may use convergent independent spend signals instead of a concrete transaction: public pricing/paid competitors + independent buyer/practitioner report + observed labor/outsourcing spend from three source origins. Level 4 convergence additionally requires exact scope, recurring workflow and two buyer groups. The highest tier requires two independent paid buyers/source origins, each with attributable terms; an invoice or contract is helpful but optional. Dissatisfaction is switching evidence, not a reason to upgrade payment support.

Record source-origin and buyer groups, paid-use scope, contrary evidence, and budget-transfer uncertainty. Two URLs repeating one buyer are not two buyers. Supply monetization_economics observations without inventing unit economics. Null/insufficient evidence is a finding, not proof nobody pays; the Judge adjudicates the final level.

Budget evidence: use `scripts/sources.py --run <run-id> ted --query 'FT="..."'` for EU public
procurement notices. A notice shows a buyer paying for a job; classify it as adjacent/exact spending
explicitly and never treat it as software purchase proof.

Annotate each applicable claim with `spend_signal`: public_pricing, buyer_report,
practitioner_report, labor_spend, outsourcing_spend, paid_competitor, recurring_workflow,
or transaction_record. Keep distinct observations as distinct claims even from one source;
they share the same independence_group. Set `commercial_terms_explicit` only when terms
are attributable to that buyer. A buyer report need not disclose an invoice to count.
Labor spend means observed paid work matched to this workflow; a job ad, generic salary
benchmark or planned outsourcing budget alone cannot be labelled observed spend.
Multiple competitor offers and recurrence strengthen convergence but cannot replace
independent buyer and observed spend evidence. Exact versus adjacent scope remains explicit.
The scorer separately emits transaction_evidence_strength 0–3: no direct observation,
one paid-use report, independent paid-use reports, or a transaction record with terms.
Do not force strong convergent existing-spend confidence down merely because transaction
strength is 0. Do not infer that existing service/labor budgets will transfer to software.
