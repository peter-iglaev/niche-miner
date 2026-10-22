# Connecting demand signal sources (free)

Instructions for the v2 pipeline (`docs/demand-first-spec.md`). Everything below is free tiers and
public APIs. Paid options are mentioned only where no free option exists, and are not required
for operation.

Reliability markers: **[✓]** verified by a live call in September 2026; **[~]** from the official
documentation, no call was made; **[?]** the procedure may have changed — check against the official page
when connecting.

## How long to wait: read before you start

Connect first whatever works today. The waits below are not our timelines but Google's and Microsoft's.

| Source | Own actions | Third-party decision wait | What it gives the pipeline |
|---|---|---|---|
| Ubersuggest MCP | already connected | none; 3 reports per day, reset at UTC | volume and CPC for 3 clusters per day |
| Upwork MCP, Freelancer, marketplaces, autocompletes, HN | nothing or `UPWORK_ORG_UID` | none | money, paid software, reachability |
| Brave Search (SERP) | a key in 5 minutes | none | SERP reachability |
| DeepSeek | top up the balance | none | clustering, agent verification, narrative |
| Telegram | a bot and chat_id, 10 minutes | none | the report and notifications |
| Bing Webmaster | 15–20 minutes, requires your own domain | none; DNS verification can stretch to a day, the meta tag is instant | volume and CPC for all clusters in one pass |
| Google Ads Keyword Planner | 30–60 minutes | **yes**: brand verification and Basic approval — Google promises minutes, but OAuth app verification can take **up to 7 days and more** [?]; a new Google Ads account also sometimes goes "under review" | volume, CPC, monthly trend for all clusters |

Bottom line: Google Ads is the best source, but it is connected "for the future". To get numbers today,
you need Bing or the daily Ubersuggest quota.

---

## 0. Where to put the keys

The scripts read environment variables, and when absent — the `.env.local` files, then `.env` at the repo
root (`scripts/env.py`). `.env.local` never reaches git.

Format:

```
DEEPSEEK_API_KEY=...
BRAVE_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

**For Claude Code cloud sessions** the variables are set once in the environment settings
(claude.ai/code → Environments → your environment → Environment variables), and the allow-listed domains —
in the **Custom** network policy there as well. The full domain list is in section 12. Leave the Setup script
field empty: dependencies are installed by the SessionStart hook (`scripts/install_pkgs.sh`).

Also set `NICHE_MINER_DATA` there — the path to the private data repository (profile, runs,
backlog), see [`workspace.md`](workspace.md).

Check everything at once: `python3 scripts/demand.py doctor` — it will show, per source,
`available / missing key / quota left`. Before `demand.py` exists — `python3 scripts/preflight.py`
and `python3 scripts/search.py providers`.

---

## 1. Already connected (verify it is alive)

| Source | Variable | Check | Status |
|---|---|---|---|
| DeepSeek | `DEEPSEEK_API_KEY` | `python3 scripts/llm.py ping --provider deepseek --model deepseek-chat` | [✓] works |
| Brave Search | `BRAVE_API_KEY` | `python3 scripts/search.py providers` | [✓] works |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | `python3 scripts/telegram.py check` | [✓] keys present |
| Ubersuggest MCP | none (OAuth in the session) | `auth_status` from the Claude Code session | [✓] tier free |
| Upwork MCP | none (OAuth in the session) | `upwork__get_account` from the session | [✓] connected |
| Contra MCP | none (OAuth in the session) | `auth_status` from the session | [✓] connected |

Brave: the free plan is about 2,000 queries per month and 1 query per second [~]; the pipeline
already keeps a 1.05 s interval. That is enough to check the SERP for ~600 clusters per
month at 2–3 phrases each.

---

## 2. Autocomplete (demand, phrasings) — no keys

Nothing to configure. Endpoints that respond without authorization [✓]:

| Source | Call |
|---|---|
| Google | `https://suggestqueries.google.com/complete/search?client=firefox&hl=en&gl=us&q=<phrase>` |
| YouTube | the same URL with `&ds=yt` |
| Amazon | `https://completion.amazon.com/api/2017/suggestions?mid=ATVPDKIKX0DER&alias=aps&prefix=<phrase>` |
| Shopify App Store | `https://apps.shopify.com/search/autocomplete?q=<phrase>` |

