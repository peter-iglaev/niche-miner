# Sourcing and tools — shared protocol

Read this before collecting anything. It applies to every research and review role.

## Tools, in priority order

1. **`scripts/search.py` — machine web search.** The primary way to search the open web.
   Providers are swappable (Brave by default, Tavily as fallback); keys are read from the
   environment or from `.env.local`. The raw provider response and every failed attempt are archived
   in `runs/<run-id>/sources/` and logged in `runs/<run-id>/searches.jsonl`; results are returned as
   `{rank, title, url, snippet, score}` and must still be opened and archived before a claim cites them.
   ```
   python3 scripts/search.py providers                                  # key availability, offline
   python3 scripts/search.py search --run <run-id> --query "..." --count 10 --assignment <assignment-id> --role <role>
   python3 scripts/search.py fetch --run <run-id> --url https://... --assignment <assignment-id> --role <role>
   ```
   Always pass `--assignment` (and `--role`) with your own assignment id. It records who pulled the
   source in `source_usage.jsonl` without changing the source identity or your query budget; without it
   the source shows up on the dashboard as unattributed and nobody can tell why it was fetched.
   Failure behaviour: a missing key marks the provider `capability_unavailable` and the affected
   stage is reported blocked — never replaced by scraping. A 429 or transport failure may switch
   provider once; empty results return `[]` and are not retried with a broader query. The fallback is
   another provider attempt for the same `logical_query_id`, so it consumes one semantic-query budget.
   Rate-limit, transport, HTTP and parse failures are counted separately.
   Reddit and other JavaScript-only hosts answer an automated fetch with an empty shell. The executor
   refuses them outright and marks such search results `openable: false`. They remain valid leads, but
   nothing may be cited from their snippet: record the gap, or find the same fact where it can be opened.

2. **Browser (`cua_repl`).** Use it for search engines and pages the provider cannot reach, for gated
   or JavaScript-only sources, and when you need to read a page as a person would. Search engines and
   many sites answer automated `curl` with anti-bot pages (HTTP 202/403, captcha, JS-only shells). Extract
   text with `tab.playwright.evaluate(() => document.body.innerText)` or `tab.content.export()` instead of
   dumping the accessibility tree: one search page as an AX tree costs thousands of tokens, as text it
   costs hundreds. Keep tabs hidden unless the user asked to watch. Never log in, create an account,
   accept permissions, solve a captcha or contact anyone without explicit authorization in the user request.
3. **`scripts/sources.py`.** Use it for the free, keyless machine APIs below. It stores the raw
   response and a locator sidecar inside the run. It takes the same `--assignment` and `--role` flags.
4. **Direct `curl`.** Only for machine APIs that are not covered by the scripts, and never for
   ordinary web pages.

Search results are a lead, not evidence: provider ranking is not payment proof and a snippet is not
a source. A semantic query counts once against the assignment budget regardless of provider fallback;
opening returned pages does not consume that budget.

## Free keyless sources

- **TED Search API** — `scripts/sources.py --run <run-id> ted --query 'FT="..."'`. Anonymous access to
  published EU procurement notices: buyer, date, notice number, links. This is budget evidence: a
  public body spending money on a job. A tender for the labour/service is *adjacent* spending unless
  the notice is for the exact job or product; it does not prove software budget.
- **Wayback Machine** — `scripts/sources.py --run <run-id> wayback --query <url>`. Confirms which
  version of a page existed on the access date.
- **HN Algolia** — `scripts/sources.py --run <run-id> hn --query "<text>"`. Practitioner discussion.
  Reported experience, never representative prevalence.
- **Google Trends** — via the browser, no account. Query-interest proxy only.

Ubersuggest/Semrush-style keyword tools are secondary and account-gated; use only when the user has
authorized that access, and label the data as query interest, never as payment evidence.

## Every external observation must be archived

Archive the raw response byte-for-byte inside `runs/<run-id>/sources/` through `scripts/sources.py` or
by keeping the browser page snapshot, and reference the resulting `source_id` in the claim. Preserve
the original bytes, SHA-256, content type and extension; PDF and other binary bodies must never be
decoded as UTF-8 or truncated. A claim that points only at `/tmp`, another external path, a paraphrase,
or a search snippet is not acceptable. Record the exact query, provider, geography, language, period,
units, collection date and any access limitation.

## What each kind of evidence does and does not establish

| Observation | Establishes | Does not establish |
| --- | --- | --- |
| Public price page | An offer exists | That anyone paid |
| Tender / procurement notice | A buyer budgeted for that job | Software budget for our product |
| Job advertisement | Hiring intent for the work | That tooling was purchased |
| Review, customer story published by the vendor | A vendor-published account | Independent paid use |
| Review with payment/verified-use contents | Possibly paid use, if the text supports it | Prevalence, recurrence |
| Search volume, CPC | Query and advertiser activity | Payment, willingness to pay |
| Repeated URLs of one buyer or vendor | One origin | Independent corroboration |

Unknown values stay `null` with a reason. Access failures, empty results and inconclusive results are
different outcomes and are recorded as such. One query = one submitted search; opening linked pages
does not consume the query budget.
