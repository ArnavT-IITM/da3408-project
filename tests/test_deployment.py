import pytest

from app import deploy
from app.common import read_json, write_json


def test_failed_candidate_features_do_not_change_active_inputs(tmp_path, monkeypatch, feature_frame):
    from app import operations

    monkeypatch.setattr(operations, "ROOT", tmp_path)
    monkeypatch.setattr(operations, "DATA", tmp_path / "data")
    (tmp_path / "data").mkdir()
    feature_frame.to_parquet(tmp_path / "approved.parquet")
    changed = feature_frame.copy()
    changed["city_code"] = 999
    changed.to_parquet(tmp_path / "data/features.parquet")
    active = {"features_path": "approved.parquet"}
    assert operations.features(active).city_code.iloc[0] == 50


def test_alias_changes_only_after_serving_verification(monkeypatch):
    events = []

    class Client:
        def set_registered_model_alias(self, *args):
            events.append("alias")

    monkeypatch.setattr(deploy, "MlflowClient", Client)
    monkeypatch.setattr(deploy, "wait_served", lambda target: events.append("verified"))
    deploy.activate({"version": "1", "run_id": "one", "model_uri": "models:/aircast-aqi/1"})
    assert events == ["verified", "alias"]


def test_failed_deployment_restores_previous_intent(monkeypatch):
    previous = {"version": "1", "run_id": "one", "model_uri": "models:/aircast-aqi/1"}
    write_json(deploy.STATE / "deployment.json", previous)
    monkeypatch.setattr(deploy, "MlflowClient", lambda: None)
    checked = []

    def wait(target):
        checked.append(target["version"])
        if target["version"] == "2":
            raise RuntimeError("smoke test failed")

    monkeypatch.setattr(deploy, "wait_served", wait)
    with pytest.raises(RuntimeError, match="smoke"):
        deploy.activate({"version": "2", "run_id": "two", "model_uri": "models:/aircast-aqi/2"})
    assert read_json(deploy.STATE / "deployment.json") == previous
    assert checked == ["2", "1"]
