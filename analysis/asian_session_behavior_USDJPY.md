# USD/JPY: Market Behavior After the Asian Session

**Data:** HistData.com 1-minute USD/JPY bars, years 2016, 2022, 2023 (777 trading days total)
**Method:** `scripts/parse_xlsx_to_parquet.py` + `scripts/analyze_asian_session.py`

## Session definitions (UTC)

| Session | Window (UTC) |
|---|---|
| Asian (Tokyo) | 00:00 – 09:00 |
| Post-Asian (London + New York) | 09:00 – 24:00 (rest of the day) |

Source timestamps are fixed Eastern Standard Time (UTC-5, no DST adjustment, per HistData's format) and are converted to UTC before session bucketing.

## Headline numbers (777 days, 2016 + 2022 + 2023 combined)

| Metric | Asian session | Post-Asian (London+NY) |
|---|---|---|
| Avg range (high–low) | **77.2 pips** (median 64.5) | **97.6 pips** (median 85.1) |
| Avg absolute net move (\|close−open\|) | **37.4 pips** | **49.6 pips** |
| Mean signed net move | −1.3 pips | +2.0 pips |

- **The post-Asian session (London + NY) moves about 1.3–1.7x more than the Asian session** — average range 97.6 pips vs. 77.2 pips; average net move 49.6 pips vs. 37.4 pips. This holds in every year tested (2016: 1.40x, 2022: 1.60x, 2023: 1.69x).
- **Handover gap is negligible.** The jump between the Asian session's close and the post-Asian open averages only ±0.1–0.5 pips — there's essentially no "gap," the price flows continuously into London.
- **First hour after Asian close (09:00–10:00 UTC):** average absolute move ≈ **12.1 pips**.

## Does the market continue the Asian move, or reverse it?

| Year | Continuation rate | Correlation (Asian net move vs. post-Asian net move) |
|---|---|---|
| 2016 | 52.3% | −0.026 |
| 2022 | 54.2% | +0.064 |
| 2023 | 51.4% | −0.046 |
| **All years** | **52.6%** | **+0.003** |

**Bottom line: there is essentially no statistical edge from the Asian session's direction.** The correlation between the Asian session's net move and the following London/NY move is ~0 (0.003), and the "continuation rate" (52.6%) is indistinguishable from a coin flip. Whichever way USD/JPY drifts during Tokyo hours tells you almost nothing about which way it will go afterward — a classic finding behind "Asian range breakout" strategies, which trade the *breakout of the Asian range* rather than betting on directional continuation of the Asian move itself.

## Practical takeaways

1. **Expect the real move after Tokyo closes.** London/NY session ranges run ~25-70% wider than the Asian session across all three years — the "action" for USD/JPY is concentrated after 09:00 UTC.
2. **Don't extrapolate direction from the Asian session.** Average move size is a reasonable expectation to plan around, but which way it breaks is close to random relative to what happened overnight.
3. **Numbers above are averages across an entire year** (including quiet holiday days and volatile CB-event days) — expect the standard deviation to be roughly as large as the mean (std ≈ 55-60 pips vs. mean ≈ 77-98 pips), i.e. individual days vary a lot.

## Reproduce

```bash
pip install pandas numpy pyarrow
python scripts/parse_xlsx_to_parquet.py \
    2016=data/DAT_XLSX_USDJPY_M1_2016.xlsx \
    2022=data/DAT_XLSX_USDJPY_M1_2022.xlsx \
    2023=data/DAT_XLSX_USDJPY_M1_2023.xlsx \
    -o analysis/usdjpy_m1.parquet
python scripts/analyze_asian_session.py analysis/usdjpy_m1.parquet
```

Full per-day breakdown (range, net move, gap, continuation flag for every one of the 777 trading days) is in [`asian_vs_post_daily_USDJPY_2016_2022_2023.csv`](./asian_vs_post_daily_USDJPY_2016_2022_2023.csv).
