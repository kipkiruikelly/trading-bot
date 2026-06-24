# MetaTrader5 shim — wraps mt5linux (RPyC bridge) when the server is running,
# otherwise falls back to offline constants so backtest.py can use local Parquet data.

TIMEFRAME_M1  = 1
TIMEFRAME_M5  = 5
TIMEFRAME_M15 = 15
TIMEFRAME_H1  = 60
TIMEFRAME_H4  = 240
TIMEFRAME_D1  = 1440

ORDER_TYPE_BUY       = 0
ORDER_TYPE_SELL      = 1
TRADE_ACTION_DEAL    = 1
TRADE_RETCODE_DONE   = 10009
ORDER_TIME_GTC       = 0
ORDER_FILLING_IOC    = 1

_MT5_HOST = "localhost"
_MT5_PORT = 18812

_mt5 = None  # live mt5linux instance, set after successful initialize()


def _get_live():
    global _mt5
    if _mt5 is None:
        try:
            from mt5linux import MetaTrader5 as _MT5Class
            _mt5 = _MT5Class(host=_MT5_HOST, port=_MT5_PORT, timeout=60)
        except Exception:
            pass
    return _mt5


def initialize(*args, **kwargs):
    live = _get_live()
    if live is None:
        return False
    try:
        return live.initialize(*args, **kwargs)
    except Exception:
        return False


def shutdown():
    live = _get_live()
    if live:
        try:
            live.shutdown()
        except Exception:
            pass


def last_error():
    live = _get_live()
    if live:
        try:
            return live.last_error()
        except Exception:
            pass
    return (-1, "MT5 RPyC server not reachable")


def copy_rates_range(symbol, timeframe, date_from, date_to):
    live = _get_live()
    if live:
        try:
            return live.copy_rates_range(symbol, timeframe, date_from, date_to)
        except Exception:
            pass
    return None


def copy_rates_from_pos(symbol, timeframe, start_pos, count):
    live = _get_live()
    if live:
        try:
            return live.copy_rates_from_pos(symbol, timeframe, start_pos, count)
        except Exception:
            pass
    return None


def account_info():
    live = _get_live()
    if live:
        try:
            return live.account_info()
        except Exception:
            pass
    return None


def symbol_info(symbol):
    live = _get_live()
    if live:
        try:
            return live.symbol_info(symbol)
        except Exception:
            pass
    return None


def symbol_info_tick(symbol):
    live = _get_live()
    if live:
        try:
            return live.symbol_info_tick(symbol)
        except Exception:
            pass
    return None


def positions_get(**kwargs):
    live = _get_live()
    if live:
        try:
            return live.positions_get(**kwargs)
        except Exception:
            pass
    return []


def order_send(request):
    live = _get_live()
    if live:
        try:
            return live.order_send(request)
        except Exception:
            pass
    return None
