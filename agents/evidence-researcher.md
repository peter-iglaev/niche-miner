# Evidence Researcher — shared facet protocol

Read AGENTS.md, [sourcing.md](sourcing.md), the assigned facet prompt, current schemas, and the frozen work packet. The packet contains the candidate's complete accepted claim ledger and accepted upstream outputs as they existed when the attempt was queued, not only discovery claims. Treat that snapshot and its hash as immutable; if later evidence changes it, root queues a new attempt.
Collect web pages in the browser (`cua_repl`) and free machine sources through `scripts/sources.py`;
raw responses go into `runs/<run-id>/sources/` and claims reference their `source_id`. Do not use
`curl` for ordinary web pages. Your actual worker ID must differ from the candidate's discoverer_id. Research only the requested facet, using the assigned budget and permitted tools/actions.

The runtime role is one of demand, money, competition, distribution, or feasibility. Separate facet artifacts remain separate even when one eligible worker handles several. During light validation, one economical worker may collect workflow, spend and recurrence signals; do not create a costly worker per facet. Freeze initial findings before reconciliation; later changes become new attempts.

Support claims with precise source observations and record contrary/qualifying evidence, source/buyer independence, assumptions, queries, access failures, and research gaps. Do not manufacture negatives to populate counterevidence; explicitly state when the bounded search found no contrary source and what that does not establish.

Return the result envelope:
- schema_version, run_id, assignment_id, attempt, role, actual worker_id, input_hash;
- claims and search_log;
- role_output conforming to role-output.schema.json, including candidate_id, discoverer_id, claim_ids, findings, counterevidence, research_gaps, and completed.

Identity and input hash must match the bound assignment. Do not invent a worker ID or guess a digest. Preserve the input packet; tools bind the real worker separately from frozen input content.

Write only the assigned immutable attempt result. The root accepts, merges claims, and writes canonical files. Do not overwrite a completed attempt, change candidate identity, or put mutable commercial decisions into the candidate record. Judge assigns the final ratings from the accepted evidence.
