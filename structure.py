import MetaTrader5 as mt5
import pandas as pd
from config import SYMBOL, SWING_LOOKBACK


def get_candles(timeframe_str: str, count: int) -> pd.DataFrame:
    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
    }
    tf = tf_map[timeframe_str]
    rates = mt5.copy_rates_from_pos(SYMBOL, tf, 0, count)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def find_swings(df: pd.DataFrame, lookback: int = SWING_LOOKBACK):
    highs, lows = [], []
    for i in range(lookback, len(df) - lookback):
        if df["high"].iloc[i] == df["high"].iloc[i - lookback:i + lookback + 1].max():
            highs.append((i, df["high"].iloc[i]))
        if df["low"].iloc[i] == df["low"].iloc[i - lookback:i + lookback + 1].min():
            lows.append((i, df["low"].iloc[i]))
    return highs, lows


def detect_mss(df: pd.DataFrame):
    """
    Returns dict with keys: direction ('bullish'|'bearish'), sweep_high, sweep_low,
    mss_index, confirmed (bool) — or None if no setup found.

    Bullish MSS: price sweeps a prior swing low (liquidity grab below),
                 then breaks above a recent swing high.
    Bearish MSS: price sweeps a prior swing high, then breaks below a recent swing low.
    """
    if len(df) < SWING_LOOKBACK * 3:
        return None

    highs, lows = find_swings(df)
    if len(highs) < 2 or len(lows) < 2:
        return None

    last_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]

    # --- Bearish MSS ---
    # Sweep last swing high then break below last swing low
    last_high_idx, last_high_val = highs[-1]
    last_low_idx, last_low_val = lows[-1]

    if last_high_idx < last_low_idx:
        # Structure: high formed before low — look for sweep of high then break down
        swept_high = any(
            df["high"].iloc[i] > last_high_val
            for i in range(last_high_idx + 1, len(df))
        )
        if swept_high:
            broke_low = last_candle["close"] < last_low_val
            if broke_low:
                return {
                    "direction": "bearish",
                    "sweep_level": last_high_val,
                    "structure_break_level": last_low_val,
                    "sweep_candle_high": df["high"].iloc[last_high_idx],
                    "confirmed": True,
                }

    # --- Bullish MSS ---
    # Sweep last swing low then break above last swing high
    if last_low_idx < last_high_idx:
        swept_low = any(
            df["low"].iloc[i] < last_low_val
            for i in range(last_low_idx + 1, len(df))
        )
        if swept_low:
            broke_high = last_candle["close"] > last_high_val
            if broke_high:
                return {
                    "direction": "bullish",
                    "sweep_level": last_low_val,
                    "structure_break_level": last_high_val,
                    "sweep_candle_low": df["low"].iloc[last_low_idx],
                    "confirmed": True,
                }

    return None
