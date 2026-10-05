# Redwood Retail CDC service: Eventarc -> BigQuery Storage Write API.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so image layers cache across source-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY firestore_event.py schemas.py bq_cdc_writer.py main.py ./

EXPOSE 8080

# One worker with several threads: the work is IO-bound on the BigQuery append
# stream, and a single worker lets the warm AppendRowsStream be shared rather
# than reopened per process. --timeout 0 defers request timeouts to Cloud Run.
CMD exec gunicorn \
    --bind :${PORT:-8080} \
    --workers 1 \
    --threads 8 \
    --timeout 0 \
    --access-logfile - \
    main:app
