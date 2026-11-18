"""Register a passed candidate, have app/serve.py load and verify it, then move the alias."""

import os
import shutil
import time

import mlflow
from mlflow import MlflowClient

from app.common import ARTIFACTS, DATA, MODEL_NAME, ROOT, STATE, read_json, sha256, write_json


def wait_served(target, timeout=120):
    """Wait until the model server reports this exact run as verified."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        served = read_json(STATE / "served.json", {})
        if served.get("run_id") == target["run_id"]:
            if served.get("status") == "ready":
                return served
            if served.get("status") == "failed":
                raise RuntimeError(served.get("error", "Model server failed to load the model."))
        time.sleep(0.5)
    raise TimeoutError(f"Model server did not confirm the model within {timeout} seconds.")


def activate(target):
    """Serve `target`; if it never becomes ready, restore and re-verify the previous deployment."""
    previous = read_json(STATE / "deployment.json", {})
    if previous.get("run_id") == target["run_id"]:
        wait_served(target)
        return previous
    if previous:
        target = {**target, "previous": {k: v for k, v in previous.items() if k != "previous"}}
    write_json(STATE / "deployment.json", target)
    try:
        wait_served(target)
        MlflowClient().set_registered_model_alias(MODEL_NAME, "champion", str(target["version"]))
    except Exception:
        write_json(STATE / "deployment.json", previous)
        if previous:
            wait_served(previous)
        raise
    return target


def promote():
    """Deploy the evaluated candidate with its own copy of the features it was trained on."""
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", f"sqlite:///{STATE / 'mlflow.db'}"))
    candidate = read_json(ARTIFACTS / "candidate.json")
    evaluation = read_json(ARTIFACTS / "evaluation.json", {})
    if not evaluation.get("passed") or evaluation.get("run_id") != candidate["run_id"]:
        raise ValueError("Only the evaluated candidate may be promoted.")
    if sha256(DATA / "features.parquet") != candidate["data_sha256"]:
        raise ValueError("Features changed after training; rerun the pipeline.")
    snapshot = ARTIFACTS / "serving" / candidate["run_id"]
    snapshot.mkdir(parents=True, exist_ok=True)
    if not (snapshot / "features.parquet").exists():
        shutil.copyfile(DATA / "features.parquet", snapshot / "features.parquet")
        shutil.copyfile(ARTIFACTS / "reference.json", snapshot / "reference.json")
    client = MlflowClient()
    versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    version = next((v for v in versions if v.run_id == candidate["run_id"]), None)
    if version is None:
        version = mlflow.register_model(candidate["model_uri"], MODEL_NAME)
        client.set_model_version_tag(MODEL_NAME, version.version, "validation_status", "passed")
    return activate(
        {
            "version": str(version.version),
            "run_id": candidate["run_id"],
            "model_uri": f"models:/{MODEL_NAME}/{version.version}",
            "features_path": str((snapshot / "features.parquet").relative_to(ROOT)),
            "reference_path": str((snapshot / "reference.json").relative_to(ROOT)),
            "data_config": candidate["data_config"],
            "experiment_id": candidate.get("experiment_id"),
        }
    )


def rollback():
    previous = read_json(STATE / "deployment.json", {}).get("previous")
    if not previous:
        raise ValueError("There is no previous approved model to restore.")
    return activate(previous)
