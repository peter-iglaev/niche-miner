# Runbook

**The current pipeline is v2, demand-first.** The orchestrator procedure: `.claude/commands/research.md`;
commands: `python3 scripts/demand.py --help`; specification: `docs/demand-first-spec.md`; sources:
`docs/sources-setup.md`. Below is the v1 runbook, relevant only for old `runs/*` without `kind: demand`.

---

# Runbook v1 (superseded)

A step-by-step procedure for the root agent. Root's role is planning, assignment binding, canonical acceptance,
and the final Russian report; root itself is not a research role.

## Prerequisites

- `python3 scripts/preflight.py` → `passed: true`.
- The objective, scope, and stopping condition are in the user request or in `tasks/`.
- `config/founder-profile.yaml` does not contradict the request; unknowns remain unknown.

## Procedure

1. **Create the run** (objective, scope, stopping condition, limits, capability snapshot):
   ```
   python3 scripts/app.py create-run --run <run-id> --objective "<objective>" --scope mining \
     --stopping-condition "<condition>" --config <limits.json> --capabilities <capabilities.json> \
     --brief tasks/standing-objective.md
   ```
2. **Cheap hypotheses (mining only)**: before the control plane, accumulate a staging file with no claims and
   no acceptance. The first run follows `config/research-policy.yaml` → `funnel.first_run` (40 → 12 → 5 → 3);
   the target scale is `funnel.target_scale` (300 → 60 → 15 → 5).
   ```
   python3 scripts/hypotheses.py add --run <run-id> --input <hypotheses.jsonl>
   python3 scripts/hypotheses.py audit --run <run-id>
   python3 scripts/hypotheses.py select --run <run-id> --limit 12
   ```
   Only selected hypotheses are promoted; the rest stay in staging and are not counted as candidates.
   Portfolio quotas are computed over the accepted corpus, not staging.
   `preferred_industry_buckets` define the grid for cheap anti-bias staging, but not a closed dictionary:
   discovery keeps a more precise industry from the explicitly given run scope, and the portfolio audit marks
   it as outside the preferred grid.
3. **Check model routing.** Roles receive their provider and model from
   `config/model-routing.yaml`; `null` means a choice at run time. The values land in the
   frozen assignment packet (`execution_policy`), so change them before `queue`:
   ```
   python3 scripts/routing.py show
   python3 scripts/routing.py set --role skeptic --provider deepseek --model deepseek-reasoner
   python3 scripts/routing.py validate
   ```
   Skeptic and Judge are worth keeping on a different model than the authors of the material under review:
   a shared model gives no independence, and the policy records that.
4. **Queue discovery** for the buyer's industry from `config/portfolio.yaml`:
   ```
   python3 scripts/app.py queue --run <run-id> --candidate <candidate-id> --role discovery --input <packet.json>
   ```
5. **Execute the assignment.** The normal path is the built-in executor: it binds itself to the assignment with
   the actual model, collects sources, and writes the result.
   ```
   python3 scripts/worker.py --run <run-id> --assignment <candidate-id>-discovery
   ```
   Run no more than `max_active_agents` in parallel. The alternative is to launch an agent
   with a delegation tool using the provider and model from the packet and **bind its actual ID
   and model manually**:
   ```
   python3 scripts/app.py bind --run <run-id> --assignment <candidate-id>-discovery --agent-id <actual-id> \
     --provider <actual provider> --model <actual model>
   ```
   The requested and actual models are stored separately: if the runtime delivered something other than requested, it is visible
   on the dashboard, not hidden.
   A queue entry is not an agent launch. Until there is a `bind`, no work has been done.
   One assignment is issued per worker turn. In the prompt, explicitly forbid the worker from running
   `scripts/app.py`, changing the manifest, accepting results, writing decisions, cancelling other
   workers, and finalizing the run. Issue the next assignment only after reconciling the current result.
6. **Accept the result** after checking the schema, hashes, authorship, and claim references:
   ```
   python3 scripts/app.py accept --run <run-id> --assignment <assignment-id> --result <path>/result.json
   ```
7. **Light validation**: one economical discovery worker collects workflow, spend, and
   recurrence signals. `status` computes Investigation Priority from accepted claims before Judge.
8. **Serious validation**: for the shortlisted candidates, check the needed facets. One eligible worker
   may perform several facets, but the results remain separate and frozen.
9. **Deep investigation**: `Skeptic` independently reviews the full frozen snapshot of all accepted claims,
   then a fresh independent `Judge` assesses the same corpus. The discovery author does not validate the candidate;
   Skeptic and Judge do not reuse context and are not authors of the material under review.
10. **Sending the report** (only if the user authorized sending and
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are filled in):
   ```
   python3 scripts/telegram.py check
   python3 scripts/telegram.py send --run <run-id> --file runs/<run-id>/report.md --caption "<topic>"
   ```
   Sending does not replace the run archive: the result (including a Telegram refusal) is stored in `sources/`.
   The token never reaches artifacts or URLs.
