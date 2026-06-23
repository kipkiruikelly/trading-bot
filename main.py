import time
import logging
import MetaTrader5 as mt5
from datetime import datetime, timezone

from config import SYMBOL, STRUCTURE_TF, MAGIC, SLIPPAGE, DRY_RUN
from killzone import in_kill_zone, active_session
from structure import get_candles, detect_mss
from entry import find_fvg, price_in_fvg
from risk import calculate_lot_size, calculate_tp
from ml_filter import extract_features, load_model, should_take_trade
from trade_tracker import TradeTracker
from journal import TradeJournal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def connect():
    if not mt5.initialize():
        log.error("MT5 init failed: %s", mt5.last_error())
        return False
    info = mt5.account_info()
    log.info("Connected | Account: %s | Balance: %.2f", info.login, info.balance)
    return True


def has_open_trade() -> bool:
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        return False
    return any(p.magic == MAGIC for p in positions)


def place_order(direction: str, entry: float, sl: float, tp: float):
    sl_points = abs(entry - sl)
    lot = calculate_lot_size(sl_points)
    if lot <= 0:
        log.warning("Lot size calculation failed, skipping trade")
        return

    if DRY_RUN:
        log.info(
            "[DRY RUN] SIGNAL | %s | Lot: %.2f | Entry: %.2f | SL: %.2f | TP: %.2f | RR: 2:1",
            direction.upper(), lot, entry, sl, tp,
        )
        return None

    order_type = mt5.ORDER_TYPE_BUY if direction == "bullish" else mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lot,
        "type": order_type,
        "price": entry,
        "sl": sl,
        "tp": tp,
        "deviation": SLIPPAGE,
        "magic": MAGIC,
        "comment": f"ICT2022 {direction}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            "ORDER PLACED | %s | Lot: %.2f | Entry: %.2f | SL: %.2f | TP: %.2f",
            direction.upper(), lot, entry, sl, tp,
        )
        # return the position ticket so the caller can track the trade
        time.sleep(0.5)
        positions = mt5.positions_get(symbol=SYMBOL)
        if positions:
            newest = max(
                (p for p in positions if p.magic == MAGIC),
                key=lambda p: p.time,
                default=None,
            )
            return newest.ticket if newest else None
    else:
        log.error("Order failed | retcode: %s | comment: %s", result.retcode, result.comment)

    return None


def run():
    if not connect():
        return

    mode = "DRY RUN (no real trades)" if DRY_RUN else "LIVE TRADING"
    log.info("Bot started — %s | watching %s", mode, SYMBOL)

    ml_model = load_model()
    if ml_model:
        log.info("ML filter loaded — minimum win probability: %.0f%%",
                 ml_model.get("threshold", 0.60) * 100)
    else:
        log.info("ML filter inactive — run 'python ml_filter.py' after backtesting to enable")

    journal  = TradeJournal()
    tracker  = TradeTracker(journal=journal)
    mss_cache = None  # holds last confirmed MSS to avoid re-triggering

    while True:
        try:
            if not in_kill_zone():
                log.debug("Outside kill zone (%s UTC) — waiting", datetime.now(timezone.utc).strftime("%H:%M"))
                mss_cache = None  # reset between sessions
                time.sleep(30)
                continue

            if has_open_trade():
                log.debug("Trade already open — monitoring")
                newly_closed = tracker.check_closed_trades()
                if newly_closed and tracker.should_retrain():
                    ml_model = tracker.retrain_and_reload()
                    log.info("Model retrained and reloaded with latest live trades")
                time.sleep(10)
                continue

            # --- 15m structure ---
            df_15m = get_candles(STRUCTURE_TF, 100)
            if df_15m.empty:
                time.sleep(10)
                continue

            mss = detect_mss(df_15m)
            if mss is None or not mss["confirmed"]:
                log.debug("No MSS detected — session: %s", active_session())
                time.sleep(10)
                continue

            # Avoid acting on same MSS twice
            mss_key = (mss["direction"], round(mss["sweep_level"], 2))
            if mss_cache == mss_key:
                time.sleep(10)
                continue

            log.info("MSS confirmed | direction: %s | sweep: %.2f | break: %.2f",
                     mss["direction"], mss["sweep_level"], mss["structure_break_level"])

            # --- 1m FVG ---
            fvg = find_fvg(mss["direction"])
            if fvg is None:
                log.debug("No FVG found after MSS")
                time.sleep(5)
                continue

            log.info("FVG found | top: %.2f | bottom: %.2f", fvg["top"], fvg["bottom"])

            # --- Feature extraction (always, for journal + ML filter) ---
            signal_time = datetime.now(timezone.utc)
            features    = extract_features(mss, fvg, df_15m, signal_time)

            # --- ML filter ---
            if ml_model:
                take, prob = should_take_trade(features, ml_model)
                if not take:
                    log.info("ML filter rejected | win prob: %.1f%% (min: %.0f%%)",
                             prob * 100, ml_model.get("threshold", 0.60) * 100)
                    mss_cache = mss_key
                    time.sleep(10)
                    continue
                log.info("ML filter approved | win probability: %.1f%%", prob * 100)

            # --- Wait for price to retrace into FVG ---
            tick = mt5.symbol_info_tick(SYMBOL)
            if tick is None:
                time.sleep(5)
                continue

            current_price = (tick.bid + tick.ask) / 2

            if not price_in_fvg(current_price, fvg):
                log.debug("Price %.2f not in FVG [%.2f - %.2f] — waiting for retrace",
                          current_price, fvg["bottom"], fvg["top"])
                time.sleep(5)
                continue

            # Kill zone must still be active at the moment of entry
            if not in_kill_zone():
                log.info("Kill zone ended before retrace completed — setup abandoned")
                mss_cache = mss_key
                time.sleep(30)
                continue

            # --- Entry ---
            direction = mss["direction"]
            entry = tick.ask if direction == "bullish" else tick.bid

            if direction == "bullish":
                sl = mss.get("sweep_candle_low", fvg["bottom"] - 10)
            else:
                sl = mss.get("sweep_candle_high", fvg["top"] + 10)

            tp = calculate_tp(entry, sl, direction)

            log.info("Entering trade | direction: %s | entry: %.2f | SL: %.2f | TP: %.2f",
                     direction, entry, sl, tp)

            position_ticket = place_order(direction, entry, sl, tp)
            mss_cache = mss_key

            if position_ticket and not DRY_RUN:
                tracker.record_open(
                    position_ticket, features,
                    entry, sl, tp, direction, signal_time,
                )
                journal.log_entry(
                    position_ticket, df_15m, mss, fvg,
                    entry, sl, tp, direction, signal_time, features,
                )

            time.sleep(60)  # cooldown after placing trade

            # --- Check closed trades + retrain ---
            newly_closed = tracker.check_closed_trades()
            if newly_closed and tracker.should_retrain():
                ml_model = tracker.retrain_and_reload()
                log.info("Model retrained and reloaded with latest live trades")

        except KeyboardInterrupt:
            log.info("Bot stopped by user")
            break
        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)
            time.sleep(10)

    mt5.shutdown()
    log.info("MT5 disconnected")


if __name__ == "__main__":
    run()
