DRY_RUN = True  # set to False to place real trades

TELEGRAM_TOKEN   = ""   # from @BotFather  e.g. "123456:ABC-DEF..."
TELEGRAM_CHAT_ID = ""   # from @userinfobot e.g. "987654321"

SYMBOL = "US100"
YF_TICKER = "^NDX"          # Yahoo Finance ticker for NAS100
STRUCTURE_TF = "M15"
ENTRY_TF = "M1"
RISK_PERCENT = 1.0
MIN_RR = 2.0
MAGIC = 202200
SLIPPAGE = 10

LONDON_START = (7, 0)    # UTC
LONDON_END = (10, 0)
NY_START = (13, 30)
NY_END = (16, 0)

SWING_LOOKBACK = 10      # candles to identify swing highs/lows
FVG_LOOKBACK = 50        # candles to scan for FVGs on 1m
SL_BUFFER = 15           # points beyond sweep candle for SL