Rules: pause 1–2 seconds between calls; on a 429 — pause 30 s and retry once. Suggestions
are personalized by region (`gl`), so the region is fixed in the config and lands in the
source sidecar. These endpoints give no numbers — only phrasings.

Ubersuggest `google_suggestions` (through the MCP bridge) expands ~60 modifiers per keyword and
**does not spend the free account's daily quota** [✓]. Used as the fifth expansion source.

---

## 3. Ubersuggest MCP (numbers for the finalists)

**What it gives:** volume, CPC, advertiser competition, a monthly series; SERP analysis with domain DA.
**Already connected** to the Claude Code session through a connector. Account: `free` [✓].

**Free limits** [✓ per server responses and the help center]: **3 reports per day** — a quota shared with
the web app; `keyword_suggestions`, `match_keywords`, `keyword_overview` count as reports.
`serp_analysis` has a separate counter of "10 metric updates per month"; cached responses
are free. `google_suggestions` and `location_suggest` do not spend the quota.

**How to spend the 3 reports:** only at the measure stage, only on clusters with the best screening results, and
only `keyword_suggestions` on a parent phrase — one such report returns up to ~250
keywords with volume and CPC [✓]. `keyword_overview` on a single keyword is wasteful.

**Access from scripts:** there is no public REST API. It works only through the MCP bridge (specification, §7):
the script writes `bridge/requests.jsonl`, the orchestrator executes it, `bridge-ingest` archives it.

**Quota accounting:** `backlog/quota.json` — how many reports the pipeline scheduled today. The quota is shared with
the web app, so the ledger can lag reality: if the tool responds
"You have reached the daily reports limit", `bridge --ingest` marks the day exhausted
(`"exhausted": true`), and the requests get the status `deferred_quota` and move to the next day.
The quota resets on the UTC calendar day.

**Paid (not required):** Individual — 150 reports per day, ~$29/mo or ~$290 one-off [~].

---

## 4. Google Ads API → Keyword Planner (the primary volume source, free)

**What it gives:** `avg_monthly_searches` with a monthly breakdown for 12 months, the
`low/high_top_of_page_bid` bids (this is the CPC), and advertiser `competition`. This is the data that
Ubersuggest, Keywords Everywhere, and DataForSEO resell.

**What it costs [✓ per Google documentation]:** the API itself is free ("There is no charge for using the
Google Ads API"), no Cloud project billing needed, and a Google Ads account works without an
attached card. **But:** Google returns exact volumes only to accounts with active advertising; without
spend, `avg_monthly_searches` arrives in buckets (10, 100, 1K–10K, 10K–100K) [~ per community reports;
Google publishes no official threshold]. The `low/high_top_of_page_bid` bids are returned either way, and they are exactly what the self_serve gate needs. If the buckets get in the way, Bing Webmaster gives exact volumes for free
(section 5); paying for ads is the last resort.

**Access scheme since 10.09.2026 [✓ per developers.google.com]:** a developer token is no longer issued and
is ignored by the server; the access level is tied to the **Google Cloud project** from which the
OAuth credentials are issued. A manager account (MCC) is not needed. Applications are submitted on the page
`console.cloud.google.com/google/ads-apis/overview`. For the **Basic** level, brand
verification of the project is mandatory; the decision is automatic, minutes. `KeywordPlanIdeaService` (what the
pipeline needs) is closed at the Explorer level — Basic is required.

**Steps (30–60 minutes, all free):**

1. **A Google Ads account.** https://ads.google.com → create an account **without a campaign** (in the wizard,
   "skip campaign creation" / expert mode). Write down a Customer ID of the form `123-456-7890`.
   Billing details are not needed until advertising is launched.
2. **A Google Cloud project.** https://console.cloud.google.com → new project → APIs & Services →
   Library → **Google Ads API** → Enable. Project billing does not need to be enabled.
3. **OAuth consent screen (this is the brand verification).** APIs & Services → OAuth consent
   screen: User type **External**; on the Branding tab fill in the app name, support email,
   logo, and authorized domain (any domain of yours); save; on the OAuth Overview page click
   **Get started** — verification takes a few minutes; after success — **Publish branding** and switch
   Publishing status to **In production**. Without "In production", a test user's refresh token
   lives 7 days and the pipeline will start failing after a week.
