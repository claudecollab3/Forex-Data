#!/usr/bin/env python3
"""Process every raw file listed in raw/sources.csv into a cleaned Parquet file.

raw/sources.csv columns:
  filename          - path relative to raw/, e.g. EURUSD5.csv
  instrument        - e.g. EURUSD
  timeframe         - one of M5, M15, H1, H4
  source_timezone   - IANA tz name the raw timestamps are expressed in
                       (e.g. "UTC", "Etc/GMT-2"). Must be confirmed against
                       the source site's documentation -- never guessed
                       silently for the final dataset.

Writes:
  data/<INSTRUMENT>_<TIMEFRAME>.parquet
  manifest/_stats/<INSTRUMENT>_<TIMEFRAME>.json   (per-file cleaning stats)
"""
from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.parsing import load_ohlcv_csv
from lib.cleaning import clean, TIMEFRAME_FREQ

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
DATA = ROOT / "data"
STATS_DIR = ROOT / "manifest" / "_stats"


def main() -> int:
    sources_csv = RAW / "sources.csv"
    if not sources_csv.exists():
        print(f"ERROR: {sources_csv} not found. Create it first (see scripts/process_all.py docstring).", file=sys.stderr)
        return 1

    DATA.mkdir(parents=True, exist_ok=True)
    STATS_DIR.mkdir(parents=True, exist_ok=True)

    ok, failed = 0, 0
    with open(sources_csv, newline="") as fh:
        for row in csv.DictReader(fh):
            filename = row["filename"].strip()
            instrument = row["instrument"].strip().upper()
            timeframe = row["timeframe"].strip().upper()
            source_tz = row["source_timezone"].strip()

            if timeframe not in TIMEFRAME_FREQ:
                print(f"SKIP {filename}: unknown timeframe {timeframe!r}")
                failed += 1
                continue

            raw_path = RAW / filename
            if not raw_path.exists():
                print(f"SKIP {filename}: raw file not found at {raw_path}")
                failed += 1
                continue

            try:
                parsed = load_ohlcv_csv(str(raw_path))
                cleaned_df, stats = clean(parsed.df, timeframe, source_tz, parsed.detected_format)
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {filename}: {exc}")
                failed += 1
                continue

            out_name = f"{instrument}_{timeframe}"
            out_path = DATA / f"{out_name}.parquet"
            cleaned_df.to_parquet(out_path, engine="pyarrow", compression="snappy", index=False)

            stats_dict = asdict(stats)
            stats_dict["instrument"] = instrument
            stats_dict["timeframe"] = timeframe
            stats_dict["source_file"] = filename
            with open(STATS_DIR / f"{out_name}.json", "w") as sfh:
                json.dump(stats_dict, sfh, indent=2)

            print(
                f"OK {out_name}: {stats.rows_in} -> {stats.rows_out} rows "
                f"({stats.start} .. {stats.end}), tz={source_tz}"
            )
            ok += 1

    print(f"\nDone: {ok} succeeded, {failed} failed/skipped")
    return 1 if failed and not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
