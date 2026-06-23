import csv
import logging
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from config import SYMBOL, SWING_LOOKBACK, FVG_LOOKBACK, MIN_RR, SL_BUFFER
from ml_filter import extract_features, FEATURES
from journal import TradeJournal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("backtest.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

LONDON_START = (7, 0)
LONDON_END = (10, 0)
NY_START = (13, 30)
NY_END = (16, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _in_window(dt: datetime, start: tuple, end: tuple) -> bool:
    s = dt.replace(hour=start[0], minute=start[1], second=0, microsecond=0)
    e = dt.replace(hour=end[0], minute=end[1], second=0, microsecond=0)
    return s <= dt <= e


def in_kill_zone_ts(dt: datetime) -> bool:
    utc = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return _in_window(utc, LONDON_START, LONDON_END) or _in_window(utc, NY_START, NY_END)


def fetch_data(tf_mt5: int, date_from: datetime, date_to: datetime) -> pd.DataFrame:
    rates = mt5.copy_rates_range(SYMBOL, tf_mt5, date_from, date_to)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Structure — MSS detection on a DataFrame slice (no live MT5 calls)
# ---------------------------------------------------------------------------

def detect_mss_slice(df: pd.DataFrame) -> dict | None:
    if len(df) < SWING_LOOKBACK * 3:
        return None

    highs, lows = [], []
    for i in range(SWING_LOOKBACK, len(df) - SWING_LOOKBACK):
        window = df["high"].iloc[i - SWING_LOOKBACK: i + SWING_LOOKBACK + 1]
        if df["high"].iloc[i] == window.max():
            highs.append((i, df["high"].iloc[i], df["low"].iloc[i]))

        window = df["low"].iloc[i - SWING_LOOKBACK: i + SWING_LOOKBACK + 1]
        if df["low"].iloc[i] == window.min():
            lows.append((i, df["high"].iloc[i], df["low"].iloc[i]))

    if len(highs) < 2 or len(lows) < 2:
        return None

    last_high_idx, last_high_val, _ = highs[-1]
    last_low_idx, _, last_low_val = lows[-1]
    last_close = df["close"].iloc[-1]

    # Bearish MSS: swing high formed before swing low
    if last_high_idx < last_low_idx:
        swept = any(df["high"].iloc[j] > last_high_val for j in range(last_high_idx + 1, len(df)))
        if swept and last_close < last_low_val:
            return {
                "direction": "bearish",
                "sweep_level": last_high_val,
                "structure_break_level": last_low_val,
                "sl": last_high_val + SL_BUFFER,
            }

    # Bullish MSS: swing low formed before swing high
    if last_low_idx < last_high_idx:
        swept = any(df["low"].iloc[j] < last_low_val for j in range(last_low_idx + 1, len(df)))
        if swept and last_close > last_high_val:
            return {
                "direction": "bullish",
                "sweep_level": last_low_val,
                "structure_break_level": last_high_val,
                "sl": last_low_val - SL_BUFFER,
            }

    return None


# ---------------------------------------------------------------------------
# Entry — FVG detection on a 1m slice
# ---------------------------------------------------------------------------

def find_fvg_slice(df: pd.DataFrame, direction: str) -> dict | None:
    end = min(FVG_LOOKBACK, len(df) - 1)
    fvgs = []

    for i in range(1, end - 1):
        prev = df.iloc[i - 1]
        nxt = df.iloc[i + 1]

        if direction == "bullish" and prev["high"] < nxt["low"]:
            fvgs.append({
                "top": nxt["low"],
                "bottom": prev["high"],
                "mid": (nxt["low"] + prev["high"]) / 2,
                "index": i,
                "time": df.iloc[i]["time"],
            })
        elif direction == "bearish" and prev["low"] > nxt["high"]:
            fvgs.append({
                "top": prev["low"],
                "bottom": nxt["high"],
                "mid": (prev["low"] + nxt["high"]) / 2,
                "index": i,
                "time": df.iloc[i]["time"],
            })

    return fvgs[-1] if fvgs else None


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def simulate_trade(df_1m: pd.DataFrame, fvg: dict, direction: str, sl: float) -> dict | None:
    sl_dist = abs(fvg["mid"] - sl)
    tp_dist = sl_dist * MIN_RR
    entry = fvg["mid"]
    tp = entry + tp_dist if direction == "bullish" else entry - tp_dist

    retrace_found = False

    for i in range(fvg["index"] + 1, len(df_1m)):
        c = df_1m.iloc[i]

        if not retrace_found:
            # Abandon if kill zone has ended before price retraces
            if not in_kill_zone_ts(c["time"]):
                break
            in_fvg = fvg["bottom"] <= c["low"] <= fvg["top"] or fvg["bottom"] <= c["high"] <= fvg["top"]
            if in_fvg:
                retrace_found = True
            continue

        if direction == "bullish":
            if c["low"] <= sl:
                return _trade_result("loss", fvg["time"], entry, sl, tp, sl - entry)
            if c["high"] >= tp:
                return _trade_result("win", fvg["time"], entry, sl, tp, tp - entry)
        else:
            if c["high"] >= sl:
                return _trade_result("loss", fvg["time"], entry, sl, tp, entry - sl)
            if c["low"] <= tp:
                return _trade_result("win", fvg["time"], entry, sl, tp, entry - tp)

    return None  # trade not resolved within data range


def _trade_result(result, time, entry, sl, tp, pnl_points):
    utc = time if hasattr(time, "tzinfo") and time.tzinfo else time.replace(tzinfo=timezone.utc)
    if _in_window(utc, LONDON_START, LONDON_END):
        session = "London"
    elif _in_window(utc, NY_START, NY_END):
        session = "NY"
    else:
        session = "Unknown"
    return {
        "result": result,
        "time": time,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "pnl_points": pnl_points,
        "session": session,
    }


# ---------------------------------------------------------------------------
# Stats + CSV export
# ---------------------------------------------------------------------------

def monthly_breakdown(trades: list) -> list[dict]:
    if not trades:
        return []

    rows = {}
    for t in trades:
        key = t["time"].strftime("%Y-%m")
        if key not in rows:
            rows[key] = {"month": key, "trades": 0, "wins": 0, "losses": 0, "net_points": 0.0}
        rows[key]["trades"] += 1
        rows[key]["net_points"] += t["pnl_points"]
        if t["result"] == "win":
            rows[key]["wins"] += 1
        else:
            rows[key]["losses"] += 1

    for r in rows.values():
        r["win_rate"] = r["wins"] / r["trades"] * 100 if r["trades"] else 0.0

    return sorted(rows.values(), key=lambda x: x["month"])


def print_monthly_table(trades: list):
    rows = monthly_breakdown(trades)
    if not rows:
        return

    header = f"  {'Month':<10} {'Trades':>6} {'Wins':>5} {'Losses':>7} {'Win %':>7} {'Net Pts':>10}"
    sep = "  " + "-" * (len(header) - 2)

    log.info(sep)
    log.info("  MONTHLY BREAKDOWN")
    log.info(sep)
    log.info(header)
    log.info(sep)
    for r in rows:
        marker = "+" if r["net_points"] >= 0 else "-"
        log.info(
            "  %-10s %6d %5d %7d %6.1f%% %+10.1f",
            r["month"], r["trades"], r["wins"], r["losses"],
            r["win_rate"], r["net_points"],
        )
    log.info(sep)


def session_breakdown(trades: list) -> dict:
    data = {
        "London": {"trades": 0, "wins": 0, "losses": 0, "net_points": 0.0},
        "NY":     {"trades": 0, "wins": 0, "losses": 0, "net_points": 0.0},
    }
    for t in trades:
        s = t.get("session", "Unknown")
        if s not in data:
            continue
        data[s]["trades"] += 1
        data[s]["net_points"] += t["pnl_points"]
        if t["result"] == "win":
            data[s]["wins"] += 1
        else:
            data[s]["losses"] += 1
    for s in data.values():
        s["win_rate"] = s["wins"] / s["trades"] * 100 if s["trades"] else 0.0
        gross_wins = sum(t["pnl_points"] for t in trades
                        if t.get("session") == list(data.keys())[list(data.values()).index(s)]
                        and t["result"] == "win")
        gross_losses = abs(sum(t["pnl_points"] for t in trades
                               if t.get("session") == list(data.keys())[list(data.values()).index(s)]
                               and t["result"] == "loss"))
        s["profit_factor"] = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    return data


def print_session_table(trades: list):
    data = session_breakdown(trades)
    if not any(v["trades"] for v in data.values()):
        return

    header = f"  {'Session':<10} {'Trades':>6} {'Wins':>5} {'Losses':>7} {'Win %':>7} {'Net Pts':>10} {'PF':>6}"
    sep = "  " + "-" * (len(header) - 2)

    log.info(sep)
    log.info("  SESSION BREAKDOWN")
    log.info(sep)
    log.info(header)
    log.info(sep)
    for session, s in data.items():
        pf = f"{s['profit_factor']:.2f}" if s["profit_factor"] != float("inf") else "∞"
        log.info(
            "  %-10s %6d %5d %7d %6.1f%% %+10.1f %6s",
            session, s["trades"], s["wins"], s["losses"],
            s["win_rate"], s["net_points"], pf,
        )
    log.info(sep)


def plot_session_panel(trades: list, ax: plt.Axes):
    data = session_breakdown(trades)
    sessions = list(data.keys())
    metrics = ["trades", "wins", "losses"]
    colors  = ["#58a6ff", "#3fb950", "#f85149"]
    labels  = ["Trades", "Wins", "Losses"]

    x = range(len(sessions))
    width = 0.22

    for i, (metric, color, label) in enumerate(zip(metrics, colors, labels)):
        vals = [data[s][metric] for s in sessions]
        offset = (i - 1) * width
        bars = ax.bar([xi + offset for xi in x], vals, width=width,
                      color=color, label=label, zorder=3)
        for bar, val in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.3,
                    str(val), ha="center", va="bottom",
                    fontsize=8, color="#c9d1d9")

    # Win rate line on secondary axis
    ax2 = ax.twinx()
    win_rates = [data[s]["win_rate"] for s in sessions]
    ax2.plot(list(x), win_rates, color="#e3b341", marker="o",
             linewidth=2, markersize=7, label="Win %", zorder=5)
    for xi, wr in zip(x, win_rates):
        ax2.text(xi + 0.05, wr + 1.5, f"{wr:.1f}%",
                 color="#e3b341", fontsize=8)
    ax2.set_ylim(0, 110)
    ax2.set_ylabel("Win Rate %", color="#e3b341")
    ax2.tick_params(colors="#e3b341")
    ax2.spines[:].set_color("#30363d")
    ax2.set_facecolor("#0d1117")

    # Net points text below session names
    for xi, s in enumerate(sessions):
        net = data[s]["net_points"]
        col = "#3fb950" if net >= 0 else "#f85149"
        ax.text(xi, -max(data[s]["trades"] for s in sessions) * 0.15,
                f"Net: {net:+.0f} pts\nPF: {data[s]['profit_factor']:.2f}",
                ha="center", color=col, fontsize=8)

    ax.set_xticks(list(x))
    ax.set_xticklabels(sessions, fontsize=10, color="#c9d1d9")
    ax.set_ylabel("Count", color="#c9d1d9")
    ax.set_title("Session Breakdown — London vs NY", color="#e6edf3", fontsize=10, pad=6)
    ax.set_facecolor("#0d1117")
    ax.tick_params(colors="#c9d1d9")
    ax.spines[:].set_color("#30363d")
    ax.yaxis.label.set_color("#c9d1d9")

    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2,
              facecolor="#161b22", edgecolor="#30363d",
              labelcolor="#c9d1d9", fontsize=8, loc="upper right")


