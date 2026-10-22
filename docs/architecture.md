# Codex Application Architecture

## Two execution layers

Codex performs the semantic work: it understands the request, plans assignments, launches workers, assesses evidence, and explains the outcome. The local Python code provides the queue, state persistence, schema/hash/authorship validation, artifact acceptance, index computation, and assembly of the machine part of the report.

Codex is the only orchestrator. It is not a research role: it calls the real collaboration tools (`spawn_agent`, `send_input`, `wait_agent`, `close_agent`, `resume_agent`), receives the actual agent identity, and binds it to the assignment.

A `queued` record does not mean an agent was launched. Root must call a real Codex collaboration tool and bind the returned agent identity to the assignment. A script that only creates files cannot report agent-performed work.

## Roles and separation of authorship

The canonical set is eight research roles:

- `discovery` — the workflow and the pain; it assigns no commercial ratings;
- `demand`, `money`, `competition`, `distribution`, `feasibility` — separate facets, each with its own result;
- `skeptic` — independent evidence review;
- `judge` — assessment based on the frozen record.

The orchestrator is not part of this list. One compatible worker may perform several roles, but every result remains separate. Self-review is not allowed: a discovery author cannot validate or judge their own candidate, `skeptic` cannot author the facets it reviews, `judge` cannot author the analysis under assessment. Root may judge only when no such conflict exists.

The `qualified` recommendation threshold: accepted `discovery`, `money`, `distribution`, `feasibility`, `skeptic`, `judge`. The `demand` and `competition` facets are mandatory only when the assessment references their claims; otherwise their absence is recorded as an explicit gap in the report.

## Run type and portfolio quotas

`create-run` requires an explicit `--scope`:

- `targeted` — hypothesis verification or evidence refresh; discovery and candidate counts follow the task;
- `mining` — a broad search across industries; the portfolio anti-bias audit applies to it.

Quotas never block assignment queuing. For `mining` they are computed over the corpus, shown in `status`, and checked at `finalize`; a failure allows only an explicit override with a recorded reason (`report --override-quota REASON`), which lands in the manifest, the events, and the report. For `targeted` the audit is marked as not assessed.

## Request-driven work

The request defines the objective, type, constraints, budget, and stopping condition. Assignments are built from dependencies, not from a fixed funnel. Discovery can continue while the run is active; evidence work requires an accepted discovery result for a concrete candidate, `skeptic` requires at least one accepted facet, `judge` requires an accepted `skeptic` analysis.

Before dispatching, root verifies worker independence and freezes the input packet. After a result arrives, root performs the machine checks and accepts the attempt or returns the specific error. Changed inputs make dependent results stale. Budget exhaustion means a gap or a deferral, not permission to invent an answer.

## Artifacts and states

The core structure is `runs/<run-id>/`; the directory is not committed to git.

- the manifest stores the objective, type, stopping condition, capability snapshot, authors, decisions, quota overrides, and the report link;
- frozen packets and results — `work/<assignment-id>/attempt-<n>/input.json` and `result.json`;
- canonical dossiers — `candidates/<candidate-id>/hypothesis.json`, `research/<role>.json`, `assessment.json`, computed `score.json`;
- the claim registry — `claims.jsonl` at the run root;
- the technical report — `report.md`; the primary user deliverable — `report.pdf`, previews, and a hash-bound visual review record.

The assignment input packet is described by a dedicated schema: role, objective, run type, budget, allowed actions, exact result path, acceptance requirements, configuration hash, discovery author and already accepted workers, hypothesis, and claims. The actual worker binding is stored in the attempt, and the delivery mode (`direct` or `relayed`) is recorded explicitly: an environment where a sub-agent cannot write to the main tree must not hide that fact. A spike in the current environment showed that a sub-agent writes directly to the main tree, so `direct` is the normal mode and `relayed` remains the fallback.

An accepted attempt cannot be overwritten with corrected content; a new attempt is created. The decision on a candidate (`qualified`, `rejected`, `deferred`, `duplicate`) is stored separately from the immutable candidate description.

## Application commands

`create-run --run ID --objective TEXT --scope {targeted,mining} --stopping-condition TEXT [--brief path] [--capabilities path]`, `queue --run ID --candidate CID --role ROLE --input packet.json [--retry]`, `bind --run ID --assignment AID --agent-id ACTUAL [--reused-context] [--provider P] [--model M]`, `accept --run ID --assignment AID --result PATH [--delivery direct|relayed]`, `next|status --run ID`, `decide --run ID --candidate CID --status STATUS --reason TEXT --author-id ACTUAL [--claim-id ...] [--duplicate-of CID]`, `cancel --run ID --assignment AID --reason TEXT`, `validate --run ID`, `report --run ID [--override-quota REASON]`, `pdf-review --run ID --author-id ACTUAL --reason NOTES`, `finalize --run ID`. Exact parameters are listed in `scripts/app.py --help`.

## The report

