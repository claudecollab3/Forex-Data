//+------------------------------------------------------------------+
//|                                          FifthTouchHedgeEA.mq5    |
//|                                                                    |
//| Direct MQL5 port of fifth_touch_hedge_strategy.py, as tested and  |
//| accepted (11 profitable years / 1 accepted loss year across 12    |
//| real historical year-pair backtests, GBPUSD/USDJPY/EURUSD/XAUUSD).|
//|                                                                    |
//| STRATEGY LOGIC (unchanged from the Python backtest):              |
//|  1. Open a single-direction (long) "cycle" at the current price.  |
//|  2. Grid: every time price falls GridStepPips below the LAST grid |
//|     entry, add a new doubled-lot long position (cascading         |
//|     distance trigger), capped at MaxGridLevels.                   |
//|  3. Touches: independently, count how many times price crosses    |
//|     +/-10 pips from the CYCLE'S OPEN price and fully returns      |
//|     inside before the next touch can count (side-agnostic).       |
//|  4. On the 5th touch: freeze the grid (no more distance-based     |
//|     adds) and open ONE opposite-direction hedge position, sized   |
//|     as double the last grid level's lot. Carry both overnight.    |
//|  5. Each new day, once 240 minutes (4h) of that day have elapsed, |
//|     compute the choppy-day formula ONCE using that day's own      |
//|     early session. While it says "choppy", keep holding.          |
//|  6. The moment a NEW day (after the hedge-entry day) computes     |
//|     "not choppy" -> close everything (grid + hedge), realize P&L, |
//|     and immediately open a fresh cycle at the current price.      |
//|  7. If the grid recovers to weighted-average + CloseProfitPips    |
//|     BEFORE the 5th touch ever happens, close it as a normal win   |
//|     and open a fresh cycle immediately.                           |
//|                                                                    |
//| CHOPPY-DAY FORMULA (fit on GBPUSD 2023, ~85% cross-validated      |
//| accuracy -- see choppy_day_formula.py for the derivation):        |
//|   score = 0.3178 * early_touches_4h                               |
//|         - 0.0643 * early_max_excursion_pips                       |
//|         + 0.4705 * impulse_window_touches   (touches 00:00-04:00) |
//|         - 2.5169                                                  |
//|   score > 0  ->  CHOPPY                                           |
//|                                                                    |
//| REQUIREMENTS / IMPORTANT NOTES:                                   |
//|  - Requires a HEDGING-mode MT5 account (not netting). The strategy|
//|    holds simultaneous long and short positions on the same symbol |
//|    once a hedge opens -- a netting account will net them into one |
//|    position and silently break the logic. Most brokers outside    |
//|    the US offer hedging accounts; check before running this.      |
//|  - "Day" boundaries and the 00:00-04:00 impulse window use the    |
//|    MT5 SERVER time (TimeCurrent()), matching how the backtest     |
//|    used the raw broker timestamps in the HistData files. If your  |
//|    broker's server timezone differs from what the formula was fit |
//|    on (EST-based HistData), the impulse-window feature specifically|
//|    may need the ImpulseWindowStartHour/EndHour inputs adjusted.   |
//|  - This EA is single-direction-long-only by design, matching every|
//|    backtest run. It does not also run a short-side cycle.         |
//|  - PAPER/DEMO TEST FIRST. See the conversation this was built in  |
//|    for the full list of validated vs. unvalidated assumptions     |
//|    (spread costs were modeled at ~1.5-1.8 pips forex / $0.25 gold;|
//|    live slippage, margin requirements, and execution behavior at  |
//|    the exact moment of a hedge-open or unwind have NOT been live- |
//|    tested).                                                       |
//|                                                                    |
//| v1.01 FIX: grid-add and hedge lot sizes (StartLot*Multiplier^n)   |
//|    were sent to the broker with no rounding to SYMBOL_VOLUME_STEP |
//|    and no clamp to SYMBOL_VOLUME_MIN/MAX. Any Multiplier other    |
//|    than one that keeps every level an exact multiple of the       |
//|    broker's lot step (e.g. 2.0 with a 0.01 step) produced lot     |
//|    sizes like 0.015 or 0.0225 that most brokers reject outright.  |
//|    The rejected order failed silently (logged, not alerted) and   |
//|    the grid got stuck re-attempting the same invalid size forever |
//|    -- this is what showed up as "missing a lot of trades" after   |
//|    changing Multiplier. Fixed via NormalizeLot(), applied to every|
//|    lot size before it's sent, plus a startup projection in        |
//|    OnInit() that logs the full lot ladder so a bad Multiplier is  |
//|    visible immediately instead of discovered mid-run.             |
//+------------------------------------------------------------------+
#property copyright "Built collaboratively -- see chat history for full backtest validation"
#property version   "1.01"
#property strict