def plot_monthly_bars(trades: list, ax: plt.Axes):
    rows = monthly_breakdown(trades)
    if not rows:
        return

    labels = [r["month"] for r in rows]
    values = [r["net_points"] for r in rows]
    colors = ["#3fb950" if v >= 0 else "#f85149" for v in values]

    x = range(len(labels))
    bars = ax.bar(x, values, color=colors, width=0.6, zorder=3)
    ax.axhline(0, color="#30363d", linewidth=0.8, linestyle="--")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7.5)
    ax.set_ylabel("Net Points", color="#c9d1d9")
    ax.set_title("Monthly Net Points", color="#e6edf3", fontsize=10, pad=6)
    ax.set_facecolor("#0d1117")
    ax.tick_params(colors="#c9d1d9")
    ax.spines[:].set_color("#30363d")
    ax.yaxis.label.set_color("#c9d1d9")

    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + (max(values) * 0.02),
            f"{val:+.0f}",
            ha="center", va="bottom", fontsize=7, color="#c9d1d9",
        )


def print_stats(trades: list):
    if not trades:
        log.info("No trades taken in backtest period")
        return

    wins = [t for t in trades if t["result"] == "win"]
    losses = [t for t in trades if t["result"] == "loss"]

    total_gain = sum(t["pnl_points"] for t in wins)
    total_loss = abs(sum(t["pnl_points"] for t in losses))
    profit_factor = total_gain / total_loss if total_loss > 0 else float("inf")
    net = sum(t["pnl_points"] for t in trades)

    running = peak = max_dd = 0
    for t in trades:
        running += t["pnl_points"]
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)

    log.info("=" * 52)
    log.info("  BACKTEST RESULTS — %s", SYMBOL)
    log.info("=" * 52)
    log.info("  Total Trades  : %d", len(trades))
    log.info("  Wins          : %d  |  Losses: %d", len(wins), len(losses))
    log.info("  Win Rate      : %.1f%%", len(wins) / len(trades) * 100)
    log.info("  Profit Factor : %.2f", profit_factor)
    log.info("  Net Points    : %.2f", net)
    log.info("  Max Drawdown  : %.2f pts", max_dd)
    log.info("=" * 52)


