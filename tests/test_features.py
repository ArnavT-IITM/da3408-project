import numpy as np
import pandas as pd
import pytest

from app.common import FEATURES, config
from app.features import category, make_features, split_data
from app.pipeline import check_gate


def fixture_data():
    labels = pd.DataFrame(
        {
            "City": ["Delhi"] * 3,
            "Date": ["2019-12-30", "2019-12-31", "2020-01-02"],
            "AQI": [40.0, 60.0, 100.0],
        }
    )
    daily = labels.drop(columns="AQI").assign(
        **{"PM2.5": 10.0, "PM10": 20.0, "NO2": 3.0, "CO": 1.0, "station_count": 2}
    )
    return daily, labels


def test_missing_calendar_day_is_not_next_available_label():
    daily, labels = fixture_data()
    frame = make_features(daily, labels, ["Delhi"])
    december31 = frame.loc[frame.Date == "2019-12-31"].iloc[0]
    assert np.isnan(december31.target)
    assert december31.forecast_date == pd.Timestamp("2020-01-01")


def test_future_observations_do_not_change_past_inputs():
    daily, labels = fixture_data()
    before = make_features(daily, labels, ["Delhi"])
    labels.loc[labels.Date == "2020-01-02", "AQI"] = 999.0
    after = make_features(daily, labels, ["Delhi"])
    pd.testing.assert_frame_equal(
        before.loc[before.Date < "2020-01-02", FEATURES], after.loc[after.Date < "2020-01-02", FEATURES]
    )


def test_split_uses_target_date_and_excludes_missing_targets():
    frame = pd.DataFrame(
        {
            "forecast_date": pd.to_datetime(["2018-12-31", "2019-01-01", "2019-12-31", "2020-01-01"]),
            "target": [50.0, 80.0, np.nan, 100.0],
            "aqi_today": [40.0] * 4,
        }
    )
    train, validation = split_data(frame, config())
    assert list(train.index) == [0]
    assert list(validation.index) == [1]


def test_duplicate_labels_rejected():
    daily, labels = fixture_data()
    with pytest.raises(ValueError, match="Duplicate"):
        make_features(daily, pd.concat([labels, labels.head(1)]), ["Delhi"])


def test_missing_pollutants_remain_missing():
    daily, labels = fixture_data()
    daily["PM2.5"] = np.nan
    frame = make_features(daily, labels, ["Delhi"])
    assert frame["PM2.5"].isna().all()


def test_city_histories_are_separate():
    daily, labels = fixture_data()
    other_labels = labels.assign(City="Chennai", AQI=500.0)
    other_daily = daily.assign(City="Chennai")
    frame = make_features(
        pd.concat([daily, other_daily]), pd.concat([labels, other_labels]), ["Delhi", "Chennai"]
    )
    assert frame.loc[(frame.City == "Delhi") & (frame.Date == "2019-12-31"), "aqi_lag1"].iloc[0] == 40.0


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "Good"),
        (50, "Good"),
        (51, "Satisfactory"),
        (100, "Satisfactory"),
        (101, "Moderate"),
        (200, "Moderate"),
        (201, "Poor"),
        (300, "Poor"),
        (301, "Very Poor"),
        (400, "Very Poor"),
        (401, "Severe"),
    ],
)
def test_category_boundaries(value, expected):
    assert category(value)[0] == expected


@pytest.mark.parametrize(
    "change",
    [
        {"all_finite": False},
        {"prediction_min": -1},
        {"prediction_max": 1001},
        {"metrics": {"mae": 151, "validation_rows": 100}},
        {"metrics": {"mae": 20, "validation_rows": 2}},
    ],
)
def test_bad_candidates_fail_gate(change):
    candidate = {
        "all_finite": True,
        "prediction_min": 0,
        "prediction_max": 350,
        "metrics": {"mae": 30, "validation_rows": 100},
    }
    gate = {
        "min_validation_rows": 30,
        "max_mae": 150.0,
        "min_prediction": 0.0,
        "max_prediction": 1000.0,
    }
    assert check_gate(candidate, gate)
    assert not check_gate({**candidate, **change}, gate)
