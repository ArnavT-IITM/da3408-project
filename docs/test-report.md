# Test report

I ran these tests on 15 November 2026 on the local Compose stack, with the code in this commit. The MLflow runs record the Git commit at that time (`c474cf6`, before I combined the history into one commit), `git_dirty=true`, and the changed files in the `git_dirty_files` tag.

Data: `station_hour.csv` has 2,589,083 rows, SHA-256 `26a423c6...` (also in `data/raw.dvc`). The feature table hash starts with `8d454c1cbbfc9435`.

## Summary

| Check | Result | Evidence |
|---|---|---|
| Lint: `ruff check` and `ruff format --check` | Pass, 20 files | CI log, `make test` |
| Unit tests: `make test` | **54 passed, 0 failed, 0 skipped** | [HTML](../evidence/test-results/tests.html), [XML](../evidence/test-results/tests.xml) |
| Live checks: `scripts/verify_runtime.py` | **7 of 7 passed** in 57.7 s | [`runtime.json`](../evidence/runtime.json) |
| Browser test: `scripts/browser_e2e.py` | **30 of 30 passed**, no JavaScript errors | [`ui/browser-e2e.json`](../evidence/ui/browser-e2e.json), screenshots |
| Monitoring | 7 of 7 services healthy, 4 of 4 targets up, 5 alert rules, 18 panels | [`stack-final.json`](../evidence/stack-final.json) |
| Alert | The node exporter alert went from inactive to pending, to firing and back to inactive. | [`alert-transition.json`](../evidence/alert-transition.json) |

The unit tests use synthetic data. The live checks and the browser test use the real data, the real MLflow server and the real model. They use no mocks.

## Live checks

| Check | Result |
|---|---|
| Real prediction | Delhi, D = 2020-02-01: 253.2 for 2020-02-02, from version 6 (run `571f60dc...`). |
| Bad input | `422`. |
| Delayed feedback | A 5-day replay (2020-01-04 to 2020-01-08) showed each observed AQI only after its date. |
| Rejected model | With the gate at MAE 0, evaluate failed with `ValueError: Candidate failed the configured acceptance gate`. Version 6 stayed in service. `params.yaml` was restored exactly. |
| Retrain | Forced job `62a3719fdba1` (40.2 s) trained run `01acd7bc...`, registered version 7 and verified it with a prediction. |
| Rollback | Job `ee8f8965e9bf` (8.0 s) put version 6 back in service. A prediction confirmed it. |
| Metrics | `/metrics` gave `200` with the forecast error metrics. |

## Browser test

Headless Chrome used the real frontend at `https://localhost:8443`. All 30 checks passed:

- **Forecast:** **Start over** set the date to 31 Dec 2019. Each city had a forecast card with a category, a health note and a comparison with today. **Next day** moved the forecast by one day.
- **Accuracy:** a 7-day move gave per-city scores, a chart and a list of checked days. **Jump to** went to 20 Jan and then back to 4 Jan. The Forecast page followed the date.
- **Operations:** the graph agreed with `dvc dag`. Seven services showed. The rejected run `284c9f1cb4a8` showed `ValueError: Candidate failed the configured acceptance gate; current model is unchanged.` first. Its details stayed open after the refresh.
- **Layout:** no page was wider than a 375 px screen. Help opened and closed.

## Pipeline and model

Forced job `62a3719fdba1`, with every stage run again. Laptop with 16 logical cores. Spark ran in `local[2]` mode.

| Stage | Time | Rows | Rows per second |
|---|---|---|---|
| ingest | 0.3 s | 2,589,083 | 8.5 million |
| validate | 2.1 s | 2,589,083 | 1.2 million |
| transform (Spark) | 12.1 s | 2,589,083 in, 6,027 out | 214,780 |
| train | 3.6 s (model fit 0.21 s) | 4,122 | 1,130 |
| evaluate | 0.0 s | none | none |
| deploy | 8.1 s | none | none |

The full job took 40.2 s. See [`pipeline-stages.json`](../evidence/pipeline-stages.json) and [`transform-stage.json`](../evidence/transform-stage.json).

Run `01acd7bc7baf4046b46879fe52b02e07`: 4,122 training rows and 1,095 validation rows (2019).

| Metric | Value |
|---|---|
| Model MAE | **19.14** |
| Model RMSE | 30.02 |
| Persistence MAE | **23.12** |
| MAE for Delhi, Chennai, Bengaluru | 30.10, 18.53, 8.78 |

The gate passed. See [`final-candidate.json`](../evidence/final-candidate.json) and [`final-evaluation.json`](../evidence/final-evaluation.json). The model gave the same numbers before and after I simplified the code.

## Result

All 25 cases in the [test plan](test-plan.md) met their criteria.

## Limits

- The replay uses recorded 2020 data, not live data.
- I did not do a load test, a keyboard-only accessibility test or a crash of the model server during a request. A unit test (R02) covers a failed model start.
- At the monitoring snapshot, the drift alert was active for a real reason. In Chennai, the mean AQI of the last 30 days (about 78) was 1.18 standard deviations below the January training mean (141).
