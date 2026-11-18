import pandas as pd
import pytest

from app import pipeline
from app.common import config


def test_malformed_timestamp_stops_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RAW", tmp_path)
    pd.DataFrame({"StationId": ["S1"], "City": ["Delhi"]}).to_csv(tmp_path / "stations.csv", index=False)
    pd.DataFrame({"City": ["Delhi"], "Date": ["2020-01-01"], "AQI": [50]}).to_csv(
        tmp_path / "city_day.csv", index=False
    )
    pd.DataFrame(
        {"StationId": ["S1"], "Datetime": ["broken"], "PM2.5": [1], "PM10": [1], "NO2": [1], "CO": [1]}
    ).to_csv(tmp_path / "station_hour.csv", index=False)
    with pytest.raises(ValueError, match="invalid timestamps"):
        pipeline.validate(config())


def test_duplicate_station_mapping_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RAW", tmp_path)
    pd.DataFrame({"StationId": ["S1", "S1"], "City": ["Delhi", "Chennai"]}).to_csv(
        tmp_path / "stations.csv", index=False
    )
    pd.DataFrame({"City": ["Delhi"], "Date": ["2020-01-01"], "AQI": [50]}).to_csv(
        tmp_path / "city_day.csv", index=False
    )
    with pytest.raises(ValueError, match="unique StationId"):
        pipeline.validate(config())


def test_missing_required_column_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RAW", tmp_path)
    pd.DataFrame({"StationId": ["S1"]}).to_csv(tmp_path / "stations.csv", index=False)
    pd.DataFrame({"City": ["Delhi"], "Date": ["2020-01-01"], "AQI": [50]}).to_csv(
        tmp_path / "city_day.csv", index=False
    )
    with pytest.raises(ValueError, match="City"):
        pipeline.validate(config())
