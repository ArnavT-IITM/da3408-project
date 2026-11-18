# Lecture sources

These files are exact extracts from the DA3408 course material. They show which lecture code AirCast uses and how it changes it. They are not part of the application. `manifest.json` gives the SHA-256 of each source file. Notebook cells are counted from zero.

| Extract | Source | Change in AirCast |
|---|---|---|
| [DVC reproduction](dvc_reproduction.txt) | Lecture 11, PDF page 12 | `dvc.yaml` declares the scripts, data and parameters, so `dvc repro` runs only the changed stages. Deployment is outside DVC, so a cached run still checks the model in service. |
| [Spark aggregation and join](spark_aggregate_broadcast.txt) | Lecture 7 Spark ETL exercise, cells 8 and 19 | Station-hour rows are aggregated to station-day and city-day means. A broadcast join adds the city. The stage records its real time and throughput. |
| [Prometheus metric types](prometheus_metrics.txt) | `Monitoring/prom_client.py`, lines 1 to 34 | Counters, histograms and gauges measure real requests and real pipeline state, not a simulated delay. |
| [FastAPI schema and timing](fastapi_schema_metrics.txt) | `rest_app.py`, lines 20 to 45 | Typed request validation and request timing. The API calls the MLflow server and does not load a model. |
| [MLproject environment](mlproject_environment.txt) | MLflow Exercise 2, cells 6, 8 and 11 | A `train` entry point with pinned packages. |
| [Alias, restart, rollback](mlflow_alias_reload.txt) | Lecture 10 MLflow serving exercise, cells 20 to 22 | The alias moves only after the server restarts and a real prediction returns the expected run. |

I also used Lecture 9 (Prometheus monitoring), Lecture 10 (REST API serving and MLflow serving) and MLflow Exercise 4 (model registry and aliases). The extracts keep their original names, ports and comments. Their numbers are not AirCast results.
