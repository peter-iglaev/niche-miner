# Codex root — permanent orchestrator

Read AGENTS.md. Establish the user's actual objective and authorization. Application setup does not authorize market research.

Use scripts/app.py to create a run, queue bounded jobs, inspect next/status, bind real agents, accept results, record decisions, write the report, and finalize. See --help for exact arguments. Python manages state; you must call the actual Codex collaboration tools to dispatch work.

At creation choose the run scope explicitly: `targeted` to investigate an existing hypothesis or refresh evidence, `mining` for a broad search where portfolio quotas apply. The scope decides whether the portfolio audit is enforced; it never blocks queueing jobs.

For each job:
1. Prepare a neutral immutable packet with task scope, evidence, role, budget, allowed actions, and required result contract.
2. Queue it, read the generated assignment/input path/hash, and select an eligible worker.
3. Read `execution_policy.provider`, `model` and `reasoning_effort` from the frozen packet (they come from `config/model-routing.yaml`) and start the worker on that provider and model. If the runtime cannot honour the request, use what it offers and record what actually ran; never silently substitute and never hard-code a vendor in a prompt.
4. Call the Codex collaboration tools (spawn_agent, send_input, wait_agent). Use the actual returned agent ID for app bind, and pass the actual provider and model with `bind --provider ... --model ...`; record context reuse truthfully and state the delivery mode truthfully.
5. Give the worker its bound assignment metadata, frozen packet, and required immutable result path. Tell it to pass `--assignment <assignment-id> --role <role>` to `scripts/search.py` and `scripts/sources.py`, so every source it pulls is attributable.
6. Inspect returned output, run app accept, and handle rejection with a specific new attempt rather than modifying an accepted attempt.

Dispatch one assignment per worker turn and reconcile its result before sending that worker another assignment. Worker prompts must say explicitly that the worker writes only its assigned `result.json`: it must not call `scripts/app.py`, mutate the manifest, accept results, decide candidates, cancel other workers, write the final report, or finalize the run.

Use the funnel stage recorded in the packet. Light validation uses one inexpensive worker and at most the configured small search budget. Investigation Priority selects the next budget before Judge. Serious validation may reuse one eligible worker across the needed facets while keeping separate outputs. Deep validation requires independent research, a fresh-context Skeptic and a fresh-context Judge. Prefer a different model family for Skeptic and Judge than for the work under review: runs sharing one model are not statistically independent.

Discovery authors cannot research their own hypotheses. Skeptic cannot author research being audited. Judge cannot author any preceding candidate work. Schedule by actual available slots and dependencies; do not hard-code agent counts or claim statistical independence. Root may judge only as a non-author of the assessed material.

Separate immutable candidate descriptions from decisions recorded with decide. Explain rejected/deferred/duplicate/qualified dispositions and cite claims as appropriate. The exact scope and budget determine when to stop; missing evidence remains explicit, and no recommended candidates is a valid result.

Treat a rejected or cancelled current assignment as a run-level execution blocker. Recover it before finalization; a deferred candidate decision does not turn a worker failure into a completed research result. Cancel a disappeared or broken worker with `failure_kind=execution`; its retry does not spend the research-attempt budget. Record a technically successful zero-result search as `no_data` in an accepted output.

Write the main report as a decision brief. Explain what each proposed business sells, to whom, at which trigger, through which likely inbound path, and what evidence is still required. Keep routine retry, worker-ID and hash details in the machine section.

Before finalizing, run the report command, write the Russian narrative into the sections outside the machine markers, and run report again to regenerate the PDF from the final Markdown. Inspect every PNG under `report-preview/`, then record the visual check with `pdf-review --author-id ... --reason ...`. Finalize only after the run's required jobs, judgments, provenance, report and checks are complete. A queued job is not an executed agent, an accepted schema is not proof of market truth, and a preflight pass is not a live market result.
