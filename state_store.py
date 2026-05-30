import json
import os
import tempfile
from typing import Any, Dict, Optional

DEFAULT_STATE: Dict[str, Any] = {
    "in_cycle": False,
    "tranches_bought": [False, False, False, False],
    "entries": [],
    "avg_entry_price": None,
    "total_invested_usd": 0.0,
    "total_units": 0.0,
    "peak_price": None,
    "cycle_budget_usd": None,
    "last_buy_date": None,
    "last_buy_ts": None,      # ISO datetime (UTC) for sub-day cooldown tracking
    "emergency_paused": False,
    "peak_profit_pct": 0.0,
}


def _state_path(symbol: str) -> str:
    slug = symbol.lower().replace("/", "_")
    return f"state_{slug}.json"


def load_state(symbol: str) -> Dict[str, Any]:
    path = _state_path(symbol)
    if not os.path.exists(path):
        return dict(DEFAULT_STATE)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        # Fill any keys added after initial creation
        for key, default in DEFAULT_STATE.items():
            if key not in data:
                data[key] = default
        return data
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_STATE)


def save_state(symbol: str, state: Dict[str, Any]) -> None:
    path = _state_path(symbol)
    dir_ = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def reset_state(symbol: str) -> Dict[str, Any]:
    state = dict(DEFAULT_STATE)
    state["tranches_bought"] = [False, False, False, False]
    state["entries"] = []
    save_state(symbol, state)
    return state
