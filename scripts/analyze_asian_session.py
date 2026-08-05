"""
Compare USD/JPY behavior during the Asian session vs. after it, using
1-minute OHLC bars produced by parse_xlsx_to_parquet.py.

Session definitions (UTC, matching standard forex session clocks):
  - Asian (Tokyo)        00:00 - 09:00 UTC
  - Post-Asian (London+NY) 09:00 - 24:00 UTC (rest of the trading day)

All timestamps in the source HistData files are fixed Eastern Standard
Time (UTC-5, no DST); parse_xlsx_to_parquet.py already converts to UTC.

Usage:
    python scripts/analyze_asian_session.py [parquet_path]
"""
import sys
import pandas as pd
import numpy as np

PIP = 0.01  # USD/JPY pip

parquet_path = sys.argv[1] if len(sys.argv) > 1 else "analysis/usdjpy_m1.parquet"
df = pd.read_parquet(parquet_path)
df["year"] = df.ts_utc.dt.year
df["date"] = df.ts_utc.dt.date
df["hour"] = df.ts_utc.dt.hour

# Asian session definition: 00:00-09:00 UTC (Tokyo 09:00-18:00 JST)
asian = df[(df.hour >= 0) & (df.hour < 9)]
# Post-Asian = rest of the UTC day: 09:00 -> 24:00 (London + NY)
post = df[(df.hour >= 9)]
# First-hour reaction right after Asian close (09:00-10:00 UTC, early London)
first_hr = df[(df.hour == 9)]
# London session proper 08:00-17:00 UTC (for reference, overlaps last hr of Asian)
# NY session 13:00-22:00 UTC
ny = df[(df.hour >= 13) & (df.hour < 22)]

def session_ohlc(g):
    if g.empty:
        return None
    g = g.sort_values("ts_utc")
    return pd.Series({
        "open": g.open.iloc[0],
        "high": g.high.max(),
        "low": g.low.min(),
        "close": g.close.iloc[-1],
        "n": len(g),
    })

asian_daily = asian.groupby(["year", "date"]).apply(session_ohlc, include_groups=False).dropna()
post_daily = post.groupby(["year", "date"]).apply(session_ohlc, include_groups=False).dropna()
first_hr_daily = first_hr.groupby(["year", "date"]).apply(session_ohlc, include_groups=False).dropna()

asian_daily["range_pips"] = (asian_daily["high"] - asian_daily["low"]) / PIP
asian_daily["net_pips"] = (asian_daily["close"] - asian_daily["open"]) / PIP
post_daily["range_pips"] = (post_daily["high"] - post_daily["low"]) / PIP
post_daily["net_pips"] = (post_daily["close"] - post_daily["open"]) / PIP
first_hr_daily["net_pips"] = (first_hr_daily["close"] - first_hr_daily["open"]) / PIP

# merge asian close -> post open gap too
merged = asian_daily.join(post_daily, lsuffix="_asian", rsuffix="_post", how="inner")
merged["gap_pips"] = (merged["open_post"] - merged["close_asian"]) / PIP  # jump at Asian->London handover
merged["continuation"] = np.sign(merged["net_pips_asian"]) == np.sign(merged["net_pips_post"])
merged = merged[(merged["net_pips_asian"] != 0) & (merged["net_pips_post"] != 0)]

def summarize(label, g):
    print(f"\n=== {label} (n={len(g)} trading days) ===")
    print(f"Asian session (00:00-09:00 UTC) avg range: {g.range_pips_asian.mean():.1f} pips (median {g.range_pips_asian.median():.1f}, std {g.range_pips_asian.std():.1f})")
    print(f"Asian session avg |net move|: {g.net_pips_asian.abs().mean():.1f} pips (mean signed {g.net_pips_asian.mean():+.2f})")
    print(f"Post-Asian (09:00-24:00 UTC, London+NY) avg range: {g.range_pips_post.mean():.1f} pips (median {g.range_pips_post.median():.1f}, std {g.range_pips_post.std():.1f})")
    print(f"Post-Asian avg |net move|: {g.net_pips_post.abs().mean():.1f} pips (mean signed {g.net_pips_post.mean():+.2f})")
    print(f"Avg handover gap (Asian close -> London/post open): {g.gap_pips.mean():+.2f} pips, avg |gap| {g.gap_pips.abs().mean():.2f} pips")
    print(f"Continuation rate (post move same direction as Asian move): {g.continuation.mean()*100:.1f}%")
    corr = g.net_pips_asian.corr(g.net_pips_post)
    print(f"Correlation(Asian net move, Post-Asian net move): {corr:+.3f}")
    print(f"Post-Asian range / Asian range ratio (avg): {(g.range_pips_post / g.range_pips_asian).mean():.2f}x")

for yr in [2016, 2022, 2023]:
    summarize(str(yr), merged.loc[yr])

summarize("ALL YEARS COMBINED (2016, 2022, 2023)", merged)

# first hour reaction stats
fh = first_hr_daily.join(asian_daily[["net_pips"]], lsuffix="_fh", rsuffix="_asian", how="inner")
fh = fh[(fh.net_pips_fh != 0) & (fh.net_pips_asian != 0)]
print(f"\n=== First hour after Asian close (09:00-10:00 UTC) across all years, n={len(fh)} ===")
print(f"Avg |move| in first hour post-Asian: {fh.net_pips_fh.abs().mean():.2f} pips")
print(f"Continuation rate in first hour vs Asian direction: {(np.sign(fh.net_pips_fh)==np.sign(fh.net_pips_asian)).mean()*100:.1f}%")

out_csv = "analysis/asian_vs_post_daily_USDJPY_2016_2022_2023.csv"
merged.to_csv(out_csv)
print(f"\nSaved per-day detail to {out_csv}")
