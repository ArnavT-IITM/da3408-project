"""Forecasts, the replay simulation and the single background job slot."""

import logging
import os
import re
import subprocess
import threading
import time
import uuid

import httpx
import numpy as np
import pandas as pd

from app import store
from app.common import ARTIFACTS, DATA, FEATURES, ROOT, STAGES, STATE, config, read_json, write_json
from app.deploy import promote
from app.features import category

LOG = logging.getLogger("aircast.operations")
# Pipeline, replay and rollback all change serving or forecast state, so only one runs at a time.
JOB_LOCK = threading.Lock()
BUSY = "Another pipeline, replay or deployment is running. Wait for it to finish."
_cache = {}


def served():
    return read_json(STATE / "served.json", {})


def data_config():
    return served().get("data_config", config()["data"])


def features(deployment=None):
    """Feature table of the served model, so a rejected retrain cannot change live inputs."""
    deployment = deployment or served()
    path = (
        ROOT / deployment["features_path"] if deployment.get("features_path") else DATA / "features.parquet"
    )
    if not path.exists():
        raise ValueError("No prepared data yet. Run the pipeline first.")
    key = (path, path.stat().st_mtime_ns)
    if key not in _cache:
        _cache.clear()
        _cache[key] = pd.read_parquet(path)
    return _cache[key]


def predict(city, date):
    """Forecast date + 1 through the MLflow server. ValueError means bad input, RuntimeError unavailable."""
    model, period = served(), data_config()
    date = pd.Timestamp(date)
    if city not in period["cities"]:
        raise ValueError("Select one of the supported cities.")
    if not pd.Timestamp(period["validation_end"]) <= date < pd.Timestamp(period["replay_end"]):
        raise ValueError("Choose an as-of date inside the historical replay period.")
    frame = features(model)
    row = frame.loc[(frame.City == city) & (frame.Date == date)]
    if len(row) != 1 or row.aqi_today.isna().iloc[0]:
        raise ValueError("No observed AQI is available for this city on this date. Choose another day.")
    if model.get("status") != "ready":
        raise RuntimeError("The model is not ready. Complete the pipeline or wait for deployment.")
    sample = row[FEATURES].astype(object).where(row[FEATURES].notna(), None)
    url = os.getenv("MODEL_SERVER_URL", "http://127.0.0.1:5001") + "/invocations"
    response = httpx.post(url, json={"dataframe_records": sample.to_dict("records")}, timeout=15)
    response.raise_for_status()
    result = response.json()["predictions"][0]
    if result["run_id"] != model["run_id"]:
        raise RuntimeError("Model is changing; retry after deployment completes.")
    value = float(result["aqi"])
    if not np.isfinite(value) or value < 0:
        raise RuntimeError("Model returned an invalid AQI.")
    forecast_date = str((date + pd.Timedelta(days=1)).date())
    record = store.add_prediction(
        city, str(date.date()), forecast_date, value, model["version"], result["run_id"]
    )
    record["category"], record["guidance"] = category(value)
    record["today_aqi"] = float(row.aqi_today.iloc[0])
    return record


def update_drift(frame, date):
    """Per city: |mean AQI of the last 30 days - training mean for that month| / training std."""
    reference = read_json(ROOT / served().get("reference_path", "missing"), {})
    window = config()["monitoring"]["drift_window"]
    scores = {}
    for city in data_config()["cities"]:
        recent = frame.loc[
            (frame.City == city) & frame.Date.between(date - pd.Timedelta(days=window - 1), date)
        ]
        recent = recent.aqi_today.dropna()
        baseline = reference.get(f"{city}:{date.month}")
        if baseline and len(recent) >= 5:
            scores[city] = float(abs(recent.mean() - baseline["mean"]) / baseline["std"])
    write_json(STATE / "drift.json", {"date": str(date.date()), "scores": scores})


