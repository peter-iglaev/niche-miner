# Specification: demand-first pipeline v2

Status: draft specification, 2026-09-16. It replaces the way candidates are produced in v1 and changes
the meaning of validation; the control plane, data provenance, skeptic, judge, report, and Telegram are preserved.

Read together with `docs/sources-setup.md` (how to connect the sources).

---

## 1. Why change

Seven v1 runs, about a hundred candidates, zero qualified. This is a property of the design, not bad luck:

- **The model invented ideas out of its head.** Everything a model knows has already been described by someone and,
  as a rule, already built by someone. The skeptic found free OSS or a finished product every time.
- **The tool did not match the bar.** The bar was "observed payment for the exact job", the tool was
  web search. Web search returns vendor pages; payment evidence is absent from them by construction.
  Evidence level 2 out of 5 for all twenty candidates of the last run is the ceiling of the tool, not a judgment on the ideas.
- **The direction was inverted.** The flow went from idea to money; it was called "money-first".
- **Nothing accumulated.** Every run started from scratch.

v2 starts from what is **already observed**: what people type into search, what they pay
freelancers for, what they buy on marketplaces, who occupies the results. The idea becomes a consequence of
the data, not its cause.

## 2. Principles (they double as acceptance criteria for any change)

1. **Every signal is observable.** A number from an API with the raw response archived, not a model output.
   The model clusters, interprets, and verifies — but does not measure.
2. **Free by default.** All sources are free tiers and public APIs. Paid ones only as an
   explicitly enabled option in the config, off by default.
3. **DeepSeek wherever tokens flow.** Clustering, interpretation, skeptic, judge — DeepSeek.
   Claude (the orchestrator in a cloud session) only runs commands, executes the MCP bridge, and writes
   the report narrative.
4. **Clarity over flexibility.** Seven stages with descriptive names, each with one input, one output,
   and one folder. Any stage can be rerun separately and its files inspected directly.
5. **Zero candidates is a valid result.** Thresholds live in the config, not in someone's head; they are not
   loosened to fit a plan.
6. **Everything accumulates.** Every cluster ever seen lives in the backlog between runs.
7. **Provenance as in v1.** Raw source responses are archived byte-for-byte, claims reference
   them, the manifest stores hashes, attempts are immutable.

## 3. What is kept, what is replaced

| v1 component | Fate in v2 |
|---|---|
| `scripts/app.py` — manifest, assignments, attempts, acceptance, decisions, report, finalization | **Kept.** New roles and stages are added. |
| `scripts/worker.py` — the DeepSeek executor with search tools | **Kept** for the `interpret`, `skeptic`, `judge` roles. Receives new tools for reading the signal archive. |
| `scripts/search.py`, `scripts/sources.py` — Brave, TED, Wayback, HN | **Kept**, extended with new connectors. |
| `scripts/hypotheses.py` — staging of model-composed atoms | **Replaced** by `scripts/demand.py` (stages 1–5). The `hypotheses.jsonl` format goes away; `clusters.jsonl` replaces it. |
| The `discovery` role — "search and cite" | **Replaced** by the `interpret` role: from the already collected cluster signals, formulate the job, product shape, and price point, with references to raw responses. |
| Roles `money`, `distribution`, `feasibility`, `demand`, `competition` | **Folded away.** Their questions are now covered by signals at stages 3–4. One `feasibility` role remains on request (when the judge asks). |
| Roles `skeptic`, `judge` | **Kept**, with a changed subject: checking numbers and interpretation, not hunting incumbents (incumbents are already in the SERP). |
| `config/scoring.yaml` — payment tiers 0–5 | **Replaced** by the signal card and numeric gates in `config/demand.yaml`. |
| `config/portfolio.yaml` — quotas over 12 industries | **Not applied** in v2 (coverage is set by themes in the config). The file stays for v1 runs. |
| `scripts/report.py`, `scripts/report_pdf.py` | **Kept**, a "cluster card" template is added. |
| `scripts/telegram.py` | **Kept**, a `notify` command is added for interim messages. |
| `scripts/pipeline_viewer.py` | **Kept**, a clusters tab is added. |
| `.claude/commands/research.md` | **Rewritten** for the seven v2 stages. |

## 4. Seven stages