`report` assembles a compact machine section: top candidates, Investigation Priority, Payment Confidence, Transaction Evidence Strength, commercial status, and links to the canonical registry. The full `claims.jsonl` is not copied into a large table. Narrative sections are written by Codex as a Russian executive summary and candidate cards: why it is interesting, spend, pain/frequency, competition, distribution, feasibility, the main unknown, and the cheapest decisive test. A repeated `report` refreshes the machine section, preserves the narrative, and generates `report.pdf`.

The PDF is the primary deliverable. It uses embedded DejaVu Sans, is verified by reverse extraction of every Russian word and the Unicode map, and is rendered by Poppler to one PNG per page. Root reviews all previews and records a hash-bound receipt via `pdf-review`; after the Markdown or PDF changes, the receipt is invalid. `finalize` requires current Markdown, PDF, previews, and the visual receipt.

## Raw sources

A run stores the original bytes in `runs/<run-id>/sources/`: file `<source-id>.json|html|txt|pdf|bin` plus a
`<source-id>.meta.json` sidecar with the provider, exact query, URL, HTTP status, collection date, SHA-256,
size, original/detected content type, and source limitations. PDF and other binary responses are
never decoded as UTF-8, never truncated, and stored byte-for-byte; the SHA is computed over the
original response. A failed request is also stored — as `retrieval_status: error`
with the error text; it does not turn into data and does not disappear. Claims reference the `source_id`,
not temporary paths outside the run. A canonical reference to `/tmp`, the project root, or another run
is rejected at acceptance.

`scripts/sources.py` connects keyless free sources: the TED Search API (anonymous access to
published EU tenders), the Wayback Machine for confirming a page version at a date, and HN Algolia
for practitioner discussions. This data describes procurement, budgets, and people's messages; it does not replace
the Skeptic review and does not prove payment by itself.

## Funnel and cheap hypotheses

Expensive models must not see the whole corpus. Cheap hypotheses accumulate in
`runs/<run-id>/hypotheses.jsonl` before the control plane: a record contains the canonical niche atom but no
claims, and is not a candidate. `scripts/hypotheses.py` validates the atom structure, rejects
conflicting duplicates, shows portfolio industry coverage, and deterministically selects
the next batch (round-robin over industries so one industry does not consume the whole stage).

Stage numbers are run budgets, not required corpus sizes: the first run follows
`funnel.first_run` (40 → 12 → 5 → 3), the target scale is `funnel.target_scale` (300 → 60 → 15 → 5).
Light validation uses one economical worker per candidate with a small query budget. Investigation
Priority selects the next share of the budget from accepted claims, up to Judge. Serious validation allows
one compatible worker for several needed facets with separate results. Deep investigation,
Skeptic, and Judge require strict independence and fresh context. Provider and model come from `config/model-routing.yaml`
(they stay null in stage policy), so GLM, DeepSeek, and other models plug in without
contract changes. A small stage is reported as a gap, not filled with weak candidates.

## Assignment executor

`scripts/worker.py` executes one assignment on a real model. It reads the frozen packet,
takes the role prompt from `agents/`, binds itself to the assignment (`bind` with the actual provider and
model), collects evidence through `scripts/search.py` and `scripts/sources.py`, and writes
`result.json`. Acceptance stays with the orchestrator: a worker does not declare its own work done.

The executor owns what a model must not be trusted to report about itself. The search log is built from
the calls that actually happened, not from the model's retelling. Authorship and identity fields are stamped by the
harness, so someone else's authorship cannot be claimed. Founder familiarity is derived from the industry and
the frozen profile, not asserted by the model. The semantic query budget is counted and refused at
the boundary. A result that fails the schema and contract is returned to the model with the exact list of errors, and
after attempts are exhausted, execution fails instead of writing garbage.

`scripts/llm.py` is a thin client to an OpenAI-compatible endpoint: provider, address, and the key
variable come from `config/model-routing.yaml`. A provider with a different protocol is rejected explicitly.
Token spend is written next to the result in `usage.json` and lands on the dashboard.

## Model routing

The provider and model for each role are set in `config/model-routing.yaml`: `default` → role → optional
per-stage override. `null` means a choice at run time, so default behavior does not change.
`scripts/routing.py` reads, schema-validates, and edits this file (`show`, `validate`, `set`).

`app.py queue` resolves the route for the role and stage and puts the result into the frozen packet
(`execution_policy.provider/model/reasoning_effort` plus `routing_source`). Root must pass these
values to `spawn_agent` and record the model actually used via
`app.py bind --provider ... --model ...`; it is stored in the attempt next to `worker_id`. Requested and
actual stay separate: a mismatch is visible, not masked. Python still does not
call the LLM, so routing is a contract with the runtime, not a fact of execution.

Skeptic and Judge are worth keeping on a different model than the authors of the material under review: runs on
a shared model are not statistically independent (`independence.shared_model_runs_are_statistically_independent: false`).

## Search and data plane

The semantic work of agents is separated from data acquisition. The agent formulates the evidence objective, and
`scripts/search.py` queries a swappable search provider (Brave by default, Tavily as
fallback); keys are read from the environment or `.env.local`. The provider is not hard-coded into the research logic:
it can be replaced without changing the result contract.

