from typing import Dict, Any

TOTAL_BUDGET_PCT = 0.20  # 20% of ZUSD balance

# ── Enhancement 1: BTC Market Regime Filter ───────────────────────────────
# Fetch BTC-USD daily candles (via yfinance) once per main-loop iteration.
# RISK_OFF (BTC < MA200) multiplies every new tranche USD amount by RISK_OFF_SIZE_FACTOR.
# Only affects new buys — exits, stops, and profit-target sells are never modified.
BTC_REGIME_MA_PERIOD: int   = 200   # MA period for the regime filter
RISK_OFF_SIZE_FACTOR: float = 0.50  # tranche size multiplier when RISK_OFF

# ── Enhancement 2: Portfolio Exposure Cap ─────────────────────────────────
# Block new tranche buys when deployed / total_equity >= PORTFOLIO_EXPOSURE_CAP.
PORTFOLIO_EXPOSURE_CAP: float = 0.75  # 75% deployed triggers the block

# ── Enhancement 4: V2 Trailing Stop ──────────────────────────────────────
# Activates once unrealised profit reaches V2_TRAILING_STOP_ACTIVATION (10%).
# Fires a full-position exit if profit drops V2_TRAILING_STOP_DISTANCE (7%)
# below the peak profit seen since activation.
V2_TRAILING_STOP_ACTIVATION: float = 0.10  # arm threshold (10% unrealised profit)
V2_TRAILING_STOP_DISTANCE:   float = 0.07  # exit if profit drops 7% below peak
POLL_INTERVAL = 3600     # 1 hour
DAILY_BAR_LIMIT = 300    # yfinance bars for RSI + MA200 (4H: 300 bars ≈ 50 days)
TRAILING_STOP_PCT = 0.15 # 15% drop from peak → sell all
EMERGENCY_STOP_PCT = 0.20
EXIT_RSI_FLOOR = 55
MA_DEFENSIVE_PCT = 0.12  # >12% below MA200 → pause new entries

SYMBOLS: Dict[str, Any] = {
    "SPYx": {
        "yf_ticker": "SPY",
        "kraken_pair": "SPYxUSD",
        "futures_symbol": "PF_SPYXUSD",
        "alloc_pct": 0.27,
        "ma_defensive_pct": 0.12,
        "ladder": [
            {"rsi": 45, "pct": 0.20},
            {"rsi": 40, "pct": 0.25},
            {"rsi": 35, "pct": 0.30},
            {"rsi": 30, "pct": 0.25},
        ],
        "profit_targets": [1.5, 3.0, 5.0, 6.0],
    },
    "QQQx": {
        "yf_ticker": "QQQ",
        "kraken_pair": "QQQxUSD",
        "futures_symbol": "PF_QQQXUSD",
        "alloc_pct": 0.27,
        "ma_defensive_pct": 0.12,
        "ladder": [
            {"rsi": 43, "pct": 0.20},
            {"rsi": 38, "pct": 0.25},
            {"rsi": 34, "pct": 0.30},
            {"rsi": 30, "pct": 0.25},
        ],
        "profit_targets": [2.0, 3.0, 5.0, 8.0],
    },
    "AAPLx": {
        "yf_ticker": "AAPL",
        "kraken_pair": "AAPLxUSD",
        "futures_symbol": "PF_AAPLXUSD",
        "alloc_pct": 0.27,
        "ma_defensive_pct": 0.12,
        "ladder": [
            {"rsi": 44, "pct": 0.20},
            {"rsi": 39, "pct": 0.25},
            {"rsi": 34, "pct": 0.30},
            {"rsi": 30, "pct": 0.25},
        ],
        "profit_targets": [1.75, 3.0, 4.0, 5.5],
    },
    "NVDAx": {
        "yf_ticker": "NVDA",
        "kraken_pair": "NVDAxUSD",
        "futures_symbol": "PF_NVDAXUSD",
        "alloc_pct": 0.19,
        "ma_defensive_pct": 0.15,
        "ladder": [
            {"rsi": 42, "pct": 0.15},
            {"rsi": 37, "pct": 0.20},
            {"rsi": 32, "pct": 0.30},
            {"rsi": 28, "pct": 0.35},
        ],
        "profit_targets": [2.0, 3.5, 5.5, 8.0],
    },
}
