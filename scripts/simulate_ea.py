"""
Faithful Python port of ea/FifthTouchHedgeEA.mq5 (v1.05), run against real
1-minute FX bars, to verify -- independent of the user's MT5 environment --
whether the safety valves (MaxGridHoldDays, MaxHedgeHoldDays) actually bound
how long a cycle can sit open, or whether a real bug lets a cycle stay open
(floating in loss, never closing) indefinitely despite them.

This intentionally mirrors the .mq5 control flow function-by-function
(OpenCycle, AddGridLevel, OpenHedge, CloseEverything, ComputeChoppyScore,
the MODE_GRID / MODE_HEDGED branches in ManageStrategy) rather than
reimplementing the strategy from scratch, so a fix verified here translates
back to the .mq5 as a near 1:1 edit.

Floating P/L is tracked in "lot-weighted pips" (lot_i * pips_moved), NOT
account currency -- currency conversion isn't relevant to the timing/state-
machine bug under investigation, only pips-vs-time is. TPLockCurrency is
therefore approximated by TP_LOCK_PIP_EQUIV; treat it as a rough stand-in,
not a currency-accurate reproduction.

Usage:
    python scripts/simulate_ea.py [parquet_path]
"""
import sys
import pandas as pd
import numpy as np
from datetime import timedelta

PIP = 0.01  # USDJPY

# --- Inputs (mirrors the .mq5 defaults) ------------------------------------
GRID_STEP_PIPS = 10.0
CLOSE_PROFIT_PIPS = 5.0
MAX_GRID_LEVELS = 8
TOUCH_BAND_PIPS = 10.0
TOUCHES_TO_HEDGE = 5
EARLY_WINDOW_MINUTES = 240
IMPULSE_START_HR = 0
IMPULSE_END_HR = 4
MAX_HEDGE_HOLD_DAYS = 5
MAX_GRID_HOLD_DAYS = 5
TP_LOCK_PIP_EQUIV = 20.0   # rough stand-in for TPLockCurrency=2.0 (see note above)
MULTIPLIER = 2.0
START_LOT = 0.01

COEF_TOUCHES = 0.3178
COEF_MAXEXC = -0.0643
COEF_IMPULSE = 0.4705
COEF_INTERCEPT = -2.5169