def failure_summary(output):
    """The most telling line of CI output: an exception, failed tests, lint errors, then DVC."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for pattern in [r"^\w+(Error|Exception): ", r"^FAILED |\d+ failed", r"^Found \d+ error", r"^ERROR"]:
        matches = [line for line in lines if re.search(pattern, line)]
        if matches:
            return matches[-1][:300]
    return lines[-1][:300] if lines else "No output was produced."


def run_pipeline(force=False):
    """Run scripts/ci.sh, then deploy outside DVC so a fully cached run still reconciles serving."""
    log_path = STATE / "pipeline.log"
    log_path.touch()
    offset = log_path.stat().st_size
    with log_path.open("a") as log:
        env = {**os.environ, "AIRCAST_FORCE": "1" if force else "0"}
        code = subprocess.run(["sh", "scripts/ci.sh"], cwd=ROOT, env=env, stdout=log, stderr=log).returncode
    if code:
        output = log_path.read_bytes()[offset:].decode(errors="replace")
        raise RuntimeError(
            f"{failure_summary(output)}\n\nPipeline failed (exit {code}). Last output:\n{output[-1600:]}"
        )
    started = time.time()
    record = {"name": "deploy", "status": "running", "started_at": started}
    write_json(STATE / "stages" / "deploy.json", record)
    try:
        result = promote()
        record.update(status="success", version=result["version"], run_id=result["run_id"])
        return {**result, "experiment_id": read_json(ARTIFACTS / "candidate.json", {}).get("experiment_id")}
    except Exception as exc:
        record.update(status="failed", error=str(exc))
        raise
    finally:
        record.update(duration_seconds=time.time() - started, ended_at=time.time())
        write_json(STATE / "stages" / "deploy.json", record)


def next_replay_date(data_config):
    """The simulated today: the day after the last replayed date."""
    start = pd.Timestamp(data_config["validation_end"])
    last = read_json(STATE / "replay.json", {}).get("date")
    return max(start, pd.Timestamp(last) + pd.Timedelta(days=1)) if last else start


def replay(days, delay):
    """Simulate days: reveal outcomes that are now due, forecast each city, update drift."""
    frame, period = features(), data_config()
    start = next_replay_date(period)
    last_day = pd.Timestamp(period["replay_end"]) - pd.Timedelta(days=1)
    if start > last_day:
        raise ValueError("All historical dates have been replayed.")
    dates = pd.date_range(start, min(start + pd.Timedelta(days=days - 1), last_day))
    path = STATE / "replay.json"
    # The date stays at the last finished day until a new day completes, so the clock never jumps back.
    state = {"running": True, "status": "running", "completed_days": 0, "total_days": len(dates)}
    state["date"] = str((start - pd.Timedelta(days=1)).date())
    write_json(path, state)
    try:
        for date in dates:
            store.reveal(frame, str(date.date()))
            for city in period["cities"]:
                try:
                    predict(city, date)
                except ValueError:
                    pass
            update_drift(frame, date)
            state.update(completed_days=state["completed_days"] + 1, date=str(date.date()))
            write_json(path, state)
            time.sleep(delay)
        store.reveal(frame, str((dates[-1] + pd.Timedelta(days=1)).date()))
        state["status"] = "success"
    except Exception as exc:
        state.update(status="failed", error=str(exc))
        raise
    finally:
        state["running"] = False
        write_json(path, state)
    return state


def reset_replay():
    """Clear revealed outcomes and drift but keep the forecasts, which are scored again on replay."""
    if not JOB_LOCK.acquire(blocking=False):
        raise ValueError(BUSY)
    try:
        unscored = store.unreveal_all()
        write_json(STATE / "replay.json", {"running": False, "status": "idle"})
        (STATE / "drift.json").unlink(missing_ok=True)
        return {"simulated_today": str(next_replay_date(data_config()).date()), "unscored": unscored}
    finally:
        JOB_LOCK.release()


def start_job(kind, function, *args):
    """Run `function` in a background thread and return the job ID straight away."""
    if not JOB_LOCK.acquire(blocking=False):
        raise ValueError(BUSY)
    job = {"id": uuid.uuid4().hex[:12], "kind": kind, "status": "running", "started_at": time.time()}
    path = STATE / "jobs" / f"{job['id']}.json"
    write_json(path, job)

    def worker():
        try:
            job["result"] = function(*args)
            job["status"] = "success"
            if kind == "pipeline":
                write_json(STATE / "last_success.json", {"timestamp": time.time(), "id": job["id"]})
                # Stages DVC skipped keep their old record; mark them cached so the UI is not misleading.
                for name in STAGES:
                    stage = read_json(STATE / "stages" / f"{name}.json", {})
                    if stage and stage.get("started_at", 0) < job["started_at"]:
                        write_json(STATE / "stages" / f"{name}.json", {**stage, "status": "cached"})
        except Exception as exc:
            LOG.exception("%s job failed", kind)
            job.update(status="failed", error=str(exc))
        finally:
            job["ended_at"] = time.time()
            write_json(path, job)
            JOB_LOCK.release()

    threading.Thread(target=worker, daemon=True).start()
    return job["id"]


def jobs():
    """All job records, newest first."""
    records = [read_json(path) for path in (STATE / "jobs").glob("*.json")]
    return sorted(records, key=lambda job: job["started_at"], reverse=True)
