#!/usr/bin/env python3
"""Merge staged HistData.com M1 exports (raw/histdata_staging/<INSTRUMENT>/)
into the existing dataset.

For each instrument with staged files:
  1. Parse + concatenate all staged M1 XLSX files into one raw M1 series.
  2. Clean it as M1, converting from HistData's declared timezone -- fixed
     EST, no DST (user-confirmed against the source's own documentation) --
     to UTC.
  3. For each target timeframe (M1 itself, or M5/M15/M30/H1/H4/D1 via
     resampling the cleaned HistData M1 series): union with whatever is
     already in data/<INSTRUMENT>_<TF>.parquet (the forexsb-derived native
     data), then re-clean the union as a single pass. Native rows are
     concatenated first so that on an exact-timestamp collision the
     directly-sourced (non-resampled) row wins the dedup, not the derived
     one -- clean()'s dedup keeps the first occurrence after a stable sort.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from lib.parsing import load_ohlcv_csv
from lib.cleaning import clean
from lib.resample import resample_ohlcv

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "raw" / "histdata_staging"
DATA = ROOT / "data"
STATS_DIR = ROOT / "manifest" / "_stats"

HISTDATA_TZ = "Etc/GMT+5"  # fixed EST, no DST -- user-confirmed from HistData.com's documentation
TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


def merge_instrument(instrument: str) -> None:
    staged_dir = STAGING / instrument
    files = sorted(staged_dir.glob("*.xlsx"))
    if not files:
        print(f"SKIP {instrument}: no staged files")
        return

    print(f"=== {instrument}: {len(files)} staged HistData M1 files ===")
    parts = [load_ohlcv_csv(str(f)).df for f in files]
    raw_m1 = pd.concat(parts, ignore_index=True)
    hist_m1_clean, hist_stats = clean(raw_m1, "M1", HISTDATA_TZ, "histdata-xlsx")
    print(
        f"  HistData M1 cleaned: {hist_stats.rows_in} -> {hist_stats.rows_out} rows, "
        f"{hist_stats.start} .. {hist_stats.end} "
        f"(null_ts={hist_stats.null_timestamp_dropped}, null_price={hist_stats.null_price_dropped}, "
        f"invalid_ohlc={hist_stats.ohlc_invalid_dropped}, dup={hist_stats.exact_duplicate_rows_collapsed})"
    )

    for tf in TIMEFRAMES:
        existing_path = DATA / f"{instrument}_{tf}.parquet"
        existing_df = (
            pd.read_parquet(existing_path)
            if existing_path.exists()
            else pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        )

        hist_tf_df = hist_m1_clean if tf == "M1" else resample_ohlcv(hist_m1_clean, tf)

        combined_raw = pd.concat([existing_df, hist_tf_df], ignore_index=True)
        merged_df, merge_stats = clean(combined_raw, tf, "UTC", "native+histdata-resampled")
        merge_stats.source_timezone = f"HistData:{HISTDATA_TZ} (user-confirmed); native:GMT"
        merge_stats.source_timezone_confidence = "user_confirmed"

        merged_df.to_parquet(existing_path, engine="pyarrow", compression="zstd", compression_level=19, index=False)

        stats_dict = asdict(merge_stats)
        stats_dict["instrument"] = instrument
        stats_dict["timeframe"] = tf
        stats_dict["source_file"] = (
            f"forexsb-native ({len(existing_df)} rows) + "
            f"histdata M1 {len(files)}yr merged"
            + ("" if tf == "M1" else f", resampled to {tf}")
        )
        with open(STATS_DIR / f"{instrument}_{tf}.json", "w") as sfh:
            json.dump(stats_dict, sfh, indent=2)

        print(
            f"  {tf}: native {len(existing_df)} + histdata {len(hist_tf_df)} -> "
            f"merged {len(merged_df)} rows, {merge_stats.start} .. {merge_stats.end}"
        )


def main() -> int:
    for instrument in ["EURUSD", "AUDUSD", "GBPUSD"]:
        merge_instrument(instrument)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
