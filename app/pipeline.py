"""The five DVC stages. Each one records its duration, rows and any error for the UI."""

import json
import logging
import os
import sys
import time

import numpy as np
import pandas as pd

from app.common import (
    ARTIFACTS,
    DATA,
    FEATURES,
    RAW,
    STATE,
    config,
    provenance,
    read_json,
    setup,
    sha256,
    write_json,
)
from app.features import make_features, split_data

LOG = logging.getLogger("aircast.pipeline")
LOGGED_ARTIFACTS = ["ingestion.json", "validation.json", "features.json", "per_city.json"]
LOGGED_ARTIFACTS += ["validation_predictions.csv", "feature_importance.png", "reference.json"]


def ingest(params):
    """Fingerprint each raw CSV (hash, bytes, rows) so every later result is traceable."""
    files = {}
    for name in ["station_hour.csv", "stations.csv", "city_day.csv"]:
        path = RAW / name
        if not path.exists():
            raise ValueError(f"Missing {name}. Run python scripts/download_data.py first.")
        with path.open("rb") as f:
            rows = sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b"")) - 1
        files[name] = {"sha256": sha256(path), "bytes": path.stat().st_size, "rows": rows}
    write_json(ARTIFACTS / "ingestion.json", {"files": files, "source": read_json(RAW / "source.json", {})})
    return {"rows": files["station_hour.csv"]["rows"]}


def validate(params):
    """Fail on broken structure; measure missing and negative readings, which transform masks."""
    pollutants = params["data"]["pollutants"]
    stations = pd.read_csv(RAW / "stations.csv")
    labels = pd.read_csv(RAW / "city_day.csv")
    if not {"StationId", "City"}.issubset(stations) or stations.StationId.duplicated().any():
        raise ValueError("Station metadata needs unique StationId and City columns.")
    if not {"City", "Date", "AQI"}.issubset(labels) or labels.duplicated(["City", "Date"]).any():
        raise ValueError("City labels need unique City/Date keys and an AQI column.")
    rows = bad_dates = negative = unmatched = 0
    missing = dict.fromkeys(pollutants, 0)
    columns = ["StationId", "Datetime", *pollutants]
    for chunk in pd.read_csv(RAW / "station_hour.csv", chunksize=100000, usecols=columns):
        rows += len(chunk)
        unmatched += int((~chunk.StationId.isin(stations.StationId)).sum())
        bad_dates += int(pd.to_datetime(chunk.Datetime, errors="coerce").isna().sum())
        for col in pollutants:
            values = pd.to_numeric(chunk[col], errors="coerce")
            missing[col] += int(values.isna().sum())
            negative += int((values < 0).sum())
    if not rows or bad_dates:
        raise ValueError(f"Invalid hourly input: {rows} rows, {bad_dates} invalid timestamps.")
    for city in params["data"]["cities"]:
        if city not in set(stations.City) or labels.loc[labels.City == city, "AQI"].notna().sum() < 60:
            raise ValueError(f"Insufficient station mapping or labelled history: {city}")
    report = {
        "rows": rows,
        "missing_ratio": {c: missing[c] / rows for c in pollutants},
        "negative_values_to_mask": negative,
        "unmatched_station_rows_excluded": unmatched,
        "missing_target_rows": int(labels.AQI.isna().sum()),
        "policy": "Missing/negative pollutant values become NaN; missing AQI targets are never filled.",
    }
    write_json(ARTIFACTS / "validation.json", report)
    return {"rows": rows}


