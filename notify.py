import os
import logging
from typing import Optional

import requests

logger = logging.getLogger("xstock-bot")

_TOKEN: Optional[str] = None
_CHAT_ID: Optional[str] = None


def _init() -> bool:
    global _TOKEN, _CHAT_ID
    _TOKEN = os.getenv("TELEGRAM_TOKEN")
    _CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    return bool(_TOKEN and _CHAT_ID)


def _send(text: str) -> None:
    if not _init():
        logger.debug("Telegram not configured — skipping notification")
        return
    try:
        url = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": _CHAT_ID, "text": text},
                             timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)


def send_buy(
    symbol: str,
    tranche: int,
    price: float,
    units: float,
    usd_spent: float,
    avg_entry: float,
    pnl_pct: float,
) -> None:
    text = (
        f"🟡 xStock BUY | {symbol} | Tranche {tranche}\n"
        f"Price: ${price:.4f} | Units: {units:.6f} | Spent: ${usd_spent:.2f}\n"
        f"Avg entry: ${avg_entry:.4f} | Unrealised P&L: {pnl_pct:+.2f}%"
    )
    _send(text)


def send_sell(
    symbol: str,
    reason: str,
    price: float,
    avg_entry: float,
    pnl_pct: float,
    held_days: int,
    usd_pnl: float,
) -> None:
    text = (
        f"🟡 xStock SELL | {symbol} | {reason}\n"
        f"Price: ${price:.4f} | Avg entry: ${avg_entry:.4f}\n"
        f"P&L: {pnl_pct:+.2f}% (${usd_pnl:+.2f}) | Held: {held_days}d"
    )
    _send(text)


def send_emergency(symbol: str, unrealised_pct: float) -> None:
    text = (
        f"🟡 xStock EMERGENCY PAUSE | {symbol}\n"
        f"Unrealised loss: {unrealised_pct:.2f}% — new entries suspended"
    )
    _send(text)


def send_startup(symbols_str: str, paper: bool) -> None:
    mode = "PAPER" if paper else "LIVE"
    _send(f"🟡 xStock Bot starting | {mode} | {symbols_str}")


def send_daily_summary(lines: list) -> None:
    body = "\n".join(lines)
    _send(f"🟡 xStock Daily Summary\n{body}")
