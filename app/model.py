"""MLflow pyfunc wrapper, adapted from Lecture 10's versioned serving wrapper."""

import mlflow.pyfunc
import numpy as np
import pandas as pd

from app.common import FEATURES


class AQIModel(mlflow.pyfunc.PythonModel):
    """Every prediction also reports the MLflow run that trained the model."""

    def __init__(self, estimator, run_id):
        self.estimator = estimator
        self.run_id = run_id

    def predict(self, context, model_input, params=None):
        features = pd.DataFrame(model_input)[FEATURES].astype(float)
        return pd.DataFrame({"aqi": np.maximum(0, self.estimator.predict(features)), "run_id": self.run_id})