11. **Decision** and **report**:
   ```
   python3 scripts/app.py decide --run <run-id> --candidate <cid> --status <qualified|rejected|deferred|duplicate> \
     --reason "<reason>" --author-id <actual-id> --claim-id <claim-id>
   python3 scripts/app.py report --run <run-id>          # the machine section and a preliminary PDF
   # root writes the Russian narrative outside the machine markers
   python3 scripts/app.py report --run <run-id>          # a current PDF from the final Markdown
   # root visually reviews every PNG page in report-preview/
   python3 scripts/app.py pdf-review --run <run-id> --author-id <actual-id> --reason "<what was checked>"
   python3 scripts/app.py finalize --run <run-id>
   ```
   `report.pdf` is the primary deliverable. `finalize` rejects a stale PDF, a missing embedded
   Cyrillic font, lost Russian words, incomplete previews, or a receipt from another SHA.

   Before finalizing, check `status.execution_health`. A value of `blocked` means at least
   one already queued assignment ended `rejected` or `cancelled`; such a run cannot be completed
   as successful. Recover the assignment first. Mark a technical worker failure with
   `cancel --failure-kind execution`: a retry after that does not consume the research-attempt limit.
   `no_data` is recorded only for a technically successful query without relevant results.

   The main report answers a decision. For the shortlist, state the buyer, trigger, paid job,
   product boundary, delivery format, unit of payment, assumed inbound channel, main risk, and
   the cheapest decisive test. Retry details, worker IDs, and hashes stay in the verifiable summary.

## Data collection

- `scripts/search.py` — machine web search (Brave by default, Tavily as fallback). Keys come from
  the environment or `.env.local`; `providers` shows availability without the network.
  ```
  python3 scripts/search.py providers
  python3 scripts/search.py search --run <run-id> --query "<query>" --count 10 --assignment <assignment-id> --role <role>
  python3 scripts/search.py fetch --run <run-id> --url <url> --assignment <assignment-id> --role <role>
  python3 scripts/search.py log --run <run-id>
  ```
  `--assignment` and `--role` record who exactly fetched the source (`runs/<run-id>/source_usage.jsonl`
  and the stamp in the sidecar). They are not part of the `source_id` or the `logical_query_id`, so they change neither the
  archive nor the budget. Without them, the source appears on the dashboard as "unattributed".
  Failure behavior: no key → `capability_unavailable`, the stage is reported as blocked,
  scraping is not substituted for search. A `429` or transport failure allows one provider switch.
  An empty result set → `[]` without query expansion. One semantic query shares one `logical_query_id` and
  is counted once; provider attempts and rate-limit/transport/HTTP/parse failures are stored separately.
  Calls to one provider are serialized between worker processes. For Brave, a
  1.05-second interval is kept by default; if there is no fallback provider, after a `429` Brave is retried
  once within the same logical query. The interval can be changed via
  `MONEY_FIRST_BRAVE_MIN_INTERVAL_SECONDS` without changing the research budget.
- The browser (`cua_repl`) — for search engines and pages the provider cannot reach. Do not use `curl` for ordinary
  pages.
- `scripts/sources.py` — TED (budgets), Wayback (the page version at a date), HN Algolia (the practitioners' voice).
  The same `--assignment` and `--role` flags.
- Raw responses are stored byte-for-byte in `runs/<run-id>/sources/`; claims reference the
  `source_id`, and the sidecar records the MIME type, extension, length, and SHA of the original bytes. `/tmp` is forbidden.
- Budget: one semantic query = one query even with provider fallback; opening pages spends no budget.
- Buyer contacts, purchases, campaigns, and publications require separate permission.

## Launching from a phone

A run is started from the Claude mobile app as a cloud session over this repository.

The environment is configured once at claude.ai/code: network level **Custom** with the domain list
(`api.deepseek.com`, `api.search.brave.com`, and if needed `api.ted.europa.eu`,
`hn.algolia.com`, `archive.org`) and the variables `DEEPSEEK_API_KEY` and `BRAVE_API_KEY`.

**Leave the Setup script field empty.** The setup script starts in an undefined working directory, and
`pip install -r requirements.txt` there fails with `No such file or directory`, so the session does not
open at all. Dependencies are installed by the repository's SessionStart hook: `.claude/settings.json` calls
`scripts/install_pkgs.sh`, which reads `$CLAUDE_PROJECT_DIR` and therefore always finds the project
root. Locally the script exits immediately, installing nothing.

Then, in the session, the command `/research <topic and budget>` is enough: it walks the orchestrator through the whole
cycle — hypotheses, assignments, workers, acceptance, decisions, report, commit, and Telegram delivery.
Run artifacts live in git, so the cloud session sees previous runs from the clone, and its
results come back with a push.

The cost is distributed like this: the orchestrator only hands out assignments and accepts results, while all
research moves go through the cheap model via workers.

## Dashboard

`docs/pipeline-map.html` — a self-contained page with a snapshot of all runs: data, styles, and script
are embedded in the file; it needs no server or network.

```
python3 scripts/pipeline_viewer.py --open
```

Tabs: **Overview** (what the project is and where we stand, in plain language), **Sources** (every
stored outward call: which agent made it, why, and whether it reached the claims),
**Agents and models** (the role's requested and actual model; the form generates the
`scripts/routing.py set` command), **Candidates**, **Technical** (stages, chronology, limits).

The page writes nothing: it shows state and produces a command a human executes.
After a configuration change or new runs it must be rebuilt.

## Signs of unreadiness

- No bound actual agent ID — the work is not done, whatever sits in the queue.
- A claim relies on a temporary path, a retelling, or a snippet instead of a raw source.
- An unknown value turned into a zero or into "nobody pays".
- A qualification issued without accepted `money`, `distribution`, `feasibility`, `skeptic`, `judge`.
- A source downloaded but no claim references it: budget spent with no trace in the conclusions.
- The agent did not pass `--assignment`: on the dashboard the source appears as "unattributed", and the reason
  for fetching it can no longer be explained.