4. **An OAuth client.** APIs & Services → Credentials → Create credentials → OAuth client ID → type
   **Desktop app** → download the JSON (it contains `client_id` and `client_secret`).
5. **The Basic access level.** `console.cloud.google.com/google/ads-apis/overview` → section
   **Upgrade access level** → Basic. Fill in the questionnaire (usage: keyword research for
   your own project, no management of other accounts). The answer usually arrives within an hour.
6. **Refresh token.** Once, on your own machine with a browser:
   ```
   pip install google-ads
   python3 scripts/google_ads_oauth.py ~/Downloads/client_secret_*.json
   ```
   The script detects the client type itself, raises a local server on a suitable address, opens the
   browser, asks for access to the scope `https://www.googleapis.com/auth/adwords`, and prints ready-made
   lines for `.env.local`. On the "Google hasn't verified this app" screen — Advanced → Go to app.

   Two places where this breaks, and what to do:
   - **`redirect_uri_mismatch`.** The client was created as a Web application, not a Desktop app. A web client's
     redirect URI must match letter for letter. Either create a new Desktop app client, or add
     `http://localhost:8080/` to Authorized redirect URIs — the script will pick it up itself.
   - **`pip` and `python` are different interpreters** (a venv without pip, pyenv, uv). Install and run
     with the same one: `python3 -m pip install google-ads`, then the same `python3`. Check:
     `python3 -c "import sys; print(sys.executable)"` and `pip -V` must show one path.
7. **Variables** in `.env.local` (the file is in .gitignore and never reaches the repository):
   ```
   GOOGLE_ADS_CLIENT_ID=...
   GOOGLE_ADS_CLIENT_SECRET=...
   GOOGLE_ADS_REFRESH_TOKEN=...
   GOOGLE_ADS_CUSTOMER_ID=1234567890          # without hyphens
   GOOGLE_ADS_LOGIN_CUSTOMER_ID=              # empty; fill in only if there is a manager account
   GOOGLE_ADS_DEVELOPER_TOKEN=                # empty; the old scheme, ignored by the server
   ```
   In a Claude Code cloud session the same four keys are set in the environment settings
   (Environment → variables) — the connector reads both `.env.local` and process variables.
8. **Verification.** The `google-ads` library is in `requirements.txt`; the cloud session installs it
   itself via the SessionStart hook. Run `python3 -B scripts/demand.py doctor` — in the `google_ads` block
   it should say `"available": true`. The real call will be made by the `measure` stage: it takes Google Ads
   as the first provider, and the Ubersuggest bridge is no longer needed.

**Where to store keys for cloud runs:** claude.ai/code → Environments → the needed environment →
Environment variables. The session container is temporary; `.env.local` will not survive a restart there.
Environment variables are picked up when a new session starts. The environment's network policy must include
`googleads.googleapis.com` and `oauth2.googleapis.com` (section 12).

**Quota:** Basic — 15,000 operations per day [✓]. Enough for thousands of phrases per day.
**Pitfalls:** Keyword Planner merges close phrase variants and rounds; narrow queries often
return 10 or 0 — that is not "no demand" but grouping. That is why the measure stage sums over the
cluster, not a single phrase. A `DEVELOPER_TOKEN_NOT_APPROVED`/`PERMISSION_DENIED` error
at the Test or Explorer level means the Basic application has not been approved yet.

---

## 5. Bing Webmaster Tools → Keyword Research (exact Bing volumes, free)

**What it gives:** exact (not bucketed) Bing search volumes, related keywords. Bing is 3–8 % of the market, but
the proportions between phrases are the same, and in the long tail the data is not merged as in Google.
A good second voice; the primary one if Google Ads is not configured.

**Steps:**

1. https://www.bing.com/webmasters → sign in with a Microsoft account.
2. **Add and verify any site of yours** (one domain is needed; verification is an XML file,
   a meta tag, or a DNS record). Without a verified site no API key is issued [~].
3. Settings → **API access** → generate an API key.
4. Variables:
   ```
   BING_WEBMASTER_API_KEY=...
   BING_WEBMASTER_SITE_URL=https://your-domain.tld
   ```