def transform(params):
    """Spark: station-hour rows to station-day means to city-day means, then the feature table."""
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F  # noqa: N812

    spark = (
        SparkSession.builder.master("local[2]")
        .appName("AirCast daily features")
        .config("spark.sql.session.timeZone", "Asia/Kolkata")
        .config("spark.sql.ansi.enabled", "false")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    try:
        data = params["data"]
        stations = spark.read.option("header", True).csv(str(RAW / "stations.csv"))
        stations = stations.select("StationId", "City").filter(F.col("City").isin(data["cities"]))
        raw = spark.read.option("header", True).csv(str(RAW / "station_hour.csv"))
        # The station table is tiny, so broadcasting it avoids shuffling the hourly rows.
        hourly = raw.join(F.broadcast(stations), "StationId").withColumn("Date", F.to_date("Datetime"))
        for col in data["pollutants"]:
            value = F.col(f"`{col}`").cast("double")
            hourly = hourly.withColumn(col, F.when(value >= 0, value))
        hourly = hourly.dropDuplicates(["StationId", "Datetime"])
        # A station-day mean needs enough valid hours; every station then weighs the same in its city.
        station_daily = hourly.groupBy("StationId", "City", "Date").agg(
            *[
                F.when(F.count(F.col(f"`{c}`")) >= data["min_station_hours"], F.avg(F.col(f"`{c}`"))).alias(c)
                for c in data["pollutants"]
            ]
        )
        daily = (
            station_daily.groupBy("City", "Date")
            .agg(
                *[F.avg(F.col(f"`{c}`")).alias(c) for c in data["pollutants"]],
                F.count("StationId").alias("station_count"),
            )
            .toPandas()
        )
        features = make_features(daily, pd.read_csv(RAW / "city_day.csv"), data["cities"])
        features.to_parquet(DATA / "features.parquet", index=False)
        train, validation = split_data(features, params)
        write_json(
            ARTIFACTS / "features.json",
            {
                "rows": len(features),
                "train_rows": len(train),
                "validation_rows": len(validation),
                "cities": data["cities"],
                "features": FEATURES,
            },
        )
        rows = read_json(ARTIFACTS / "ingestion.json")["files"]["station_hour.csv"]["rows"]
        return {"rows": rows, "output_rows": len(features)}
    finally:
        spark.stop()


def train(params):
    """MLproject entry point: fit, compare with persistence and log everything to MLflow."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import mlflow
    import mlflow.sklearn
    from mlflow.models import ModelSignature
    from mlflow.types import ColSpec, Schema
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import mean_absolute_error, root_mean_squared_error

    from app.model import AQIModel

    frame = pd.read_parquet(DATA / "features.parquet")
    training, validation = split_data(frame, params)
    if len(training) < 30 or len(validation) < params["gate"]["min_validation_rows"]:
        raise ValueError("Not enough chronological training/validation rows.")
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", f"sqlite:///{STATE / 'mlflow.db'}"))
    if not os.getenv("MLFLOW_RUN_ID"):
        mlflow.set_experiment("AirCast")
    mlflow.sklearn.autolog(log_models=False, log_datasets=False)
    settings = params["model"]
    model = HistGradientBoostingRegressor(
        max_iter=settings["max_iter"],
        max_leaf_nodes=settings["max_leaf_nodes"],
        learning_rate=settings["learning_rate"],
        random_state=settings["seed"],
        early_stopping=False,
    )
    with mlflow.start_run() as run:
        run_id = run.info.run_id
        started = time.perf_counter()
        model.fit(training[FEATURES], training.target)
        training_seconds = time.perf_counter() - started
        prediction = np.maximum(0, model.predict(validation[FEATURES]))
        # Persistence baseline: tomorrow's AQI equals today's.
        metrics = {
            "mae": float(mean_absolute_error(validation.target, prediction)),
            "rmse": float(root_mean_squared_error(validation.target, prediction)),
            "baseline_mae": float(mean_absolute_error(validation.target, validation.aqi_today)),
            "training_seconds": training_seconds,
            "validation_rows": len(validation),
            "training_rows": len(training),
        }
        mlflow.log_metrics(metrics)
        mlflow.set_tags(provenance())
        mlflow.log_params(
            {
                "data_sha256": sha256(DATA / "features.parquet"),
                "train_end": params["data"]["train_end"],
                "validation_end": params["data"]["validation_end"],
                "cities": ",".join(params["data"]["cities"]),
            }
        )
        scored = validation[["City", "Date", "forecast_date", "target"]].assign(
            prediction=prediction, absolute_error=abs(validation.target - prediction)
        )
        scored.to_csv(ARTIFACTS / "validation_predictions.csv", index=False)
        per_city = scored.groupby("City").absolute_error.mean().to_dict()
        write_json(ARTIFACTS / "per_city.json", per_city)
        mlflow.log_metrics({f"mae_{city}": error for city, error in per_city.items()})
        importance = permutation_importance(
            model,
            validation[FEATURES],
            validation.target,
            n_repeats=2,
            random_state=42,
            scoring="neg_mean_absolute_error",
        )
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.barh(FEATURES, importance.importances_mean)
        ax.set_xlabel("Increase in validation MAE after shuffling")
        fig.tight_layout()
        fig.savefig(ARTIFACTS / "feature_importance.png")
        plt.close(fig)
        # Inputs may be missing (the model handles NaN); the output carries run_id for serving checks.
        signature = ModelSignature(
            inputs=Schema([ColSpec("double", c, required=False) for c in FEATURES]),
            outputs=Schema([ColSpec("double", "aqi"), ColSpec("string", "run_id")]),
        )
        info = mlflow.pyfunc.log_model(
            name="aqi",
            python_model=AQIModel(model, run_id),
            signature=signature,
            input_example=training[FEATURES].head(2),
            code_paths=["app"],
            pip_requirements=[
                "mlflow==3.15.1",
                "scikit-learn==1.9.0",
                "pandas==2.3.3",
                "numpy==2.5.2",
                "PyYAML==6.0.3",
            ],
        )
        # Training mean and std of AQI per city and month: the reference for the drift score.
        reference = {
            f"{city}:{int(month)}": {
                "mean": float(g.aqi_today.mean()),
                "std": max(float(g.aqi_today.std(ddof=0)), 1),
            }
            for (city, month), g in training.groupby(["City", "month"])
        }
        write_json(ARTIFACTS / "reference.json", reference)
        write_json(
            ARTIFACTS / "candidate.json",
            {
                "run_id": run_id,
                "experiment_id": run.info.experiment_id,
                "model_uri": info.model_uri,
                "metrics": metrics,
                "prediction_min": float(min(prediction)),
                "prediction_max": float(max(prediction)),
                "all_finite": bool(np.isfinite(prediction).all()),
                "data_sha256": sha256(DATA / "features.parquet"),
                "data_config": params["data"],
                **provenance(),
            },
        )
        for name in LOGGED_ARTIFACTS:
            mlflow.log_artifact(str(ARTIFACTS / name))
        mlflow.log_artifact("params.yaml")
    return {"rows": len(training), "run_id": run_id, "experiment_id": run.info.experiment_id, **metrics}


def check_gate(candidate, gate):
    return (
        candidate["all_finite"]
        and candidate["metrics"]["validation_rows"] >= gate["min_validation_rows"]
        and candidate["metrics"]["mae"] <= gate["max_mae"]
        and candidate["prediction_min"] >= gate["min_prediction"]
        and candidate["prediction_max"] <= gate["max_prediction"]
    )


def evaluate(params):
    """Record the gate decision; failing raises, so DVC stops and nothing is deployed."""
    candidate = read_json(ARTIFACTS / "candidate.json")
    passed = check_gate(candidate, params["gate"])
    evaluation = {
        "passed": passed,
        "gate": params["gate"],
        "run_id": candidate["run_id"],
        "metrics": candidate["metrics"],
    }
    write_json(ARTIFACTS / "evaluation.json", evaluation)
    if not passed:
        raise ValueError("Candidate failed the configured acceptance gate; current model is unchanged.")
    return {"validation_rows": candidate["metrics"]["validation_rows"], "run_id": candidate["run_id"]}


def run_stage(name):
    setup()
    started = time.time()
    record = {"name": name, "status": "running", "started_at": started}
    path = STATE / "stages" / f"{name}.json"
    write_json(path, record)
    try:
        values = globals()[name](config())
        duration = time.time() - started
        record.update(values, status="success", duration_seconds=duration)
        if "rows" in values:
            record["rows_per_second"] = values["rows"] / max(duration, 0.001)
    except Exception as exc:
        record.update(status="failed", duration_seconds=time.time() - started, error=str(exc))
        LOG.exception("Stage %s failed", name)
        raise
    finally:
        record["ended_at"] = time.time()
        write_json(path, record)
    LOG.info("Stage completed: %s", json.dumps(record))


if __name__ == "__main__":
    run_stage(sys.argv[1])
