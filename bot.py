import os
import signal
import sys
import time
from datetime import date, datetime
from typing import Any, Dict, Optional

import pytz
import schedule
from dotenv import load_dotenv

load_dotenv()

from config import POLL_INTERVAL, TOTAL_BUDGET_PCT, SYMBOLS, DAILY_BAR_LIMIT
from logger_setup import setup_logger
from notify import send_startup, send_buy, send_sell, send_emergency, send_daily_summary
from state_store import load_state, save_state, reset_state
from strategy import fetch_daily_bars, compute_rsi, compute_ma200, get_signal
from xstock_client import XStockClient

logger = setup_logger()

_running = True


def _handle_sigterm(signum: int, frame: Any) -> None:
    global _running
    logger.info("SIGTERM received — shutting down gracefully")
    _running = False


signal.signal(signal.SIGTERM, _handle_sigterm)


# ------------------------------------------------------------------ #
# NYSE hours guard                                                     #
# ------------------------------------------------------------------ #

def _is_market_hours() -> bool:
    """True if current UTC time falls within NYSE trading hours (Mon-Fri 09:30-16:00 ET)."""
    et_tz = pytz.timezone("America/New_York")
    now_et = datetime.now(tz=et_tz)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et < market_close


# ------------------------------------------------------------------ #
# Per-symbol execution                                                 #
# ------------------------------------------------------------------ #

def _held_days(state: Dict[str, Any]) -> int:
    entries = state.get("entries", [])
    if not entries:
        return 0
    try:
        first_date = datetime.strptime(entries[0]["date"], "%Y-%m-%d").date()
        return (date.today() - first_date).days
    except (KeyError, ValueError):
        return 0


def _process_symbol(
    symbol: str,
    cfg: Dict[str, Any],
    client: XStockClient,
    zusd_balance: float,
    xstock_budget: float,
) -> None:
    state = load_state(symbol)

    # ---- Snapshot cycle budget on first tranche -------------------- #
    if not any(state["tranches_bought"]) and state.get("cycle_budget_usd") is None:
        cycle_budget = xstock_budget * cfg["alloc_pct"]
        state["cycle_budget_usd"] = cycle_budget
        logger.info("%s | cycle budget snapshotted: $%.2f", symbol, cycle_budget)

    # ---- Fetch price ----------------------------------------------- #
    price = client.get_price(cfg["futures_symbol"])
    if price is None:
        logger.error("%s | could not fetch price — skipping", symbol)
        return

    # ---- Update peak price ----------------------------------------- #
    if state.get("total_units", 0.0) > 0:
        peak = state.get("peak_price")
        if peak is None or price > peak:
            state["peak_price"] = price

    # ---- Fetch daily bars ------------------------------------------ #
    closes = fetch_daily_bars(cfg["yf_ticker"], DAILY_BAR_LIMIT)
    if not closes:
        logger.error("%s | could not fetch daily bars — skipping", symbol)
        return

    rsi = compute_rsi(closes)
    ma200 = compute_ma200(closes)
    tranches_active = sum(1 for t in state["tranches_bought"] if t)
    avg_entry = state.get("avg_entry_price")

    pnl_pct: Optional[float] = None
    if avg_entry and state.get("total_units", 0.0) > 0:
        pnl_pct = ((price - avg_entry) / avg_entry) * 100.0

    logger.info(
        "%s | price=%.4f rsi=%s ma200=%s tranches=%d pnl=%s",
        symbol,
        price,
        f"{rsi:.1f}" if rsi is not None else "N/A",
        f"{ma200:.2f}" if ma200 is not None else "N/A",
        tranches_active,
        f"{pnl_pct:+.2f}%" if pnl_pct is not None else "N/A",
    )

    # ---- Get signal ------------------------------------------------ #
    signal_dict = get_signal(symbol, cfg, state, price, closes)
    action = signal_dict["action"]
    reason = signal_dict.get("reason", "")

    logger.info("%s | action=%s reason=%s", symbol, action, reason)

    # ---- Execute action -------------------------------------------- #
    if action == "EMERGENCY":
        if not state.get("emergency_paused"):
            state["emergency_paused"] = True
            save_state(symbol, state)
            send_emergency(symbol, signal_dict.get("pnl_pct", 0.0))
        return

    if action == "SELL":
        _execute_sell(symbol, cfg, client, state, price, reason)
        return

    if action == "BUY":
        _execute_buy(symbol, cfg, client, state, price, signal_dict)
        return

    # HOLD / DEFENSIVE — save updated peak
    save_state(symbol, state)


