# ICT 2022 NAS100 Algo Trading Bot

A fully automated algorithmic trading bot for **US100 (NAS100)** built on the **ICT 2022 model** — Market Structure Shift, liquidity sweeps, and Fair Value Gap entries. Runs on MetaTrader 5 via Python.

---

## Features

| Module | Description |
|---|---|
| `main.py` | Live trading bot — kill zone filter, MSS detection, FVG entry, MT5 order execution |
| `backtest.py` | Historical backtester with equity curve, monthly breakdown, and session breakdown charts |
| `ml_filter.py` | Random Forest classifier that filters setups by predicted win probability |
| `trade_tracker.py` | Detects closed live trades, appends results to training data, auto-retrains the model |
| `journal.py` | Annotated candlestick screenshots + self-contained HTML trade journal |

---

## Strategy — ICT 2022 Setup

| Step | Timeframe | Logic |
|---|---|---|
| Bias | 15m | Identify swing highs/lows |
| Liquidity sweep | 15m | Price takes out a swing high/low |
| MSS | 15m | Price breaks structure in the opposite direction |
| FVG | 1m | Find Fair Value Gap created during the impulse move |
| Entry | 1m | Price retraces into the FVG |
| SL | — | Beyond the sweep candle |
| TP | — | 2:1 RR minimum |

**Kill zones:** London (07:00–10:00 UTC) and NY (13:30–16:00 UTC) only.

---

## Requirements

- Windows PC with **MetaTrader 5** installed and logged in
- Python 3.11+
- US100 visible in MT5 Market Watch

```bash
pip install MetaTrader5 pandas matplotlib scikit-learn
```

---

## Quickstart

### 1. Clone the repo
```bash
git clone https://github.com/kipkiruikelly/trading-bot.git
cd trading-bot
```

### 2. Configure settings
Edit `config.py`:
```python
SYMBOL      = "US100"     # match your broker's exact symbol name
RISK_PERCENT = 1.0        # % of account risked per trade
DRY_RUN     = True        # set False only when ready to go live
```

### 3. Run the backtester
```bash
python backtest.py
```
Outputs:
- `equity_curve.png` — equity curve, drawdown, monthly bars, session breakdown
- `backtest_results.csv` — per-trade data with ML features
- `journal/journal.html` — annotated chart screenshots for every trade

### 4. Train the ML filter
```bash
python ml_filter.py
```
Requires at least 50 trades in `backtest_results.csv`. Saves `model.pkl`.

### 5. Run the live bot
```bash
python main.py
```
MT5 must be open and logged in. With `DRY_RUN = True` the bot logs signals without placing real orders.

---

## Machine Learning Filter

The bot uses a **Random Forest classifier** trained on backtest results to predict whether a setup will win or lose before placing an order.

**Features used:**

| Feature | What it captures |
|---|---|
| `session` | London vs NY edge |
| `day_of_week` | Best performing days |
| `hour_utc` | Best hours within session |
| `direction` | Bullish vs bearish bias |
| `atr_15m` | Market volatility at signal time |
| `fvg_size` | Quality of the FVG |
| `fvg_size_atr_ratio` | FVG size relative to volatility |
| `sweep_to_break_distance` | Strength of the MSS |
| `sweep_to_break_atr_ratio` | MSS strength relative to volatility |

The model **retrains automatically** every 10 closed live trades — no restarts needed.

---

## Trade Journal

Every trade (backtest and live) generates:
- An **annotated candlestick chart** (entry/SL/TP lines, FVG zone, MSS marker, result badge)
- An entry in `journal/journal.html` — open in any browser

```
journal/
├── journal.html          # browse all trades
├── trades.json           # raw trade data
└── screenshots/
    └── 20240115_0930_BULLISH_1.png
```

---

## File Structure

```
trading-bot/
├── config.py          # symbol, risk, timeframes, settings
├── killzone.py        # London/NY session time filter
├── structure.py       # 15m swing detection, liquidity sweep, MSS
├── entry.py           # 1m FVG detection and retrace check
├── risk.py            # position sizing, SL/TP calculation
├── main.py            # live bot loop + MT5 order execution
├── backtest.py        # historical backtester + charts
├── ml_filter.py       # Random Forest trade filter + training
├── trade_tracker.py   # live trade outcome tracking + auto-retrain
└── journal.py         # chart screenshots + HTML journal
```

---

## Risk Warning

This bot trades real financial markets. Past backtest performance does not guarantee future results. Always:
- Run in `DRY_RUN = True` mode for at least 2–4 weeks before going live
- Backtest over multiple market conditions before trusting the strategy
- Never risk money you cannot afford to lose
