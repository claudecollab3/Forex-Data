# USD/JPY: Hunting for a Clean 10-20 Pip Directional Window

**Data:** HistData.com 1-minute USD/JPY bars, 2016 / 2022 / 2023 (777+ trading days)
**Method:** `scripts/find_clean_moves.py`

## Part 1 — Alternate Asian session windows

Re-ran the Asian-vs-post-Asian comparison from the earlier report (`asian_session_behavior_USDJPY.md`, which used 00:00–09:00 UTC) with two other common "Asian session" definitions:

| Window definition | Asian avg range | Asian avg \|net\| | Post-Asian avg range | Post-Asian avg \|net\| | Range ratio | Continuation rate | Correlation |
|---|---|---|---|---|---|---|---|
| **00:00–09:00 UTC** (Tokyo, original) | 77.2 pips | 37.4 pips | 97.6 pips | 49.6 pips | 1.56x | 52.6% | +0.003 |
| **23:00–08:00 UTC** (Tokyo, alt convention) | 73.7 pips | 35.2 pips | 85.4 pips | 42.7 pips | 1.38x | 50.5% | +0.009 |
| **22:00–08:00 UTC** (Sydney+Tokyo combined) | 75.4 pips | 36.1 pips | 98.6 pips | 49.9 pips | 1.56x | 51.1% | −0.008 |

**Conclusion is unchanged regardless of which Asian window you use:** post-Asian (London/NY) always moves 1.4–1.6x more than the Asian session, and the correlation between the Asian session's direction and what follows stays at ~0 (all three definitions land between −0.008 and +0.009 — noise). Widening the window to include Sydney doesn't create a directional edge either.

## Part 2 — Is there an hour where price moves 10-20 pips cleanly (without much back-and-forth)?

**Short answer: not really — no hour of the day is "clean."** Every window tested is dominated by chop, not trend.

### Method

For every (start hour, duration) combination across the full 24-hour UTC day, computed per trading day:

- **Net move** = close − open of the window (pips)
- **Efficiency Ratio (ER)** = `|net move| / (sum of all 1-minute price changes in the window)` — this is Kaufman's Efficiency Ratio. **ER = 1.0** means price walked in a straight line with zero retracement. **ER → 0** means it thrashed back and forth and barely netted out. This is the direct, quantitative measure of "back and forth."
- **MAE (max adverse excursion)** = the worst retracement against the eventual direction, in pips — i.e., how much heat you'd sit through before/while the move plays out.

### Result: efficiency caps out around 0.13-0.15 for every window

| Duration | Avg ER across all start hours |
|---|---|
| 1 hour | 0.129 |
| 2 hours | 0.096 |
| 3 hours | 0.081 |
| 4 hours | 0.072 |

**Efficiency drops the longer the window runs** — the longer price is "in play," the more of its movement is back-and-forth rather than net progress. There's no duration or start time where ER gets anywhere close to what you'd call "clean" (ER ≥ ~0.4-0.5 would start to look directional; nothing here comes close).

### Best available candidate (1-hour windows, ranked by ER, restricted to windows whose average move lands near 10-20 pips)

| Start (UTC) | End (UTC) | Avg \|net move\| | Efficiency Ratio | Avg MAE | % of days landing in 10-20 pip band |
|---|---|---|---|---|---|
| **13:00** | **14:00** | **20.0 pips** | **0.147** | **8.5 pips** | 25% |
| 00:00 | 01:00 | 12.4 pips | 0.141 | 5.5 pips | 27% |
| 18:00 | 19:00 | 10.1 pips | 0.139 | 4.3 pips | 21% |
| 23:00 | 00:00 | 8.9 pips | 0.134 | 3.8 pips | 20% |
| 08:00 | 09:00 | 14.7 pips | 0.132 | 7.0 pips | 28% |

**13:00–14:00 UTC (8:00–9:00 AM New York) is the standout** — it's the hour immediately before the NYSE cash open, when most high-impact US data releases (CPI, NFP, retail sales, etc.) land at 8:30 AM ET. It has both the best efficiency ratio of any window *and* an average move that sits right in your 10-20 pip target.

**But read the MAE column honestly:** on the average 13:00-14:00 UTC day, price still retraces ~8.5 pips against the eventual direction (i.e., about 43% of the final move) before/while netting out ~20 pips. "Low chop" here is relative — it's the best of a bad field, not a clean staircase. And it only lands in the 10-20 pip band on 25% of days; the rest of the time it's bigger, smaller, or doesn't net out cleanly at all.

### Practical takeaway

- If you need one hour to watch for a directional 10-20 pip move with comparatively less back-and-forth than other times of day, **13:00-14:00 UTC (pre-NY-open / US data release hour)** is the best-supported candidate in this data, not the Asian session or the London open.
- There is no hour in the 24-hour cycle — Asian, London, or NY — where 1-minute USD/JPY price action is genuinely "clean." Efficiency ratios of 0.13-0.15 mean roughly 85-87% of total up/down travel is retracement, not net progress, everywhere. Any strategy targeting a 10-20 pip move should size stops around the ~8 pip average MAE for that hour, not assume a straight run.
- Multi-hour windows are *worse* for this purpose (efficiency keeps falling with duration) — if you want a "clean" move, shorter is better, not longer.

## Reproduce

```bash
python scripts/find_clean_moves.py analysis/usdjpy_m1.parquet
```

Full scan of all 96 (start hour × duration) combinations is in [`window_scan_all_hours_USDJPY_2016_2022_2023.csv`](./window_scan_all_hours_USDJPY_2016_2022_2023.csv).
