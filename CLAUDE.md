# xstock-bot

RSI ladder mean-reversion bot trading Kraken xStocks (tokenized US stocks) via direct Kraken REST API calls. No ccxt, no krakenex.

## Architecture

```
xstock-bot/
├── bot.py           — main loop, orchestration, NYSE hours guard, daily summary
├── config.py        — all constants and per-symbol configuration
├── strategy.py      — indicators (Wilder RSI, MA200) and signal logic
├── xstock_client.py — Kraken REST client (HMAC-SHA512 auth)
├── state_store.py   — per-symbol JSON state with atomic writes
├── notify.py        — Telegram notifications (🟡 emoji, fail-silent)
├── logger_setup.py  — file + console handlers
└── .env.example     — required environment variables
```

## Setup

```bash
pip3 install yfinance requests python-dotenv pytz schedule numpy
cp .env.example .env
# Edit .env with real credentials
python3 bot.py
```

## Environment variables

| Variable | Description |
|---|---|
| `KRAKEN_API_KEY` | Kraken API key |
| `KRAKEN_API_SECRET` | Kraken API secret (base64-encoded) |
| `PAPER_TRADE` | `true` to log orders without sending (`false` default) |
| `TELEGRAM_TOKEN` | Bot token for Telegram notifications |
| `TELEGRAM_CHAT_ID` | Chat ID to send messages to (default `-517596211`) |

## Traded symbols

Each symbol gets 25% of the xstock budget (`TOTAL_BUDGET_PCT = 0.20` of ZUSD balance).

| Symbol | yfinance | Kraken pair | Futures ticker | MA defensive |
|---|---|---|---|---|
| NVDAx | NVDA | NVDAxUSD | PF_NVDAXUSD | 15% |
| AAPLx | AAPL | AAPLxUSD | PF_AAPLXUSD | 12% |
| QQQx | QQQ | QQQxUSD | PF_QQQXUSD | 12% |
| SPYx | SPY | SPYxUSD | PF_SPYXUSD | 12% |

## Strategy

### Budget allocation

- Total xstock budget = `ZUSD_balance × 0.20`
- Per-symbol budget = `xstock_budget × alloc_pct` (25% each = 5% of ZUSD per symbol)
- `cycle_budget_usd` is **snapshotted** when the first tranche fires and reused for T2–T4

### RSI ladder — entry tranches

Each symbol has 4 rungs. Each rung fires **once** per cycle.

**Example — NVDAx:**

| Tranche | RSI trigger | % of cycle budget |
|---|---|---|
| T1 | ≤ 42 | 15% |
| T2 | ≤ 37 | 20% |
| T3 | ≤ 32 | 30% |
| T4 | ≤ 28 | 35% |

**Entry conditions (ALL required):**

1. Tranche not already bought this cycle
2. RSI ≤ rung threshold AND RSI rising (`rsi_now > rsi_prev − 2.0`, grace ±2 pts)
3. Price ≥ MA200 × (1 − `ma_defensive_pct`) — per-symbol defensive floor
4. Not in emergency pause
5. Cooldown: ≥ 3 daily bars since last buy (`last_buy_date`)

### Exit — profit target

**Exit conditions (ALL required):**

1. Unrealised P&L ≥ `profit_targets[tranches_active − 1]`
2. RSI ≥ 55 (`EXIT_RSI_FLOOR`)
3. RSI momentum slowing (`rsi_now < rsi_prev` OR `rsi_prev ≤ rsi_prev2`)
4. Cooldown: ≥ 3 daily bars since last buy

**Profit targets by tranche count:**

| T active | NVDAx | AAPLx | QQQx | SPYx |
|---|---|---|---|---|
| 1 | 2.0% | 1.75% | 2.0% | 1.5% |
| 2 | 3.5% | 3.0% | 3.0% | 2.5% |
| 3 | 5.5% | 4.0% | 4.5% | 3.5% |
| 4 | 8.0% | 5.5% | 6.0% | 5.0% |

### Exit — trailing stop

- Tracks `peak_price` of the combined position (resets upward as price rises, updated in `bot.py` before signal evaluation)
- `TRAILING_STOP_PCT = 0.08` — if price drops 8% from peak → **SELL ALL**, regardless of RSI
- Checked before profit target in `get_signal()`

### Emergency pause

- If unrealised loss > `EMERGENCY_STOP_PCT = 0.20` (20%) → set `emergency_paused = True`
- **Does not force-sell** — pauses new buys only
- Alert sent via Telegram, state saved, subsequent cycles skip entry for this symbol
- To resume: manually edit the state file and set `"emergency_paused": false`

