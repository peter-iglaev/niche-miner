# Money-First Niche Miner

An application for finding niches **from observed demand**: what people type into search, what
they pay freelancers for, what they buy on marketplaces, who occupies the results — and only then
interpretation, verification, and decisions on cheap models (DeepSeek). Orchestration comes from a
Claude Code cloud session with the `/research` command; the report is a PDF delivered to Telegram.

## Pipeline v2 (current)

Seven stages, each its own `runs/<id>/<n>-<stage>/` folder with a `summary.json`:

| # | Stage | What it does | Model |
|---|---|---|---|
| 1 | expand | seed phrases → hundreds of real phrasings (autocomplete, three narrow-niche operators) | none |
| 2 | clusters | phrases → buyer jobs | deepseek-chat |
| 3 | screen | do people pay freelancers (Upwork/Freelancer), do they pay for software (marketplaces), is the SERP passable (Brave) | none |
| 4 | measure | volume, CPC, trend (Google Ads Keyword Planner / Bing / Ubersuggest via the MCP bridge) | none |
| 5 | gates | numeric thresholds (calibrated on the control set) and CPC mode (`self_serve` / `services` / `any`) | none |
| 6 | review | interpret → skeptic → judge, fresh context per role | deepseek-chat / deepseek-v4-pro |
| 7 | report | cluster cards, narrative draft, PDF, Telegram | deepseek-chat (draft) |

- Config: [`config/demand.yaml`](config/demand.yaml) — themes, mode, thresholds, models, notifications.
- Threshold calibration: [`docs/calibration.md`](docs/calibration.md), `python3 -B scripts/calibrate.py --run calib-<date>`.
  Thresholds are validated against a sample of niches with known answers; zero candidates is only
  meaningful once the control set passes.
- Entry point: `python3 scripts/demand.py` (`doctor`, `create`, `auto`, `bridge`, `decide`, `report`, …).
- Specification: [`docs/demand-first-spec.md`](docs/demand-first-spec.md); source setup:
  [`docs/sources-setup.md`](docs/sources-setup.md); orchestrator procedure: [`.claude/commands/research.md`](.claude/commands/research.md).
- Dashboard: `docs/demand-dashboard.html` (`python3 scripts/demand.py dashboard`).
- Cluster backlog between runs: `backlog/clusters.jsonl`.
- Run and fix journal: `docs/journal.md` — which run exposed which defect
  and what changed because of it. Machine trail: `runs/<id>/events.jsonl` and `manifest.json`.
- Runs, backlog, journal, dashboards, and the founder profile are owner data. They live in
  a private data repository and are linked into the working copy: [`docs/workspace.md`](docs/workspace.md).
- A run report is stored as `runs/<id>/report-<id>.pdf`: the name carries the topic and date, because the file
  travels out of the run to Telegram and to downloads.

**MCP bridge.** Ubersuggest and Upwork are available only inside a Claude Code session. A stage writes the list of calls to
`runs/<id>/bridge/requests.jsonl`, the orchestrator executes them and stores the responses, and `bridge --ingest` archives
them into `sources/` with hashes. Everything else the scripts call themselves.

## Quick start

Start with the founder profile: copy `config/founder-profile.example.yaml` to
`config/founder-profile.yaml` or link the private data repository
(`python3 scripts/workspace.py link`, see [`docs/workspace.md`](docs/workspace.md)).

```
python3 -B scripts/demand.py doctor
python3 -B scripts/demand.py create --run demand-ecommerce-2026-09-20 --themes ecommerce-tech
python3 -B scripts/demand.py auto --run demand-ecommerce-2026-09-20      # up to the first bridge
python3 -B scripts/demand.py bridge --run demand-ecommerce-2026-09-20    # what to execute in the session
```

Then run `bridge --ingest`, `auto` again, then `decide --auto`, `report`, `narrative`, `pdf-review`, `finalize`, `deliver`.

---

## Pipeline v1 (previous approach, kept for the history of runs)

An agent application for Codex: it researches concrete buyer workflows and ties commercial conclusions to verifiable evidence.

**The application itself is under development. Market research is not running.** That requires a separate user request.

## How the work is organized

The user sets an objective and its type: `targeted` — verify an existing hypothesis or refresh evidence, `mining` — a broad search for jobs across industries. The type is stated explicitly when a run is created. The root Codex agent plans the work and delegates assignments to real agents through the Codex collaboration tools; Python maintains the queue, state, artifacts, and checks.

