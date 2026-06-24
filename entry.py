import pandas as pd
from structure import get_candles
from config import ENTRY_TF, FVG_LOOKBACK


def find_fvg(direction: str) -> dict | None:
    """
    Scans 1m candles for the most recent Fair Value Gap in the given direction.
    Bullish FVG: candle[i-1].high < candle[i+1].low  (gap up)
    Bearish FVG: candle[i-1].low  > candle[i+1].high (gap down)
    Returns the most recent unfilled FVG or None.
    """
    df = get_candles(ENTRY_TF, FVG_LOOKBACK)
    if df.empty or len(df) < 3:
        return None

    fvgs = []
    for i in range(1, len(df) - 1):
        c_prev = df.iloc[i - 1]
        c_curr = df.iloc[i]
        c_next = df.iloc[i + 1]

        if direction == "bullish":
            if c_prev["high"] < c_next["low"]:
                fvgs.append({
                    "top": c_next["low"],
                    "bottom": c_prev["high"],
                    "mid": (c_next["low"] + c_prev["high"]) / 2,
                    "index": i,
                    "time": c_curr["time"],
                })
        elif direction == "bearish":
            if c_prev["low"] > c_next["high"]:
                fvgs.append({
                    "top": c_prev["low"],
                    "bottom": c_next["high"],
                    "mid": (c_prev["low"] + c_next["high"]) / 2,
                    "index": i,
                    "time": c_curr["time"],
                })

    if not fvgs:
        return None

    # Return the most recent FVG
    return fvgs[-1]


def price_in_fvg(current_price: float, fvg: dict, direction: str = "") -> bool:
    # OTE: enter at the lower half of a bullish FVG, upper half of a bearish FVG
    if direction == "bullish":
        return fvg["bottom"] <= current_price <= fvg["mid"]
    if direction == "bearish":
        return fvg["mid"] <= current_price <= fvg["top"]
    return fvg["bottom"] <= current_price <= fvg["top"]
