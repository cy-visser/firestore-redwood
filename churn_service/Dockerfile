# Redwood Retail churn function: HTTP -> BigQuery ML pipeline.
#
# Built from the repository root so the single copy of
# bigquery_churn_sentiment_analysis.sql can be copied in. Terraform's daily
# scheduled query renders that same file, so there is one SQL source with two
# consumers rather than two files that drift.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so image layers cache across source-only changes.
COPY churn_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY churn_service/pipeline.py churn_service/main.py ./
COPY bigquery_churn_sentiment_analysis.sql ./

EXPOSE 8080

# gunicorn is invoked directly rather than through the `functions-framework`
# CLI so the timeout is stated here instead of inherited. --timeout 0 defers
# the request deadline to Cloud Run's own 900s: BQML training is minutes long,
# and gunicorn killing the worker mid-MERGE would leave the console with a
# truncated log and no [exit N] line to tell it what happened.
#
# One worker, because a run is a serial sequence of BigQuery jobs and Cloud Run
# is configured to send one request at a time. Threads cover the health probe
# arriving while a run is in flight.
CMD exec gunicorn \
    --bind :${PORT:-8080} \
    --workers 1 \
    --threads 8 \
    --timeout 0 \
    --access-logfile - \
    "functions_framework:create_app('recalculate', 'main.py', 'http')"
