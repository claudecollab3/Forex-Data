"""
Two analyses on USD/JPY 1-minute bars:

1. Re-run the Asian-session vs. post-Asian comparison using an alternate
   Asian session window (Sydney+Tokyo combined: 22:00-08:00 UTC, and
   Tokyo-only alt convention: 23:00-08:00 UTC) instead of the 00:00-09:00 UTC
   window used previously.

2. "Clean move" hunt: scan every (start hour, duration) window of the day
   and rank by how often it produces a 10-20 pip directional move with
   LOW back-and-forth. Chop is measured with Kaufman's Efficiency Ratio:

        ER = |close_end - open_start| / sum(|price[i] - price[i-1]|)

   ER = 1.0 means the price moved in a straight line (no retracement at
   all). ER near 0 means it round-tripped a lot to net out to ~0. We also
   track "max adverse excursion" (MAE): the worst retracement against the
   eventual direction, in pips, so you can see how much heat a trade in
   that window would have taken before it worked.

Usage:
    python scripts/find_clean_moves.py [parquet_path]
"""
import sys
import pandas as pd
import numpy as np

PIP = 0.01

parquet_path = sys.argv[1] if len(sys.argv) > 1 else "analysis/usdjpy_m1.parquet"
df = pd.read_parquet(parquet_path)
df["date"] = df.ts_utc.dt.date
df["hour"] = df.ts_utc.dt.hour
df["year"] = df.ts_utc.dt.year
df = df.sort_values("ts_utc").reset_index(drop=True)

# "Trading day" bucket: forex day runs roughly 22:00 UTC -> 22:00 UTC (Sunday
# open). For windows that wrap past midnight UTC we bucket by the date the
# window STARTS on.

def session_stats(g):
    """g: 1-min bars sorted by time, columns open/high/low/close."""
    if len(g) < 2:
        return None
    g = g.sort_values("ts_utc")
    o = g.open.iloc[0]
    c = g.close.iloc[-1]
    closes = g.close.values
    path_len = np.sum(np.abs(np.diff(closes))) + abs(closes[0] - o)
    net = c - o
    er = abs(net) / path_len if path_len > 0 else np.nan
    # max adverse excursion: worst move against the eventual direction, from open
    if net >= 0:
        mae = o - g.low.min()  # how far it dipped below open before/while net'ing up
    else:
        mae = g.high.max() - o
    return pd.Series({
        "open": o, "close": c, "high": g.high.max(), "low": g.low.min(),
        "net_pips": net / PIP, "range_pips": (g.high.max() - g.low.min()) / PIP,
        "mae_pips": mae / PIP, "er": er, "n": len(g),
    })


def window_mask(hour_col, start, dur):
    """Handles windows that wrap past midnight (e.g. start=22, dur=10 -> 22..24,0..8)."""
    end = (start + dur) % 24
    if start + dur <= 24:
        return (hour_col >= start) & (hour_col < start + dur)
    else:
        return (hour_col >= start) | (hour_col < end)


def daily_window(df, start, dur):
    """Bucket 1-min bars into per-day instances of the [start, start+dur) UTC
    window, using the UTC date the window STARTS on as the bucket key (so a
    window that wraps past midnight, e.g. 22:00-08:00, stays one instance)."""
    mask = window_mask(df["hour"], start, dur)
    sub = df[mask].copy()
    if sub.empty:
        return pd.DataFrame()
    bucket_date = sub["ts_utc"].dt.date.copy()
    if start + dur > 24:
        # rows with hour < wrap-point belong to the window that STARTED the previous
        # calendar date
        wrapped = sub["hour"] < (start + dur) % 24
        bucket_date = np.where(wrapped, (sub["ts_utc"] - pd.Timedelta(days=1)).dt.date, bucket_date)
    sub["bucket_date"] = bucket_date
    out = sub.groupby("bucket_date").apply(session_stats, include_groups=False)
    return out.dropna()


