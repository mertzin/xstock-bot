"""
risk_controls.py — Pure-function risk management utilities.

No side effects, no framework imports, no I/O.
One copy lives in each bot directory so each bot can import it without
cross-directory path hacks.

Functions
─────────
btc_regime()         — RISK_ON / RISK_OFF from BTC price vs its MA200
compute_atr()        — ATR(14) via Wilder smoothing (V1 tranche spacing)
portfolio_exposure() — deployed / total_equity ratio (exposure cap check)
"""
from typing import List, Optional


def btc_regime(
    btc_closes: List[float],
    ma_period: int = 200,
    risk_off_factor: float = 0.50,
) -> tuple:
    """Determine BTC market regime from a sequence of daily close prices.

    Returns (regime, btc_price, ma200, size_factor) where:
      regime       — 'RISK_ON' or 'RISK_OFF'
      btc_price    — most recent close (float, or nan when btc_closes is empty)
      ma200        — MA200 value (float) or None when fewer than ma_period bars
      size_factor  — 1.0 for RISK_ON, risk_off_factor for RISK_OFF

    Defaults to RISK_ON (fail-open) whenever the MA cannot be computed.
    """
    if not btc_closes:
        return ("RISK_ON", float("nan"), None, 1.0)
    btc_price = float(btc_closes[-1])
    if len(btc_closes) < ma_period:
        return ("RISK_ON", btc_price, None, 1.0)
    ma200 = sum(btc_closes[-ma_period:]) / ma_period
    if btc_price >= ma200:
        return ("RISK_ON", btc_price, ma200, 1.0)
    return ("RISK_OFF", btc_price, ma200, risk_off_factor)


def compute_atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14,
) -> Optional[float]:
    """ATR(period) using Wilder smoothing.

    True Range for bar i = max(H[i]-L[i], |H[i]-C[i-1]|, |L[i]-C[i-1]|)
    ATR is seeded as the simple mean of the first `period` True Ranges,
    then updated as: ATR = (ATR * (period-1) + TR) / period.

    Returns None when data is insufficient (fewer than period+1 bars, or
    any of the three input lists is shorter than len(closes)).
    """
    n = len(closes)
    if n < period + 1 or len(highs) < n or len(lows) < n:
        return None
    trs: List[float] = []
    for i in range(1, n):
        h = highs[i]
        l = lows[i]
        c_prev = closes[i - 1]
        trs.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def portfolio_exposure(
    deployed: float,
    total_equity: float,
) -> float:
    """Return deployed / total_equity, or 0.0 when total_equity <= 0."""
    if total_equity <= 0.0:
        return 0.0
    return deployed / total_equity


def load_risk_state(
    risk_state_path: str = "/root/risk_state.json",
    stale_minutes: float = 10.0,
) -> dict:
    """Load and validate the dashboard-written risk_state.json once per cycle.

    Returns a dict with:
      reason               — "ok" | "cap_reached" | "safe_mode_missing" |
                             "safe_mode_parse_error" | "safe_mode_stale"
      entries_allowed      — bool
      exposure_pct         — 0.0–1.0 fraction
      global_deployed_eur  — float
      global_equity_eur    — float
      cap                  — float (e.g. 0.75)
      eur_usd              — float EUR/USD rate (e.g. 1.09)
      acc_eur              — float 0.0  ← mutable: callers add committed EUR per confirmed buy

    Returns a safe-mode dict (entries_allowed=False, all EUR values=0) on any error.
    Fail-closed: missing / stale / unparseable file → safe mode.
    """
    import json as _json
    import os as _os
    from datetime import datetime as _dt, timezone as _tz

    _safe = {
        "reason": "safe_mode_missing", "entries_allowed": False,
        "exposure_pct": 0.0, "global_deployed_eur": 0.0,
        "global_equity_eur": 0.0, "cap": 0.75, "eur_usd": 1.1, "acc_eur": 0.0,
    }

    if not _os.path.exists(risk_state_path):
        return dict(_safe)

    try:
        with open(risk_state_path, "r", encoding="utf-8") as fh:
            state = _json.load(fh)
    except Exception:
        return {**_safe, "reason": "safe_mode_parse_error"}

    updated_at = state.get("updated_at")
    if not updated_at:
        return dict(_safe)

    try:
        ts = _dt.fromisoformat(updated_at.replace("Z", "+00:00"))
        age_secs = (_dt.now(tz=_tz.utc) - ts).total_seconds()
        if age_secs > stale_minutes * 60:
            return {**_safe, "reason": "safe_mode_stale"}
    except Exception:
        return {**_safe, "reason": "safe_mode_stale"}

    cap             = float(state.get("cap", 0.75))
    exposure_pct    = float(state.get("exposure_pct", 0.0))
    entries_allowed = bool(state.get("entries_allowed", False))
    deployed        = float(state.get("global_deployed_eur", 0.0))
    equity          = float(state.get("global_equity_eur", 0.0))
    eur_usd         = float(state.get("eur_usd", 1.1)) or 1.1
    reason          = "ok" if entries_allowed else "cap_reached"
    return {
        "reason":              reason,
        "entries_allowed":     entries_allowed,
        "exposure_pct":        exposure_pct,
        "global_deployed_eur": deployed,
        "global_equity_eur":   equity,
        "cap":                 cap,
        "eur_usd":             eur_usd,
        "acc_eur":             0.0,
    }


def check_global_exposure(
    risk_state_path: str = "/root/risk_state.json",
    stale_minutes: float = 10.0,
) -> tuple:
    """Check the dashboard-written risk_state.json for cross-bot exposure cap.

    Returns (allowed: bool, reason: str, exposure_pct: float) where:
      allowed      — True when entries_allowed=True and file is fresh
      reason       — "ok" | "cap_reached" | "safe_mode_missing" |
                     "safe_mode_parse_error" | "safe_mode_stale"
      exposure_pct — 0.0–1.0 float from the file, or 0.0 if unknown

    Fail-closed: missing / stale / unparseable file blocks new entries.
    """
    import json as _json
    import os as _os
    from datetime import datetime as _dt, timezone as _tz

    if not _os.path.exists(risk_state_path):
        return (False, "safe_mode_missing", 0.0)

    try:
        with open(risk_state_path, "r", encoding="utf-8") as fh:
            state = _json.load(fh)
    except Exception:
        return (False, "safe_mode_parse_error", 0.0)

    exposure_pct = float(state.get("exposure_pct", 0.0))

    updated_at = state.get("updated_at")
    if not updated_at:
        return (False, "safe_mode_missing", exposure_pct)

    try:
        ts = _dt.fromisoformat(updated_at.replace("Z", "+00:00"))
        now = _dt.now(tz=_tz.utc)
        age_secs = (now - ts).total_seconds()
        if age_secs > stale_minutes * 60:
            return (False, "safe_mode_stale", exposure_pct)
    except Exception:
        return (False, "safe_mode_stale", exposure_pct)

    entries_allowed = bool(state.get("entries_allowed", False))
    if not entries_allowed:
        return (False, "cap_reached", exposure_pct)
    return (True, "ok", exposure_pct)
