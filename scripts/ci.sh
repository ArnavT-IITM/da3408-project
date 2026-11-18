#!/bin/sh
# The same local command is called by the UI, Git hook and scheduled retraining.
set -eu
mkdir -p artifacts state
dvc dag --mermaid > artifacts/dag.mmd
ruff check app tests scripts
ruff format --check app tests scripts
python -m pytest -q --junitxml=artifacts/tests.xml --html=artifacts/tests.html --self-contained-html
if [ "${AIRCAST_FORCE:-0}" = 1 ]; then
    dvc repro --force
else
    dvc repro
fi
