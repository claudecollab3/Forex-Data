"""Cleaning rules applied uniformly to every instrument/timeframe file.

Rules (deliberately conservative -- never invents a price):
  1. Rows with a null/unparseable timestamp are dropped (can't be placed).
  2. Rows with a null O/H/L/C are dropped (never interpolated/filled).
  3. Rows where the OHLC relationship is physically invalid
     (high < max(open,close,low) or low > min(open,close,high)) are dropped
     as corrupt, not "fixed".
  4. Exact duplicate timestamps are collapsed to a single row (first kept;
     count logged). Non-duplicate rows are never removed.
  5. Volume nulls are left as null (FX volume is broker tick-volume, not a
     true traded volume, so it is common and legitimate for it to be
     absent -- it is never fabricated or zero-filled).
  6. Data is sorted ascending by timestamp and re-indexed.
  7. Timestamps are converted from the declared source timezone to UTC.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TIMEFRAME_FREQ = {
    "M1": pd.Timedelta(minutes=1),
    "M5": pd.Timedelta(minutes=5),
    "M15": pd.Timedelta(minutes=15),
    "M30": pd.Timedelta(minutes=30),
    "H1": pd.Timedelta(hours=1),
    "H4": pd.Timedelta(hours=4),
    "D1": pd.Timedelta(days=1),
}


@dataclass
class CleaningStats:
    rows_in: int = 0
    rows_out: int = 0
    null_timestamp_dropped: int = 0
    null_price_dropped: int = 0
    ohlc_invalid_dropped: int = 0
    exact_duplicate_rows_collapsed: int = 0
    volume_null_count: int = 0
    source_timezone: str = ""
    source_timezone_confidence: str = "user_declared_unverified"
    detected_format: str = ""
    gap_count_weekday: int = 0
    largest_gap_hours: float = 0.0
    suspicious_gaps_over_72h_weekday: int = 0
    start: str | None = None
    end: str | None = None
    notes: list = field(default_factory=list)


def _is_weekend_utc(ts: pd.Series) -> pd.Series:
    # FX market closed roughly Fri 22:00 UTC -> Sun 22:00 UTC. Approximate
    # (ignores DST shift in the actual broker close/open time and holidays);
    # used only to avoid mislabeling the normal weekend closure as a gap.
    dow = ts.dt.dayofweek  # Mon=0 ... Sun=6
    hour = ts.dt.hour
    is_sat = dow == 5
    is_sun_before_22 = (dow == 6) & (hour < 22)
    is_fri_after_22 = (dow == 4) & (hour >= 22)
    return is_sat | is_sun_before_22 | is_fri_after_22


def clean(df: pd.DataFrame, timeframe: str, source_tz: str, detected_format: str) -> tuple[pd.DataFrame, CleaningStats]:
    stats = CleaningStats(rows_in=len(df), source_timezone=source_tz, detected_format=detected_format)

    df = df.copy()

    null_ts = df["timestamp"].isna()
    stats.null_timestamp_dropped = int(null_ts.sum())
    df = df.loc[~null_ts]

    price_cols = ["open", "high", "low", "close"]
    null_price = df[price_cols].isna().any(axis=1)
    stats.null_price_dropped = int(null_price.sum())
    df = df.loc[~null_price]

    stats.volume_null_count = int(df["volume"].isna().sum())

    hi_valid = df["high"] >= df[["open", "close", "low"]].max(axis=1)
    lo_valid = df["low"] <= df[["open", "close", "high"]].min(axis=1)
    invalid = ~(hi_valid & lo_valid)
    stats.ohlc_invalid_dropped = int(invalid.sum())
    df = df.loc[~invalid]

    # Localize to declared source tz, then convert to UTC.
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(source_tz, ambiguous="NaT", nonexistent="NaT")
        newly_null = df["timestamp"].isna()
        if newly_null.any():
            stats.null_timestamp_dropped += int(newly_null.sum())
            df = df.loc[~newly_null]
    df["timestamp"] = df["timestamp"].dt.tz_convert("UTC")

    df = df.sort_values("timestamp", kind="mergesort")
    before_dedup = len(df)
    df = df.drop_duplicates(subset="timestamp", keep="first")
    stats.exact_duplicate_rows_collapsed = before_dedup - len(df)

    df = df.reset_index(drop=True)
    df["open"] = df["open"].astype("float64")
    df["high"] = df["high"].astype("float64")
    df["low"] = df["low"].astype("float64")
    df["close"] = df["close"].astype("float64")
    df["volume"] = df["volume"].astype("float64")

    stats.rows_out = len(df)
    if len(df):
        stats.start = df["timestamp"].iloc[0].isoformat()
        stats.end = df["timestamp"].iloc[-1].isoformat()

        deltas = df["timestamp"].diff().dropna()
        freq = TIMEFRAME_FREQ[timeframe]
        gap_mask = deltas > freq
        gap_ends = df["timestamp"].iloc[1:][gap_mask.values]
        gap_starts = df["timestamp"].iloc[:-1][gap_mask.values]
        weekday_gap_mask = ~(_is_weekend_utc(gap_starts).values & _is_weekend_utc(gap_ends).values)
        weekday_gap_hours = (gap_ends.values - gap_starts.values)[weekday_gap_mask].astype("timedelta64[s]").astype(float) / 3600.0
        stats.gap_count_weekday = int(len(weekday_gap_hours))
        stats.largest_gap_hours = float(weekday_gap_hours.max()) if len(weekday_gap_hours) else 0.0
        stats.suspicious_gaps_over_72h_weekday = int((weekday_gap_hours > 72).sum()) if len(weekday_gap_hours) else 0

    return df, stats