Each stage is a separate command `python3 scripts/demand.py <stage> --run <id>`, one folder
`runs/<id>/<number>-<stage>/`, a `summary.json` with the outcome, and a `telegram` notification on completion.
Stages 1, 3, 4, 5 are deterministic scripts with no model. Stages 2 and 6 are DeepSeek through
`worker.py`. Stage 7 is the v1 control plane.

```
themes from the config
   │
   ▼
[1] expand ────► hundreds of phrases         (autocomplete, no model, free)
   │
   ▼
[2] cluster ───► dozens of jobs              (DeepSeek-chat groups the phrases)
   │
   ▼
[3] screen ────► binary checks               (Upwork, marketplaces, Brave; no model)
   │
   ▼
[4] measure ───► volume, CPC, trend          (Google Ads KP / Bing / Ubersuggest bridge)
   │
   ▼
[5] gate ──────► passed / failed             (thresholds from the config, CPC mode)
   │
   ▼
[6] review ────► interpret → skeptic → judge (DeepSeek, fresh context)
   │
   ▼
[7] decision ──► decide → report → PDF → Telegram
```

### Stage 1 — Expand

**Input:** `config/demand.yaml` → `themes[].seeds` plus seed phrases from the user request.
**What it does:** for each seed — a fan of suggestions from free autocomplete endpoints and three
narrow-niche generation operators:

| Operator | Template | What it catches |
|---|---|---|
| "From A to B" pairs | `{A} to {B} migration`, `{A} to {B}`, `migrate from {A} to {B}` | Narrow niches at the junction of two platforms; the platform list comes from the theme config |
| Product switch | `{X} alternative`, `{X} vs`, `{X} pricing`, `{X} replacement` | People who already pay for X |
| Tool shape | `{job} calculator / checker / validator / generator / converter / template / audit / linter` | The query literally describes a product |

Plus generic modifiers: `free`, `api`, `for {vertical}`, `open source`, `self hosted`, `tool`,
`software`, `saas`.

**Sources:** Google, YouTube, Amazon, Shopify App Store autocomplete; Ubersuggest
`google_suggestions` through the MCP bridge (does not spend the daily quota). Pause 1–2 s between calls.
**Output:** `1-expand/phrases.jsonl` — the phrase, where it came from (seed, operator, source), the raw response
in `sources/`. Deduplication by normalized phrase.
**Cost:** zero; no model involved.

### Stage 2 — Cluster

**Input:** `phrases.jsonl`.
**What it does:** DeepSeek-chat groups phrases into buyer jobs. One cluster = one
job a person wants to get done, with a canonical name, a phrase list, an assumed
product shape (`tool | template | service | content`), and an intent class
(`commercial | tool_shaped | informational | navigational`). A cluster below
`cluster.min_phrases` (default 5) is discarded — one phrase is not a business.
**Dedup against the backlog:** every cluster is checked against `backlog/clusters.jsonl`; a match
receives a `backlog_id` and the status `refresh`, not `new`.
**Splitting.** After merging, every cluster with `2 × min_phrases` or more phrases passes a
cohesion check: one model call with `temperature=0` asks whether this is a single job, and, if needed,
splits the cluster by subject and buyer. Splitting by journey stage is not allowed: "what is X", "X tool",
and "X pricing" are one job. An answer with more than `cluster.split_max_groups` groups, one that lost
more than half the phrases, or a truncated one is discarded entirely — the cluster stays as it was.
Subclusters inherit the parent's intent and product_shape unless the model redefines them.

**Output:** `2-cluster/clusters.jsonl`.
**Cost:** one DeepSeek-chat call per ~200 phrases; a guideline of 20–50k tokens per run.

### Stage 3 — Screen

**Input:** `clusters.jsonl`.
**What it does:** three binary checks, each an observation from a source, not an opinion:

| Check | Source | What is recorded | Passes if |
|---|---|---|---|
| Someone pays a person | Upwork (MCP bridge, filter by `title`); Freelancer.com API as a backup | number of jobs in 90 days, budgets, `proposals_tier`, `client.total_spent` | ≥ `screen.upwork.min_jobs` jobs with budget ≥ `screen.upwork.min_budget` **or** the check is disabled |
| Someone pays for software | Apify Store, WordPress.org, Atlassian, VS Code, Obsidian, Chrome WS | paid listings, installs, update date | at least 1 paid listing with users **or** only free and abandoned ones (a gap) |
| Can it be reached | Brave Search (top 10 for 2–3 cluster phrases) | domains, share of forums/Reddit/YouTube, number of branded SaaS | branded SaaS in the top 5 ≤ `screen.serp.max_brands_top5` |

Thresholds are in the config. The `feasibility_hint` check is technical: whether the product (by product
shape) needs access to the buyer's closed systems — a flag only, not a gate.
**Output:** `3-screen/screened.jsonl` with fields `signals.upwork`, `signals.marketplace`,
`signals.serp`, each referencing a `source_id`.
**Cost:** zero (free tiers); Upwork goes through the bridge.

### Stage 4 — Measure

**Input:** clusters that passed screening.
**What it does:** volume, CPC, advertiser competition, and a 12-month trend for the cluster phrases.
The source is chosen by config and availability, in priority order:

1. **Google Ads API Keyword Planner** — the primary source, free, 15,000 operations/day on Basic.
   If configured, it is used for everything.
2. **Bing Webmaster Tools API** — exact Bing volumes (3–8 % of the market, same proportions). Used
   as a second voice, or as the primary one if Google Ads is not configured.
3. **Ubersuggest via the MCP bridge** — 3 reports per day on the free tier. Spent only on clusters with
   the best screening results; one `keyword_suggestions` report for a parent phrase yields up to ~100–250 keywords
   with volume and CPC. The day's budget is stored in `backlog/quota.json`; a "daily reports limit" response from
   the tool marks the day as exhausted and the request as `deferred_quota` until the next day.

A cluster receives: `volume_sum` (the sum of averages over phrases for the whole window), `volume_recent` (the cluster's
average monthly volume over the last year — the gate judges exactly this), `volume_head`,
`cpc_median` (the middle of top-of-page bids), `cpc_low_median` and `cpc_high_median` (the 20th and 80th
percentiles), `cpc_max`, `competition_median`, `volume_top_share`, `trend_yoy` (year over year,
24 months required), `trend_slope` (slope, for the card only), `months`, `source`.

The monthly series is requested for `measure.months` months (default 24): on one year
seasonality is indistinguishable from a trend, and a series starting at an autumn peak always looks declining.
**Output:** `4-measure/measured.jsonl`.
**Cost:** zero with Google Ads/Bing configured; otherwise the limit is 3 clusters per day.

### Stage 5 — Gate

**Input:** `measured.jsonl`, `config/demand.yaml → mode`.
**What it does:** numeric thresholds that depend on the mode:

| Mode | CPC range | Meaning | Additional |
|---|---|---|---|
| `self_serve` (default) | `cpc.self_serve = [2, 40]` | a tool at $9–49/mo, the buyer arrives on their own | `marketplace` required, services filtered out by phrasing |
| `services` | `cpc.services = [12, 60]` | a service or an expensive product, conversations needed | `upwork` required |
| `any` | unrestricted | exploration | gates are informational |

Common gates: `volume_recent ≥ min_volume_sum` (the cluster's average monthly volume over the last
year, default 120), `intent ∈ {commercial, tool_shaped}`, `trend_yoy ≥ baseline + min_trend_yoy_vs_baseline`.
**Output:** `5-gate/gated.jsonl` with `gate.passed`, `gate.failed_rules[]`, `gate.flags[]`.
The thresholds behind each verdict are printed in the report's machine summary together with the search
baseline and the calibration name: without them the card cannot be re-verified later. A cluster that
fails the gates **does not move on**, but stays in the backlog with a reason.