Scale and limits come from the request. The scale funnel targets roughly 300 cheap hypotheses → 60 light validations → 15 serious validations → 5 deep investigations; these are budgets and landmarks, not required counts of positive results. A light validation is performed by a single economical agent, while strict independence is enabled for deep investigation, Skeptic, and Judge. Provider and model are chosen per role in `config/model-routing.yaml`. Portfolio anti-bias quotas apply only to `mining` runs; in `targeted` the audit is marked as not assessed and nothing is blocked.

The research roles are eight:

- **Discovery** — finds the concrete job, buyer, trigger, and current workaround.
- **Facets** — `demand`, `money`, `competition`, `distribution`, `feasibility`; each has its own result.
- **Skeptic** — independently checks the evidence and looks for contradictions.
- **Judge** — assesses the frozen record and Skeptic findings without adding market facts of its own.

The root agent (Codex) is the orchestrator, not a research role: it assigns jobs, launches real agents, accepts results, and writes the report.

An author does not validate their own hypothesis. A recommendation (`qualified`) requires accepted `money`, `distribution`, `feasibility`, `skeptic`, and `judge` results; `demand` and `competition` are mandatory only when the assessment references them.

## Evidence and outcome

A public price is not confirmed payment. An independent combination of buyer reports, staffing or outsourcing spend, paid competitors, and recurrence can establish existing spend without an invoice. Payment Confidence reflects confidence that money exists around a workflow, while Transaction Evidence Strength records how directly a concrete transaction is observed.

Investigation Priority determines who gets the next research budget and tolerates unknown distribution/feasibility. Commercial Qualification remains a strict gate to a market experiment/MVP. Unknown metrics stay unknown; founder fit does not compensate for failed mandatory conditions. The founder profile passes schema validation and distinguishes confirmed familiarity, confirmed unfamiliarity, unknown, and merely outside confirmed familiarity.

Each run stores the objective, type, stopping condition, authors, frozen packets, attempts, claims, and decisions in `runs/<run-id>/`. A new worker sees a snapshot of all already accepted claims and the candidate's upstream outputs. Raw sources are stored byte-for-byte under `sources/` with a stable `source_id`, MIME type/extension, and SHA; temporary paths are not canonical references. One semantic query with Brave/Tavily fallback counts as one query, while provider attempts and error classes are accounted separately.

The technical record stays in `report.md`, and the primary user deliverable is the Russian `report.pdf` with an embedded Cyrillic font. `finalize` checks Unicode, Markdown/PDF freshness, hashes, rendered PNGs of every page, and the record of actual visual review. A run with no recommended candidates is a valid outcome. Finalization is blocked if a queued assignment remains `rejected` or `cancelled`: a technical refusal must not be passed off as a lack of market data or hidden inside a generic `deferred`.

## Getting started

Read [CODEX_TASK.md](CODEX_TASK.md), [AGENTS.md](AGENTS.md), and the [architecture](docs/architecture.md). Dependencies are pinned in [requirements.txt](requirements.txt). Before research, run `python3 -B scripts/preflight.py`; it checks schemas, runs the offline tests and a synthetic run, but does not launch agents or research itself.

The provider and model for each role are set in `config/model-routing.yaml` (`python3 scripts/routing.py show`),
frozen into the assignment, and cross-checked against the model actually used at `bind`. Run state,
sources, and models are visible on the self-contained page `docs/pipeline-map.html`
(`python3 scripts/pipeline_viewer.py --open`): what was found, which agent pulled which source and why,
and what is left without references.

Assignments are executed by `scripts/worker.py`: it runs the role on the model routed to it, collects
sources, and writes the result, leaving acceptance to the orchestrator. From a phone, a run is started
with `/research` in a Claude Code cloud session.

The local application interface: `python3 scripts/app.py --help`. Its commands: `create-run`, `queue`, `bind`, `accept`, `next`, `status`, `decide`, `cancel`, `validate`, `report`, `pdf-review`, `finalize`. Root uses them together with the real Codex delegation tools (`spawn_agent`, `send_input`, `wait_agent`); a queue entry by itself does not mean an agent was launched. Run artifacts are not committed to the public repository: `runs/` is a private path (`.private-paths`), its content is stored in the data repository.

## License

[Apache License 2.0](LICENSE).
