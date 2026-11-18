# Low-level design and API

The browser calls the API at `window.AIRCAST_API_URL`. The frontend container sets this value from the `AIRCAST_API_URL` variable at start-up. The default is `/api`, which nginx sends to the API service. The API calls the model server at `MODEL_SERVER_URL`. It never imports the model.

Each request and response is a Pydantic class in `app/api.py`. `python -m app.api` exports the same classes to [`openapi.json`](openapi.json) (20 schemas). The API serves interactive documentation at `/docs`. `tests/test_operations.py` checks the schema and the live responses.

## Endpoints

| Method and path | Request | Success | Errors |
|---|---|---|---|
| `GET /healthz` | none | `200 {"status": "ok"}` | none |
| `GET /catalog` | none | `200 CatalogResponse` | none |
| `POST /predict` | `ForecastRequest` | `200 ForecastResponse` | `422` bad city, date or body; `503` model not ready |
| `GET /accuracy?city=&days=` | `city` (required); `days` 1 to 365, default 60 | `200 AccuracyResponse` | `422` unknown city or bad `days` |
| `POST /replay` | `ReplayRequest` | `202 JobAccepted` | `409` no model, job running or replay complete; `422` bad body |
| `POST /replay/reset` | none | `200 ResetResponse` | `409` job running |
| `GET /pipeline` | none | `200 PipelineResponse` | none |
| `POST /pipeline/run` | optional `PipelineRequest` | `202 JobAccepted` | `409` job running |
| `POST /models/rollback` | none | `202 JobAccepted` | `409` no previous version or job running |
| `GET /status` | none | `200 StatusResponse` | none |
| `GET /metrics` | none | `200` Prometheus text | none (not in the schema) |

All errors use the body `{"detail": "<message>"}`. A `422` from request validation gives a list of field errors in `detail`. An unexpected error gives `500` and a general message, and the log gets the full trace. CORS accepts browser requests only from the origins in `AIRCAST_ALLOWED_ORIGINS`. The default is `https://localhost:8443` and `http://localhost:8088`.

## Request schemas

| Class | Field | Type and limits | Meaning |
|---|---|---|---|
| `ForecastRequest` | `city` | string, 1 to 60 characters | One of the cities of the model in service. |
| | `date` | ISO date | Day D. The forecast is for D + 1. D must be from 2019-12-31 to 2020-06-29. |
| `ReplayRequest` | `days` | integer, 1 to 182, default 7 | Days to replay, from the next day not yet replayed. |
| | `delay` | number, 0.1 to 10, default 0.5 | Seconds of wait for each simulated day. |
| `PipelineRequest` | `force` | boolean, default `false` | If `true`, `dvc repro --force` runs every stage again. |

## Response schemas

**`ForecastResponse`**

| Field | Type | Meaning |
|---|---|---|
| `id` | integer | Forecast ID. The same request to the same model gives the same ID. |
| `city`, `as_of`, `forecast_date` | string | City, day D and day D + 1. |
| `predicted_aqi` | number | Model output, 0 or more. |
| `model_version`, `model_run_id` | string | Registry version and MLflow run of the model that answered. |
| `actual_aqi`, `error` | number or null | Observed AQI and absolute error. They are null until the replay reaches `forecast_date`. |
| `created_at` | number | Unix time. |
| `category`, `guidance` | string | CPCB category and health note. |
| `today_aqi` | number | Observed AQI on day D. |

Example from the running stack:

```json
{"id": 589, "city": "Delhi", "as_of": "2020-02-01", "forecast_date": "2020-02-02",
 "predicted_aqi": 253.18, "model_version": "6", "model_run_id": "571f60dc86c0474d9fc72d16d7927f7d",
 "actual_aqi": null, "error": null, "created_at": 1794700723.93,
 "category": "Poor", "guidance": "Breathing discomfort may occur with prolonged exposure.", "today_aqi": 244.0}
```

**Other responses**

