# Forex-Data

Cleaned, standardized historical OHLCV data for major FX pairs and XAU/USD,
stored as per-instrument/per-timeframe Apache Parquet files with an
accompanying data-quality manifest.

## Instruments & timeframes

**Required 8:** EUR/USD, GBP/USD, USD/JPY, USD/CHF, USD/CAD, AUD/USD,
NZD/USD, XAU/USD — each at M1, M5, M15, M30, H1, H4, D1 (M5/M15/H1/H4 were
the originally-scoped timeframes; M1/M30/D1 were added on request).

**Bonus instruments** (not part of the original request, included because
raw data was provided): AUDCHF, AUDCAD, BTCUSD, XAGUSD (silver) — same
timeframe set.

### Actual history coverage (years, as of the last manifest build)

| Instrument | M1 | M5 | M15 | M30 | H1 | H4 | D1 |
|---|---|---|---|---|---|---|---|
| EUR/USD, AUD/USD, GBP/USD | 26.2 | 26.2 | 26.2 | 26.2 | 26.2 | 26.2 | 26.2 |
| USD/JPY, USD/CHF, USD/CAD, NZD/USD | 0.3 | 1.3 | 4.0 | 8.0 | 16.0 | 16.0 | 16.0 |
| XAU/USD | 0.3 | 1.4 | 4.2 | 8.5 | 16.7 | 16.8 | 16.8 |
| AUDCAD, AUDCHF (bonus) | 0.3 | 1.3 | 4.0 | 8.0 | 16.0 | 16.0 | 16.0 |
| XAGUSD (bonus) | 0.3 | 1.4 | 4.2 | 8.3 | 15.4 | 15.4 | 15.4 |
| BTCUSD (bonus) | 0.2 | 1.0 | 2.9 | 5.8 | 9.2 | 9.2 | 9.2 |

**EUR/USD, AUD/USD, and GBP/USD exceed the 20-year target uniformly across
every timeframe** (2000-05/06 → 2026-07, ~26.2 years). This was achieved by
merging in HistData.com's M1 exports (chunked by full calendar year, not
row-capped) for 2000-2023, then deterministically resampling that M1 series
into M5/M15/M30/H1/H4/D1 (open=first, high=max, low=min, close=last,
volume=sum -- a real aggregation, not fabrication) and merging the result
with the existing forexsb-derived native data. See
`scripts/merge_histdata.py`.

The remaining 5 required instruments (USD/JPY, USD/CHF, USD/CAD, NZD/USD,
XAU/USD) and all 4 bonus instruments have **not** had this treatment yet --
they're still forexsb-only. H1/H4/D1 for those land at ~16-17 years (the
practical ceiling for that source alone); M1/M5/M15/M30 fall well short
(down to a few months for M1) because forexsb.com caps a single export at
~100,000 rows. Applying the same HistData merge to these instruments would
close that gap the same way it did for EUR/USD, AUD/USD, and GBP/USD.
See `manifest/manifest.csv` for the exact per-file range.

## Layout

- `raw/` — raw exports as downloaded from the source (gitignored; only
  `raw/sources.csv` is tracked). `raw/sources.csv` maps each raw file to an
  instrument, timeframe, and the timezone its timestamps are declared in.
  `raw/histdata_staging/<INSTRUMENT>/` holds staged HistData.com M1 XLSX
  exports (gitignored) awaiting `merge_histdata.py`.
- `scripts/` — the processing pipeline:
  - `lib/parsing.py` — schema-flexible parser: MT4/MT5-style CSV, generic
    date/datetime + OHLCV CSV layouts, and HistData.com's XLSX export.
  - `lib/cleaning.py` — cleaning rules (see below) and gap analysis.
  - `lib/resample.py` — deterministic OHLC resampling (M1 -> coarser
    timeframes) used by the HistData merge; not used on forexsb data, which
    is already natively per-timeframe.
  - `process_all.py` — runs parsing + cleaning for every row in
    `raw/sources.csv`, writes `data/<INSTRUMENT>_<TIMEFRAME>.parquet` and a
    per-file stats sidecar under `manifest/_stats/`.
  - `merge_histdata.py` — for each instrument staged under
    `raw/histdata_staging/`, cleans the combined HistData M1 series,
    resamples it to every other timeframe, and unions the result with
    whatever's already in `data/` (native rows win on an exact-timestamp
    collision), overwriting `data/<INSTRUMENT>_<TIMEFRAME>.parquet`.
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
python3 scripts/process_all.py      # raw/*.csv,xlsx -> data/*.parquet (per raw/sources.csv)
python3 scripts/merge_histdata.py   # raw/histdata_staging/* -> merges into data/*.parquet
python3 scripts/build_manifest.py   # data/*.parquet -> manifest/*
```

## Source & license

Raw data was downloaded by hand from two sources and supplied to this
pipeline:

- **forexsb.com** (Forex Strategy Builder's historical data downloads) —
  CSV exports, all instruments.
- **HistData.com** — free XLSX exports, chunked by calendar year, used for
  EUR/USD, AUD/USD, and GBP/USD M1 to extend history to ~26 years across
  every timeframe. Declared timezone: fixed EST, no DST (user-confirmed
  from HistData's own documentation), mapped to `Etc/GMT+5`.

**Neither site's specific usage terms have been independently verified by
the pipeline author** (this session had no direct network access to either
source). Before treating this dataset as cleared for a given use (e.g.
redistribution, commercial use), confirm both forexsb.com's and
HistData.com's actual license/terms of use for their historical data
downloads.

## Known limitations

- Weekend/holiday gap detection uses a fixed UTC heuristic, not a full
  market-holiday calendar, so some legitimate holiday closures may still be
  reported as "gaps" in the manifest rather than being excluded.
- `source_timezone` in `raw/sources.csv` reflects what is declared for the
  source export; it is not independently re-derived from the price data.
