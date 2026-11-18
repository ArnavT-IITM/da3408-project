"""REST API used by the frontend. Inference itself happens in the separate MLflow model server.

The request and response models below are the interface in docs/lld.md; `python -m app.api`
exports them to docs/openapi.json.
"""

import asyncio
import datetime
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from app import metrics, operations, store
from app.common import ARTIFACTS, ROOT, STAGES, STATE, read_json, setup, write_json
from app.deploy import rollback

LOG = logging.getLogger("aircast.api")
HEALTH = {}
PROBES = {
    "frontend": "http://frontend/healthz",
    "model-server": "http://model-server:5001/health",
    "mlflow": "http://mlflow:5000/health",
    "prometheus": "http://prometheus:9090/-/healthy",
    "grafana": "http://grafana:3000/api/health",
    "node-exporter": "http://node-exporter:9100/metrics",
}


async def monitor():
    """Probe every service each 5 s; this covers the ones without a metrics endpoint."""
    targets = json.loads(os.getenv("AIRCAST_PROBES", json.dumps(PROBES)))
    async with httpx.AsyncClient(timeout=2) as client:

        async def probe(name, url):
            started = time.perf_counter()
            try:
                HEALTH[name] = (await client.get(url)).status_code == 200
            except httpx.HTTPError:
                HEALTH[name] = False
            metrics.SERVICES.labels(name).set(HEALTH[name])
            metrics.PROBE_SECONDS.labels(name).set(time.perf_counter() - started)

        while True:
            await asyncio.gather(*(probe(name, url) for name, url in targets.items()))
            HEALTH["api"] = True
            metrics.SERVICES.labels("api").set(1)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app):
    """On start, mark jobs a previous process left running as failed, then start the probes."""
    setup()
    for path in (STATE / "jobs").glob("*.json"):
        if read_json(path)["status"] == "running":
            write_json(
                path, {**read_json(path), "status": "failed", "error": "Application restarted during job."}
            )
    replay = read_json(STATE / "replay.json", {})
    if replay.get("running"):
        write_json(
            STATE / "replay.json", {**replay, "running": False, "status": "failed", "error": "Interrupted."}
        )
    task = asyncio.create_task(monitor())
    yield
    task.cancel()


app = FastAPI(title="AirCast API", version="1.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("AIRCAST_ALLOWED_ORIGINS", "https://localhost:8443,http://localhost:8088").split(
        ","
    ),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def instrument(request: Request, call_next):
    """Count, time and log every request; an unexpected error becomes a readable 500."""
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        LOG.exception("Unhandled request failure")
        response = JSONResponse(status_code=500, content={"detail": "Unexpected server error. See the log."})
    route = request.scope.get("route")
    path = route.path if route else "unmatched"
    if path != "/metrics":
        elapsed = time.perf_counter() - started
        metrics.REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        metrics.LATENCY.labels(request.method, path).observe(elapsed)
        LOG.info(
            "method=%s path=%s status=%s seconds=%.4f", request.method, path, response.status_code, elapsed
        )
    return response


class ForecastRequest(BaseModel):
    city: str = Field(min_length=1, max_length=60, examples=["Delhi"])
    date: datetime.date = Field(description="Observation cutoff D; the forecast is for D + 1.")


class ReplayRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=182, description="Historical days to advance.")
    delay: float = Field(default=0.5, ge=0.1, le=10, description="Seconds per simulated day.")


class PipelineRequest(BaseModel):
    force: bool = Field(default=False, description="Rerun every DVC stage instead of using the cache.")


class ErrorResponse(BaseModel):
    detail: str


class DateRange(BaseModel):
    min: str
    max: str


class CatalogResponse(BaseModel):
    cities: list[str]
    dates: DateRange


class ForecastResponse(BaseModel):
    id: int
    city: str
    as_of: str = Field(description="Observation date D.")
    forecast_date: str = Field(description="D + 1.")
    predicted_aqi: float
    model_version: str
    model_run_id: str = Field(description="MLflow run that trained the model which answered.")
    actual_aqi: Optional[float] = Field(description="Observed AQI, null until replay reaches it.")
    error: Optional[float] = Field(description="Absolute error once observed.")
    created_at: float
    category: str
    guidance: str
    today_aqi: float = Field(description="Observed AQI on D, for comparison.")


class StageRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    status: str
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    duration_seconds: Optional[float] = None
    rows: Optional[int] = None
    rows_per_second: Optional[float] = None
    error: Optional[str] = None


class JobRecord(BaseModel):
    id: str
    kind: str
    status: str
    started_at: float
    ended_at: Optional[float] = None
    error: Optional[str] = None
    result: Optional[Any] = None


class PipelineResponse(BaseModel):
    running: bool
    stages: list[StageRecord]
    runs: list[JobRecord]
    dag: str = Field(description="Mermaid text from `dvc dag --mermaid`.")
    log_tail: str


class JobAccepted(BaseModel):
    run_id: str


class ModelState(BaseModel):
    version: Optional[str]
    status: str
    previous_version: Optional[str]


class ReplayState(BaseModel):
    model_config = ConfigDict(extra="allow")
    running: bool
    status: str


class StatusResponse(BaseModel):
    services: dict[str, bool]
    model: ModelState
    links: dict[str, str]
    replay: ReplayState


class CityAccuracy(BaseModel):
    city: str
    scored: int = Field(description="Forecast dates with an observed AQI, each counted once.")
    mae: Optional[float] = Field(description="Mean absolute error in AQI points.")