### Defensive mode

- If `price < MA200 × (1 − ma_defensive_pct)` → no new tranches
- Exits (trailing stop, profit target) are still allowed
- Logged as `action=DEFENSIVE` when no position is open

## State files

One JSON file per symbol written to the working directory:

```
state_nvdax.json
state_aaplx.json
state_qqqx.json
state_spyx.json
```

**Schema:**

```json
{
  "in_cycle": false,
  "tranches_bought": [false, false, false, false],
  "entries": [],
  "avg_entry_price": null,
  "total_invested_usd": 0.0,
  "total_units": 0.0,
  "peak_price": null,
  "cycle_budget_usd": null,
  "last_buy_date": null,
  "emergency_paused": false
}
```

- `entries` — list of `{tranche, date, price, volume, usd}` for each fill
- `avg_entry_price` — volume-weighted average across all tranches
- `peak_price` — highest price seen while position is open (trailing stop reference)
- `cycle_budget_usd` — snapshotted at T1, `null` when no cycle is active
- Atomic writes: `tempfile.mkstemp()` + `os.replace()`
- A full exit via `reset_state()` wipes all fields back to defaults

## Main loop (bot.py)

1. Sends startup Telegram message on launch
2. Schedules daily summary at **08:00 UTC** via `schedule` library
3. Every `POLL_INTERVAL = 3600s`:
   - Checks NYSE hours (Mon–Fri 09:30–16:00 ET via `pytz`) — sleeps if outside
   - Fetches ZUSD balance
   - Processes each symbol: load state → get price → fetch bars → get signal → execute → save state
4. SIGTERM handled gracefully — `_running = False`, sleep loop exits within 1 second

**Log line per evaluation:**
```
YYYY-MM-DD HH:MM:SS | INFO | NVDAx | price=123.4567 rsi=38.2 ma200=118.50 tranches=1 pnl=+4.21%
YYYY-MM-DD HH:MM:SS | INFO | NVDAx | action=HOLD reason=no conditions met
```

## Kraken client (xstock_client.py)

- **Auth**: HMAC-SHA512 — `sha256(nonce + POST_body)` → `sha512(path + digest)` signed with `base64.b64decode(secret)`
- **Price**: `GET https://futures.kraken.com/derivatives/api/v3/tickers/{futures_symbol}` → `ticker.indexPrice` (fallback `ticker.last`)
- **Balance**: `POST /0/private/Balance` → `result.ZUSD` (fallback `result.USD`)
- **Order**: `POST /0/private/AddOrder` with `ordertype=market`, skipped entirely when `PAPER_TRADE=true`
- **Pair info**: `GET /0/public/AssetPairs?aclass_base=tokenized_asset` → `costmin`, `lot_decimals`
- All methods return `None` on error (never raise to caller)

## Notifications (notify.py)

All 🟡 to distinguish from IBKR bot (🟢) and Kraken V1 (🟣). All functions are fail-silent.

| Function | Trigger |
|---|---|
| `send_startup` | Bot launch |
| `send_buy` | Each tranche fill |
| `send_sell` | Full position exit (any reason) |
| `send_emergency` | First time emergency_paused fires |
| `send_daily_summary` | 08:00 UTC each day |

## Key constants (config.py)

| Constant | Value | Meaning |
|---|---|---|
| `TOTAL_BUDGET_PCT` | 0.20 | 20% of ZUSD balance for all xstocks |
| `POLL_INTERVAL` | 3600 | Seconds between evaluations |
| `DAILY_BAR_LIMIT` | 300 | Daily closes fetched from yfinance |
| `TRAILING_STOP_PCT` | 0.08 | 8% drop from peak triggers sell |
| `EMERGENCY_STOP_PCT` | 0.20 | 20% unrealised loss pauses buys |
| `EXIT_RSI_FLOOR` | 55 | Minimum RSI required to take profit |
| `MA_DEFENSIVE_PCT` | 0.12 | Global default; per-symbol overrides in SYMBOLS |

## Python version compatibility

Python 3.9+. All type hints use `Optional[X]` from `typing` — no `X | None` union syntax.

## Dependencies

```
yfinance       — daily OHLCV bars for RSI + MA200
requests       — Kraken REST API calls
python-dotenv  — .env loading
pytz           — NYSE timezone handling
schedule       — daily summary cron
numpy          — available but stdlib math used in indicators
```