def plot_equity_curve(trades: list, path: str = "equity_curve.png"):
    if not trades:
        return

    times = [t["time"].to_pydatetime() if hasattr(t["time"], "to_pydatetime") else t["time"] for t in trades]
    pnls = [t["pnl_points"] for t in trades]

    equity = [0.0]
    for p in pnls:
        equity.append(equity[-1] + p)

    # Drawdown series
    peak = 0.0
    drawdown = []
    for e in equity[1:]:
        peak = max(peak, e)
        drawdown.append(peak - e)

    wins = [i for i, t in enumerate(trades) if t["result"] == "win"]
    losses = [i for i, t in enumerate(trades) if t["result"] == "loss"]

    fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, 1, figsize=(14, 14), sharex=False,
                                              gridspec_kw={"height_ratios": [3, 1, 2, 2]})
    fig.patch.set_facecolor("#0d1117")
    for ax in (ax1, ax2, ax3, ax4):
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#c9d1d9")
        ax.spines[:].set_color("#30363d")

    # --- Equity curve ---
    trade_nums = list(range(len(trades) + 1))
    ax1.plot(trade_nums, equity, color="#58a6ff", linewidth=1.8, zorder=3)
    ax1.fill_between(trade_nums, equity, alpha=0.12, color="#58a6ff")

    # Win/loss markers
    ax1.scatter([w + 1 for w in wins],  [equity[w + 1] for w in wins],
                color="#3fb950", s=40, zorder=4, label="Win")
    ax1.scatter([l + 1 for l in losses], [equity[l + 1] for l in losses],
                color="#f85149", s=40, zorder=4, label="Loss")

    ax1.axhline(0, color="#30363d", linewidth=0.8, linestyle="--")
    ax1.set_ylabel("Cumulative Points", color="#c9d1d9")
    ax1.set_title(f"ICT 2022 — {SYMBOL} Equity Curve", color="#e6edf3", fontsize=13, pad=10)
    ax1.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")
    ax1.yaxis.label.set_color("#c9d1d9")

    # --- Drawdown ---
    dd_x = list(range(1, len(drawdown) + 1))
    ax2.fill_between(dd_x, [-d for d in drawdown], color="#f85149", alpha=0.4)
    ax2.plot(dd_x, [-d for d in drawdown], color="#f85149", linewidth=1.2)
    ax2.set_ylabel("Drawdown (pts)", color="#c9d1d9")
    ax2.set_xlabel("Trade #", color="#c9d1d9")
    ax2.yaxis.label.set_color("#c9d1d9")
    ax2.xaxis.label.set_color("#c9d1d9")

    # Stats annotation
    total = len(trades)
    win_rate = len(wins) / total * 100
    net = equity[-1]
    stats_text = (
        f"Trades: {total}   Win Rate: {win_rate:.1f}%   "
        f"Net: {net:+.0f} pts   Max DD: {max(drawdown):.0f} pts"
    )
    fig.text(0.5, 0.01, stats_text, ha="center", color="#8b949e", fontsize=9)

    # --- Monthly bars ---
    plot_monthly_bars(trades, ax3)

    # --- Session breakdown ---
    plot_session_panel(trades, ax4)

    plt.tight_layout(rect=[0, 0.03, 1, 1])
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    log.info("Equity curve saved to %s", path)


