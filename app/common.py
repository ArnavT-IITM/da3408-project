"""Paths, configuration and JSON state files shared by every service."""

import hashlib
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
ARTIFACTS = ROOT / "artifacts"
STATE = ROOT / "state"
MODEL_NAME = "aircast-aqi"
STAGES = ["ingest", "validate", "transform", "train", "evaluate"]
# Column order is part of the MLflow signature.
FEATURES = ["city_code", "month", "weekday", "aqi_today", "aqi_lag1", "aqi_lag7", "aqi_mean7"]
FEATURES += ["PM2.5", "PM10", "NO2", "CO", "station_count"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def config():
    return yaml.safe_load((ROOT / "params.yaml").read_text())


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def write_json(path, value):
    """Write to a temporary file and rename it, so readers never see half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2, allow_nan=False, default=str)
    os.replace(f.name, path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def provenance():
    """Git commit and changed files, logged with every MLflow run."""
    git = lambda *args: subprocess.check_output(["git", *args], cwd=ROOT, text=True)  # noqa: E731
    changed = sorted(line[3:] for line in git("status", "--porcelain").splitlines())
    return {
        "git_commit": git("rev-parse", "HEAD").strip(),
        "git_dirty": str(bool(changed)).lower(),
        "git_dirty_files": ",".join(changed)[:500],
    }


def setup():
    for path in (DATA, ARTIFACTS, STATE):
        path.mkdir(parents=True, exist_ok=True)
