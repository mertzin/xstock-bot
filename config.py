from typing import Dict, Any

TOTAL_BUDGET_PCT = 0.20  # 20% of ZUSD balance
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
        "alloc_pct": 0.22,
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
        "alloc_pct": 0.22,
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
        "alloc_pct": 0.22,
        "ma_defensive_pct": 0.12,
        "ladder": [
            {"rsi": 44, "pct": 0.20},
            {"rsi": 39, "pct": 0.25},
            {"rsi": 34, "pct": 0.30},
            {"rsi": 30, "pct": 0.25},
        ],
        "profit_targets": [1.75, 3.0, 4.0, 5.5],
    },
    "MSFTx": {
        "yf_ticker": "MSFT",
        "kraken_pair": "MSFTxUSD",
        "futures_symbol": "PF_MSFTXUSD",
        "alloc_pct": 0.22,
        "ma_defensive_pct": 0.12,
        "ladder": [
            {"rsi": 45, "pct": 0.20},
            {"rsi": 40, "pct": 0.25},
            {"rsi": 35, "pct": 0.30},
            {"rsi": 31, "pct": 0.25},
        ],
        "profit_targets": [1.5, 2.5, 4.0, 5.5],
    },
    "NVDAx": {
        "yf_ticker": "NVDA",
        "kraken_pair": "NVDAxUSD",
        "futures_symbol": "PF_NVDAXUSD",
        "alloc_pct": 0.12,
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
