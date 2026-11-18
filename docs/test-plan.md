# Test plan

## Scope

The tests check the data and features, the API contract, the pipeline, deployment and rollback, monitoring and the user interface. Unit tests use small synthetic data. All accuracy and throughput results come from runs on the real data. The [test report](test-report.md) gives the results.

## Acceptance criteria

- All unit tests pass, with 0 failures and 0 skips.
- `ruff check` and `ruff format --check` pass.
- Each case below meets its criterion.
- The model passes the gate in `params.yaml`: at least 30 validation rows, finite predictions from 0 to 1,000, and MAE of 150 or less. The report also gives the persistence baseline MAE on the same rows.

## Test cases

| ID | Level | Input or action | Acceptance criterion | Evidence |
|---|---|---|---|---|
| D01 | Unit | Missing column, bad timestamp or duplicate station | Validation stops with a specific error. | `tests/test_validation.py` |
| D02 | Unit and data | Missing pollutants, unmatched stations, missing labels | Missing values stay missing. The validation report counts them. | `tests/test_features.py`, `artifacts/validation.json` |
| D03 | Unit | A missing day, a changed future value, two cities | The target is exactly D + 1. Future values and other cities do not change the features. The split uses the target date. | `tests/test_features.py` |
| M01 | Integration | Train on the real data | MLflow has MAE, RMSE, per-city MAE and the persistence MAE on the same rows. | MLflow run, `evidence/final-candidate.json` |
| M02 | Unit and live | Stricter gate | The gate fails. The model in service and its inputs do not change. | `tests/test_features.py`, `tests/test_deployment.py`, `evidence/runtime.json` |
| M03 | Integration | Train through MLproject and DVC | MLflow has the parameters, the data hash, the Git commit, the signature and the artifacts. `dvc dag` gives the graph. | MLflow run, `dvc.lock`, `evidence/dag.mmd` |
| A01 | Unit and live | Good request; bad city, date or body | A good request gives a finite AQI for D + 1. A bad one gives `422`. | `tests/test_api.py`, `evidence/runtime.json` |
| A02 | Unit | Model not ready, or wrong model run ID | The API gives `503` and stores no forecast. | `tests/test_api.py` |
| A03 | Unit | The same forecast twice, then reveal twice | One forecast and one outcome are stored. The outcome stays hidden until its date. | `tests/test_api.py` |
| A04 | Unit | OpenAPI schema; `/pipeline` and `/status` | The schema and the responses agree with the LLD. | `tests/test_operations.py` |
| A05 | Unit | Accuracy, reset, two model versions, a job running, a running replay, 25 replays | Each date counts once. Reset clears scores and keeps forecasts. Reset during a job gives `409`. The simulated date never goes back. Pipeline runs stay in the run list. | `tests/test_operations.py` |
| P01 | Unit | Start a job, then start a second job | The first gets `202` and a run ID. The second gets `409`. | `tests/test_api.py` |
| P02 | Live | Cached run, stricter gate, forced retrain | DVC skips unchanged stages. A failed gate does not deploy. A forced run trains and deploys a new version. | `evidence/runtime.json`, CI log |
| P03 | Unit | CI output with an exception, a failed test, a lint error, no output | The error summary is the cause, not the last log line. Evaluate gives no rows per second. | `tests/test_operations.py` |
| R01 | Unit and live | Deploy, verify, roll back | The alias moves only after verification. A real prediction shows the new version, then the previous version. | `tests/test_deployment.py`, `evidence/runtime.json` |
| R02 | Unit | The new model fails its check | The previous deployment comes back and is verified again. | `tests/test_deployment.py` |
| O01 | Integration | Spark on the real CSVs | The stage records input rows, output rows, time and rows per second. | `evidence/transform-stage.json` |
| O02 | Live | Replay real days | Real labels give real errors. Drift uses the training reference. | `evidence/runtime.json`, `evidence/stack-final.json` |
| O03 | Live | Monitoring stack | All Prometheus targets are up. The dashboard and the 5 alert rules load. An alert starts and stops. | `evidence/stack-final.json`, `evidence/alert-transition.json` |
| U01 | Browser | **Start over**, then Forecast | The date is 31 Dec 2019. Each city has a forecast with a category, a health note and a comparison with today. | `evidence/ui/browser-e2e.json` |
| U02 | Browser | **Next day**, then **+1 week** | The forecast moves by one day. The Accuracy page shows scores, a chart and the checked days. | `evidence/ui/browser-e2e.json` |
| U03 | Browser | **Jump to** a later date, then an earlier date | Each jump stops on the exact date. The Forecast page follows the date. | `evidence/ui/browser-e2e.json` |
| U04 | Browser | Operations page | The graph agrees with `dvc dag`. Seven services show. A failed run shows its cause first, and it stays open after the refresh. | `evidence/ui/` |
| U05 | Browser | Each page at 375 px; Help | No page is wider than the screen. Help opens and closes. There are no JavaScript errors. | `evidence/ui/` |
| S01 | Live | Start the Compose stack | Seven services are healthy. Ports are on localhost only. The browser connection uses TLS. | `evidence/stack-final.json` |
