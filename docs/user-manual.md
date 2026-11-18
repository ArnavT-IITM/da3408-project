# User manual

## What AirCast does

AirCast forecasts the AQI for tomorrow in Delhi, Chennai and Bengaluru. The data is real, but it ends in June 2020. Thus AirCast replays 2020 one day at a time. You can see each forecast and then compare it with the real AQI.

AQI is an index of air quality. A higher number means worse air. Each forecast shows a category, from Good to Severe, in words and in colour.

## Open AirCast

1. Go to **https://localhost:8443** in your browser.
2. If the browser shows a certificate warning, accept it. This is a local certificate.

The top bar shows **Connected** when AirCast is ready. To read a short guide, click **Help**.

## Move the date

The bar at the top shows **Today in AirCast**. This is the simulated date. The Forecast page and the Accuracy page use this date.

| Button | Result |
|---|---|
| **Next day** | Moves one day forward. The forecasts for the day that passed get a score. |
| **+1 week**, **+1 month** | Moves 7 or 30 days forward. Each day gets a score. |
| **Jump to**, **Go** | Moves to the date that you select. To go back, AirCast replays from the start to that date. |
| **Start over** | Goes back to 31 Dec 2019. All scores go back to zero. |

The buttons are not available while a move is in progress.

## Forecast page

The Forecast page shows one card for each city. Each card shows:

- The forecast AQI for tomorrow and its category.
- A health note for that category.
- The difference from the AQI today.

The key below the cards gives the AQI range of each category. If a city has no data for the day, its card tells you. Click **Next day**.

## Accuracy page

The Accuracy page shows the quality of the forecasts after their day passed.

- Each city card shows the mean error in AQI points and the number of forecasts. To show the chart for a city, click its card.
- The chart shows the forecast and the real AQI for each day. To see the values, put the pointer on a day.
- **Latest checked days** shows the most recent forecasts, the real AQI and the error.

## Operations page

The Operations page is for the person who operates AirCast.

- **Serving model** shows the model version in service and the previous version. **Training run** opens the MLflow run of the model.
- **Services** shows the health of the seven services.
- **Tools** opens Grafana, MLflow, Prometheus and the API documentation.
- **Pipeline** shows the pipeline graph from `dvc dag`. Green is success, grey is cached (not changed), blue is running and red is failed. The table shows the time, the rows and the rows per second of each stage.
- **Pipeline runs and model switches** shows the latest runs. A red run failed. To see the full output, click the red line.
- **CI log** shows the output of the last run.

To run the pipeline:

1. Click **Run pipeline**.
2. Wait until the label shows **Last run: success**.

A new model goes into service only if lint, tests and the quality gate pass. If not, the previous model stays.

To roll back to the previous model:

1. Click **Switch to version N**.
2. Click **OK**.
3. Wait until **Current** shows the new version. Forecasts are not available for a few seconds.

## Troubleshooting

| Problem | Action |
|---|---|
| The top bar shows **Offline** | Make sure that the services are running (operator). |
| No model is ready | Run the pipeline. Look for a red run on the Operations page. |
| A city card shows no forecast | That city has no data for the day. Click **Next day**. |
| A button is not available | Wait until the current job is complete. |
| A Grafana panel is empty | Move the date forward, then wait 10 seconds. |
| **Switch to version N** is not available | There is no previous version, or a job is running. |

## Operator commands

Run these commands in the `aircast` directory:

| Command | Result |
|---|---|
| `python scripts/download_data.py` | Downloads the three CSV files. |
| `make up` | Builds and starts the seven services. |
| `make pipeline` | Starts CI, training and deployment. |
| `make test` | Runs lint and the unit tests. |
| `make replay` | Replays 30 days. |
| `make logs` | Shows the service logs. |
| `make down` | Stops the services. Data stays on disk. |

| Address | Content |
|---|---|
| https://localhost:8443 | AirCast |
| http://localhost:18000/docs | API documentation |
| http://localhost:15000 | MLflow |
| http://localhost:19090 | Prometheus |
| http://localhost:13000/d/aircast/aircast-operations | Grafana |