| Class | Fields |
|---|---|
| `CatalogResponse` | `cities`, `dates: {min, max}` (first and last valid day D) |
| `AccuracyResponse` | `simulated_today`, `last_day`, `by_city: [{city, scored, mae}]`, `points: [{forecast_date, predicted_aqi, actual_aqi, error, model_version}]`, oldest first. Each city and date counts once. If two versions forecast the same date, the later forecast counts. |
| `ResetResponse` | `simulated_today`, `unscored` (number of cleared outcomes) |
| `PipelineResponse` | `running`, `stages` (five DVC stages, then `deploy`), `runs` (latest 20 pipeline and rollback jobs plus latest 20 replays), `dag` (text from `dvc dag --mermaid`), `log_tail` (last 4,000 characters of CI output) |
| `StageRecord` | `name`, `status` (`pending`, `running`, `success`, `failed`, `cached`), `started_at`, `ended_at`, `duration_seconds`, `rows`, `rows_per_second`, `error`. Train and deploy also give `run_id` and `experiment_id`. |
| `JobRecord` | `id`, `kind` (`pipeline`, `replay`, `rollback`), `status`, `started_at`, `ended_at`, `error`, `result` |
| `JobAccepted` | `run_id`, the ID of the job in `runs` |
| `StatusResponse` | `services: {name: bool}`, `model: {version, status, previous_version}`, `links` (MLflow, Grafana, Prometheus, API documentation, MLflow run of the model in service), `replay: {running, status, completed_days, total_days, date}` |

If a job fails, the first line of `error` is the most useful line of the CI output. This is an exception, a failed test count or a lint error count. The end of the CI output follows. Example: `ValueError: Candidate failed the configured acceptance gate; current model is unchanged.`

## Jobs

Pipeline, replay and rollback run in a background thread. The request returns `202` immediately. One lock permits one job at a time. Each job record is in `state/jobs/<id>.json`. When the API starts, it marks jobs that were still running as failed.

The pipeline job runs `scripts/ci.sh`: `dvc dag`, `ruff check`, `ruff format --check`, pytest and `dvc repro`. If all steps pass, `promote()` deploys the candidate. The `deploy` stage record shows the result. `scripts/install-hook.sh` adds an optional Git post-commit hook that starts this job.

`POST /replay/reset` clears all observed AQI values, errors, replay progress and drift scores. It keeps the forecasts, and the replay scores them again.

## Model contract

The API sends one row in the `FEATURES` order to `POST /invocations` as `{"dataframe_records": [...]}`. A missing value is JSON `null`. The MLflow signature:

- Inputs: `city_code, month, weekday, aqi_today, aqi_lag1, aqi_lag7, aqi_mean7, PM2.5, PM10, NO2, CO, station_count`, all optional doubles.
- Outputs: `aqi` (double) and `run_id` (string).

If the returned `run_id` is not the run in `state/served.json`, the API returns `503`. The request time limit is 15 s.

`app/deploy.py` writes the version to `state/deployment.json`: `model_uri`, `run_id`, `experiment_id`, `features_path`, `reference_path` and `data_config`. `app/serve.py` then restarts `mlflow models serve`, sends a real prediction and sets `state/served.json` to `ready` only if the run ID is correct. After that, `deploy.py` moves the `champion` alias. If the check fails, the previous deployment comes back.

## Modules

| Module | Function |
|---|---|
| `app/common.py` | Paths, feature list, parameters, hashes, JSON files, Git commit data |
| `app/features.py` | Date grid, lag features, time split, AQI categories |
| `app/pipeline.py` | The five DVC stages |
| `app/model.py` | MLflow model class that returns `aqi` and `run_id` |
| `app/deploy.py`, `app/serve.py` | Registration, verified deployment, rollback, model server supervisor |
| `app/api.py`, `app/operations.py` | REST API, health probes, forecasts, replay, drift, jobs |
| `app/store.py`, `app/metrics.py` | SQLite forecast store, Prometheus metrics |

JSON state files are written to a temporary file and then renamed, so a reader never sees half a file. The forecast table has the key `UNIQUE(city, as_of, model_run_id)`. Thus a repeated request or replay does not add a second forecast.

## Metrics

| Metric | Source |
|---|---|
| `aircast_http_requests_total{method,path,status}`, `aircast_http_request_duration_seconds{method,path}` | API middleware. `path` is the route template. |
| `aircast_service_up{service}`, `aircast_service_probe_seconds{service}` | Probe of each service every 5 s |
| `aircast_pipeline_stage_seconds{stage}`, `aircast_pipeline_rows_per_second{stage}`, `aircast_pipeline_last_success_timestamp_seconds` | Saved stage records |
| `aircast_data_missing_ratio{pollutant}` | Validation report |
| `aircast_prediction_total{city}`, `aircast_prediction_absolute_error{city}` (`_sum`, `_count`) | SQLite forecast store |
| `aircast_drift_score{city}` | Drift: \|30-day mean AQI - training mean for the month\| / training standard deviation (1 or more). It needs 5 days of data. |
| `aircast_model_version` | Version in service, only after verification |

The alert rules in `infra/alerts.yml` start if:

- The API is down for 15 s.
- A service probe fails for 30 s.
- The drift of a city is above 1.0.
- More than half of the readings of a pollutant are missing.
- No pipeline succeeds for 7 days.
