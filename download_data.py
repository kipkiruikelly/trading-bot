"""
Downloads US100 (^NDX) historical OHLCV data via yfinance and saves to
data/US100_M15.parquet and data/US100_M1.parquet for offline backtesting.

Usage:
    python download_data.py
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

TICKER   = "^NDX"
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)


def download(interval: str, period: str, filename: Path) -> pd.DataFrame:
    print(f"Downloading {TICKER} {interval} ({period}) ...", end=" ", flush=True)
    df = yf.download(TICKER, interval=interval, period=period,
                     auto_adjust=True, progress=False)
    if df.empty:
        print("FAILED — no data returned")
        sys.exit(1)

    df = df.reset_index()

    # yfinance uses "Datetime" for intraday, "Date" for daily
    time_col = "Datetime" if "Datetime" in df.columns else "Date"
    df = df.rename(columns={
        time_col: "time",
        "Open":   "open",
        "High":   "high",
        "Low":    "low",
        "Close":  "close",
        "Volume": "volume",
    })

    # Flatten MultiIndex columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    df = df[["time", "open", "high", "low", "close", "volume"]].dropna()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").reset_index(drop=True)

    df.to_parquet(filename, index=False)
    span = f"{df['time'].iloc[0].strftime('%Y-%m-%d')} → {df['time'].iloc[-1].strftime('%Y-%m-%d')}"
    print(f"{len(df):,} candles  ({span})  → {filename}")
    return df


def download_1m_chunked(filename: Path, days: int = 28) -> pd.DataFrame:
    """Yahoo limits 1m requests to 8 days — fetch in chunks and merge."""
    from datetime import timedelta
    import pytz

    chunks = []
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    chunk_days = 7

    t = start
    while t < end:
        t_end = min(t + timedelta(days=chunk_days), end)
        print(f"  1m chunk {t.strftime('%Y-%m-%d')} → {t_end.strftime('%Y-%m-%d')} ...",
              end=" ", flush=True)
        df = yf.download(TICKER, interval="1m", start=t, end=t_end,
                         auto_adjust=True, progress=False)
        if not df.empty:
            df = df.reset_index()
            time_col = "Datetime" if "Datetime" in df.columns else "Date"
            df = df.rename(columns={time_col: "time", "Open": "open", "High": "high",
                                    "Low": "low", "Close": "close", "Volume": "volume"})
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            df = df[["time", "open", "high", "low", "close", "volume"]].dropna()
            df["time"] = pd.to_datetime(df["time"], utc=True)
            chunks.append(df)
            print(f"{len(df):,} bars")
        else:
            print("no data")
        t = t_end

    if not chunks:
        print("ERROR: no 1m data downloaded")
        sys.exit(1)

    combined = (pd.concat(chunks)
                  .drop_duplicates("time")
                  .sort_values("time")
                  .reset_index(drop=True))
    combined.to_parquet(filename, index=False)
    span = f"{combined['time'].iloc[0].strftime('%Y-%m-%d')} → {combined['time'].iloc[-1].strftime('%Y-%m-%d')}"
    print(f"1m total: {len(combined):,} candles  ({span})  → {filename}")
    return combined


if __name__ == "__main__":
    df_15m = download("15m", "60d", DATA_DIR / "US100_M15.parquet")
    df_5m  = download("5m",  "60d", DATA_DIR / "US100_M5.parquet")

    print("Downloading ^NDX 1m in 7-day chunks ...")
    df_1m = download_1m_chunked(DATA_DIR / "US100_M1.parquet", days=28)

    start = max(df_15m["time"].iloc[0], df_5m["time"].iloc[0])
    end   = min(df_15m["time"].iloc[-1], df_5m["time"].iloc[-1])
    print(f"\nOverlapping 15m+5m range (backtest uses this): {start.date()} → {end.date()}")
    print(f"Run:  python backtest.py")
