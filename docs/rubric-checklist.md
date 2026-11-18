# Rubric checklist

Each question of the evaluation guideline, and where to find the answer. The [test report](test-report.md) gives the results.

## Demonstration

| Question | Answer |
|---|---|
| Intuitive UX | One simulated date for all pages. Forecast shows tomorrow for each city. **Next day** moves time. Accuracy shows the result. |
| Easy for a non-technical user | No input fields on the Forecast page. AQI key, **Help** dialog and [user manual](user-manual.md). |
| Foolproof | Dates change only with buttons or a limited date picker. Buttons stop during a job. Going back and rollback ask for confirmation. The API gives clear `422`, `409` and `503` messages. |
| No UI errors | 30 of 30 browser checks, no JavaScript errors ([evidence](../evidence/ui/browser-e2e.json)). |
| Look and feel | Cards in AQI category colours, light and dark themes ([screenshots](../evidence/ui/)). |
| Responsive | No page is wider than a 375 px screen ([phone screenshot](../evidence/ui/forecast-mobile.png)). |
| User manual | [user-manual.md](user-manual.md) |
| Pipeline screen with data engineering | Operations page: the `dvc dag` graph (raw data, ingest, validate, Spark transform, train, evaluate) and the deploy step, in status colours. |
| Tool UIs together | Links from Operations to the MLflow run of each stage, run and model, and to Grafana, Prometheus and the API documentation. |
| Pipeline console | **Run pipeline**, run list, one-job lock, CI log, rollback. |
| Errors, failures and successes | Run list with status. A failed run shows its cause first, for example the gate rejection. |
| Speed and throughput | Stage table and Grafana. Spark: 2.59 million rows in 12.1 s (214,780 rows per second). |

## Software engineering

| Question | Answer |
|---|---|
| Design document | [architecture.md](architecture.md), [decisions.md](decisions.md) |
| OO or functional | Functional pipeline and feature code. Classes for the API contract (Pydantic) and the model (`AQIModel`). |
| LLD with endpoints and I/O | [lld.md](lld.md) and [openapi.json](openapi.json) (20 schemas) |
| Architecture diagram | [architecture.md](architecture.md), with a table for each block |
| HLD diagram | Data and model flow in [architecture.md](architecture.md) |
| Loose coupling, configurable REST | Separate frontend and API containers. The browser calls only REST at `AIRCAST_API_URL`. The API calls the model only over HTTP. |
| Python style | Ruff (pycodestyle, pyflakes, isort, pep8-naming, bugbear) and `ruff format --check` in CI. |
| Logging | One line for each request, stage logs, deployment and model server logs, full trace for errors. |
| Exception handling | `422`, `409`, `503` and `500` with a log trace. A failed deployment brings back the previous model. Interrupted jobs show as failed. |
| Design followed | The contract tests (A04) check the API against the LLD. |
| Inline documentation | Module and function docstrings. Comments on date alignment, leakage, deployment checks and metrics. |
| API follows the spec | Response classes enforce it. `tests/test_operations.py` checks it. |
| Unit tests | 54 tests in `tests/` |
| Test plan and cases | [test-plan.md](test-plan.md): 25 cases with criteria |
| Test report with counts | [test-report.md](test-report.md): 54 passed, 0 failed |
| Acceptance criteria defined and met | [test-plan.md](test-plan.md) and [test-report.md](test-report.md) |

## AIOps

| Question | Answer |
|---|---|
| Spark data engineering | `transform` stage: local PySpark, broadcast join, masks bad values, station-day then city-day means. Validation report: [data-validation.json](../evidence/data-validation.json). |
| Throughput | Spark 12.1 s, 214,780 rows per second. Full forced pipeline 40.2 s ([evidence](../evidence/transform-stage.json)). |
| CI with DVC | `scripts/ci.sh`: `dvc dag`, lint, tests, `dvc repro`. Deployment only if all pass. Start from the UI, `make pipeline` or a Git hook (`scripts/install-hook.sh`). |
| DVC DAG | `dvc.yaml`, `dvc.lock`, [dag.mmd](../evidence/dag.mmd), shown on the Operations page |
| Git and DVC versioning | Git for code. DVC for raw data (`data/raw.dvc`) and stage outputs, with a local remote. MLflow registry for models. |
| Experiment tracking | MLflow experiment `AirCast`. The `train` MLproject entry point logs each run. |
| Metrics, parameters, artifacts | MAE, RMSE, baseline MAE, per-city MAE, rows, time. Hyperparameters (autolog), split dates, cities, data hash. Model with signature, feature importance plot, predictions, data reports, `params.yaml`. |
| More than autolog | Git commit and changed files, data SHA-256, persistence baseline, per-city error, permutation importance, drift reference, run ID in each prediction. |
| Prometheus | Counters, histograms and gauges in the API, and a collector for saved pipeline and forecast data. Four scrape targets. |
| Monitored information | Request rate, latency, errors, service health, stage time and throughput, missing data, error and drift per city, model version, host CPU and memory. |
| All components monitored | Scraped: API, Prometheus, Grafana, node exporter. Probed every 5 s: frontend, MLflow, model server. A failure starts `AircastServiceDown`. |
| Grafana in near real time | 18 panels, 2 s scrape. Each **Next day** adds points. A tested alert ([snapshot](../evidence/stack-final.json)). |
| MLflow APIfication | `mlflow models serve` in its own container. The API calls `/invocations`. |
| MLproject for environment parity | `MLproject` with `python_env.yaml`, run in the same pinned image as the tests and the server. |
| FastAPI | Each UI action is a FastAPI route with typed schemas. |
| Docker and Compose | `Dockerfile`, `infra/Dockerfile.frontend`, `compose.yaml` with seven services. |

## Mandatory documents

| Document | File |
|---|---|
| Architecture diagram with explanation | [architecture.md](architecture.md) |
| HLD with choices and reasons | [architecture.md](architecture.md), [decisions.md](decisions.md) |
| LLD with endpoints and I/O | [lld.md](lld.md) |
| Test plan and test cases | [test-plan.md](test-plan.md) |
| User manual | [user-manual.md](user-manual.md) |