Flags cut no one off and travel on in the card: `cpc_high` (a bid at the level of a service — check the buyer), `crowded_serp` (the top 5 are taken by brands), `one_phrase` (one phrase carries
the cluster's volume), `trend_unknown` (the series is shorter than two years), `baseline_missing`.

**Every failing threshold is validated on a control sample** — `config/calibration.yaml`,
run `python3 -B scripts/calibrate.py --run calib-<date>`. A rule that rejects obviously live
self-serve niches cannot be a threshold: see [calibration](calibration.md).

### Stage 6 — Review

Three roles on DeepSeek through the existing `worker.py`, for every cluster that passed the gates
(no more than `review.max_clusters`, default 6):

- **`interpret`** (deepseek-chat, a serious stage): from the frozen set of signals and
  phrases, formulate the buyer's job, the product shape, the price point (from the CPC range),
  the unit of payment, and the cheapest decisive test. Every claim references a `source_id`.
  It may make up to 4 search queries to name specific incumbents from the results.
- **`skeptic`** (deepseek-v4-pro, fresh context): checks the **numbers and the interpretation**, it does not
  hunt competitors: branded traffic inside the volume (`{brand} pricing` is not our demand), seasonality,
  CPC inflated by one advertiser, autocomplete artifacts, variant merging in Keyword
  Planner, phrase/job mismatch, a false `tool_shaped`.
- **`judge`** (deepseek-v4-pro, fresh context): issues a v2 `assessment` — `decision_context`
  (`build_test | investigate | drop`), a signal card with ratings 0–4, critical unknowns.
  `build_test` is possible only with all gates passed and no unresolved skeptic objections.

Independence as in v1: different worker_ids; skeptic and judge do not reuse context.
**Cost:** a guideline of 100–400k DeepSeek tokens per cluster; six clusters — up to ~2M
DeepSeek tokens, roughly $1–2 at current prices.

### Stage 7 — Decision and report (`decide`, `report`)

`app.py decide` for each cluster: `build_test` (build a test page),
`investigate`, `dropped`, `duplicate`. Then `report` → the orchestrator's narrative → `report` → PDF →
`pdf-review` → `finalize` → `telegram.py send`. Everything as in v1.

## 5. Cluster card (what the founder reads)

In the report, one cluster gets one card:

- **Job** in one phrase, **product shape**, **price point** (from CPC), **unit of payment**.
- **Demand:** cluster volume/month, number of phrases, the three top phrases with volume, the trend with an arrow.
- **Money:** CPC median/max; Upwork — N jobs, median budget; paid listings — which ones.
- **SERP passability:** the top 5 in one line (brand / forum / video / blog), DA of the best
  non-branded result.
- **Competitors in the results** by name.
- **Skeptic objections** — only unresolved ones.
- **Decision** and **the cheapest test** (a page/tool built for the cluster; or $5–10/day
  exact-match in Google Ads as an option).
- Links to `source_id` for every number.

The machine summary (the cluster table, hashes, worker_id) — a separate section, as in v1.

## 6. Config `config/demand.yaml`

One file, read by all stages; frozen into `runs/<id>/config.json` when the run is
created. An example with every key:

```yaml
schema_version: '2.0'

mode: self_serve            # self_serve | services | any — the business model switch

cpc:                        # CPC ranges in USD per mode
  self_serve: [2, 12]
  services:   [12, 60]

themes:                     # what we research; each run takes one or more themes
  - id: devops-platform
    seeds: [terraform drift, kubernetes upgrade, argocd, github actions migration, on-call]
    platforms: [jenkins, github actions, gitlab ci, circleci, argocd, flux]   # for A→B pairs
    verticals: [fintech, ecommerce, saas]                                       # for "for X"
  - id: ecommerce-tech
    seeds: [shopify migration, product feed, pim, checkout performance]
    platforms: [shopify, woocommerce, magento, bigcommerce, prestashop, wix, squarespace]
    verticals: [fashion, furniture, b2b wholesale]
  - id: ai-agents-mcp
    seeds: [mcp server, llm evaluation, prompt regression, agent tracing, token cost]
    platforms: [openai, anthropic, langchain, litellm]
    verticals: [customer support, legal, sales]

expand:
  sources: [google, youtube, shopify, amazon, ubersuggest_suggestions]
  modifiers: [free, api, tool, software, saas, open source, self hosted, template]
  operators: [pairs, alternatives, tool_shaped]
  pause_seconds: 1.5
  max_phrases_per_seed: 300

cluster:
  model_role: interpret       # role from model-routing.yaml → deepseek-chat
  min_phrases: 5
  max_clusters: 60

screen:
  upwork:  {enabled: true, min_jobs: 3, min_budget: 200, window_days: 90}
  freelancer: {enabled: true}
  marketplaces: {enabled: true, sources: [apify, wordpress, atlassian, vscode, obsidian, chrome]}
  serp:    {enabled: true, provider: brave, phrases_per_cluster: 2, max_brands_top5: 2}

measure:
  providers: [google_ads, bing, ubersuggest]   # priority order; unavailable ones are skipped
  ubersuggest_daily_reports: 3                 # free-tier limit; spent on the best-screened
  country: US
  language: en

gate:
  min_volume_sum: 120                # the cluster's average monthly volume over the last year
  min_trend_yoy_vs_baseline: -0.3    # decline relative to the search baseline, not relative to zero
  allowed_intents: [commercial, tool_shaped]
  cpc_flag_above: 20                 # a flag, not a rejection
  flag_top_share: 0.9                # a flag, not a rejection
  max_services_share: 0.3            # the share of phrases where person-hours are bought

review:
  max_clusters: 6
  interpret_search_queries: 4
  skeptic_search_queries: 6

notify:
  telegram: true
  stages: [expand, cluster, screen, measure, gate, review, report]   # after which stages to notify
  send_pdf: true

limits:                     # as in v1 — frozen into the manifest
  max_active_agents: 8
  max_attempts_per_assignment: 2
```

Switching the mode is one line `mode:`. Changing themes is the `themes` block. Disabling any
source is `enabled: false`, and the stage will honestly record `not_collected`, not zero.

## 7. The MCP bridge (why it exists and how it works)

Ubersuggest and Upwork are available **only as MCP servers inside a Claude Code session**; the DeepSeek
scripts have no access to them, and there is no public REST API. To avoid losing provenance and spending
Claude tokens on reasoning, the bridge is deliberately mechanical:

1. A stage (`expand`, `screen`, `measure`) writes `runs/<id>/bridge/requests.jsonl` — a list of
   calls: `{"id": "...", "tool": "ubersuggest.keyword_suggestions", "args": {...}}`.
2. The orchestrator (Claude) executes them **in exactly that order and with those arguments**, unchanged, and
   stores each response as-is in `runs/<id>/bridge/responses/<id>.json`.
3. `scripts/demand.py bridge-ingest --run <id>` validates the responses against the requests, puts them into
   `sources/` with a sidecar (SHA, length, time), stamps the `source_id`, and continues the stage.

The `/research` command shows the orchestrator exactly this list; quotas (3 Ubersuggest reports per day)
are counted by the script before writing the requests, not after a refusal.

## 8. Backlog between runs

`backlog/clusters.jsonl` — one file in the repository, appended to by every stage:

```json
{"backlog_id": "bl-000123", "key": "terraform-drift-detection", "theme": "devops-platform",
 "first_seen_run": "demand-2026-09-20", "last_seen_run": "demand-2026-10-04",
 "status": "gated_out", "last_gate": {"passed": false, "failed_rules": ["min_volume_sum"]},
 "last_numbers": {"volume_sum": 210, "cpc_median": 7.3, "source": "ubersuggest", "at": "2026-09-20"},
 "decision": null, "notes": []}
```

Statuses: `new → screened → measured → gated_out | gated_in → reviewed → build_test | investigate |
dropped | duplicate`. A new run does not re-measure anything measured less than `backlog.refresh_days`
ago (default 60), and shows in the report what changed for older clusters.

## 9. Models and tokens

| Where | Model | Why | Token guideline per run |
|---|---|---|---|
| Stage 2 | deepseek-chat | phrase clustering | 20–50k |
| Stage 6, `interpret` | deepseek-chat | signal interpretation | 50–150k per cluster |
| Stage 6, `skeptic` | deepseek-v4-pro | number verification | 100–300k per cluster |
| Stage 6, `judge` | deepseek-v4-pro | assessment | 50–150k per cluster |
| Orchestrator | Claude (session) | commands, bridge, narrative | only command output and ~3–4k words of narrative |

Routing lives in `config/model-routing.yaml`, roles `interpret`, `skeptic`, `judge`. Stages 1, 3,
4, 5 spend no tokens at all.

## 10. Launch from Claude Code cloud

As before: `/research <topic or theme id from the config> [--mode services] [--themes a,b]`.
The rewritten `.claude/commands/research.md` walks the orchestrator through the stages:

1. `preflight` (v1) + `demand.py doctor` — which sources are available, which quotas remain.
2. `app.py create-run --scope demand --config config/demand.yaml`.
3. Stages 1–5 by commands; after each one, a `telegram.py notify` with one line:
   "Expand: 1,240 phrases from 14 seeds. Clusters: 37, 9 of them already in the backlog", and so on.
4. Bridge: the orchestrator executes `bridge/requests.jsonl`, then runs `bridge-ingest`.
5. Stage 6 — `worker.py` in batches up to `max_active_agents`.
6. Stage 7 — decisions, report, narrative, PDF, `pdf-review`, `finalize`, `telegram.py send`.
7. Dashboard, commit, push.

Telegram notifications are short, one per stage; the final one carries the PDF. All of it goes through
the existing `scripts/telegram.py`, which gains `notify --run <id> --text "..."`.

## 11. Run layout

```
runs/demand-2026-09-20/
├── manifest.json            # as in v1: hashes, assignments, decisions
├── config.json              # the frozen demand.yaml + limits
├── brief.md
├── 1-expand/   phrases.jsonl, summary.json
├── 2-cluster/  clusters.jsonl, summary.json
├── 3-screen/   screened.jsonl, summary.json
├── 4-measure/  measured.jsonl, summary.json
├── 5-gate/     gated.jsonl, summary.json
├── bridge/     requests.jsonl, responses/
├── sources/    raw responses + sidecar (as in v1)
├── work/       interpret/skeptic/judge assignments (as in v1)
├── candidates/ <cluster>/signals.json, assessment.json, score.json
├── claims.jsonl
├── report.md, report.pdf, report-preview/
└── events.jsonl
```

Every stage folder is readable by eye: `summary.json` states how many items entered, how many came out, and
why the rest fell away.

## 12. Implementation plan

| Stage | What appears | Files |
|---|---|---|
| 1 | Config, schema, the "expand" stage with free autocomplete, backlog | `config/demand.yaml`, `schemas/demand-*.json`, `scripts/demand.py` (expand), `backlog/` |
| 2 | The "cluster" stage on DeepSeek, the `interpret` role in routing | `scripts/demand.py` (cluster), `agents/interpret.md`, `config/model-routing.yaml` |
| 3 | The "screen" stage: Brave SERP composition, marketplace connectors, Freelancer | `scripts/demand.py` (screen), `scripts/connectors/*.py` |
| 4 | MCP bridge: requests/responses/ingest; Ubersuggest and Upwork through it | `scripts/demand.py` (bridge-ingest), the rules in `research.md` |
| 5 | The "measure" stage: Google Ads KP, Bing; provider choice by availability | `scripts/connectors/google_ads.py`, `bing.py` |
| 6 | The "gate" stage and CPC modes | `scripts/demand.py` (gate) |
| 7 | Skeptic and judge v2, `assessment` v2, the signal card | `agents/skeptic.md`, `agents/judge.md`, `schemas/assessment.schema.json` |
| 8 | The report with cluster cards, `telegram.py notify`, dashboard | `scripts/report.py`, `scripts/telegram.py`, `scripts/pipeline_viewer.py` |
| 9 | The `/research` command v2, `doctor`, documentation | `.claude/commands/research.md`, `docs/runbook.md` |

Each stage ends with a run on one theme and a commit. Stage 1 already yields a visible result
(a thousand phrases with provenance); by stage 3 the first honest screening exists without a single token.

## 13. What remains open

- Google Ads API: since September 9, 2026 access is granted per Cloud project, not per developer token;
  the path to Basic level is described in `docs/sources-setup.md`, but it is verified in practice
  at first connection.
- Bing requires a verified site — any founder domain will do.
- Reddit is not connected for now: as of June 2026 the approval queue is 2–4 weeks. Its place in stage 3
  (the pain signal) is reserved; HN Algolia partially covers it.
- Gate thresholds are calibrated not by eye but on a control sample: `docs/calibration.md`.
  The CPC band of the `services` mode is not yet validated by the control — those numbers are still by eye.
- The definition of "branded SaaS in the results" is a heuristic (a product domain with pricing); the skeptic
  reviews contested cases.