#include <Trade\Trade.mqh>

//--- Inputs -----------------------------------------------------------
input double StartLot              = 0.01;   // Initial / first grid level lot size
input double Multiplier            = 2.0;    // Doubling multiplier per grid level / hedge
input double GridStepPips          = 10.0;   // Distance (pips) between cascading grid adds
input double CloseProfitPips       = 5.0;    // Profit (pips above weighted avg) to close a winning cycle
input int    MaxGridLevels         = 8;      // Hard cap on grid depth (safety cap -- do not remove)
input double TouchBandPips         = 10.0;   // Band half-width (pips) for touch counting, from cycle open
input int    TouchesToHedge        = 5;      // Number of touches that triggers the hedge lock
input int    EarlyWindowMinutes    = 240;    // Minutes after day-start to compute the choppy formula (4h)
input int    ImpulseWindowStartHr  = 0;      // Impulse window start hour (server time), see notes above
input int    ImpulseWindowEndHr    = 4;      // Impulse window end hour (server time, exclusive)
input ulong  MagicNumber           = 555001; // Unique magic number for this EA's positions
input string TradeComment          = "5thTouchHedge";

//--- Choppy-day formula coefficients (see choppy_day_formula.py) ------
double COEF_TOUCHES  =  0.3178;
double COEF_MAXEXC   = -0.0643;
double COEF_IMPULSE  =  0.4705;
double COEF_INTERCEPT= -2.5169;

//--- Globals ------------------------------------------------------------
CTrade trade;

enum StrategyMode { MODE_FLAT, MODE_GRID, MODE_HEDGED };
StrategyMode mode = MODE_FLAT;

double pip;                  // pip size in price units, adjusted for 3/5-digit brokers
double cycleOpenPrice = 0;
datetime cycleOpenTime = 0;
int    touchesThisCycle = 0;
bool   stateOutside = false;

// shadow tracking of the grid (long side) -- tickets in the order opened
ulong  gridTickets[];
double gridLots[];
double gridPrices[];

ulong  hedgeTicket = 0;
double hedgeLot = 0;

datetime hedgeEntryDayStart = 0;

datetime currentDayStart = 0;
bool     dayFormulaComputed = false;
bool     dayIsChoppy = true;   // default true (safe/conservative) until computed

datetime lastBarTime = 0;

