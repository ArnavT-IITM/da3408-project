"""SQLite store of forecasts and their revealed outcomes."""

import sqlite3
import time
from contextlib import contextmanager

from app.common import STATE

# One forecast per city, date and model run, so repeated requests and replays are idempotent.
SCHEMA = """CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY, city TEXT, as_of TEXT, forecast_date TEXT,
    predicted_aqi REAL, model_version TEXT, model_run_id TEXT,
    actual_aqi REAL, error REAL, created_at REAL,
    UNIQUE(city, as_of, model_run_id))"""
# When several model versions forecast the same city and date, only the latest one is scored.
LATEST_SCORED = """FROM predictions p WHERE actual_aqi IS NOT NULL AND id=(
    SELECT MAX(id) FROM predictions q
    WHERE q.city=p.city AND q.forecast_date=p.forecast_date AND q.actual_aqi IS NOT NULL)"""


@contextmanager
def database():
    STATE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE / "app.db", timeout=15)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute(SCHEMA)
            yield db
    finally:
        db.close()


def add_prediction(city, as_of, forecast_date, prediction, version, run_id):
    with database() as db:
        db.execute(
            """INSERT OR IGNORE INTO predictions
            (city,as_of,forecast_date,predicted_aqi,model_version,model_run_id,created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (city, as_of, forecast_date, prediction, version, run_id, time.time()),
        )
        row = db.execute(
            "SELECT * FROM predictions WHERE city=? AND as_of=? AND model_run_id=?", (city, as_of, run_id)
        ).fetchone()
    return dict(row)


def reveal(frame, through_date):
    """Attach the observed AQI to forecasts dated up to through_date, never later ones."""
    count = 0
    with database() as db:
        pending = db.execute(
            "SELECT * FROM predictions WHERE actual_aqi IS NULL AND forecast_date <= ?", (through_date,)
        ).fetchall()
        for row in pending:
            match = frame.loc[
                (frame.City == row["city"]) & (frame.forecast_date.astype(str) == row["forecast_date"])
            ]
            if len(match) == 1 and match.target.notna().iloc[0]:
                actual = float(match.target.iloc[0])
                db.execute(
                    "UPDATE predictions SET actual_aqi=?, error=? WHERE id=?",
                    (actual, abs(actual - row["predicted_aqi"]), row["id"]),
                )
                count += 1
    return count


def unreveal_all():
    with database() as db:
        return db.execute(
            "UPDATE predictions SET actual_aqi=NULL, error=NULL WHERE actual_aqi IS NOT NULL"
        ).rowcount


def city_accuracy():
    with database() as db:
        rows = db.execute(f"SELECT city, COUNT(*) AS scored, AVG(error) AS mae {LATEST_SCORED} GROUP BY city")
        return {row["city"]: dict(row) for row in rows}


def scored_series(city, limit):
    """The latest `limit` scored dates for a city, oldest first."""
    with database() as db:
        rows = db.execute(
            f"""SELECT forecast_date, predicted_aqi, actual_aqi, error, model_version {LATEST_SCORED}
            AND city=? ORDER BY forecast_date DESC LIMIT ?""",
            (city, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def summary():
    with database() as db:
        rows = db.execute("""SELECT city, COUNT(*) AS predictions, COUNT(error) AS scored,
            SUM(error) AS error_sum, AVG(error) AS mae FROM predictions GROUP BY city""")
        return [dict(row) for row in rows]
