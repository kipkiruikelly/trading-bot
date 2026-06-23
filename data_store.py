"""
Downloads and stores historical MT5 data to local Parquet files.
Run once to build your local dataset, then --update periodically.

Usage:
    python data_store.py              # full download (all broker history)
    python data_store.py --update     # only fetch new candles since last save
    python data_store.py --info       # show what is stored locally
"""

import argparse
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from config import SYMBOL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

DATA_DIR   = Path("data")
META_FILE  = DATA_DIR / "metadata.json"
CHUNK_DAYS = 90   # download 90 days at a time to avoid MT5 buffer limits

TIMEFRAMES = {
    "M15": ("M15", None),   # filled after mt5.initialize()
    "M1":  ("M1",  None),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parquet_path(tf_str: str) -> Path:
    return DATA_DIR / f"{SYMBOL}_{tf_str}.parquet"


def _load_meta() -> dict:
    if META_FILE.exists():
        return json.loads(META_FILE.read_text())
    return {}


def _save_meta(meta: dict):
    META_FILE.write_text(json.dumps(meta, indent=2, default=str))


def _fetch_chunk(tf_mt5: int, date_from: datetime, date_to: datetime) -> pd.DataFrame:
    rates = mt5.copy_rates_range(SYMBOL, tf_mt5, date_from, date_to)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return (df[["time", "open", "high", "low", "close", "tick_volume"]]
              .rename(columns={"tick_volume": "volume"}))


def _earliest_available(tf_mt5: int) -> datetime:
    """Ask MT5 for the oldest available candle for this timeframe."""
    # Request from far back; MT5 returns what it has
    probe = datetime(2000, 1, 1, tzinfo=timezone.utc)
    rates = mt5.copy_rates_range(SYMBOL, tf_mt5, probe,
                                  probe + timedelta(days=1))
    if rates is not None and len(rates) > 0:
        return datetime.fromtimestamp(rates[0]["time"], tz=timezone.utc)
    # Fallback: 10 years back
    return datetime.now(timezone.utc) - timedelta(days=365 * 10)


# ---------------------------------------------------------------------------
# Main download
# ---------------------------------------------------------------------------

def download(update_only: bool = False):
    if not mt5.initialize():
        log.error("MT5 init failed: %s", mt5.last_error())
        return

    DATA_DIR.mkdir(exist_ok=True)
    meta = _load_meta()
    now  = datetime.now(timezone.utc)

    tf_map = {
        "M15": mt5.TIMEFRAME_M15,
        "M1":  mt5.TIMEFRAME_M1,
    }

    for tf_str, tf_mt5 in tf_map.items():
        path    = _parquet_path(tf_str)
        tf_key  = f"{SYMBOL}_{tf_str}"
        tf_meta = meta.get(tf_key, {})

        # --- Determine start date ---
        if update_only and tf_meta.get("to"):
            # Start from last saved candle
            date_from = (datetime.fromisoformat(tf_meta["to"])
                         .replace(tzinfo=timezone.utc))
            log.info("%s %s — incremental update from %s",
                     SYMBOL, tf_str, date_from.date())
        else:
            date_from = _earliest_available(tf_mt5)
            log.info("%s %s — full download from %s (broker earliest: %s)",
                     SYMBOL, tf_str, date_from.date(), date_from.date())

        # --- Chunked download ---
        chunks      = []
        chunk_start = date_from
        total_bars  = 0

        while chunk_start < now:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), now)
            chunk     = _fetch_chunk(tf_mt5, chunk_start, chunk_end)

            if not chunk.empty:
                chunks.append(chunk)
                total_bars += len(chunk)
                log.info("  %-4s  %s → %s  +%d bars  (total: %d)",
                         tf_str,
                         chunk_start.strftime("%Y-%m-%d"),
                         chunk_end.strftime("%Y-%m-%d"),
                         len(chunk), total_bars)

            chunk_start = chunk_end

        if not chunks:
            log.warning("%s %s — no data returned by broker", SYMBOL, tf_str)
            continue

        new_df = (pd.concat(chunks)
                    .drop_duplicates("time")
                    .sort_values("time")
                    .reset_index(drop=True))

        # Merge with existing file when updating
        if update_only and path.exists():
            existing = pd.read_parquet(path)
            new_df   = (pd.concat([existing, new_df])
                          .drop_duplicates("time")
                          .sort_values("time")
                          .reset_index(drop=True))

        new_df.to_parquet(path, index=False)

        meta[tf_key] = {
            "from":       str(new_df["time"].iloc[0].date()),
            "to":         str(new_df["time"].iloc[-1].date()),
            "rows":       len(new_df),
            "downloaded": str(now.strftime("%Y-%m-%d %H:%M UTC")),
        }
        _save_meta(meta)

        size_mb = path.stat().st_size / 1_048_576
        log.info("✓ %s %s — %d candles saved  (%s → %s)  %.1f MB  →  %s",
                 SYMBOL, tf_str, len(new_df),
                 new_df["time"].iloc[0].strftime("%Y-%m-%d"),
                 new_df["time"].iloc[-1].strftime("%Y-%m-%d"),
                 size_mb, path)

    mt5.shutdown()
    log.info("Download complete. Run 'python data_store.py --info' to verify.")


# ---------------------------------------------------------------------------
# Info
# ---------------------------------------------------------------------------

def show_info():
    meta = _load_meta()
    if not meta:
        print("\nNo local data found.")
        print("Run:  python data_store.py\n")
        return

    print(f"\n{'─'*62}")
    print(f"  Local data store — {SYMBOL}")
    print(f"{'─'*62}")
    print(f"  {'TF':<6} {'From':<12} {'To':<12} {'Candles':>10}  {'Size':>8}  Downloaded")
    print(f"{'─'*62}")
    for key, m in meta.items():
        tf   = key.split("_")[-1]
        path = _parquet_path(tf)
        size = f"{path.stat().st_size / 1_048_576:.1f} MB" if path.exists() else "missing"
        print(f"  {tf:<6} {m['from']:<12} {m['to']:<12} "
              f"{m['rows']:>10,}  {size:>8}  {m['downloaded']}")
    print(f"{'─'*62}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ICT Bot — Historical Data Store")
    parser.add_argument("--update", action="store_true",
                        help="Incremental update — only fetch candles since last save")
    parser.add_argument("--info", action="store_true",
                        help="Show locally stored data info without downloading")
    args = parser.parse_args()

    if args.info:
        show_info()
    else:
        download(update_only=args.update)
