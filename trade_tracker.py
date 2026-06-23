"""
Tracks live trades after they are placed, detects when they close,
appends results + ML features to backtest_results.csv, and triggers
model retraining every RETRAIN_EVERY new closed trades.
"""

import csv
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import MetaTrader5 as mt5

from config import MAGIC, SYMBOL
from ml_filter import FEATURES, train

log = logging.getLogger(__name__)

PENDING_PATH  = Path("pending_trades.json")
RETRAIN_EVERY = 10   # retrain after every N new live trades closed


class TradeTracker:

    def __init__(self, csv_path: str = "backtest_results.csv", journal=None):
        self.csv_path   = Path(csv_path)
        self.pending    = self._load_pending()
        self._new_since_retrain = 0
        self._journal   = journal   # optional TradeJournal instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_open(self, position_id: int, features: dict,
                    entry: float, sl: float, tp: float,
                    direction: str, signal_time: datetime):
        self.pending[str(position_id)] = {
            "features":  features,
            "entry":     entry,
            "sl":        sl,
            "tp":        tp,
            "direction": direction,
            "time":      signal_time.strftime("%Y-%m-%d %H:%M"),
        }
        self._save_pending()
        log.info("Tracking position %d for ML retraining", position_id)

    def check_closed_trades(self) -> int:
        """
        Check MT5 history for any pending trades that have now closed.
        Appends each closed trade to the CSV and returns the count of
        newly closed trades found this call.
        """
        if not self.pending:
            return 0

        open_ids = {
            str(p.ticket)
            for p in (mt5.positions_get(symbol=SYMBOL) or [])
            if p.magic == MAGIC
        }

        newly_closed = 0
        for pos_id in list(self.pending.keys()):
            if pos_id in open_ids:
                continue

            outcome = self._get_outcome(int(pos_id))
            if outcome is None:
                continue

            data = self.pending.pop(pos_id)
            self._append_to_csv(data, outcome)
            self._save_pending()
            newly_closed            += 1
            self._new_since_retrain += 1
            log.info("Live trade closed | pos: %s | result: %s | pnl_pts: %+.1f",
                     pos_id, outcome["result"], outcome["pnl_points"])

            if self._journal:
                self._journal.log_exit(
                    int(pos_id),
                    outcome["result"],
                    outcome["pnl_points"],
                    exit_time=datetime.now(timezone.utc),
                )

        return newly_closed

    def should_retrain(self) -> bool:
        return self._new_since_retrain >= RETRAIN_EVERY

    def retrain_and_reload(self) -> dict | None:
        """Retrain the model and return the freshly loaded model dict."""
        log.info("Retraining model on %d new live trades...", self._new_since_retrain)
        train(str(self.csv_path))
        self._new_since_retrain = 0
        from ml_filter import load_model
        return load_model()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_pending(self) -> dict:
        if PENDING_PATH.exists():
            with open(PENDING_PATH) as f:
                return json.load(f)
        return {}

    def _save_pending(self):
        with open(PENDING_PATH, "w") as f:
            json.dump(self.pending, f, indent=2, default=str)

    def _get_outcome(self, position_id: int) -> dict | None:
        from_time = datetime.now(timezone.utc) - timedelta(days=30)
        to_time   = datetime.now(timezone.utc)
        deals     = mt5.history_deals_get(from_time, to_time)
        if not deals:
            return None

        closing = [
            d for d in deals
            if d.position_id == position_id
            and d.entry      == mt5.DEAL_ENTRY_OUT
            and d.magic      == MAGIC
        ]
        if not closing:
            return None

        deal   = closing[-1]
        result = "win" if deal.profit > 0 else "loss"
        return {"result": result, "pnl_points": deal.profit}

    def _append_to_csv(self, data: dict, outcome: dict):
        direction = data["direction"]
        entry, sl, tp = data["entry"], data["sl"], data["tp"]

        if outcome["result"] == "win":
            pnl_points = (tp - entry) if direction == "bullish" else (entry - tp)
        else:
            pnl_points = (sl - entry) if direction == "bullish" else (entry - sl)

        session_label = {0: "London", 1: "NY"}.get(
            data["features"].get("session", -1), "Unknown"
        )

        base_fields = ["time", "session", "result", "entry", "sl", "tp", "pnl_points"]
        fieldnames  = base_fields + FEATURES
        file_exists = self.csv_path.exists()

        row = {
            "time":       data["time"],
            "session":    session_label,
            "result":     outcome["result"],
            "entry":      entry,
            "sl":         sl,
            "tp":         tp,
            "pnl_points": round(pnl_points, 2),
        }
        row.update(data["features"])

        with open(self.csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        log.info("Appended live trade to %s → total rows: %d",
                 self.csv_path,
                 sum(1 for _ in open(self.csv_path)) - 1)
