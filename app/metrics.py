"""Prometheus metrics: live request metrics plus a collector that reads saved pipeline and forecast state."""

from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, SummaryMetricFamily

from app import store
from app.common import ARTIFACTS, STAGES, STATE, read_json

REQUESTS = Counter("aircast_http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram("aircast_http_request_duration_seconds", "HTTP duration", ["method", "path"])
SERVICES = Gauge("aircast_service_up", "Service health probe result", ["service"])
PROBE_SECONDS = Gauge("aircast_service_probe_seconds", "Health probe response time", ["service"])


def gauge(name, help, labels, values):
    family = GaugeMetricFamily(name, help, labels=labels)
    for label, value in values:
        family.add_metric(label, value)
    return family


class SavedMetrics:
    """Stages run as short subprocesses, so the API reports their saved records at scrape time."""

    def collect(self):
        stages = [(name, read_json(STATE / "stages" / f"{name}.json", {})) for name in STAGES]
        yield gauge(
            "aircast_pipeline_stage_seconds",
            "Last stage duration",
            ["stage"],
            [([name], s["duration_seconds"]) for name, s in stages if "duration_seconds" in s],
        )
        yield gauge(
            "aircast_pipeline_rows_per_second",
            "Last stage input throughput",
            ["stage"],
            [([name], s["rows_per_second"]) for name, s in stages if "rows_per_second" in s],
        )
        success = read_json(STATE / "last_success.json", {})
        yield gauge(
            "aircast_pipeline_last_success_timestamp_seconds",
            "Last successful deployment",
            [],
            [([], success["timestamp"])] if success else [],
        )
        missing = read_json(ARTIFACTS / "validation.json", {}).get("missing_ratio", {})
        yield gauge(
            "aircast_data_missing_ratio",
            "Fraction missing in raw input",
            ["pollutant"],
            [([k], v) for k, v in missing.items()],
        )
        total = CounterMetricFamily("aircast_prediction", "Persisted predictions", labels=["city"])
        errors = SummaryMetricFamily("aircast_prediction_absolute_error", "Revealed errors", labels=["city"])
        for row in store.summary():
            total.add_metric([row["city"]], row["predictions"])
            errors.add_metric([row["city"]], row["scored"], row["error_sum"] or 0)
        yield total
        yield errors
        drift = read_json(STATE / "drift.json", {}).get("scores", {})
        yield gauge(
            "aircast_drift_score", "Standardised mean shift", ["city"], [([k], v) for k, v in drift.items()]
        )
        served = read_json(STATE / "served.json", {})
        ready = [([], float(served["version"]))] if served.get("status") == "ready" else []
        yield gauge("aircast_model_version", "Verified served registry version", [], ready)


REGISTRY.register(SavedMetrics())