# ---------------------------------------------------------------------------
# PART 1: Alternate Asian session windows
# ---------------------------------------------------------------------------
print("=" * 70)
print("PART 1: Asian session re-defined, vs. post-Asian (rest of day)")
print("=" * 70)

ALT_WINDOWS = {
    "Tokyo only, alt convention 23:00-08:00 UTC": (23, 9),
    "Sydney+Tokyo combined 22:00-08:00 UTC": (22, 10),
}

for label, (start, dur) in ALT_WINDOWS.items():
    asian = daily_window(df, start, dur)
    asian.columns = [f"{c}_asian" for c in asian.columns]
    post_start = (start + dur) % 24
    post_dur = 24 - dur
    post = daily_window(df, post_start, post_dur)
    post.columns = [f"{c}_post" for c in post.columns]
    m = asian.join(post, how="inner")
    m = m[(m.net_pips_asian != 0) & (m.net_pips_post != 0)]
    m["continuation"] = np.sign(m.net_pips_asian) == np.sign(m.net_pips_post)
    print(f"\n--- {label} (n={len(m)} days) ---")
    print(f"Asian avg range: {m.range_pips_asian.mean():.1f} pips | avg |net|: {m.net_pips_asian.abs().mean():.1f} pips")
    print(f"Post-Asian avg range: {m.range_pips_post.mean():.1f} pips | avg |net|: {m.net_pips_post.abs().mean():.1f} pips")
    print(f"Post/Asian range ratio: {(m.range_pips_post / m.range_pips_asian).mean():.2f}x")
    print(f"Continuation rate: {m.continuation.mean()*100:.1f}%  |  corr: {m.net_pips_asian.corr(m.net_pips_post):+.3f}")

# ---------------------------------------------------------------------------
# PART 2: Hunt for a clean 10-20 pip directional window (low back-and-forth)
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("PART 2: Scanning all (start hour, duration) windows for a clean")
print("10-20 pip directional move (high efficiency ratio = low chop)")
print("=" * 70)

results = []
for start in range(24):
    for dur in (1, 2, 3, 4):
        w = daily_window(df, start, dur)
        if len(w) < 100:
            continue
        w = w[w.net_pips != 0]
        abs_net = w.net_pips.abs()
        in_band = (abs_net >= 10) & (abs_net <= 20)
        results.append({
            "start_utc": start, "dur_h": dur, "end_utc": (start + dur) % 24,
            "n_days": len(w),
            "avg_abs_net_pips": abs_net.mean(),
            "median_abs_net_pips": abs_net.median(),
            "avg_er": w.er.mean(),
            "avg_mae_pips": w.mae_pips.mean(),
            "pct_in_10_20_band": in_band.mean() * 100,
            "avg_er_in_band": w.loc[in_band, "er"].mean() if in_band.any() else np.nan,
            "avg_mae_in_band": w.loc[in_band, "mae_pips"].mean() if in_band.any() else np.nan,
        })

res = pd.DataFrame(results)

# Candidate windows: average move actually lands near 10-20 pips AND efficiency is high
candidates = res[(res.avg_abs_net_pips >= 8) & (res.avg_abs_net_pips <= 24)].copy()
candidates = candidates.sort_values("avg_er", ascending=False)

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)
print("\nTop 15 windows by efficiency ratio (ER), restricted to windows whose")
print("average |move| falls near the 10-20 pip target zone (8-24 pip band):")
cols = ["start_utc", "end_utc", "dur_h", "n_days", "avg_abs_net_pips", "avg_er", "avg_mae_pips", "pct_in_10_20_band"]
print(candidates[cols].head(15).to_string(index=False, float_format=lambda x: f"{x:.2f}"))

print("\nFor reference, top 10 windows by efficiency ratio with NO move-size filter:")
print(res.sort_values("avg_er", ascending=False)[cols].head(10).to_string(index=False, float_format=lambda x: f"{x:.2f}"))

res.to_csv("analysis/window_scan_all_hours.csv", index=False)
print("\nSaved full scan (all start-hour x duration combos) to analysis/window_scan_all_hours.csv")
