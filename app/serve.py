"""Supervisor that runs `mlflow models serve` for whatever state/deployment.json names."""

import logging
import os
import signal
import subprocess
import sys
import time

import httpx
import pandas as pd

from app.common import FEATURES, ROOT, STATE, read_json, setup, write_json

LOG = logging.getLogger("aircast.serve")
URL = "http://127.0.0.1:5001"


def stop(child):
    """Stop the server and its workers, which share its process group."""
    if child and child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)


def verify(child, desired):
    """A real prediction must return the expected run ID; a 200 from /health would not prove that."""
    rows = pd.read_parquet(ROOT / desired["features_path"])
    sample = rows.loc[rows.aqi_today.notna(), FEATURES].head(1)
    body = {"dataframe_records": sample.astype(object).where(sample.notna(), None).to_dict("records")}
    deadline = time.monotonic() + 110
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError("MLflow server exited; see state/model-server.log.")
        try:
            response = httpx.post(URL + "/invocations", json=body, timeout=3)
            if response.json()["predictions"][0]["run_id"] == desired["run_id"]:
                return
        except (httpx.HTTPError, KeyError, ValueError):
            pass
        time.sleep(0.5)
    raise TimeoutError("MLflow prediction smoke test timed out.")


def main():
    setup()
    child, current = None, None
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            desired = read_json(STATE / "deployment.json", {})
            if not desired:
                stop(child)
                child, current = None, None
                write_json(STATE / "served.json", {"status": "waiting", "version": None})
            elif desired["run_id"] != current or child.poll() is not None:
                stop(child)
                write_json(STATE / "served.json", {**desired, "status": "loading"})
                command = [sys.executable, "-m", "mlflow", "models", "serve", "-m", desired["model_uri"]]
                command += "--host 0.0.0.0 --port 5001 --env-manager local --workers 1".split()
                with (STATE / "model-server.log").open("a") as log:
                    child = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
                try:
                    verify(child, desired)
                    current = desired["run_id"]
                    write_json(STATE / "served.json", {**desired, "status": "ready"})
                    LOG.info("Verified serving model version %s", desired["version"])
                except Exception as exc:
                    LOG.exception("Model deployment failed")
                    stop(child)
                    write_json(STATE / "served.json", {**desired, "status": "failed", "error": str(exc)})
                    time.sleep(3)
            time.sleep(2)
    finally:
        stop(child)


if __name__ == "__main__":
    main()
