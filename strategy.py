import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import yfinance as yf
import numpy as np

from config import (
    TRAILING_STOP_PCT, EMERGENCY_STOP_PCT, EXIT_RSI_FLOOR,
    V2_TRAILING_STOP_ACTIVATION, V2_TRAILING_STOP_DISTANCE,
)

logger = logging.getLogger("xstock-bot")


# ------------------------------------------------------------------ #
# Market data                                                          #
# ------------------------------------------------------------------ #

def fetch_4h_bars(yf_ticker: str, limit: int = 300) -> Optional[List[float]]:
    """Return up to `limit` 4-hour close prices, oldest first.

    180 days × 6 bars/day = ~1 080 bars, giving MA200 headroom on a 4H chart.
    """
    try:
        ticker = yf.Ticker(yf_ticker)
        hist = ticker.history(period="180d", interval="4h")
        if hist.empty:
            logger.warning("yfinance returned empty 4H history for %s", yf_ticker)
            return None
        closes = hist["Close"].dropna().tolist()
        return closes[-limit:]
    except Exception as exc:
        logger.error("fetch_4h_bars(%s) failed: %s", yf_ticker, exc)
        return None


# ------------------------------------------------------------------ #
# Indicators                                                           #
# ------------------------------------------------------------------ #

def compute_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Wilder RSI using EMA-style smoothing."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_ma200(closes: List[float]) -> Optional[float]:
    if len(closes) < 200:
        return None
    return sum(closes[-200:]) / 200.0


# ------------------------------------------------------------------ #
# Signal logic                                                         #
# ------------------------------------------------------------------ #

