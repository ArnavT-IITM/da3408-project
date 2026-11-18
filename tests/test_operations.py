"""Pipeline reporting and API contract checks added for the UI's pipeline console."""

import pytest
from fastapi.testclient import TestClient

from app import api, operations, pipeline, store
from app.common import read_json, write_json

CI_FAILURE = """
.........................................  [100%]
ERROR: failed to reproduce 'evaluate': failed to run: python -m app.pipeline evaluate, exited with 1
Traceback (most recent call last):
ValueError: Candidate failed the configured acceptance gate; current model is unchanged.
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
"""


@pytest.mark.parametrize(
    "output, expected",
    [
        (CI_FAILURE, "ValueError: Candidate failed the configured acceptance gate"),
        ("tests/test_api.py F\nFAILED tests/test_api.py::test_x - assert 1 == 2\n1 failed", "1 failed"),
        ("app/api.py:3:1: F401 unused\nFound 1 error.", "Found 1 error."),
        ("", "No output was produced."),
    ],
)
def test_failure_summary_prefers_the_cause(output, expected):
    assert operations.failure_summary(output).startswith(expected)


def test_evaluate_stage_reports_no_fake_throughput(monkeypatch):
    candidate = {
        "run_id": "abc",
        "all_finite": True,
        "prediction_min": 10.0,
        "prediction_max": 300.0,
        "metrics": {"validation_rows": 100, "mae": 20.0},
    }
    write_json(pipeline.ARTIFACTS / "candidate.json", candidate)
    gate = {"min_validation_rows": 30, "max_mae": 150.0, "min_prediction": 0.0, "max_prediction": 1000.0}
    monkeypatch.setattr(pipeline, "config", lambda: {"gate": gate})
    pipeline.run_stage("evaluate")
    record = read_json(pipeline.STATE / "stages" / "evaluate.json")
    assert record["status"] == "success"
    assert "rows_per_second" not in record
    assert read_json(pipeline.ARTIFACTS / "evaluation.json")["passed"] is True


def test_pipeline_and_status_follow_the_documented_contract():
    (api.ARTIFACTS / "dag.mmd").write_text('flowchart TD\n\tnode1["ingest"]\n')
    with TestClient(api.app) as client:
        body = client.get("/pipeline").json()
        status = client.get("/status").json()
    assert set(body) == {"running", "stages", "runs", "dag", "log_tail"}
    assert [stage["name"] for stage in body["stages"]][-1] == "deploy"
    assert body["dag"].startswith("flowchart")
    assert set(status) == {"services", "model", "links", "replay"}
    assert status["model"]["status"] == "waiting"


def test_openapi_documents_response_schemas():
    schema = api.app.openapi()
    predict = schema["paths"]["/predict"]["post"]["responses"]
    assert predict["200"]["content"]["application/json"]["schema"]["$ref"].endswith("ForecastResponse")
    assert "503" in predict and "422" in predict


def test_forecast_includes_today_and_reset_allows_scoring_again(client, monkeypatch, feature_frame):
    from tests.test_api import ready_model

    ready_model(monkeypatch)
    forecast = client.post("/predict", json={"city": "Delhi", "date": "2020-01-01"}).json()
    assert forecast["today_aqi"] == 50.0
    assert store.reveal(feature_frame, "2020-01-02") == 1
    accuracy = client.get("/accuracy", params={"city": "Delhi"}).json()
    assert accuracy["by_city"][0] == {"city": "Delhi", "scored": 1, "mae": 5.0}
    assert accuracy["points"][0]["actual_aqi"] == 90.0

    reset = client.post("/replay/reset").json()
    assert reset == {"simulated_today": "2019-12-31", "unscored": 1}
    assert client.get("/accuracy", params={"city": "Delhi"}).json()["points"] == []
    assert store.reveal(feature_frame, "2020-01-02") == 1


def test_clock_does_not_jump_back_while_a_replay_runs(client, monkeypatch):
    write_json(operations.STATE / "replay.json", {"completed_days": 3, "date": "2020-01-03"})
    seen = []

    def predict(city, date):
        seen.append(client.get("/accuracy?city=Delhi").json()["simulated_today"])

    monkeypatch.setattr(operations, "predict", predict)
    operations.replay(1, 0)
    assert set(seen) == {"2020-01-04"}
    assert client.get("/accuracy?city=Delhi").json()["simulated_today"] == "2020-01-05"


def test_replays_do_not_push_pipeline_runs_out_of_the_list(client):
    jobs = operations.STATE / "jobs"
    write_json(jobs / "old.json", {"id": "old", "kind": "pipeline", "status": "failed", "started_at": 1.0})
    for index in range(25):
        job = {"id": f"r{index}", "kind": "replay", "status": "success", "started_at": 10.0 + index}
        write_json(jobs / f"r{index}.json", job)
    runs = client.get("/pipeline").json()["runs"]
    assert [run["kind"] for run in runs].count("replay") == 20
    assert runs[-1]["id"] == "old"


def test_reset_refused_while_a_job_runs(client):
    operations.JOB_LOCK.acquire()
    try:
        assert client.post("/replay/reset").status_code == 409
    finally:
        operations.JOB_LOCK.release()


def test_accuracy_rejects_unknown_city(client):
    assert client.get("/accuracy", params={"city": "Atlantis"}).status_code == 422


def test_accuracy_counts_each_forecast_date_once(client, monkeypatch, feature_frame):
    from tests.test_api import ready_model

    for run_id in ["run-one", "run-two"]:
        ready_model(monkeypatch, run_id)
        write_json(operations.STATE / "served.json", {"status": "ready", "version": "1", "run_id": run_id})
        client.post("/predict", json={"city": "Delhi", "date": "2020-01-01"})
    assert store.reveal(feature_frame, "2020-01-02") == 2
    delhi = client.get("/accuracy", params={"city": "Delhi"}).json()
    assert delhi["by_city"][0]["scored"] == 1
    assert len(delhi["points"]) == 1
