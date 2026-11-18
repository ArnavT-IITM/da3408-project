"""Daily features: the inputs of calendar day D predict the AQI of day D+1."""

import numpy as np
import pandas as pd

from app.common import FEATURES

CATEGORIES = [
    (50, "Good", "Minimal impact."),
    (100, "Satisfactory", "Minor breathing discomfort may occur in sensitive people."),
    (200, "Moderate", "Breathing discomfort may occur in people with lung or heart disease."),
    (300, "Poor", "Breathing discomfort may occur with prolonged exposure."),
    (400, "Very Poor", "Respiratory illness may occur with prolonged exposure."),
    (float("inf"), "Severe", "May affect healthy people and seriously affect those with existing disease."),
]


def make_features(daily, labels, cities):
    """One row per city and calendar day, on a full date grid so shift(1) means one day, not one row."""
    parts = []
    for code, city in enumerate(cities):
        target = labels.loc[labels.City == city, ["Date", "AQI"]].copy()
        target["Date"] = pd.to_datetime(target.Date)
        if target.empty or target.Date.duplicated().any():
            raise ValueError(f"Duplicate or missing city-day labels: {city}")
        frame = pd.DataFrame({"Date": pd.date_range(target.Date.min(), target.Date.max())})
        frame = frame.merge(target, on="Date", how="left")
        observations = daily.loc[daily.City == city].assign(Date=lambda d: pd.to_datetime(d.Date))
        frame = frame.merge(observations.drop(columns="City"), on="Date", how="left")
        frame["City"], frame["city_code"] = city, float(code)
        frame["month"], frame["weekday"] = frame.Date.dt.month, frame.Date.dt.dayofweek
        frame["aqi_today"] = frame.AQI
        frame["aqi_lag1"] = frame.AQI.shift(1)
        frame["aqi_lag7"] = frame.AQI.shift(7)
        frame["aqi_mean7"] = frame.AQI.rolling(7, min_periods=1).mean()
        frame["target"] = frame.AQI.shift(-1)
        frame["forecast_date"] = frame.Date + pd.Timedelta(days=1)
        parts.append(frame)
    result = pd.concat(parts, ignore_index=True)
    result[FEATURES] = result[FEATURES].astype(float)
    return result.replace([np.inf, -np.inf], np.nan)


def split_data(frame, params):
    """Split by target date; rows without a target or today's AQI are dropped for model and baseline."""
    valid = frame.target.notna() & frame.aqi_today.notna()
    train_end = pd.Timestamp(params["data"]["train_end"])
    validation_end = pd.Timestamp(params["data"]["validation_end"])
    train = frame.loc[valid & (frame.forecast_date <= train_end)]
    validation = frame.loc[
        valid & (frame.forecast_date > train_end) & (frame.forecast_date <= validation_end)
    ]
    return train, validation


def category(value):
    """CPCB category and health note, chosen from the same rounded value the UI shows."""
    return next((name, note) for upper, name, note in CATEGORIES if round(value) <= upper)