def _days_since(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        last = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (date.today() - last).days
    except ValueError:
        return None


def _hours_since_last_buy(state: Dict[str, Any]) -> float:
    """Return hours since the last buy.

    Prefers `last_buy_ts` (ISO datetime stored on each buy) for sub-day
    resolution.  Falls back to `last_buy_date` × 24 for states written
    before `last_buy_ts` was added.  Returns 999.0 if no buy has occurred.
    """
    ts_str = state.get("last_buy_ts")
    if ts_str:
        try:
            last_ts = datetime.fromisoformat(ts_str)
            # Make naive datetimes UTC-aware
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            now = datetime.now(tz=timezone.utc)
            return (now - last_ts).total_seconds() / 3600.0
        except (ValueError, OverflowError):
            pass
    # Fallback: day granularity
    days = _days_since(state.get("last_buy_date"))
    return float(days * 24) if days is not None else 999.0


def _unrealised_pnl_pct(state: Dict[str, Any], current_price: float) -> Optional[float]:
    avg = state.get("avg_entry_price")
    if not avg or not state.get("total_units"):
        return None
    return ((current_price - avg) / avg) * 100.0


def _unrealised_pnl_usd(state: Dict[str, Any], current_price: float) -> Optional[float]:
    units = state.get("total_units", 0.0)
    invested = state.get("total_invested_usd", 0.0)
    if not units or not invested:
        return None
    return (current_price * units) - invested


def get_signal(
    symbol: str,
    cfg: Dict[str, Any],
    state: Dict[str, Any],
    current_price: float,
    closes: List[float],
) -> Dict[str, Any]:
    """
    Evaluate strategy and return an action dict:
      { "action": "BUY"|"SELL"|"HOLD"|"DEFENSIVE"|"EMERGENCY",
        "tranche": int,           # BUY only
        "usd_amount": float,      # BUY only
        "reason": str }
    """
    ladder: List[Dict[str, Any]] = cfg["ladder"]
    profit_targets: List[float] = cfg["profit_targets"]
    ma_defensive_pct: float = cfg.get("ma_defensive_pct", 0.12)

    # ---- Compute indicators ---------------------------------------- #
    if len(closes) < 3:
        return {"action": "HOLD", "reason": "insufficient bars"}

    rsi_now: Optional[float] = compute_rsi(closes)
    rsi_prev: Optional[float] = compute_rsi(closes[:-1])
    rsi_prev2: Optional[float] = compute_rsi(closes[:-2])
    ma200: Optional[float] = compute_ma200(closes)

    if rsi_now is None:
        return {"action": "HOLD", "reason": "RSI unavailable"}

    tranches_bought: List[bool] = state.get("tranches_bought", [False] * len(ladder))
    total_units: float = state.get("total_units", 0.0)
    total_invested: float = state.get("total_invested_usd", 0.0)
    peak_price: Optional[float] = state.get("peak_price")
    last_buy_date: Optional[str] = state.get("last_buy_date")
    emergency_paused: bool = state.get("emergency_paused", False)

    # ---- Unrealised P&L -------------------------------------------- #
    pnl_pct = _unrealised_pnl_pct(state, current_price)
    pnl_usd = _unrealised_pnl_usd(state, current_price)

    # ---- Emergency check ------------------------------------------- #
    if total_invested > 0 and pnl_pct is not None:
        if pnl_pct <= -(EMERGENCY_STOP_PCT * 100):
            return {
                "action": "EMERGENCY",
                "reason": f"unrealised loss {pnl_pct:.2f}% exceeds {EMERGENCY_STOP_PCT*100:.0f}%",
                "pnl_pct": pnl_pct,
                "new_peak_profit_pct": 0.0,
            }

    # ---- V2 profit-based trailing stop ----------------------------- #
    # Arms once unrealised profit reaches V2_TRAILING_STOP_ACTIVATION (10%).
    # Fires when profit drops V2_TRAILING_STOP_DISTANCE (7%) below peak profit.
    _peak_profit_pct: float = state.get("peak_profit_pct", 0.0)
    _new_peak_profit_pct: float = _peak_profit_pct
    if total_units > 0 and pnl_pct is not None:
        pnl_frac = pnl_pct / 100.0
        if pnl_frac > _peak_profit_pct:
            _new_peak_profit_pct = pnl_frac
        if (
            _new_peak_profit_pct >= V2_TRAILING_STOP_ACTIVATION
            and pnl_frac <= _new_peak_profit_pct - V2_TRAILING_STOP_DISTANCE
        ):
            return {
                "action": "SELL_TRAILING_STOP",
                "reason": "v2_trailing_stop",
                "pnl_pct": pnl_pct,
                "pnl_usd": pnl_usd,
                "new_peak_profit_pct": _new_peak_profit_pct,
            }

    # ---- Price-based trailing stop --------------------------------- #
    if total_units > 0 and peak_price is not None:
        drop_from_peak = (peak_price - current_price) / peak_price
        if drop_from_peak >= TRAILING_STOP_PCT:
            return {
                "action": "SELL",
                "reason": "trailing_stop",
                "pnl_pct": pnl_pct,
                "pnl_usd": pnl_usd,
                "new_peak_profit_pct": _new_peak_profit_pct,
            }

    # ---- Profit target exit ---------------------------------------- #
    if total_units > 0 and pnl_pct is not None:
        tranches_active = sum(1 for t in tranches_bought if t)
        if tranches_active > 0:
            target_pct = profit_targets[tranches_active - 1]
            rsi_slowing = (
                rsi_prev is not None
                and (rsi_now < rsi_prev or
                     (rsi_prev2 is not None and rsi_prev <= rsi_prev2))
            )
            # 3 × 4H bars = 12 hours cooldown after any buy
            cooldown_ok = _hours_since_last_buy(state) >= 12.0
            if (pnl_pct >= target_pct
                    and rsi_now >= EXIT_RSI_FLOOR
                    and rsi_slowing
                    and cooldown_ok):
                return {
                    "action": "SELL",
                    "reason": "profit_target",
                    "pnl_pct": pnl_pct,
                    "pnl_usd": pnl_usd,
                    "new_peak_profit_pct": _new_peak_profit_pct,
                }

    # ---- Defensive mode (MA200 filter) ----------------------------- #
    in_defensive = False
    if ma200 is not None:
        ma_floor = ma200 * (1.0 - ma_defensive_pct)
        if current_price < ma_floor:
            in_defensive = True

    # ---- New tranche entry ----------------------------------------- #
    if not emergency_paused and not in_defensive:
        # 3 × 4H bars = 12 hours cooldown after any buy
        if _hours_since_last_buy(state) >= 12.0:
            # RSI rising: current > previous, with ±2pt grace
            rsi_rising = rsi_prev is not None and (rsi_now > rsi_prev - 2.0)

            for idx, rung in enumerate(ladder):
                if tranches_bought[idx]:
                    continue
                if rsi_now <= rung["rsi"] and rsi_rising:
                    # First tranche snapshots the budget
                    cycle_budget = state.get("cycle_budget_usd")
                    usd_amount = (cycle_budget or 0.0) * rung["pct"]
                    if usd_amount <= 0:
                        continue
                    return {
                        "action": "BUY",
                        "tranche": idx,
                        "usd_amount": usd_amount,
                        "rsi": rsi_now,
                        "ma200": ma200,
                        "reason": f"rsi={rsi_now:.1f} <= {rung['rsi']}",
                        "new_peak_profit_pct": _new_peak_profit_pct,
                    }

    if in_defensive and total_units == 0:
        return {
            "action": "DEFENSIVE",
            "reason": f"price {current_price:.2f} < MA200 floor {ma200*(1-ma_defensive_pct) if ma200 else 'N/A':.2f}",
            "rsi": rsi_now,
            "ma200": ma200,
            "new_peak_profit_pct": _new_peak_profit_pct,
        }

    return {
        "action": "HOLD",
        "reason": "no conditions met",
        "rsi": rsi_now,
        "ma200": ma200,
        "pnl_pct": pnl_pct,
        "new_peak_profit_pct": _new_peak_profit_pct,
    }
