import httpx
import pytest

from app import operations, store
from app.common import write_json


def ready_model(monkeypatch, run_id="run-one"):
    write_json(operations.STATE / "served.json", {"status": "ready", "version": "1", "run_id": "run-one"})
    monkeypatch.setattr(
        operations.httpx,
        "post",
        lambda *a, **kw: httpx.Response(
            200,
            json={"predictions": [{"aqi": 85.0, "run_id": run_id}]},
            request=httpx.Request("POST", "http://model/invocations"),
        ),
    )


def test_liveness_and_catalog_before_training(client):
    assert client.get("/healthz").status_code == 200
    assert "Delhi" in client.get("/catalog").json()["cities"]
    assert client.get("/status").json()["model"]["status"] == "waiting"


@pytest.mark.parametrize(
    "body",
    [
        {"city": "Unknown", "date": "2020-01-01"},
        {"city": "Delhi", "date": "2026-01-01"},
        {"city": "Delhi", "date": "not-a-date"},
        {},
    ],
)
def test_invalid_prediction_inputs(client, body):
    assert client.post("/predict", json=body).status_code == 422


def stored_forecasts():
    with store.database() as db:
        return db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]


def test_prediction_before_model_returns_503(client):
    assert client.post("/predict", json={"city": "Delhi", "date": "2020-01-01"}).status_code == 503


def test_prediction_uses_served_identity_and_does_not_reveal_future(client, monkeypatch):
    ready_model(monkeypatch)
    payload = {"city": "Delhi", "date": "2020-01-01"}
    first = client.post("/predict", json=payload).json()
    second = client.post("/predict", json=payload).json()
    assert first["predicted_aqi"] == 85
    assert first["model_version"] == "1"
    assert first["actual_aqi"] is None
    assert first["forecast_date"] == "2020-01-02"
    assert first["id"] == second["id"]
    assert stored_forecasts() == 1


def test_model_identity_mismatch_rejected(client, monkeypatch):
    ready_model(monkeypatch, run_id="unexpected")
    assert client.post("/predict", json={"city": "Delhi", "date": "2020-01-01"}).status_code == 503
    assert stored_forecasts() == 0


def test_feedback_is_delayed_and_idempotent(client, monkeypatch, feature_frame):
    ready_model(monkeypatch)
    client.post("/predict", json={"city": "Delhi", "date": "2020-01-01"})
    assert store.reveal(feature_frame, "2020-01-01") == 0
    assert store.reveal(feature_frame, "2020-01-02") == 1
    assert store.reveal(feature_frame, "2020-01-02") == 0
    assert store.summary()[0]["scored"] == 1
    assert store.summary()[0]["mae"] == 5.0
    text = client.get("/metrics").text
    assert 'aircast_prediction_absolute_error_count{city="Delhi"} 1.0' in text


def test_replay_continues_from_the_next_unplayed_day(client, monkeypatch):
    ready_model(monkeypatch)
    dates = []
    monkeypatch.setattr(operations.store, "reveal", lambda *_: 0)
    monkeypatch.setattr(operations, "predict", lambda city, day: dates.append((city, str(day.date()))))
    monkeypatch.setattr(operations, "update_drift", lambda *_: None)
    monkeypatch.setattr(operations.time, "sleep", lambda *_: None)

    first = operations.replay(2, 0.1)
    second = operations.replay(2, 0.1)

    assert first["date"] == "2020-01-01" and second["date"] == "2020-01-03"
    assert len(dates) == 12
    assert sorted({day for _, day in dates}) == ["2019-12-31", "2020-01-01", "2020-01-02", "2020-01-03"]


def test_replay_reports_when_all_dates_are_complete(client, monkeypatch):
    ready_model(monkeypatch)
    write_json(operations.STATE / "replay.json", {"completed_days": 1, "date": "2020-06-29"})
    response = client.post("/replay", json={"days": 7, "delay": 0.5})
    assert response.status_code == 409
    assert "All historical dates" in response.json()["detail"]


def test_conflicting_pipeline_request_rejected(client):
    operations.JOB_LOCK.acquire()
    try:
        assert client.post("/pipeline/run").status_code == 409
    finally:
        operations.JOB_LOCK.release()


def test_rollback_requires_previous_model(client):
    assert client.post("/models/rollback").status_code == 409
