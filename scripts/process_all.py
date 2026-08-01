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

Multiple rows may share the same (instrument, timeframe) -- e.g. several
date-range-limited exports needed to cover the full history because the
source caps a single export at ~100k rows. All matching raw files are
concatenated before cleaning/deduplication, so overlapping ranges between
exports are collapsed rather than duplicated.

Writes:
  data/<INSTRUMENT>_<TIMEFRAME>.parquet
  manifest/_stats/<INSTRUMENT>_<TIMEFRAME>.json   (per-file cleaning stats)
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import pandas as pd

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

    groups: dict[tuple[str, str], dict] = defaultdict(lambda: {"filenames": [], "source_tz": None})
    with open(sources_csv, newline="") as fh:
        for row in csv.DictReader(fh):
            instrument = row["instrument"].strip().upper()
            timeframe = row["timeframe"].strip().upper()
            key = (instrument, timeframe)
            groups[key]["filenames"].append(row["filename"].strip())
            tz = row["source_timezone"].strip()
            if groups[key]["source_tz"] is None:
                groups[key]["source_tz"] = tz
            elif groups[key]["source_tz"] != tz:
                print(f"WARN {instrument}_{timeframe}: conflicting source_timezone across files ({groups[key]['source_tz']!r} vs {tz!r}); using {groups[key]['source_tz']!r}")

    ok, failed = 0, 0
    for (instrument, timeframe), info in groups.items():
        if timeframe not in TIMEFRAME_FREQ:
            print(f"SKIP {instrument}_{timeframe}: unknown timeframe {timeframe!r}")
            failed += 1
            continue

        source_tz = info["source_tz"]
        parts = []
        detected_formats = set()
        missing = False
        for filename in info["filenames"]:
            raw_path = RAW / filename
            if not raw_path.exists():
                print(f"SKIP {instrument}_{timeframe}: raw file not found at {raw_path}")
                missing = True
                break
            try:
                parsed = load_ohlcv_csv(str(raw_path))
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {instrument}_{timeframe} ({filename}): {exc}")
                missing = True
                break
            parts.append(parsed.df)
            detected_formats.add(parsed.detected_format)

        if missing:
            failed += 1
            continue

        combined = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]

        try:
            cleaned_df, stats = clean(combined, timeframe, source_tz, "+".join(sorted(detected_formats)))
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {instrument}_{timeframe}: {exc}")
            failed += 1
            continue

        out_name = f"{instrument}_{timeframe}"
        out_path = DATA / f"{out_name}.parquet"
        cleaned_df.to_parquet(out_path, engine="pyarrow", compression="zstd", compression_level=19, index=False)

        stats_dict = asdict(stats)
        stats_dict["instrument"] = instrument
        stats_dict["timeframe"] = timeframe
        stats_dict["source_file"] = ";".join(info["filenames"])
        with open(STATS_DIR / f"{out_name}.json", "w") as sfh:
            json.dump(stats_dict, sfh, indent=2)

        print(
            f"OK {out_name}: {stats.rows_in} -> {stats.rows_out} rows "
            f"({stats.start} .. {stats.end}), tz={source_tz}, files={len(info['filenames'])}"
        )
        ok += 1

    print(f"\nDone: {ok} succeeded, {failed} failed/skipped")
    return 1 if failed and not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