class AccuracyPoint(BaseModel):
    forecast_date: str
    predicted_aqi: float
    actual_aqi: float
    error: float
    model_version: str


class AccuracyResponse(BaseModel):
    simulated_today: str = Field(description="Next day the replay will forecast from.")
    last_day: str = Field(description="Last cutoff date the data supports.")
    by_city: list[CityAccuracy]
    points: list[AccuracyPoint] = Field(description="Scored forecasts for `city`, oldest first.")


class ResetResponse(BaseModel):
    simulated_today: str
    unscored: int = Field(description="Forecasts whose observed AQI was cleared.")


def errors(*codes):
    return {code: {"model": ErrorResponse} for code in codes}


def last_day(period):
    return str(datetime.date.fromisoformat(period["replay_end"]) - datetime.timedelta(days=1))


def launch(kind, function, *args):
    try:
        return {"run_id": operations.start_job(kind, function, *args)}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/catalog", response_model=CatalogResponse)
def catalog():
    period = operations.data_config()
    return {"cities": period["cities"], "dates": {"min": period["validation_end"], "max": last_day(period)}}


@app.post("/predict", response_model=ForecastResponse, responses=errors(422, 503))
def predict(request: ForecastRequest):
    try:
        return operations.predict(request.city, str(request.date))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (RuntimeError, httpx.HTTPError, KeyError) as exc:
        LOG.warning("Prediction unavailable: %s", exc)
        raise HTTPException(503, "Model service unavailable or changing. Check the Operations page.") from exc


@app.get("/accuracy", response_model=AccuracyResponse, responses=errors(422))
def accuracy(city: str, days: int = Query(default=60, ge=1, le=365)):
    """Error per city so far, and the scored forecast-vs-actual series for one city."""
    period = operations.data_config()
    if city not in period["cities"]:
        raise HTTPException(422, "Select one of the supported cities.")
    scored = store.city_accuracy()
    return {
        "simulated_today": str(operations.next_replay_date(period).date()),
        "last_day": last_day(period),
        "by_city": [
            {"city": name, "scored": 0, "mae": None, **scored.get(name, {})} for name in period["cities"]
        ],
        "points": store.scored_series(city, days),
    }


@app.post("/replay", status_code=202, response_model=JobAccepted, responses=errors(409))
def replay(request: ReplayRequest):
    period = operations.data_config()
    if operations.served().get("status") != "ready":
        raise HTTPException(409, "Train and deploy a model before starting replay.")
    if str(operations.next_replay_date(period).date()) > last_day(period):
        raise HTTPException(409, "All historical dates have been replayed. Press Start over.")
    return launch("replay", operations.replay, request.days, request.delay)


@app.post("/replay/reset", response_model=ResetResponse, responses=errors(409))
def replay_reset():
    try:
        return operations.reset_replay()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/pipeline", response_model=PipelineResponse)
def pipeline():
    """Stage records, recent jobs, the real DVC DAG and the end of the CI log."""
    jobs = operations.jobs()
    # Replays are listed separately so a burst of Next day clicks cannot push pipeline runs out.
    runs = [job for job in jobs if job["kind"] != "replay"][:20] + [
        job for job in jobs if job["kind"] == "replay"
    ][:20]
    dag, log = ARTIFACTS / "dag.mmd", STATE / "pipeline.log"
    return {
        "running": operations.JOB_LOCK.locked(),
        "stages": [
            read_json(STATE / "stages" / f"{n}.json", {"name": n, "status": "pending"})
            for n in [*STAGES, "deploy"]
        ],
        "runs": sorted(runs, key=lambda job: job["started_at"], reverse=True),
        "dag": dag.read_text() if dag.exists() else "",
        "log_tail": log.read_text()[-4000:] if log.exists() else "",
    }


@app.post("/pipeline/run", status_code=202, response_model=JobAccepted, responses=errors(409))
def pipeline_run(request: Optional[PipelineRequest] = None):
    """Run CI (lint, tests, dvc repro) and deploy the candidate if it passes the gate."""
    return launch("pipeline", operations.run_pipeline, bool(request and request.force))


@app.post("/models/rollback", status_code=202, response_model=JobAccepted, responses=errors(409))
def rollback_model():
    if not read_json(STATE / "deployment.json", {}).get("previous"):
        raise HTTPException(409, "No previous approved model is available.")
    return launch("rollback", rollback)


@app.get("/status", response_model=StatusResponse)
def status():
    served, mlflow = operations.served(), os.getenv("MLFLOW_PUBLIC_URL", "http://localhost:15000")
    links = {
        "mlflow": mlflow,
        "grafana": os.getenv("GRAFANA_PUBLIC_URL", "http://localhost:13000/d/aircast/aircast-operations"),
        "prometheus": "http://localhost:19090",
        "api_docs": "http://localhost:18000/docs",
    }
    if served.get("experiment_id"):
        links["mlflow_run"] = f"{mlflow}/#/experiments/{served['experiment_id']}/runs/{served['run_id']}"
    return {
        "services": HEALTH,
        "model": {
            "version": served.get("version"),
            "status": served.get("status", "waiting"),
            "previous_version": read_json(STATE / "deployment.json", {}).get("previous", {}).get("version"),
        },
        "links": links,
        "replay": read_json(STATE / "replay.json", {"running": False, "status": "idle"}),
    }


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    write_json(ROOT / "docs" / "openapi.json", app.openapi())
