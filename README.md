# Forex-Data

Cleaned, standardized historical OHLCV data for major FX pairs and XAU/USD,
stored as per-instrument/per-timeframe Apache Parquet files with an
accompanying data-quality manifest.

## Instruments & timeframes

EUR/USD, GBP/USD, USD/JPY, USD/CHF, USD/CAD, AUD/USD, NZD/USD, XAU/USD
across M5, M15, H1, H4 — maximum history available per instrument/timeframe
from the source.

## Layout

- `raw/` — raw exports as downloaded from the source (gitignored; only
  `raw/sources.csv` is tracked). `raw/sources.csv` maps each raw file to an
  instrument, timeframe, and the timezone its timestamps are declared in.
- `scripts/` — the processing pipeline:
  - `lib/parsing.py` — schema-flexible CSV parser (MT4/MT5-style and
    generic date/datetime + OHLCV layouts).
  - `lib/cleaning.py` — cleaning rules (see below) and gap analysis.
  - `process_all.py` — runs parsing + cleaning for every row in
    `raw/sources.csv`, writes `data/<INSTRUMENT>_<TIMEFRAME>.parquet` and a
    per-file stats sidecar under `manifest/_stats/`.
  - `build_manifest.py` — aggregates the sidecars + Parquet file metadata
    into `manifest/manifest.json` and `manifest/manifest.csv`.
- `data/` — output Parquet files, one per instrument/timeframe. Columns:
  `timestamp` (UTC, tz-aware), `open`, `high`, `low`, `close`, `volume`
  (nullable — see below). Sorted ascending, deduplicated by timestamp.
- `manifest/manifest.json` / `manifest/manifest.csv` — per-file date range,
  row count, missing-value counts, cleaning stats, and gap statistics.

## Cleaning rules

Applied identically to every file, deliberately conservative — **no price
is ever fabricated or interpolated**:

1. Rows with an unparseable/null timestamp are dropped.
2. Rows with a null open/high/low/close are dropped (never filled).
3. Rows where the OHLC relationship is physically invalid
   (`high < max(open, close, low)` or `low > min(open, close, high)`) are
   dropped as corrupt.
4. Exact duplicate timestamps are collapsed to a single row; all other rows
   are kept as-is.
5. `volume` nulls are left null, not zero-filled — FX volume is broker
   tick-volume, not true traded volume, so its absence is expected and is
   reported in the manifest rather than papered over.
6. Timestamps are converted from the **declared source timezone**
   (`raw/sources.csv`, verified against the source's own documentation) to
   UTC and the data is sorted chronologically.
7. Gaps in the timeline are measured against the instrument's expected bar
   frequency. Gaps that fall entirely within the normal weekend market
   closure (~Fri 22:00 UTC – Sun 22:00 UTC, approximate) are not counted;
   all other gaps are reported (count + largest gap), and any single
   weekday gap over 72h is flagged separately for manual review — it is
   **not** auto-filled.

## Regenerating

```console
pip install -r requirements.txt
python3 scripts/process_all.py     # raw/*.csv -> data/*.parquet
python3 scripts/build_manifest.py  # data/*.parquet -> manifest/*
```

## Source & license

<!-- Fill in once the raw data source and its usage terms are confirmed. -->

## Known limitations

- Weekend/holiday gap detection uses a fixed UTC heuristic, not a full
  market-holiday calendar, so some legitimate holiday closures may still be
  reported as "gaps" in the manifest rather than being excluded.
- `source_timezone` in `raw/sources.csv` reflects what is declared for the
  source export; it is not independently re-derived from the price data.
