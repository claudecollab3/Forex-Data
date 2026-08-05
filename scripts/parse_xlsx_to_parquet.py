"""
Parse HistData.com "XLSX" M1 OHLC exports into a single parquet file.

These files are technically .xlsx but the worksheet XML has a malformed
<dimension> tag, which trips up openpyxl (it silently reads only 1 row).
We instead stream-parse the raw sheet1.xml directly with ElementTree,
which is both correct and much faster for a ~370k-row sheet.

Usage:
    python scripts/parse_xlsx_to_parquet.py <year1=path1> [<year2=path2> ...] -o out.parquet

Example:
    python scripts/parse_xlsx_to_parquet.py \
        2016=data/DAT_XLSX_USDJPY_M1_2016.xlsx \
        2022=data/DAT_XLSX_USDJPY_M1_2022.xlsx \
        2023=data/DAT_XLSX_USDJPY_M1_2023.xlsx \
        -o analysis/usdjpy_m1.parquet
"""
import argparse
import zipfile
import xml.etree.ElementTree as ET
import datetime as dt
import numpy as np
import pandas as pd

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

EXCEL_EPOCH = dt.datetime(1899, 12, 30)

def parse_xlsx(path):
    rows = []
    with zipfile.ZipFile(path) as z:
        with z.open("xl/worksheets/sheet1.xml") as f:
            context = ET.iterparse(f, events=("end",))
            for event, elem in context:
                if elem.tag == NS + "row":
                    cells = list(elem)
                    vals = []
                    for c in cells:
                        v = c.find(NS + "v")
                        vals.append(float(v.text) if v is not None else None)
                    if len(vals) == 6:
                        rows.append(vals)
                    elem.clear()
    arr = np.array(rows, dtype=float)
    return arr

def to_df(arr):
    # columns: serial, open, high, low, close, volume
    serials = arr[:, 0]
    # convert excel serial (days since 1899-12-30) to python datetime, this ts is HistData local (fixed EST, UTC-5, no DST)
    base = np.datetime64(EXCEL_EPOCH)
    ts_est = base + (serials * 86400).astype('timedelta64[s]')
    ts_utc = ts_est + np.timedelta64(5, 'h')  # EST fixed -> UTC = EST + 5h
    df = pd.DataFrame({
        "ts_utc": ts_utc,
        "open": arr[:, 1],
        "high": arr[:, 2],
        "low": arr[:, 3],
        "close": arr[:, 4],
        "volume": arr[:, 5],
    })
    df = df.sort_values("ts_utc").reset_index(drop=True)
    return df

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="year=path.xlsx pairs, e.g. 2016=data/DAT_XLSX_USDJPY_M1_2016.xlsx")
    ap.add_argument("-o", "--out", default="analysis/usdjpy_m1.parquet")
    args = ap.parse_args()

    all_dfs = {}
    for pair in args.files:
        year_str, path = pair.split("=", 1)
        year = int(year_str)
        print(f"Parsing {year} ({path}) ...")
        arr = parse_xlsx(path)
        df = to_df(arr)
        all_dfs[year] = df
        print(f"  {len(df)} bars, {df.ts_utc.min()} -> {df.ts_utc.max()}")

    full = pd.concat(all_dfs.values(), ignore_index=True).sort_values("ts_utc").reset_index(drop=True)
    full.to_parquet(args.out)
    print("Total bars:", len(full))
    print(full.head())
    print(full.tail())


if __name__ == "__main__":
    main()
