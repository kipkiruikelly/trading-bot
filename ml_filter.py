"""
ML trade filter — Random Forest classifier that predicts win/loss
for each ICT 2022 setup before the order is placed.

Training flow:
  1. Run backtest.py  →  generates backtest_results.csv (with features)
  2. python ml_filter.py  →  trains model, saves model.pkl
  3. main.py loads model.pkl and filters live signals automatically
"""

import pickle
import logging
from datetime import timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.preprocessing import LabelEncoder

from killzone import LONDON_START, LONDON_END, NY_START, NY_END, _in_window

log = logging.getLogger(__name__)

MODEL_PATH = Path("model.pkl")
FEATURES   = [
    "session",
    "day_of_week",
    "hour_utc",
    "direction",
    "atr_15m",
    "fvg_size",
    "fvg_size_atr_ratio",
    "sweep_to_break_distance",
    "sweep_to_break_atr_ratio",
]
THRESHOLD  = 0.60   # minimum win probability required to take a trade
MIN_TRADES = 50     # minimum labeled trades needed to train


# ---------------------------------------------------------------------------
# Feature extraction  (called by both backtest.py and main.py)
# ---------------------------------------------------------------------------

def _atr(df: pd.DataFrame, period: int = 14) -> float:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]),
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return float(val) if not np.isnan(val) else 1.0


def _session_id(dt) -> int:
    utc = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    if _in_window(utc, LONDON_START, LONDON_END):
        return 0   # London
    if _in_window(utc, NY_START, NY_END):
        return 1   # NY
    return 2       # outside


def extract_features(mss: dict, fvg: dict, df_15m: pd.DataFrame, signal_time) -> dict:
    dt = signal_time
    if hasattr(dt, "to_pydatetime"):
        dt = dt.to_pydatetime()

    atr   = _atr(df_15m)
    fvg_size = fvg["top"] - fvg["bottom"]
    sweep_to_break = abs(mss["structure_break_level"] - mss["sweep_level"])

    return {
        "session":                  _session_id(dt),
        "day_of_week":              dt.weekday(),
        "hour_utc":                 dt.hour,
        "direction":                1 if mss["direction"] == "bullish" else 0,
        "atr_15m":                  round(atr, 4),
        "fvg_size":                 round(fvg_size, 4),
        "fvg_size_atr_ratio":       round(fvg_size / atr, 4) if atr > 0 else 0.0,
        "sweep_to_break_distance":  round(sweep_to_break, 4),
        "sweep_to_break_atr_ratio": round(sweep_to_break / atr, 4) if atr > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train(csv_path: str = "backtest_results.csv"):
    df = pd.read_csv(csv_path)

    missing = [f for f in FEATURES if f not in df.columns]
    if missing:
        log.error("CSV missing feature columns: %s", missing)
        log.error("Re-run backtest.py first to regenerate the CSV with features.")
        return

    if "result" not in df.columns:
        log.error("CSV missing 'result' column")
        return

    df = df.dropna(subset=FEATURES + ["result"])
    if len(df) < MIN_TRADES:
        log.warning(
            "Only %d labeled trades — need at least %d for reliable training. "
            "Run a longer backtest first.",
            len(df), MIN_TRADES,
        )
        if len(df) < 10:
            log.error("Too few trades to train. Aborting.")
            return

    X = df[FEATURES].values
    y = (df["result"] == "win").astype(int).values

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=6,
        min_samples_leaf=3,
        class_weight="balanced",
        random_state=42,
    )

    if len(df) >= MIN_TRADES:
        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        scores = cross_val_score(model, X, y, cv=cv, scoring="accuracy")
        log.info("Cross-val accuracy: %.1f%% ± %.1f%%",
                 scores.mean() * 100, scores.std() * 100)

    model.fit(X, y)

    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "features": FEATURES, "threshold": THRESHOLD}, f)

    log.info("Model trained on %d trades → saved to %s", len(df), MODEL_PATH)

    # Feature importance
    importances = sorted(zip(FEATURES, model.feature_importances_),
                         key=lambda x: x[1], reverse=True)
    log.info("Feature importances:")
    for feat, imp in importances:
        log.info("  %-30s %.3f", feat, imp)


# ---------------------------------------------------------------------------
# Prediction  (used by main.py)
# ---------------------------------------------------------------------------

def load_model() -> dict | None:
    if not MODEL_PATH.exists():
        log.warning("model.pkl not found — run 'python ml_filter.py' after backtesting to train")
        return None
    with open(MODEL_PATH, "rb") as f:
        return pickle.load(f)


def should_take_trade(features: dict, model_data: dict) -> tuple[bool, float]:
    model    = model_data["model"]
    feat_order = model_data["features"]
    threshold  = model_data.get("threshold", THRESHOLD)

    X = np.array([[features[f] for f in feat_order]])
    prob_win = model.predict_proba(X)[0][1]
    return prob_win >= threshold, round(prob_win, 3)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    csv = sys.argv[1] if len(sys.argv) > 1 else "backtest_results.csv"
    train(csv)