def export_csv(trades: list, path: str = "backtest_results.csv"):
    if not trades:
        return
    base_fields = ["time", "session", "result", "entry", "sl", "tp", "pnl_points"]
    fieldnames  = base_fields + FEATURES
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for t in trades:
            row = {**t, "time": t["time"].strftime("%Y-%m-%d %H:%M")}
            row.update(t.get("features", {}))
            writer.writerow(row)
    log.info("Results exported to %s (includes ML features)", path)


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

def run_backtest(date_from: datetime, date_to: datetime):
    if not mt5.initialize():
        log.error("MT5 init failed: %s", mt5.last_error())
        return

    log.info("Fetching %s data: %s → %s", SYMBOL, date_from.date(), date_to.date())

    df_15m = fetch_data(mt5.TIMEFRAME_M15, date_from, date_to)
    df_1m  = fetch_data(mt5.TIMEFRAME_M1,  date_from, date_to)

    if df_15m.empty or df_1m.empty:
        log.error("No data returned — check symbol name and date range")
        mt5.shutdown()
        return

    log.info("15m candles: %d | 1m candles: %d", len(df_15m), len(df_1m))

    trades    = []
    mss_cache: set = set()
    window    = 100
    journal   = TradeJournal()
    trade_idx = 0

    for i in range(window, len(df_15m)):
        candle_time = df_15m.iloc[i]["time"]

        if not in_kill_zone_ts(candle_time):
            continue

        df_slice = df_15m.iloc[i - window: i + 1].reset_index(drop=True)
        mss = detect_mss_slice(df_slice)

        if mss is None:
            continue

        mss_key = (mss["direction"], round(mss["sweep_level"], 1))
        if mss_key in mss_cache:
            continue

        # 1m candles from this point forward
        df_1m_fwd = df_1m[df_1m["time"] >= candle_time].reset_index(drop=True)
        if df_1m_fwd.empty:
            continue

        fvg = find_fvg_slice(df_1m_fwd, mss["direction"])
        if fvg is None:
            continue

        # FVG must form within the kill zone
        if not in_kill_zone_ts(fvg["time"]):
            log.debug("FVG outside kill zone (%s) — skipping", fvg["time"])
            continue

        trade = simulate_trade(df_1m_fwd, fvg, mss["direction"], mss["sl"])
        if trade is None:
            continue

        features = extract_features(mss, fvg, df_slice, candle_time)
        trade["features"] = features
        trades.append(trade)
        mss_cache.add(mss_key)
        log.info("[%s] %s | Entry: %.2f | SL: %.2f | TP: %.2f | PnL: %+.2f pts",
                 trade["time"].strftime("%Y-%m-%d %H:%M"),
                 trade["result"].upper(),
                 trade["entry"], trade["sl"], trade["tp"], trade["pnl_points"])

        # Journal entry (use trade index as a stable ID for backtest trades)
        trade_idx += 1
        signal_dt = candle_time.to_pydatetime() if hasattr(candle_time, "to_pydatetime") \
                    else candle_time
        journal.log_entry(trade_idx, df_slice, mss, fvg,
                          trade["entry"], trade["sl"], trade["tp"],
                          mss["direction"], signal_dt, features)
        journal.log_exit(trade_idx, trade["result"], trade["pnl_points"])

    print_stats(trades)
    print_monthly_table(trades)
    print_session_table(trades)
    export_csv(trades)
    plot_equity_curve(trades)
    mt5.shutdown()


if __name__ == "__main__":
    FROM = datetime(2024, 1, 1, tzinfo=timezone.utc)
    TO   = datetime(2024, 6, 30, tzinfo=timezone.utc)
    run_backtest(FROM, TO)