def _execute_buy(
    symbol: str,
    cfg: Dict[str, Any],
    client: XStockClient,
    state: Dict[str, Any],
    price: float,
    sig: Dict[str, Any],
) -> None:
    tranche_idx: int = sig["tranche"]
    usd_amount: float = sig["usd_amount"]

    # Minimum order check
    costmin, lot_decimals = client.get_pair_info(cfg["kraken_pair"])
    costmin = costmin or 1.0
    lot_decimals = lot_decimals if lot_decimals is not None else 8

    if usd_amount < costmin:
        logger.warning("%s | tranche %d usd_amount $%.2f < costmin $%.2f — skipping",
                       symbol, tranche_idx, usd_amount, costmin)
        return

    volume = round(usd_amount / price, lot_decimals)
    if volume <= 0:
        logger.warning("%s | computed volume=0 — skipping", symbol)
        return

    result = client.place_order(cfg["kraken_pair"], "buy", volume)
    if result is None:
        logger.error("%s | order placement failed — state unchanged", symbol)
        return

    # Update state
    state["in_cycle"] = True
    state["tranches_bought"][tranche_idx] = True
    state["total_invested_usd"] = state.get("total_invested_usd", 0.0) + usd_amount
    state["total_units"] = state.get("total_units", 0.0) + volume
    state["last_buy_date"] = date.today().isoformat()

    new_total_units = state["total_units"]
    new_total_invested = state["total_invested_usd"]
    avg_entry = new_total_invested / new_total_units if new_total_units else price
    state["avg_entry_price"] = avg_entry

    if state.get("peak_price") is None or price > state["peak_price"]:
        state["peak_price"] = price

    state.setdefault("entries", []).append({
        "tranche": tranche_idx,
        "date": date.today().isoformat(),
        "price": price,
        "volume": volume,
        "usd": usd_amount,
    })

    pnl_pct = ((price - avg_entry) / avg_entry) * 100.0 if avg_entry else 0.0

    save_state(symbol, state)

    send_buy(
        symbol=symbol,
        tranche=tranche_idx + 1,
        price=price,
        units=volume,
        usd_spent=usd_amount,
        avg_entry=avg_entry,
        pnl_pct=pnl_pct,
    )
    logger.info("%s | BUY tranche %d: %.6f units @ $%.4f ($%.2f)",
                symbol, tranche_idx + 1, volume, price, usd_amount)


def _execute_sell(
    symbol: str,
    cfg: Dict[str, Any],
    client: XStockClient,
    state: Dict[str, Any],
    price: float,
    reason: str,
) -> None:
    total_units = state.get("total_units", 0.0)
    if total_units <= 0:
        logger.warning("%s | SELL signal but no units to sell", symbol)
        return

    _, lot_decimals = client.get_pair_info(cfg["kraken_pair"])
    lot_decimals = lot_decimals if lot_decimals is not None else 8
    volume = round(total_units, lot_decimals)

    result = client.place_order(cfg["kraken_pair"], "sell", volume)
    if result is None:
        logger.error("%s | sell order placement failed — state unchanged", symbol)
        return

    avg_entry = state.get("avg_entry_price") or price
    total_invested = state.get("total_invested_usd", 0.0)
    proceeds = price * volume
    pnl_usd = proceeds - total_invested
    pnl_pct = (pnl_usd / total_invested * 100.0) if total_invested else 0.0
    held = _held_days(state)

    send_sell(
        symbol=symbol,
        reason=reason,
        price=price,
        avg_entry=avg_entry,
        pnl_pct=pnl_pct,
        held_days=held,
        usd_pnl=pnl_usd,
    )
    logger.info("%s | SELL %s: %.6f units @ $%.4f pnl=%+.2f%% ($%+.2f)",
                symbol, reason, volume, price, pnl_pct, pnl_usd)

    # Reset state after full exit
    reset_state(symbol)


# ------------------------------------------------------------------ #
# Daily summary                                                        #
# ------------------------------------------------------------------ #

def _daily_summary(client: XStockClient) -> None:
    lines = [f"Date: {date.today().isoformat()}"]
    for symbol, cfg in SYMBOLS.items():
        state = load_state(symbol)
        price = client.get_price(cfg["futures_symbol"])
        avg_entry = state.get("avg_entry_price")
        units = state.get("total_units", 0.0)
        invested = state.get("total_invested_usd", 0.0)
        tranches = sum(1 for t in state["tranches_bought"] if t)

        if price and avg_entry and units:
            pnl_pct = ((price - avg_entry) / avg_entry) * 100.0
            pnl_usd = (price * units) - invested
            lines.append(
                f"{symbol}: ${price:.2f} | T{tranches} | avg ${avg_entry:.2f} "
                f"| P&L {pnl_pct:+.2f}% (${pnl_usd:+.2f})"
            )
        else:
            lines.append(f"{symbol}: no open position")

    send_daily_summary(lines)
    logger.info("Daily summary sent")


# ------------------------------------------------------------------ #
# Main loop                                                            #
# ------------------------------------------------------------------ #

def main() -> None:
    global _running

    paper = os.getenv("PAPER_TRADE", "false").lower() == "true"
    client = XStockClient()

    symbols_str = " ".join(SYMBOLS.keys())
    logger.info("xStock Bot starting | %s | %s", "PAPER" if paper else "LIVE", symbols_str)
    send_startup(symbols_str, paper)

    # Schedule daily summary at 08:00 UTC
    schedule.every().day.at("08:00").do(_daily_summary, client=client)

    while _running:
        try:
            schedule.run_pending()

            if not _is_market_hours():
                logger.debug("Outside NYSE hours — sleeping %ds", POLL_INTERVAL)
                _sleep_interruptible(POLL_INTERVAL)
                continue

            zusd = client.get_zusd_balance()
            if zusd is None:
                logger.error("Could not fetch ZUSD balance — skipping cycle")
                _sleep_interruptible(POLL_INTERVAL)
                continue

            xstock_budget = zusd * TOTAL_BUDGET_PCT
            logger.info("ZUSD balance: $%.2f | xstock budget: $%.2f", zusd, xstock_budget)

            for symbol, cfg in SYMBOLS.items():
                if not _running:
                    break
                try:
                    _process_symbol(symbol, cfg, client, zusd, xstock_budget)
                except Exception as exc:
                    logger.exception("Unhandled error processing %s: %s", symbol, exc)

        except Exception as exc:
            logger.exception("Unhandled error in main loop: %s", exc)

        _sleep_interruptible(POLL_INTERVAL)

    logger.info("xStock Bot stopped")


def _sleep_interruptible(seconds: int) -> None:
    """Sleep in 1-second chunks so SIGTERM is handled promptly."""
    global _running
    for _ in range(seconds):
        if not _running:
            break
        time.sleep(1)


if __name__ == "__main__":
    main()
