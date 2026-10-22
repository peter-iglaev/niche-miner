# Gate calibration

A run that lets no one through looks the same in two very different cases: the market is empty —
and the thresholds cut off the living. These cannot be distinguished from inside a run, so the thresholds are checked
separately, on a sample whose answer is known in advance.

- `config/calibration.yaml` — two samples: `niches` (eighteen niches where people demonstrably pay by card
  for a tool at $9–49/mo) and `negative` (eight that must be screened out, each by its own rule).
- `scripts/calibrate.py` — pushes both through the same code as a run: `google_ads.historical_metrics`
  → `measure.aggregate` → `gate.evaluate`. There is no separate arithmetic, otherwise the wrong instrument gets calibrated.
- `config/calibration-baseline.json` — the search baseline that calibration captures and the trend gate reads.

```
python3 -B scripts/calibrate.py --run calib-<date>          # the thresholds
python3 -B scripts/calibrate.py --run calib-<date> --serp   # the reachability rule as well
```

A rule that rejects live niches cannot be a threshold: it becomes a flag (`flags`)
or disappears. A rule that lets the dead through is worthless. The instrument is sound only when
both sides agree.

## What the first measurement showed (September 21, 2026)

With the thresholds used by the runs `demand-ecommerce-2026-09-16` and `selfserve-devops-ai-2026-09-20`,
**only one niche in ten passed the control**. In other words, the zero candidates in those runs said
nothing about the market.

| The rule was | Rejected live niches | What was actually going on |
|---|---|---|
| `cpc_band self_serve [2, 12]` | 9 of 10, all from above | The "phrase CPC" was taken as the 80th percentile of the bid. On the lower boundary the control gives 2.6–9.6, in the middle 5.5–36.5 |
| `min_trend −0.2` on the 12-month slope | 5 of 10 | The window starts at an autumn peak: the slope measures the November–December dip, not demand |
| `screen.require: [money, …]` | 42 clusters of 60 in the 2026-09-20 run | Marketplace jobs are a trace of a service; a $19/mo product has none by construction |
| `reachable ≤ 2 brands in the top 5` | 7 of 10 | In niches where people pay, the top 5 is taken by vendors. Brand density is a sign of money, not of a ban on entry |
| `min_volume_sum 300` | 1 of 10 | A live niche can be narrow: form backend has 129 queries per month |

Separately: `trace ai agents` from the 2026-09-20 run had `volume_top_share = 0.993` — the entire
"cluster volume" came from one unrelated phrase. The skeptic caught it manually; now the share
is always computed and raises a flag at 0.9 (the control maximum is 0.83).

## What changed

1. **CPC is the middle of top-of-page bids**, not the upper bound; both bounds stay in the card.
   The `self_serve` band is `[2, 40]` per the control range.
2. **The trend is year over year on 24 months**, and relative to the search baseline, not zero. The median decline
   across live niches is **−0.216 per year**: eight of ten decline. An absolute threshold here would again
   measure the environment, not the niche. Rejection goes to whoever is 0.3 worse than the baseline.
3. **Volume is the average month over the last year** (`volume_recent`), not the average over the whole window:
   on 24 months, a growing niche otherwise looks smaller than it is.
4. **`money` and `reachable` no longer cut off** in the self_serve mode. Both signals are collected
   and travel into the card; `reachable` raises the `crowded_serp` flag.
5. **Services are filtered by phrasing, not by the bid.** The bid does not distinguish an ERP implementation ($23
   a click) from self-serve billing ($36). The share of phrases with words like consultant, implementation,
   agency: 0.00–0.08 for the live control niches, 1.00 for ERP implementation. Threshold 0.3.

After the edits: **10 of 10 live niches passed, 5 of 5 dead ones were screened out**, each by its own rule.
`windows phone app development` was held back not by the volume threshold, as expected, but by an unknown
bid — that is, it failed, but the rule itself was not exercised on it.

## Second measurement: the 18 + 8 sample (September 22, 2026)

Ten niches are too few to trust a threshold: `min_volume_sum` stood on one observation. The sample
was extended to eighteen live ones (added: a link shortener, digital signatures, time tracking, a survey
builder, background removal, document parsing, API monitoring, a shared mailbox) and eight
dead ones (custom development, Silverlight, an RSS reader for Symbian).

**18 of 18 live passed, 8 of 8 dead were held back.** No leaks. The threshold margins are thin, though:

| Threshold | Value | Control | Margin |
|---|---|---|---|
| `cpc_band self_serve` | [2, 40] | 2.67–39.35 | almost zero on both sides |
| `min_volume_sum` | 120 | minimum 129 | 9 queries per month |
| `min_trend_yoy_vs_baseline` | −0.3 (threshold −0.516) | minimum −0.509 | 0.007 |

A thin margin means a live niche slightly beyond the boundary will be rejected. That is the price of
thresholds being derived from data rather than assigned: they cannot be widened without new observations — otherwise
they stop separating the dead. The next sample extension will refine the boundaries.

It also confirmed what a run on the old trend gate would look like: the slope over one window
**would have rejected 13 live niches out of 18**.

## When to rerun

- Before every run if the last calibration is older than a month: the search baseline shifts, and the
  trend gate computes relative to it.
- After any edit to a threshold, a metric, or the coupling with the measure stage.
- The control costs one Google Ads query per niche and no money.

The sample needs extending. Ten niches are the minimum at which the direction of the error is visible; for thresholds
like `min_volume_sum`, ten points are too few, and the number 120 stands on one observation (form backend).