5. Check: `python3 -B scripts/demand.py doctor` (the `bing` block: `"available": true`). The `measure` stage calls
   `GetKeyword` / `GetRelatedKeywords` over REST (`https://ssl.bing.com/webmaster/api.svc/json/`)
   [~]. The legacy SOAP/POX was switched off on 31.08.2026 — use JSON/REST only.

**Quota:** not officially published; practically — hundreds of calls per day without problems [?].
Documentation: https://learn.microsoft.com/en-us/bingwebmaster/

---

## 6. Upwork MCP (someone pays a person for this job)

**What it gives [✓]:** `find_jobs` returns jobs with a budget (fixed) or a rate (hourly),
`proposals_tier` (the number of proposals: "Fewer than 5" … "50+"), and per client — `total_spent`,
`total_posted_jobs`, rating; skills, duration. This is the most direct observable signal of
willingness to pay and recurrence.

**Connection:** the Upwork connector in claude.ai (Settings → Connectors → Upwork, OAuth 2.1).
Already connected to this session. An Upwork account is needed (a freelancer profile works).

**Variable:** `UPWORK_ORG_UID=<org_uid from list_accounts>` — the account identifier (not a secret), so the bridge
substitutes it into requests itself. To learn it: call `mcp__Upwork__upwork__list_accounts` in the session.

**How to use it in the pipeline:** only through the MCP bridge, filter by `title` (not `query` — it
performs semantic expansion and adds noise), `job_type: fixed`, `budget_min` from the config,
`sort: recency`, a 90-day window. The script counts the jobs and the median budget.

**Limitations:** the direct GraphQL API is by application, "personal use", a low approval rate [~];
scraping is forbidden by ToS. Through MCP — moderate volumes (dozens of calls per run); this is research,
not bulk export.

**Keyless backup [✓]:** Freelancer.com —
`GET https://www.freelancer.com/api/projects/0.1/projects/active/?query=<phrase>&limit=50&job_details=true&compact=true`
returns budgets and `bid_stats` without authorization. Noisier and cheaper on budgets; used as
a second voice. No variables needed.

**Contra MCP** — already connected; `list_job_feed`, `list_services` give productized-service prices.
A small market, an additional source.

---

## 7. Marketplaces (does anyone pay for software, how many competitors) — no keys

All endpoints respond without authorization [✓]:

| Marketplace | Call | Fields |
|---|---|---|
| Apify Store | `GET https://api.apify.com/v2/store?search=<phrase>&limit=50` | `stats.totalUsers`, `totalUsers30Days`, `totalRuns`, `pricingInfos` |
| WordPress.org | `GET https://api.wordpress.org/plugins/info/1.2/?action=query_plugins&request[search]=<phrase>&request[per_page]=50` | `active_installs`, `downloaded`, `rating`, `num_ratings`, `last_updated` |
| Atlassian | `GET https://marketplace.atlassian.com/rest/2/addons?text=<phrase>&hosting=cloud` | `distribution.totalInstalls`, `reviews.count`, price |
| VS Code | `POST https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery` (a JSON body with `filters`) | `statistics.install` — the endpoint is undocumented |
| Obsidian | `GET https://raw.githubusercontent.com/obsidianmd/obsidian-releases/HEAD/community-plugin-stats.json` | downloads per version, all plugins in one file |
| Chrome Web Store | the extension page HTML, `grep -oE '[0-9,]+ users'` | the number of users; search returns ~10 results without pagination |
| Shopify App Store | `https://apps.shopify.com/search?q=<phrase>` | rating and review count; it does not publish installs |
| Smithery (the MCP registry) | `GET https://registry.smithery.ai/servers?q=<phrase>` | `useCount`, `verified` |

`APIFY_TOKEN` is not needed for reading the Store; if one appears — limits can be raised [~].
The trap everywhere: installs are a signal of **satisfied** demand. A gap = many suggestions in
autocomplete + few, old, or low-rated results in the marketplace.

---

## 8. Hacker News Algolia (the practitioners' voice) — no key

