"""
Telegram alerts for the ICT 2022 trading bot.

Setup:
  1. Message @BotFather on Telegram → /newbot → copy the token
  2. Message @userinfobot on Telegram → copy your Chat ID
  3. Paste both into config.py: TELEGRAM_TOKEN and TELEGRAM_CHAT_ID
"""

import logging
from pathlib import Path

import requests

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, SYMBOL

log     = logging.getLogger(__name__)
ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)
_BASE   = "https://api.telegram.org/bot{token}/{method}"


# ---------------------------------------------------------------------------
# Core send helpers
# ---------------------------------------------------------------------------

def _post(method: str, data: dict, files=None) -> bool:
    if not ENABLED:
        return False
    url = _BASE.format(token=TELEGRAM_TOKEN, method=method)
    try:
        r = requests.post(url, data=data, files=files, timeout=10)
        if not r.ok:
            log.warning("Telegram %s failed: %s", method, r.text[:200])
            return False
        return True
    except Exception as exc:
        log.warning("Telegram error: %s", exc)
        return False


def send_message(text: str) -> bool:
    return _post("sendMessage", {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    })


def send_photo(path: Path, caption: str = "") -> bool:
    if not Path(path).exists():
        return send_message(caption)
    with open(path, "rb") as f:
        return _post("sendPhoto", {
            "chat_id":    TELEGRAM_CHAT_ID,
            "caption":    caption[:1024],
            "parse_mode": "HTML",
        }, files={"photo": f})


# ---------------------------------------------------------------------------
# Named alert functions
# ---------------------------------------------------------------------------

def alert_bot_started(mode: str):
    emoji = "🟢" if "LIVE" in mode.upper() else "🔵"
    send_message(
        f"{emoji} <b>Bot Started</b>\n"
        f"Symbol : <code>{SYMBOL}</code>\n"
        f"Mode   : <code>{mode}</code>"
    )


def alert_bot_stopped():
    send_message(f"🔴 <b>Bot Stopped</b> — <code>{SYMBOL}</code>")


def alert_signal(direction: str, session: str, entry: float,
                 sl: float, tp: float, rr: float, ml_prob: float = None):
    arrow   = "📈" if direction == "bullish" else "📉"
    ml_line = (f"ML Conf   : <code>{ml_prob * 100:.1f}%</code>\n"
               if ml_prob is not None else "")
    send_message(
        f"{arrow} <b>ICT Signal — {SYMBOL}</b>\n"
        f"Direction : <code>{direction.upper()}</code>\n"
        f"Session   : <code>{session}</code>\n"
        f"Entry     : <code>{entry:.2f}</code>\n"
        f"SL        : <code>{sl:.2f}</code>\n"
        f"TP        : <code>{tp:.2f}</code>\n"
        f"RR        : <code>1 : {rr:.1f}</code>\n"
        f"{ml_line}"
    )


def alert_trade_placed(direction: str, entry: float, sl: float, tp: float,
                       lot: float, position_id: int,
                       screenshot: Path = None, dry_run: bool = False):
    tag    = "[DRY RUN] " if dry_run else ""
    emoji  = "🔵" if dry_run else "✅"
    rr     = round(abs(tp - entry) / abs(entry - sl), 1) if abs(entry - sl) > 0 else 0
    text   = (
        f"{emoji} <b>{tag}Order Placed — {SYMBOL}</b>\n"
        f"Direction : <code>{direction.upper()}</code>\n"
        f"Lot       : <code>{lot}</code>\n"
        f"Entry     : <code>{entry:.2f}</code>\n"
        f"SL        : <code>{sl:.2f}</code>\n"
        f"TP        : <code>{tp:.2f}</code>\n"
        f"RR        : <code>1 : {rr}</code>\n"
        f"Position  : <code>#{position_id}</code>"
    )
    if screenshot:
        send_photo(screenshot, text)
    else:
        send_message(text)


def alert_trade_closed(position_id: int, result: str,
                       pnl_points: float, direction: str):
    emoji = "🏆" if result == "win" else "❌"
    sign  = "+" if pnl_points >= 0 else ""
    send_message(
        f"{emoji} <b>Trade Closed — {SYMBOL}</b>\n"
        f"Result    : <code>{result.upper()}</code>\n"
        f"Direction : <code>{direction.upper()}</code>\n"
        f"PnL       : <code>{sign}{pnl_points:.1f} pts</code>\n"
        f"Position  : <code>#{position_id}</code>"
    )


def alert_ml_rejected(direction: str, prob: float, threshold: float):
    send_message(
        f"🚫 <b>ML Rejected Setup — {SYMBOL}</b>\n"
        f"Direction : <code>{direction.upper()}</code>\n"
        f"Win Prob  : <code>{prob * 100:.1f}%</code>\n"
        f"Threshold : <code>{threshold * 100:.0f}%</code>"
    )


def alert_model_retrained(total_trades: int, accuracy: float = None):
    acc_line = f"CV Accuracy : <code>{accuracy * 100:.1f}%</code>\n" if accuracy else ""
    send_message(
        f"🧠 <b>ML Model Retrained</b>\n"
        f"Total Trades : <code>{total_trades}</code>\n"
        f"{acc_line}"
    )


def alert_backtest_summary(total: int, wins: int, losses: int,
                            win_rate: float, net_pts: float,
                            profit_factor: float):
    pf_str = f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞"
    send_message(
        f"📊 <b>Backtest Complete — {SYMBOL}</b>\n"
        f"Trades        : <code>{total}</code>\n"
        f"Wins / Losses : <code>{wins} / {losses}</code>\n"
        f"Win Rate      : <code>{win_rate:.1f}%</code>\n"
        f"Net Points    : <code>{net_pts:+.1f}</code>\n"
        f"Profit Factor : <code>{pf_str}</code>"
    )
