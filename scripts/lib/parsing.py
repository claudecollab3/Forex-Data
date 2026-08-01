"""Flexible parsing for historical OHLCV CSV exports (MT4/MT5-style and generic)."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import pandas as pd

STANDARD_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


@dataclass
class ParseResult:
    df: pd.DataFrame
    detected_format: str
    had_header: bool


def _sniff_header(sample_lines: list[str]) -> bool:
    first = sample_lines[0].strip()
    if not first:
        return False
    first_field = first.split(",")[0].strip().strip('"')
    # A data row's first field is a date/datetime; a header's is a label.
    return not any(ch.isdigit() for ch in first_field)


def _read_raw(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.readlines()


def load_ohlcv_csv(path: str) -> ParseResult:
    """Parse a raw OHLCV CSV export without assuming a fixed schema.

    Supports:
      - MT4/MT5 export: Date,Time,Open,High,Low,Close,Volume (no header,
        Date as YYYY.MM.DD, Time as HH:MM[:SS])
      - Generic: Datetime,Open,High,Low,Close,Volume (with or without header)
      - Generic: Date,Time,Open,High,Low,Close (no volume column)
    """
    lines = _read_raw(path)
    non_empty = [l for l in lines if l.strip()]
    if not non_empty:
        raise ValueError(f"{path}: file is empty")

    has_header = _sniff_header(non_empty)
    dialect = csv.Sniffer().sniff(non_empty[0], delimiters=",;\t")
    sep = dialect.delimiter

    raw_df = pd.read_csv(
        io.StringIO("".join(lines)),
        sep=sep,
        header=0 if has_header else None,
        dtype=str,
        engine="python",
        skip_blank_lines=True,
    )
    raw_df.columns = [str(c).strip() for c in raw_df.columns]
    ncols = raw_df.shape[1]

    if has_header:
        lower_map = {c.lower(): c for c in raw_df.columns}
        date_col = next((lower_map[k] for k in ("date",) if k in lower_map), None)
        time_col = next((lower_map[k] for k in ("time",) if k in lower_map), None)
        dt_col = next(
            (lower_map[k] for k in ("datetime", "timestamp", "gmt time", "local time") if k in lower_map),
            None,
        )
        o = lower_map.get("open")
        h = lower_map.get("high")
        l = lower_map.get("low")
        c = lower_map.get("close")
        v = next((lower_map[k] for k in ("volume", "vol", "tickvol", "tick_volume") if k in lower_map), None)

        if not (dt_col or (date_col and time_col) or date_col):
            # Unrecognized column name for the timestamp (e.g. a tz label
            # like "Etc/UTC" used as the header). Fall back to: whichever
            # column isn't O/H/L/C/Volume and parses as a datetime.
            used = {o, h, l, c, v} - {None}
            candidates = [col for col in raw_df.columns if col not in used]
            for col in candidates:
                sample = pd.to_datetime(raw_df[col].head(20), errors="coerce")
                if sample.notna().all():
                    dt_col = col
                    break

        if dt_col:
            ts = pd.to_datetime(raw_df[dt_col].str.strip(), errors="coerce", utc=False)
        elif date_col and time_col:
            ts = pd.to_datetime(
                raw_df[date_col].str.strip() + " " + raw_df[time_col].str.strip(),
                errors="coerce",
                utc=False,
            )
        elif date_col:
            ts = pd.to_datetime(raw_df[date_col].str.strip(), errors="coerce", utc=False)
        else:
            raise ValueError(f"{path}: could not find a date/time column among {list(raw_df.columns)}")

        out = pd.DataFrame(
            {
                "timestamp": ts,
                "open": pd.to_numeric(raw_df[o], errors="coerce") if o else pd.NA,
                "high": pd.to_numeric(raw_df[h], errors="coerce") if h else pd.NA,
                "low": pd.to_numeric(raw_df[l], errors="coerce") if l else pd.NA,
                "close": pd.to_numeric(raw_df[c], errors="coerce") if c else pd.NA,
                "volume": pd.to_numeric(raw_df[v], errors="coerce") if v else pd.NA,
            }
        )
        fmt = "generic-header"
    else:
        col0_sample = raw_df.iloc[0, 0].strip()
        col0_is_combined_datetime = ":" in col0_sample

        if ncols >= 7:
            # Date, Time, Open, High, Low, Close, Volume, [...]
            date_s = raw_df.iloc[:, 0].str.strip()
            time_s = raw_df.iloc[:, 1].str.strip()
            ts = pd.to_datetime(date_s + " " + time_s, errors="coerce", utc=False)
            out = pd.DataFrame(
                {
                    "timestamp": ts,
                    "open": pd.to_numeric(raw_df.iloc[:, 2], errors="coerce"),
                    "high": pd.to_numeric(raw_df.iloc[:, 3], errors="coerce"),
                    "low": pd.to_numeric(raw_df.iloc[:, 4], errors="coerce"),
                    "close": pd.to_numeric(raw_df.iloc[:, 5], errors="coerce"),
                    "volume": pd.to_numeric(raw_df.iloc[:, 6], errors="coerce"),
                }
            )
            fmt = "mt4-date-time-vol"
        elif ncols == 6 and col0_is_combined_datetime:
            # Datetime, Open, High, Low, Close, Volume (col0 already has date+time)
            ts = pd.to_datetime(raw_df.iloc[:, 0].str.strip(), errors="coerce", utc=False)
            out = pd.DataFrame(
                {
                    "timestamp": ts,
                    "open": pd.to_numeric(raw_df.iloc[:, 1], errors="coerce"),
                    "high": pd.to_numeric(raw_df.iloc[:, 2], errors="coerce"),
                    "low": pd.to_numeric(raw_df.iloc[:, 3], errors="coerce"),
                    "close": pd.to_numeric(raw_df.iloc[:, 4], errors="coerce"),
                    "volume": pd.to_numeric(raw_df.iloc[:, 5], errors="coerce"),
                }
            )
            fmt = "datetime-ohlcv"
        elif ncols == 6:
            date_s = raw_df.iloc[:, 0].str.strip()
            time_s = raw_df.iloc[:, 1].str.strip()
            ts = pd.to_datetime(date_s + " " + time_s, errors="coerce", utc=False)
            out = pd.DataFrame(
                {
                    "timestamp": ts,
                    "open": pd.to_numeric(raw_df.iloc[:, 2], errors="coerce"),
                    "high": pd.to_numeric(raw_df.iloc[:, 3], errors="coerce"),
                    "low": pd.to_numeric(raw_df.iloc[:, 4], errors="coerce"),
                    "close": pd.to_numeric(raw_df.iloc[:, 5], errors="coerce"),
                    "volume": pd.NA,
                }
            )
            fmt = "mt4-date-time-novol"
        elif ncols == 5:
            ts = pd.to_datetime(raw_df.iloc[:, 0].str.strip(), errors="coerce", utc=False)
            out = pd.DataFrame(
                {
                    "timestamp": ts,
                    "open": pd.to_numeric(raw_df.iloc[:, 1], errors="coerce"),
                    "high": pd.to_numeric(raw_df.iloc[:, 2], errors="coerce"),
                    "low": pd.to_numeric(raw_df.iloc[:, 3], errors="coerce"),
                    "close": pd.to_numeric(raw_df.iloc[:, 4], errors="coerce"),
                    "volume": pd.NA,
                }
            )
            fmt = "datetime-ohlc-novol"
        else:
            raise ValueError(f"{path}: unrecognized column layout ({ncols} columns, no header)")

    return ParseResult(df=out, detected_format=fmt, had_header=has_header)
