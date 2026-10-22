---
description: Run a v2 demand-first pipeline: expand → cluster → screen → measure → gate → review → report to Telegram
---

Run a v2 pipeline pass for the user request: $ARGUMENTS

You are the orchestrator. Stages 1–5 are deterministic scripts; stages 2 and 6 are DeepSeek through workers.
You only run commands, execute the MCP bridge, read the narrative draft, and look at the PDF.
All commands are `python3 -B scripts/demand.py <command> --run <run-id>`. Specification: `docs/demand-first-spec.md`.

## Procedure

1. **Readiness.** `python3 -B scripts/demand.py doctor`. If `DEEPSEEK_API_KEY` or `BRAVE_API_KEY` is missing —
   stop and say which variable is missing. The other sources are optional: `doctor` shows
   what is available and how many Ubersuggest reports remain today.
2. **Run.** Parse the request: themes (`--themes` by id from `config/demand.yaml`; without them — all themes), mode
   (`--mode self_serve|services|any`; by default from the config), objective. Run id — `demand-<topic>-<date>`.
   ```
   python3 -B scripts/demand.py create --run <id> --themes <a,b> [--mode ...] --objective "..." --authorization "<verbatim from the request>"
   ```
3. **Stages in sequence.** `python3 -B scripts/demand.py auto --run <id>` runs expand → cluster → screen → measure →
   gate → review and sends a line to Telegram after each stage. It stops when the bridge is needed.
4. **MCP bridge.** When `auto` prints the list of requests (or run `bridge --run <id>`), execute each one **with exactly the
   arguments** given in `bridge/requests.jsonl`, using the tool from the `mcp_tool` field
   (`mcp__Ubersuggest__google_suggestions`, `mcp__Ubersuggest__keyword_suggestions`, `mcp__Upwork__upwork__find_jobs`),
   and save the tool's response as-is (JSON) to the file in `response_path` with the Write tool. Do not
   edit or paraphrase anything. If an Upwork request has no `org_uid`, first call
   `mcp__Upwork__upwork__list_accounts` and substitute `org_uid` into all Upwork requests. A tool error is also
   saved as JSON `{"error": "..."}` — that is an honest result. Then:
   ```
   python3 -B scripts/demand.py bridge --run <id> --ingest
   python3 -B scripts/demand.py auto --run <id>
   ```
   The Ubersuggest free quota is 3 reports per day; the measure stage itself schedules no more than what remains,
   marking the rest `deferred_quota` — that is not an error. If the tool returned a "daily reports limit"
   (the quota was spent outside the pipeline), save the error as `{"error": "..."}`: `bridge --ingest` will mark the day
   exhausted in `backlog/quota.json`, return the request to the queue for tomorrow, and will not count it as a failure.
   A repeated `measure` on the same run tomorrow takes the deferred requests first.
   `screen --refresh` rebuilds the signals of all clusters (after a parser or config change);
   without the flag the stage reuses ready verdicts and only computes the ones awaiting the bridge.
5. **Decisions and report.**
   ```
   python3 -B scripts/demand.py decide --run <id> --auto        # decisions from the judge verdicts
   python3 -B scripts/demand.py report --run <id>               # cards + PDF draft
   python3 -B scripts/demand.py narrative --run <id>            # a draft of two sections from DeepSeek
   ```
   Read `runs/<id>/report.md`: the "What we got" and "What to do next" sections are the model's draft.
   Fix it if it contradicts the cards or presents an offer as payment; add no facts beyond the cards.
   If you edited it — run `report` again. Then review **every** PNG in `runs/<id>/report-preview/` and run:
   ```
   python3 -B scripts/demand.py pdf-review --run <id> --author root --reason "<what was checked>"
   python3 -B scripts/demand.py finalize --run <id>
   python3 -B scripts/demand.py deliver --run <id>              # the PDF to Telegram
   ```
6. **Publication.** `python3 -B scripts/demand.py dashboard`, commit the run artifacts and backlog, push.
   If `NICHE_MINER_DATA` is set, `runs/`, `backlog/`, and the dashboards are symlinks into the private data
   repository (`docs/workspace.md`): commit and push them there (`git -C "$NICHE_MINER_DATA" …`), while the
   code repository receives only code changes. The pre-commit hook will reject a commit with private paths
   or owner identifiers; do not bypass it with `--no-verify`.

## Rules

- Zero `build_test` candidates is a normal result. Thresholds live in `config/demand.yaml` and are not loosened to fit a plan.
- The unknown stays unknown: `not_collected`, `pending_bridge`, `unknown` — not zero and not "no demand".
- Do not write your own candidate ratings: DeepSeek interprets, verifies, and decides; you only edit the narrative.
- A technical stage failure — rerun the same stage; stages are idempotent and reuse what was collected.
- Report briefly and in Russian: what was launched, what awaits the bridge, what was accepted.
