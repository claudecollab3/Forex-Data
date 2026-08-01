"""Derive coarser timeframes from a cleaned, real M1 (or other fine-grained) series.

This is deterministic OHLC aggregation over real bars already present in the
data -- not interpolation or fabrication:
  open   = first traded price in the bin
  high   = max price in the bin
  low    = min price in the bin
  close  = last traded price in the bin
  volume = sum of volume in the bin

Bins with zero underlying source bars (e.g. weekend closures) are dropped
rather than filled -- if the market didn't trade, there is no bar to report.
"""
from __future__ import annotations

import pandas as pd

RESAMPLE_FREQ = {
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1D",
}


def resample_ohlcv(df: pd.DataFrame, target_timeframe: str) -> pd.DataFrame:
    if target_timeframe not in RESAMPLE_FREQ:
        raise ValueError(f"Cannot resample to {target_timeframe!r}; supported: {list(RESAMPLE_FREQ)}")

    freq = RESAMPLE_FREQ[target_timeframe]
    indexed = df.set_index("timestamp")
    resampler = indexed.resample(freq, label="left", closed="left")

    agg = pd.DataFrame(
        {
            "open": resampler["open"].first(),
            "high": resampler["high"].max(),
            "low": resampler["low"].min(),
            "close": resampler["close"].last(),
            # min_count=1: an all-null volume bin stays null, not 0 --
            # "no volume data" is not the same claim as "zero volume".
            "volume": resampler["volume"].sum(min_count=1),
        }
    )
    agg = agg.dropna(subset=["open", "high", "low", "close"])
    return agg.reset_index()
