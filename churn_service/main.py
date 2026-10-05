"""Cloud Run function: POST here to run the Redwood Retail churn pipeline.

Replaces the ``run_bigquery_analysis.py`` subprocess the Redwood Console used
to spawn on the presenter's workstation. The console still shows the pipeline
narrating itself line by line in its Event Log; the lines now arrive over HTTP
instead of over a pipe.

Streamed rather than returned in one piece. The pipeline takes over a minute,
and a button that produces nothing for that long is indistinguishable from a
broken one -- with the log on screen the audience watches the model retrain
instead of a spinner.

Because the response streams, the HTTP status is committed before the first
BigQuery statement runs. Failure is therefore reported in-band as the final
``[exit 1]`` line, exactly as the subprocess reported it via its return code.
Only failures that happen *before* streaming starts -- an unparseable body, an
unknown mode -- get a 4xx.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import functions_framework
from flask import Response, jsonify

import pipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("redwood-churn")

# Baked into the image by the Dockerfile. There is one copy of this SQL in the
# repository and Terraform's daily scheduled query renders the same file, so
# the scheduled run and the on-demand run cannot drift apart.
SQL_PATH = Path(os.getenv("CHURN_SQL_PATH", "/app/bigquery_churn_sentiment_analysis.sql"))


@functions_framework.http
def recalculate(request):
    """Run the churn pipeline and stream its log back as text/plain."""
    # Cloud Run's startup probe hits this. Kept on the same function because a
    # Cloud Run function has exactly one entry point.
    if request.path.rstrip("/").endswith("healthz"):
        return jsonify(status="ok", sql=SQL_PATH.name, sqlPresent=SQL_PATH.exists())

    body = request.get_json(silent=True) or {}
    mode = body.get("mode", pipeline.FULL)
    if mode not in pipeline.MODES:
        return jsonify(
            error=f"unknown mode {mode!r}; expected one of {', '.join(pipeline.MODES)}"
        ), 400

    if not SQL_PATH.exists():
        return jsonify(error=f"SQL template not found at {SQL_PATH}"), 500

    project = os.getenv("GCP_PROJECT_ID")
    if not project:
        return jsonify(error="GCP_PROJECT_ID is not set on the service"), 500

    report = bool(body.get("report", True))
    verbose = bool(body.get("verbose", False))
    logger.info("Churn run requested: mode=%s report=%s verbose=%s", mode, report, verbose)

    # Imported here rather than at module scope so the health check answers
    # without building a BigQuery client.
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    sql_text = SQL_PATH.read_text()
    context = pipeline.build_context(os.environ)

    def chunks():
        exit_line = "[exit 1]"
        try:
            for line in pipeline.run(
                client, sql_text, context, mode=mode, report=report, verbose=verbose
            ):
                if line.startswith("[exit "):
                    exit_line = line
                yield line + "\n"
        except Exception as exc:  # noqa: BLE001 - the caller is mid-stream
            logger.exception("Churn run failed outside the pipeline's own handling")
            yield f"[ERROR] {exc}\n"
            yield "[exit 1]\n"
            exit_line = "[exit 1]"
        finally:
            logger.info("Churn run finished: mode=%s %s", mode, exit_line)

    return Response(
        chunks(),
        mimetype="text/plain",
        headers={
            # Without this a proxy buffers the whole run and delivers it as one
            # block at the end, which is the thing streaming exists to avoid.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
