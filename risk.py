import MetaTrader5 as mt5
from config import SYMBOL, RISK_PERCENT, MIN_RR


def calculate_lot_size(sl_points: float) -> float:
    account = mt5.account_info()
    if account is None:
        return 0.0

    balance = account.balance
    risk_amount = balance * (RISK_PERCENT / 100)

    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        return 0.0

    tick_value = symbol_info.trade_tick_value
    tick_size = symbol_info.trade_tick_size

    if tick_size == 0 or sl_points == 0:
        return 0.0

    value_per_lot = (sl_points / tick_size) * tick_value
    if value_per_lot == 0:
        return 0.0

    lot = risk_amount / value_per_lot
    lot = max(symbol_info.volume_min, min(symbol_info.volume_max, round(lot, 2)))
    return lot


def calculate_tp(entry: float, sl: float, direction: str) -> float:
    sl_distance = abs(entry - sl)
    tp_distance = sl_distance * MIN_RR
    if direction == "bullish":
        return entry + tp_distance
    return entry - tp_distance
