# AirCast

AirCast forecasts the next-day AQI for Delhi, Chennai and Bengaluru. It is a local AIOps project. It has a Spark and DVC pipeline, MLflow tracking and serving, and a FastAPI backend. It also has a web frontend and Prometheus and Grafana monitoring.

The data ends in June 2020. The application replays 2020 one day at a time and does not show current air quality.

## Start

You need Docker with Compose, Git and Python 3. Run these commands in this directory:

```bash
python scripts/download_data.py
make up
make pipeline
```

`make up` makes a local TLS certificate and starts seven services. `make pipeline` runs CI, trains the model and deploys it. Forecasts are not available until the first deployment is complete.

| Service | Address |
|---|---|
| Application | https://localhost:8443 |
| API documentation | http://localhost:18000/docs |
| MLflow | http://localhost:15000 |
| Grafana | http://localhost:13000/d/aircast/aircast-operations |
| Prometheus | http://localhost:19090 |

The browser shows a one-time warning for the self-signed certificate. All ports are on localhost only. To stop the services, run `make down`. Data, models and monitoring history stay on disk.

## Demonstration

1. Open **Forecast**. It shows the forecast for tomorrow for each city.
2. Click **Next day**. The forecasts for the day that passed get a score.
3. Open **Accuracy**. It shows the error for each city and a forecast against actual chart.
4. Open **Operations**. It shows the DVC pipeline graph, the stage times, the runs and the CI log.
5. Click **Run pipeline**. CI runs lint, tests and `dvc repro`. A model that passes the gate is deployed.
6. Click **Switch to version N** to roll back to the previous model.

For a full retrain, run:

```bash
curl -X POST -H 'Content-Type: application/json' -d '{"force":true}' http://localhost:18000/pipeline/run
```

## Tests

```bash
make test                          # lint and 54 unit tests
python scripts/verify_runtime.py   # live gate rejection, retrain and rollback
python scripts/browser_e2e.py      # 30 browser checks over HTTPS
```

The last two need the running stack and a deployed model. `browser_e2e.py` needs Google Chrome and `websocket-client`. It clicks **Start over**. The results are in the [test report](docs/test-report.md).

## Documents

- [Explanation video](https://drive.google.com/drive/folders/1-irID-xtFiVjhVtbRs4Mdb0zRvmq0ZZ1?usp=sharing)
- [Project report](report.pdf)
- [Architecture and high-level design](docs/architecture.md)
- [Design decisions](docs/decisions.md)
- [Low-level design and API](docs/lld.md)
- [Test plan](docs/test-plan.md) and [test report](docs/test-report.md)
- [User manual](docs/user-manual.md)
- [Rubric checklist](docs/rubric-checklist.md)
- [Lecture sources](lecture_examples/README.md)
- [AI disclosure](docs/AI_DISCLOSURE.md)

Data: [Air Quality Data in India (2015 to 2020)](https://www.kaggle.com/datasets/rohanrao/air-quality-data-in-india) by Rohan Rao, from CPCB measurements, licence CC0.
