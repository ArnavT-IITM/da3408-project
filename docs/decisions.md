# Design decisions

Each row gives a decision, the reason for it and its cost. The numbers come from the [test report](test-report.md).

| Decision | Reason | Cost or limit |
|---|---|---|
| Next-day city AQI as the target | The dataset supplies this AQI, so the target is real and not calculated by me. | The data ends in 2020, so there is no live forecast. |
| Hourly station data as input | 2.59 million rows give Spark real work to do: a join and two levels of aggregation. | The join and the date alignment must be correct. Tests check them. |
| Local Spark | It meets the data engineering requirement without a cluster or a cloud service. | Spark start-up time is large for a small dataset. |
| Persistence baseline | AQI changes slowly, so "same as today" is a strong baseline. A model must beat it to be useful. | The model beats it by 17% (MAE 19.1 against 23.1). This is a modest margin. |
| `HistGradientBoostingRegressor` | It works well on tabular data and accepts missing values. | I did not tune it much. The focus is AIOps, not accuracy. |
| Split by target date | This is how forecasting works in practice, and it prevents leakage of future data. | City coverage can change between periods. The per-city errors show this. |
| Wide sanity gate (MAE 150 or less) | It blocks broken models. I set it before the final evaluation. | It does not require the model to beat persistence. A test makes the gate stricter to show a rejection. |
| Separate MLflow model server | The API calls a real MLflow serving endpoint, which meets the "MLflow APIfication" requirement. | There is one more process and one more HTTP call. |
| Verify a deployment with a real prediction | A healthy server does not prove which model it loaded. The returned run ID does. | A reload makes forecasts unavailable for a few seconds. |
| Features and drift reference copied with each model | A rejected candidate cannot change the inputs of the model in service. | Each version uses a few MB of disk. |
| Deployment outside DVC | A fully cached DVC run must still check the model in service. | CI and deployment are two steps in one job. |
| MLproject run in the Docker image | Tests, training and serving use one image with pinned packages, so the environments are the same. | `requirements.txt` and `python_env.yaml` must stay the same. |
| Local DVC remote | `dvc push` and `dvc pull` work without a cloud service. | The remote is on the same disk, so it is not a backup. |
| Typed FastAPI responses | The LLD, `/docs` and `openapi.json` come from the same classes as the code, so they always agree. | About 100 lines of model classes. |
| Health probes from the API | nginx and MLflow have no metrics endpoint. The probes put every service in Grafana and in an alert. | A probe gives up or down and a response time, not internal counters. |
| One simulated date for the whole UI | A free date picker and a separate replay clock confused users and caused extra forecasts. Now one date controls all pages. | To see an earlier day, the replay must start again from the beginning. |
| **Start over** keeps the forecasts | The replay can score the same real forecasts again. Nothing is invented. | Grafana shows the error counters drop to zero. |
| One background job at a time | This prevents conflicting writes without a queue system. | A second request gets `409`. This is acceptable for one operator. |
| Local deployment only | The guidelines do not permit cloud platforms. | The certificate is self-signed, so the browser shows a warning once. |
