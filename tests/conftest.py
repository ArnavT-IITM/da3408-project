"""Synthetic rows stay in tests; production uses only the sourced CSVs."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import api, common, deploy, metrics, operations, pipeline, store


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    for module in [api, common, deploy, metrics, operations, pipeline, store]:
        if hasattr(module, "STATE"):
            monkeypatch.setattr(module, "STATE", tmp_path / "state")
        if hasattr(module, "ARTIFACTS"):
            monkeypatch.setattr(module, "ARTIFACTS", tmp_path / "artifacts")
    common.setup()
    monkeypatch.setenv("AIRCAST_PROBES", "{}")


@pytest.fixture
def feature_frame():
    dates = pd.date_range("2019-12-31", periods=5)
    frame = pd.DataFrame(
        {
            "Date": dates,
            "forecast_date": dates + pd.Timedelta(days=1),
            "City": "Delhi",
            "target": [80.0, 90.0, 100.0, 110.0, float("nan")],
        }
    )
    for name in common.FEATURES:
        frame[name] = 50.0
    frame["month"] = frame.Date.dt.month
    return frame


@pytest.fixture
def client(feature_frame, monkeypatch):
    """API test client whose served feature table is the synthetic frame above."""
    monkeypatch.setattr(operations, "features", lambda *args: feature_frame)
    with TestClient(api.app) as client:
        yield client
