#!/usr/bin/env python3
"""Aggregate per-file cleaning stats + Parquet metadata into a single manifest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATS_DIR = ROOT / "manifest" / "_stats"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    entries = []
    for parquet_path in sorted(DATA.glob("*.parquet")):
        stem = parquet_path.stem
        stats_path = STATS_DIR / f"{stem}.json"
        if not stats_path.exists():
            print(f"WARN: no stats sidecar for {parquet_path.name}, skipping")
            continue
        stats = json.loads(stats_path.read_text())

        table = pq.read_table(parquet_path)
        df = table.to_pandas()
        null_counts = df.isna().sum().to_dict()

        entries.append(
            {
                "instrument": stats["instrument"],
                "timeframe": stats["timeframe"],
                "file": f"data/{parquet_path.name}",
                "source_file": stats.get("source_file"),
                "source_timezone_declared": stats.get("source_timezone"),
                "source_timezone_confidence": stats.get("source_timezone_confidence"),
                "detected_input_format": stats.get("detected_format"),
                "row_count": stats["rows_out"],
                "date_range_utc": {"start": stats["start"], "end": stats["end"]},
                "file_size_bytes": parquet_path.stat().st_size,
                "sha256": sha256_of(parquet_path),
                "cleaning": {
                    "rows_before_cleaning": stats["rows_in"],
                    "rows_after_cleaning": stats["rows_out"],
                    "null_timestamp_rows_dropped": stats["null_timestamp_dropped"],
                    "null_price_rows_dropped": stats["null_price_dropped"],
                    "ohlc_invalid_rows_dropped": stats["ohlc_invalid_dropped"],
                    "exact_duplicate_rows_collapsed": stats["exact_duplicate_rows_collapsed"],
                },
                "data_quality": {
                    "missing_values_by_column": {k: int(v) for k, v in null_counts.items()},
                    "volume_null_count": stats["volume_null_count"],
                    "weekday_gap_count": stats["gap_count_weekday"],
                    "largest_weekday_gap_hours": round(stats["largest_gap_hours"], 2),
                    "suspicious_gaps_over_72h_weekday": stats["suspicious_gaps_over_72h_weekday"],
                },
            }
        )

    manifest = {
        "generated_at_utc": pd.Timestamp.now("UTC").isoformat(),
        "timezone_of_all_output_data": "UTC",
        "file_count": len(entries),
        "files": entries,
    }

    out_json = ROOT / "manifest" / "manifest.json"
    out_json.write_text(json.dumps(manifest, indent=2, default=str))

    # Flat CSV for quick scanning.
    rows = []
    for e in entries:
        rows.append(
            {
                "instrument": e["instrument"],
                "timeframe": e["timeframe"],
                "file": e["file"],
                "row_count": e["row_count"],
                "start_utc": e["date_range_utc"]["start"],
                "end_utc": e["date_range_utc"]["end"],
                "rows_before_cleaning": e["cleaning"]["rows_before_cleaning"],
                "duplicates_collapsed": e["cleaning"]["exact_duplicate_rows_collapsed"],
                "null_price_rows_dropped": e["cleaning"]["null_price_rows_dropped"],
                "ohlc_invalid_rows_dropped": e["cleaning"]["ohlc_invalid_rows_dropped"],
                "volume_null_count": e["data_quality"]["volume_null_count"],
                "weekday_gap_count": e["data_quality"]["weekday_gap_count"],
                "largest_weekday_gap_hours": e["data_quality"]["largest_weekday_gap_hours"],
                "suspicious_gaps_over_72h": e["data_quality"]["suspicious_gaps_over_72h_weekday"],
                "file_size_bytes": e["file_size_bytes"],
                "sha256": e["sha256"],
            }
        )
    pd.DataFrame(rows).to_csv(ROOT / "manifest" / "manifest.csv", index=False)

    print(f"Wrote manifest for {len(entries)} files -> manifest/manifest.json, manifest/manifest.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