- One search = one semantic query. Brave/Tavily fallback attempts share one logical ID and
  together consume one budget unit. Every provider attempt, exact query, geography, language,
  date, and limitation is stored in `searches.jsonl`; the raw response goes to `sources/`; rate-limit,
  transport, HTTP, and parse failures are counted separately.
- A missing key yields `capability_unavailable`: a stage that needs search is reported as
  blocked. The system does not substitute scraping for search and does not burn budget bypassing protection.
- `429` means one provider switch, no retries of the same; an empty result set is returned as `[]` without
  query expansion; an unrecognized response is an error, not "no data".
- Search results are leads, not evidence: a provider ranking proves neither payment nor
  prevalence. Found pages are opened and archived before being referenced in a claim.

`preflight` records an offline snapshot of provider availability (`search_capabilities`) without network calls
and warns when no keys exist.

## Source attribution

Who downloaded a source is provenance, not identity. The `source_id` remains a hash of the query and response,
so the same bytes have one ID no matter which agent requested them. The calling agent passes `--assignment`, `--role`, and `--worker` to `scripts/search.py` and `scripts/sources.py`;
the first fetch stamps `first_assignment_id` in the sidecar, and every use appends a line to
`runs/<run-id>/source_usage.jsonl`. Omitting the flags breaks nothing and is recorded as `null`.

The dashboard reconstructs attribution backwards from `result.json` (`search_log`, `claims`) and `claims.jsonl` for
runs made before the stamps existed, and in every line states the path by which it was obtained. A source no one referenced remains an orphan and lands in diagnostics rather than being attached to a plausible agent.

## External actions

Sending anything outward is a separate action, not part of research. It requires explicit
authorization in the user request and runs only with `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` configured; without keys, `credentials()` returns `available: false`, and sending
is not substituted with anything else. `scripts/telegram.py` sends `report.md` as a document, archives
the Bot API response (including refusal) to `sources/`, and never writes the token into an artifact URL or a log.
Sending is not market evidence and does not replace run artifacts.

## Independent review and acceptance

Schemas, real assignment references, input/output hashes, allowed roles, separation of authorship, claim existence, dependency completeness, calculation reproducibility, and report integrity are checked automatically.

Whether a source actually supports a claim is checked by Skeptic, not JSON Schema. Root accepts results only after both applicable checks. The machine check must not assert that the truth of market conclusions is proven by syntax.

## Criteria for commercial conclusions

The configuration defines Payment Confidence and Transaction Evidence Strength separately. A public price
establishes an offer, but one alone does not prove paid use. An independent buyer/practitioner report,
observed labor/outsourcing spend, several paid competitors, and a recurring workflow can together
establish meaningful existing spend without an invoice. A direct transaction artifact raises a separate
directness rating and is mandatory only for the strongest transaction-level conclusion. Adjacent spending does not
become spending on the exact job.

Investigation Priority only allocates the next research budget and can be high with
unknown distribution or feasibility. Commercial Qualification separately requires confirmed
payment, distribution, feasibility, an independent review, and no fatal contradiction. The founder
profile is validated by a dedicated schema; `outside_confirmed_familiarity` widens the anti-bias search
but is never renamed to `confirmed_unfamiliar`.

## Ranking and switching signal

The job and business indices stay separate. Additionally, for candidates that have **already passed every
hard gate**, a deterministic weighted ranking is computed (`config/scoring.yaml` → `ranking`):
payment evidence 35, pain 15, frequency 10, distribution 10, scale 70. The weights come from the research
document with two edits: "existing spend" is merged into payment evidence (it is one observation), and founder fit is excluded so an unfamiliar industry does not lose only because of unfamiliarity. Factors
without an anchored rating (competition, MVP ease, market direction) are not part of the ranking and are shown
as gaps. The ranking is an ordinal prioritization tool, not a probability and not a calibrated forecast.

A public price or observed spend combined with dissatisfaction is a **switching signal**, not stronger
payment evidence. It is assessed in `switching_opportunity` and reported as
`switching_signal_rating`, never raising Payment Confidence.

## Dashboard

`scripts/pipeline_viewer.py` assembles the self-contained page `docs/pipeline-map.html`: data of all
runs, styles (`scripts/pipeline_viewer.css`), and the script (`scripts/pipeline_viewer.js`) are embedded into
the file. Tabs: an overview in plain language, sources with agent/type/candidate filters, agents and models
(requested vs actual, the generator for the `routing.py set` command), candidates, and a technical section with
stages, chronology, and limits. The page only reads artifacts and changes nothing.

## Readiness check

`scripts/preflight.py` checks the presence of required files, the validity of every schema, the absence of leftovers from the earlier pilot, and runs the offline test suite and a synthetic run in a temporary directory. The tests cover acceptance without an agent binding, author self-review, attempt immutability, unknown values, the `qualified` threshold, portfolio quotas, report integrity, and the full cycle up to `finalize`.

Examples are marked as synthetic. A populated queue or a passing Python test does not prove an agent launch: real delegation is verified separately in the Codex environment. Application development and checks do not launch market research.
