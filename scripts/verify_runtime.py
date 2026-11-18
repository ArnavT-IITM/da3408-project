"""Exercise real services: prediction, replay, failed gate, retraining and rollback.

Run only on this local demonstration. It temporarily tightens the gate, restores
params.yaml in a finally block, and leaves the original served model active.
"""

import json
import os
import time
from pathlib import Path

import httpx
import yaml

BASE = os.getenv("AIRCAST_API_URL", "http://127.0.0.1:18000")
ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text())


def main():
    evidence = {"started_at": time.time(), "base_url": BASE, "checks": []}

    def record(name, value):
        evidence["checks"].append({"name": name, "passed": True, "observed": value})
        print(name, value, flush=True)

    with httpx.Client(base_url=BASE, timeout=30) as client:

        def job(path, body=None, expected="success"):
            response = client.post(path, json=body) if body is not None else client.post(path)
            response.raise_for_status()
            run_id = response.json()["run_id"]
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                runs = client.get("/pipeline").json()["runs"]
                run = next(row for row in runs if row["id"] == run_id)
                if run["status"] != "running":
                    assert run["status"] == expected, run
                    return run
                time.sleep(1)
            raise TimeoutError(f"Job {run_id} did not finish")

        status = client.get("/status").json()
        original = status["model"]["version"]
        assert status["model"]["status"] == "ready"
        prediction = client.post("/predict", json={"city": "Delhi", "date": "2020-02-01"})
        prediction.raise_for_status()
        assert prediction.json()["model_version"] == original
        record("real_mlflow_prediction", prediction.json())
        invalid = client.post("/predict", json={"city": "Unknown", "date": "2020-02-01"})
        assert invalid.status_code == 422
        record("invalid_input", invalid.status_code)
        replay = job("/replay", {"days": 5, "delay": 0.1})
        by_city = client.get("/accuracy", params={"city": "Delhi"}).json()["by_city"]
        assert sum(row["scored"] for row in by_city) > 0
        record("real_delayed_feedback", replay)

        params_path = ROOT / "params.yaml"
        original_params = params_path.read_bytes()
        try:
            tightened = yaml.safe_load(original_params)
            tightened["gate"]["max_mae"] = 0.0
            params_path.write_text(yaml.safe_dump(tightened, sort_keys=False))
            failed = job("/pipeline/run", expected="failed")
            evaluation_stage = read_json(ROOT / "state/stages/evaluate.json")
            evaluation = read_json(ROOT / "artifacts/evaluation.json")
            candidate = read_json(ROOT / "artifacts/candidate.json")
            gate_error = "Candidate failed the configured acceptance gate"
            assert evaluation_stage["status"] == "failed", evaluation_stage
            assert evaluation_stage["started_at"] >= failed["started_at"], evaluation_stage
            assert gate_error in evaluation_stage["error"], evaluation_stage
            assert gate_error in failed["error"], failed
            assert evaluation["passed"] is False, evaluation
            assert evaluation["run_id"] == candidate["run_id"], evaluation
            assert evaluation["gate"]["max_mae"] == 0.0, evaluation
            still = client.post("/predict", json={"city": "Delhi", "date": "2020-02-01"})
            still.raise_for_status()
            assert still.json()["model_version"] == original
            record("rejected_candidate_preserves_serving", failed)
        finally:
            params_path.write_bytes(original_params)

        retrain = job("/pipeline/run", {"force": True})
        replacement = client.get("/status").json()["model"]["version"]
        assert replacement != original
        response = client.post("/predict", json={"city": "Delhi", "date": "2020-02-01"})
        response.raise_for_status()
        assert response.json()["model_version"] == replacement
        record("retraining_reloads_real_server", retrain)
        restored = job("/models/rollback")
        response = client.post("/predict", json={"city": "Delhi", "date": "2020-02-01"})
        response.raise_for_status()
        assert response.json()["model_version"] == original
        record("rollback_verified_by_prediction", restored)
        scrape = client.get("/metrics")
        assert scrape.status_code == 200 and "aircast_prediction_absolute_error_count" in scrape.text
        record("prometheus_export", "HTTP200; persisted prediction error metrics present")
    evidence["finished_at"] = time.time()
    path = ROOT / "evidence/runtime.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