double NormalizeLot(double lot); // forward declaration (defined below, used here and in OnInit)

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(20);

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   pip = _Point;
   if(digits == 3 || digits == 5)
      pip = _Point * 10;

   Print("FifthTouchHedgeEA initialized. Symbol=", _Symbol, " pip=", pip,
         " digits=", digits, " -- REQUIRES A HEDGING-MODE ACCOUNT.");

   if((ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      Print("WARNING: this account is NOT in hedging mode. The 5th-touch hedge will NOT work correctly ",
            "(the broker will net the hedge against the grid instead of holding both). ",
            "Switch to a hedging-enabled account before running this live.");
   }

   ArrayResize(gridTickets, 0);
   ArrayResize(gridLots, 0);
   ArrayResize(gridPrices, 0);

   //--- diagnostic: project the lot size at every grid level + the hedge for the
   //    CURRENT Multiplier/StartLot, so a broker-incompatible Multiplier (one that
   //    doesn't land on SYMBOL_VOLUME_STEP) is caught here instead of showing up
   //    later as silently missed grid adds / hedges.
   {
      double stepVol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double minVol  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      double maxVol  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
      Print("Broker volume constraints for ", _Symbol, ": step=", stepVol, " min=", minVol, " max=", maxVol);

      double rawLot = StartLot;
      bool anyDistorted = false;
      string proj = "Projected lot sizes with StartLot=" + DoubleToString(StartLot, 2) +
                    ", Multiplier=" + DoubleToString(Multiplier, 4) + " -> [";
      for(int i = 0; i < MaxGridLevels; i++)
      {
         double norm = NormalizeLot(rawLot);
         proj += DoubleToString(norm, 4);
         if(i < MaxGridLevels - 1) proj += ", ";
         if(MathAbs(norm - rawLot) > 0.0000001) anyDistorted = true;
         rawLot = rawLot * Multiplier;
      }
      double hedgeRaw = rawLot; // one more Multiplier step, matches OpenHedge()'s lastLot*Multiplier
      double hedgeNorm = NormalizeLot(hedgeRaw);
      if(MathAbs(hedgeNorm - hedgeRaw) > 0.0000001) anyDistorted = true;
      proj += "], hedge -> " + DoubleToString(hedgeNorm, 4);
      Print(proj);

      if(anyDistorted)
         Print("WARNING: with this Multiplier, one or more grid/hedge lot sizes do NOT land exactly on the ",
               "broker's volume step and had to be rounded/clamped (see NormalizeLot log lines above/below). ",
               "That rounding is now handled automatically, but if you were previously missing grid adds or ",
               "hedges after changing Multiplier, THIS was the cause -- raw sizes like StartLot*Multiplier^n ",
               "were being rejected outright by the broker with no rounding at all.");
   }

   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {}

//+------------------------------------------------------------------+
//| Returns true once per new M1 bar                                  |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime t = iTime(_Symbol, PERIOD_M1, 0);
   if(t != lastBarTime)
   {
      lastBarTime = t;
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Start-of-day timestamp (server time) for a given time             |
//+------------------------------------------------------------------+
datetime DayStart(datetime t)
{
   MqlDateTime dt;
   TimeToStruct(t, dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   return StructToTime(dt);
}

//+------------------------------------------------------------------+
//| Compute the choppy-day formula for [dayStart, dayStart+EarlyWindowMinutes] |
//+------------------------------------------------------------------+
bool ComputeChoppyScore(datetime dayStart, double &scoreOut)
{
   datetime windowEnd = dayStart + EarlyWindowMinutes * 60;
   int startIdx = iBarShift(_Symbol, PERIOD_M1, dayStart, false);
   int endIdx   = iBarShift(_Symbol, PERIOD_M1, windowEnd, false);
   if(startIdx < 0 || endIdx < 0 || startIdx <= endIdx)
      return false; // not enough bars yet

   // sanity check: the resolved start bar should actually fall on dayStart's calendar day
   // (guards against weekend/holiday gaps where the nearest bar is a different day)
   datetime resolvedStart = iTime(_Symbol, PERIOD_M1, startIdx);
   if(DayStart(resolvedStart) != dayStart)
      return false;

   int n = startIdx - endIdx + 1;
   if(n < 2) return false;

   double dayOpen = iOpen(_Symbol, PERIOD_M1, startIdx);

   bool wasOutside = false;
   int earlyTouches = 0;
   int impulseTouches = 0;
   double maxExcPips = 0;

   // iterate chronologically: index startIdx (oldest in window) down to endIdx (newest)
   for(int idx = startIdx; idx >= endIdx; idx--)
   {
      double hi = iHigh(_Symbol, PERIOD_M1, idx);
      double lo = iLow(_Symbol, PERIOD_M1, idx);
      datetime barTime = iTime(_Symbol, PERIOD_M1, idx);

      double hiOff = (hi - dayOpen) / pip;
      double loOff = (lo - dayOpen) / pip;
      if(hiOff > maxExcPips) maxExcPips = hiOff;
      if(-loOff > maxExcPips) maxExcPips = -loOff;

      bool touchedNow = (hiOff >= TouchBandPips) || (loOff <= -TouchBandPips);
      if(touchedNow)
      {
         if(!wasOutside)
         {
            earlyTouches++;
            MqlDateTime dts;
            TimeToStruct(barTime, dts);
            if(dts.hour >= ImpulseWindowStartHr && dts.hour < ImpulseWindowEndHr)
               impulseTouches++;
         }
         wasOutside = true;
      }
      else
      {
         wasOutside = false;
      }
   }

   scoreOut = COEF_TOUCHES * earlyTouches + COEF_MAXEXC * maxExcPips + COEF_IMPULSE * impulseTouches + COEF_INTERCEPT;
   return true;
}

//+------------------------------------------------------------------+
//| Round a requested lot size to a volume this SYMBOL/broker will     |
//| actually accept: snap to SYMBOL_VOLUME_STEP, clamp to             |
//| SYMBOL_VOLUME_MIN/MAX. Without this, any Multiplier that doesn't   |
//| keep StartLot*Multiplier^n exactly on the broker's lot step (e.g.  |
//| 0.01 * 1.5 = 0.015) gets silently rejected by the broker as an     |
//| invalid volume -- AddGridLevel()/OpenHedge() then fail every bar   |
//| from that point on, which shows up as the EA "missing" grid adds   |
//| and hedges after the multiplier is changed from the default 2.0.   |
//+------------------------------------------------------------------+
double NormalizeLot(double lot)
{
   double stepVol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minVol   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxVol   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(stepVol <= 0) stepVol = 0.01;

   double rounded = MathRound(lot / stepVol) * stepVol;
   rounded = MathMax(minVol, MathMin(maxVol, rounded));

   // derive decimal places from the step (0.01 -> 2, 0.1 -> 1, 1.0 -> 0, ...)
   int decimals = 0;
   double s = stepVol;
   while(MathAbs(s - MathRound(s)) > 0.0000001 && decimals < 8) { s *= 10; decimals++; }
   rounded = NormalizeDouble(rounded, decimals);

   if(MathAbs(rounded - lot) > 0.0000001)
      Print("NormalizeLot: requested ", DoubleToString(lot, 8), " -> broker volume ", rounded,
            " (step=", stepVol, ", min=", minVol, ", max=", maxVol, ")");

   return rounded;
}

//+------------------------------------------------------------------+
//| Grid helpers                                                       |
//+------------------------------------------------------------------+
double GridTotalLot()
{
   double s = 0;
   for(int i = 0; i < ArraySize(gridLots); i++) s += gridLots[i];
   return s;
}

double GridWeightedAvg()
{
   double totLot = 0, totVal = 0;
   for(int i = 0; i < ArraySize(gridLots); i++)
   {
      totLot += gridLots[i];
      totVal += gridLots[i] * gridPrices[i];
   }
   if(totLot <= 0) return 0;
   return totVal / totLot;
}

void ClearGridArrays()
{
   ArrayResize(gridTickets, 0);
   ArrayResize(gridLots, 0);
   ArrayResize(gridPrices, 0);
}

//+------------------------------------------------------------------+
//| Open a new cycle: initial long entry at current price             |
//+------------------------------------------------------------------+
void OpenCycle()
{
   double lot = NormalizeLot(StartLot);
   double price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   trade.Buy(lot, _Symbol, price, 0, 0, TradeComment);
   if(trade.ResultRetcode() == TRADE_RETCODE_DONE)
   {
      ClearGridArrays();
      ArrayResize(gridTickets, 1); ArrayResize(gridLots, 1); ArrayResize(gridPrices, 1);
      gridTickets[0] = trade.ResultOrder();
      gridLots[0] = lot;
      gridPrices[0] = trade.ResultPrice();
      cycleOpenPrice = trade.ResultPrice();
      cycleOpenTime = TimeCurrent();
      touchesThisCycle = 0;
      stateOutside = false;
      mode = MODE_GRID;
      Print("Cycle opened @ ", cycleOpenPrice, " lot=", lot);
   }
   else
   {
      Print("OpenCycle FAILED: ", trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Add the next doubled grid level (cascading distance trigger)      |
//+------------------------------------------------------------------+
void AddGridLevel()
{
   double lastLot = gridLots[ArraySize(gridLots) - 1];
   double nextLot = NormalizeLot(lastLot * Multiplier);
   double price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   trade.Buy(nextLot, _Symbol, price, 0, 0, TradeComment);
   if(trade.ResultRetcode() == TRADE_RETCODE_DONE)
   {
      int n = ArraySize(gridTickets);
      ArrayResize(gridTickets, n + 1);
      ArrayResize(gridLots, n + 1);
      ArrayResize(gridPrices, n + 1);
      gridTickets[n] = trade.ResultOrder();
      gridLots[n] = nextLot;
      gridPrices[n] = trade.ResultPrice();
      Print("Grid level ", n + 1, " added @ ", trade.ResultPrice(), " lot=", nextLot);
   }
   else
   {
      Print("AddGridLevel FAILED: ", trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Open the opposite-direction hedge on the 5th touch                |
//+------------------------------------------------------------------+
void OpenHedge()
{
   double lastLot = gridLots[ArraySize(gridLots) - 1];
   double hLot = NormalizeLot(lastLot * Multiplier);
   double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   trade.Sell(hLot, _Symbol, price, 0, 0, TradeComment + "_hedge");
   if(trade.ResultRetcode() == TRADE_RETCODE_DONE)
   {
      hedgeTicket = trade.ResultOrder();
      hedgeLot = hLot;
      hedgeEntryDayStart = currentDayStart;
      mode = MODE_HEDGED;
      Print("HEDGE opened @ ", trade.ResultPrice(), " lot=", hLot,
            " (grid frozen at ", ArraySize(gridLots), " levels, total ", GridTotalLot(), " lots)");
   }
   else
   {
      Print("OpenHedge FAILED: ", trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Close every position this EA holds (grid + hedge), realize P&L    |
//+------------------------------------------------------------------+
void CloseEverything()
{
   for(int i = ArraySize(gridTickets) - 1; i >= 0; i--)
   {
      if(PositionSelectByTicket(gridTickets[i]))
         trade.PositionClose(gridTickets[i]);
   }
   if(hedgeTicket != 0 && PositionSelectByTicket(hedgeTicket))
      trade.PositionClose(hedgeTicket);

   ClearGridArrays();
   hedgeTicket = 0;
   hedgeLot = 0;
   mode = MODE_FLAT;
}

//+------------------------------------------------------------------+
//| Main per-bar strategy step                                        |
//+------------------------------------------------------------------+
void ManageStrategy()
{
   datetime now = TimeCurrent();
   datetime todayStart = DayStart(now);

   //--- day rollover: reset the once-per-day formula computation
   if(todayStart != currentDayStart)
   {
      currentDayStart = todayStart;
      dayFormulaComputed = false;
   }

   //--- compute today's choppy formula once, after EarlyWindowMinutes has elapsed
   if(!dayFormulaComputed && (now - currentDayStart) >= EarlyWindowMinutes * 60)
   {
      double score;
      if(ComputeChoppyScore(currentDayStart, score))
      {
         dayIsChoppy = (score > 0);
         dayFormulaComputed = true;
         Print("Day formula computed: score=", score, " -> ", (dayIsChoppy ? "CHOPPY" : "NOT choppy"));
      }
   }

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double hi  = iHigh(_Symbol, PERIOD_M1, 0);
   double lo  = iLow(_Symbol, PERIOD_M1, 0);

   if(mode == MODE_FLAT)
   {
      OpenCycle(); // entry is NOT gated by the choppy formula in this (accepted) version
      return;
   }

   if(mode == MODE_GRID)
   {
      double upper = cycleOpenPrice + TouchBandPips * pip;
      double lower = cycleOpenPrice - TouchBandPips * pip;
      bool touched = (hi >= upper) || (lo <= lower);

      if(touched)
      {
         if(!stateOutside)
         {
            touchesThisCycle++;
            stateOutside = true;
            if(touchesThisCycle >= TouchesToHedge)
            {
               OpenHedge();
               return; // mode is now MODE_HEDGED
            }
         }
      }
      else
      {
         stateOutside = false;
      }

      // cascading grid add (independent trigger from touches), capped depth
      if(mode == MODE_GRID && ArraySize(gridLots) < MaxGridLevels)
      {
         double lastPrice = gridPrices[ArraySize(gridPrices) - 1];
         if(bid <= lastPrice - GridStepPips * pip)
            AddGridLevel();
      }

      // profit-close check
      if(mode == MODE_GRID)
      {
         double avg = GridWeightedAvg();
         if(bid >= avg + CloseProfitPips * pip)
         {
            Print("Grid closed on profit @ ", bid, " (avg was ", avg, ")");
            CloseEverything();
            OpenCycle();
         }
      }
   }
   else if(mode == MODE_HEDGED)
   {
      // unwind signal: a genuinely NEW day (past the hedge-entry day) computes "not choppy"
      if(currentDayStart != hedgeEntryDayStart && dayFormulaComputed && !dayIsChoppy)
      {
         Print("Unwind signal @ ", bid, " -- closing hedge + grid, opening fresh cycle");
         CloseEverything();
         OpenCycle();
      }
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(!IsNewBar()) return; // this strategy operates on closed M1 bars, matching the backtest
   ManageStrategy();
}
//+------------------------------------------------------------------+