def day_start(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.normalize()


class ChoppyFormulaCache:
    """Mirrors ComputeChoppyScore(): computed once per day, from that day's
    own first EARLY_WINDOW_MINUTES of bars."""

    def __init__(self, df):
        self.df = df
        self.ts = df["ts_utc"].values  # numpy datetime64, sorted
        self.high = df["high"].values
        self.low = df["low"].values
        self.open = df["open"].values

    def compute(self, dstart: pd.Timestamp):
        window_end = dstart + timedelta(minutes=EARLY_WINDOW_MINUTES)
        lo_idx = np.searchsorted(self.ts, np.datetime64(dstart), side="left")
        hi_idx = np.searchsorted(self.ts, np.datetime64(window_end), side="left")
        if hi_idx <= lo_idx:
            return None  # not enough bars yet (mirrors startIdx<=endIdx guard)

        # sanity check: first bar in window should actually fall on dstart's calendar day
        first_ts = pd.Timestamp(self.ts[lo_idx])
        if day_start(first_ts) != dstart:
            return None

        day_open = self.open[lo_idx]
        hi_off = (self.high[lo_idx:hi_idx] - day_open) / PIP
        lo_off = (self.low[lo_idx:hi_idx] - day_open) / PIP
        hours = pd.DatetimeIndex(self.ts[lo_idx:hi_idx]).hour

        max_exc = float(max(hi_off.max(initial=0), (-lo_off).max(initial=0)))

        touched_now = (hi_off >= TOUCH_BAND_PIPS) | (lo_off <= -TOUCH_BAND_PIPS)
        early_touches = 0
        impulse_touches = 0
        was_outside = False
        for i in range(len(touched_now)):
            if touched_now[i]:
                if not was_outside:
                    early_touches += 1
                    if IMPULSE_START_HR <= hours[i] < IMPULSE_END_HR:
                        impulse_touches += 1
                was_outside = True
            else:
                was_outside = False

        score = (COEF_TOUCHES * early_touches + COEF_MAXEXC * max_exc +
                 COEF_IMPULSE * impulse_touches + COEF_INTERCEPT)
        return score


class Cycle:
    __slots__ = ("open_time", "close_time", "close_reason", "mode_at_close",
                 "hedge_entry_time")

    def __init__(self, open_time):
        self.open_time = open_time
        self.close_time = None
        self.close_reason = None
        self.mode_at_close = None
        self.hedge_entry_time = None


def simulate(df, year_label):
    formula = ChoppyFormulaCache(df)

    mode = "FLAT"
    cycle_open_price = 0.0
    cycle_open_time = None
    touches_this_cycle = 0
    state_outside = False

    grid_lots = []
    grid_prices = []
    hedge_lot = 0.0
    hedge_open_price = 0.0
    hedge_entry_day_start = None

    current_day_start = None
    day_formula_computed = False
    day_is_choppy = True

    cycles = []
    cur_cycle = None
    events = []  # (time, message) -- capped log for spot-checking

    def log(t, msg):
        if len(events) < 4000:
            events.append((t, msg))

    def weighted_avg():
        tot_lot = sum(grid_lots)
        if tot_lot <= 0:
            return 0.0
        return sum(l * p for l, p in zip(grid_lots, grid_prices)) / tot_lot

    def floating_pips(price):
        total = 0.0
        for l, p in zip(grid_lots, grid_prices):
            total += l * (price - p) / PIP
        if hedge_lot > 0:
            total += hedge_lot * (hedge_open_price - price) / PIP
        return total

    def open_cycle(t, price):
        nonlocal mode, cycle_open_price, cycle_open_time, touches_this_cycle
        nonlocal state_outside, grid_lots, grid_prices, hedge_lot, cur_cycle
        grid_lots = [START_LOT]
        grid_prices = [price]
        hedge_lot = 0.0
        cycle_open_price = price
        cycle_open_time = t
        touches_this_cycle = 0
        state_outside = False
        mode = "GRID"
        cur_cycle = Cycle(t)

    def close_everything(t, reason):
        nonlocal mode, grid_lots, grid_prices, hedge_lot, cur_cycle
        if cur_cycle is not None:
            cur_cycle.close_time = t
            cur_cycle.close_reason = reason
            cur_cycle.mode_at_close = mode
            cycles.append(cur_cycle)
        grid_lots = []
        grid_prices = []
        hedge_lot = 0.0
        mode = "FLAT"

    for ts, o, h, l, c in zip(df["ts_utc"], df["open"], df["high"], df["low"], df["close"]):
        now = ts
        today_start = day_start(now)

        if today_start != current_day_start:
            current_day_start = today_start
            day_formula_computed = False

        if not day_formula_computed and (now - current_day_start) >= timedelta(minutes=EARLY_WINDOW_MINUTES):
            score = formula.compute(current_day_start)
            if score is not None:
                day_is_choppy = score > 0
                day_formula_computed = True

        bid = c
        hi, lo = h, l

        if mode == "FLAT":
            open_cycle(now, bid)
            continue

        # TP lock -- checked every bar here (real EA checks every tick; we only have
        # bar data, so this is an upper-bound approximation, not exact)
        if TP_LOCK_PIP_EQUIV > 0:
            fp = floating_pips(bid)
            if fp >= TP_LOCK_PIP_EQUIV:
                close_everything(now, "TP_LOCK")
                open_cycle(now, bid)
                continue

        if mode == "GRID":
            if MAX_GRID_HOLD_DAYS > 0:
                days_held = (current_day_start - day_start(cycle_open_time)).days
                if days_held >= MAX_GRID_HOLD_DAYS:
                    log(now, f"FORCED grid close, days_held={days_held}")
                    close_everything(now, "MAX_GRID_HOLD")
                    open_cycle(now, bid)
                    continue

            upper = cycle_open_price + TOUCH_BAND_PIPS * PIP
            lower = cycle_open_price - TOUCH_BAND_PIPS * PIP
            touched = (hi >= upper) or (lo <= lower)

            hedged_now = False
            if touched:
                if not state_outside:
                    touches_this_cycle += 1
                    state_outside = True
                    if touches_this_cycle >= TOUCHES_TO_HEDGE:
                        last_lot = grid_lots[-1]
                        hedge_lot = last_lot * MULTIPLIER
                        hedge_open_price = bid
                        hedge_entry_day_start = current_day_start
                        if cur_cycle is not None:
                            cur_cycle.hedge_entry_time = now
                        mode = "HEDGED"
                        hedged_now = True
            else:
                state_outside = False

            if hedged_now:
                continue

            if mode == "GRID" and len(grid_lots) < MAX_GRID_LEVELS:
                last_price = grid_prices[-1]
                if bid <= last_price - GRID_STEP_PIPS * PIP:
                    next_lot = grid_lots[-1] * MULTIPLIER
                    grid_lots.append(next_lot)
                    grid_prices.append(bid)

            if mode == "GRID":
                avg = weighted_avg()
                if bid >= avg + CLOSE_PROFIT_PIPS * PIP:
                    close_everything(now, "GRID_PROFIT")
                    open_cycle(now, bid)

        elif mode == "HEDGED":
            natural_unwind = (current_day_start != hedge_entry_day_start and
                               day_formula_computed and not day_is_choppy)
            forced_unwind = False
            if not natural_unwind and MAX_HEDGE_HOLD_DAYS > 0:
                days_held = (current_day_start - hedge_entry_day_start).days
                if days_held >= MAX_HEDGE_HOLD_DAYS:
                    forced_unwind = True

            if natural_unwind:
                close_everything(now, "NATURAL_UNWIND")
                open_cycle(now, bid)
            elif forced_unwind:
                log(now, f"FORCED hedge unwind, days_held={days_held}")
                close_everything(now, "MAX_HEDGE_HOLD")
                open_cycle(now, bid)

    # data ended with a cycle still open
    if cur_cycle is not None and cur_cycle.close_time is None:
        cur_cycle.close_time = df["ts_utc"].iloc[-1]
        cur_cycle.close_reason = "DATA_END_STILL_OPEN"
        cur_cycle.mode_at_close = mode
        cycles.append(cur_cycle)

    return cycles, events


def report(year_label, cycles):
    print(f"\n=== {year_label}: {len(cycles)} cycles ===")
    reasons = {}
    max_dur = timedelta(0)
    max_cycle = None
    hedged_count = 0
    for c in cycles:
        dur = c.close_time - c.open_time
        reasons[c.close_reason] = reasons.get(c.close_reason, 0) + 1
        if c.hedge_entry_time is not None:
            hedged_count += 1
        if dur > max_dur:
            max_dur = dur
            max_cycle = c
    print("Close reasons:", reasons)
    print(f"Cycles that reached HEDGED: {hedged_count}")
    if max_cycle is not None:
        hedge_note = f", hedged @ {max_cycle.hedge_entry_time}" if max_cycle.hedge_entry_time else ", never hedged"
        print(f"Longest cycle: {max_dur} (open {max_cycle.open_time} -> close {max_cycle.close_time}, "
              f"reason={max_cycle.close_reason}{hedge_note})")

    # +3 days buffer: calendar-day counting has no bars on a weekend, so a cycle whose
    # day-5 checkpoint would've landed on a Saturday doesn't get evaluated again until
    # Sunday's first bar -- that's expected slack, not a bug, so don't flag it as one.
    cap = timedelta(days=max(MAX_GRID_HOLD_DAYS, MAX_HEDGE_HOLD_DAYS) + 3)
    over_cap = [c for c in cycles if (c.close_time - c.open_time) > cap and c.close_reason != "DATA_END_STILL_OPEN"]
    if over_cap:
        print(f"*** BUG CONFIRMED: {len(over_cap)} cycle(s) exceeded the safety-valve cap ({cap}) without a forced close! ***")
        for c in over_cap[:5]:
            print(f"    open={c.open_time} close={c.close_time} dur={c.close_time-c.open_time} reason={c.close_reason} hedge_entry={c.hedge_entry_time}")
    else:
        print(f"No cycle exceeded the safety-valve cap ({cap}) -- MaxGridHoldDays/MaxHedgeHoldDays behaved as intended in simulation.")


if __name__ == "__main__":
    parquet_path = sys.argv[1] if len(sys.argv) > 1 else "analysis/usdjpy_m1.parquet"
    full = pd.read_parquet(parquet_path)
    full["year"] = full["ts_utc"].dt.year

    all_cycles = []
    for yr, g in full.groupby("year"):
        g = g.sort_values("ts_utc").reset_index(drop=True)
        cycles, events = simulate(g, str(yr))
        report(str(yr), cycles)
        all_cycles.extend(cycles)
        if events:
            print(f"  (sample forced-close events, first 5): {events[:5]}")

    report("ALL YEARS COMBINED", all_cycles)