`GET https://hn.algolia.com/api/v1/search?query=<phrase>&tags=comment&hitsPerPage=100&numericFilters=created_at_i><unix>`
[✓]. ~10,000 queries per hour per IP, up to 1,000 results per query. Query patterns:
`"is there a tool" <topic>`, `"I would pay for" <topic>`, `"Ask HN: what do you use for"`,
`"Show HN" alternative to <X>`. Already used in `scripts/sources.py`.

---

## 9. GitHub (unmet feature requests in popular OSS)

`GET https://api.github.com/search/issues?q=repo:<owner>/<repo> is:issue is:open "feature request"&sort=reactions-%2B1&order=desc`.
Without a token — 10 queries per minute and often 403 from a datacenter [✓ observed in runs]; with a token —
30 per minute.

1. https://github.com/settings/tokens → a Fine-grained token, public repositories only, no
   extra permissions.
2. `GITHUB_TOKEN=github_pat_...`

Plus, without a key: `https://api.npmjs.org/downloads/point/last-month/<package>` — the ecosystem's scale [✓].

---

## 10. Triggers: procurement and regulation — no keys

| Source | Call | What it gives |
|---|---|---|
| TED (EU procurement) | `POST https://api.ted.europa.eu/v3/notices/search`, an expert query `FT~"<phrase>" AND PD>=20260101` | budgets and contract recurrence; already in `scripts/sources.py` [✓] |
| Federal Register | `GET https://www.federalregister.gov/api/v1/documents.json?conditions[term]=<phrase>` | US regulatory documents with dates [✓] |
| endoflife.date | `GET https://endoflife.date/api/v1/products/<product>` | end-of-support dates — waves of "migrate / alternative" [✓] |
| SAM.gov (optional) | a free key, 10 queries per day on a personal account [~] | US procurement; low priority |

---

## 11. What we deliberately do not connect now

- **Reddit** — since June 2026 access approval is mandatory, the queue is 2–4 weeks, commercial
  use under contract [~]. Its place at the screening stage is reserved; once approved —
  `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, PRAW, 100 queries per minute.
- **G2 / Capterra / AlternativeTo** — only scraping through an anti-bot; it violates the rule
  "no scraping of search engines and protected pages".
- **Google Trends** — the official API is in closed alpha, `pytrends` is archived; forks
  catch 429s. The trend is taken from the Keyword Planner monthly series.
- **Semrush / Ahrefs** — MCP only on paid plans from $129–199 per month.
- **Serper.dev** — 2,500 free SERP queries (they show Google ad blocks, which
  Brave does not). An option: `SERPER_API_KEY=...`, provider `serper` in `screen.serp.provider`.
  Not required.
- **Keywords Everywhere** (~$84/yr) and **DataForSEO** (a $50 deposit) — paid replacements for the Google
  Ads API if the Google setup fails. In the config as providers `keywords_everywhere`,
  `dataforseo`, disabled.

---

## 12. Domains for the cloud environment's network policy

In claude.ai/code → Environments → Network: **Custom** → add:

```
api.deepseek.com
api.search.brave.com
api.telegram.org
suggestqueries.google.com
completion.amazon.com
apps.shopify.com
hn.algolia.com
api.apify.com
api.wordpress.org
marketplace.atlassian.com
marketplace.visualstudio.com
raw.githubusercontent.com
api.github.com
api.npmjs.org
chromewebstore.google.com
registry.smithery.ai
api.ted.europa.eu
www.federalregister.gov
endoflife.date
archive.org
www.freelancer.com
googleads.googleapis.com
oauth2.googleapis.com
ssl.bing.com
google.serper.dev
```

The last four are needed only with Google Ads, Bing, and Serper configured. MCP servers
(Ubersuggest, Upwork, Contra) go through claude.ai connectors and are not part of this list.

---

## 13. Connection order (recommended)

1. Do nothing — the expand and screen stages (marketplaces, Brave, Freelancer, HN) and
   the MCP bridge work with what already exists. The first run is possible right away; numbers — only through
   3 Ubersuggest reports per day.
2. One evening — the **Google Ads API** (section 4). It removes the 3-clusters-per-day limit.
3. Fifteen minutes — **Bing Webmaster** (section 5), if you have your own domain.
4. Two minutes — a **GitHub token** (section 9).
5. When approved — Reddit.

After each step: `python3 scripts/demand.py doctor`.
