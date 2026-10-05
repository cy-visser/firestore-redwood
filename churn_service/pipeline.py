"""Render and execute the Redwood Retail churn pipeline against BigQuery.

Moved out of ``run_bigquery_analysis.py``, which was executed as a subprocess
from the operator's workstation. The logic is unchanged; only its output is.
What the CLI printed, :func:`run` yields, so a caller that wants a live log
iterates it and a caller that just wants the pipeline done exhausts it.

Statement splitting is done with a small scanner rather than ``sql.split(";")``.
The naive version breaks on any semicolon inside a string literal or a comment,
and the churn script contains both; a mis-split produces a syntax error whose
message points at a fragment that appears nowhere in the source file, which is
a miserable thing to debug.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Mapping, Optional

# Run modes. ``FULL`` rebuilds the feature views, retrains the model and
# rescores; ``RESCORE`` runs the scoring MERGE alone against the existing
# model, which is what a demo reset needs -- nothing about a reset changes what
# churn looks like, so retraining would cost a minute to arrive at the same
# coefficients.
FULL = "full"
RESCORE = "rescore"
MODES = (FULL, RESCORE)

# The placeholders the SQL template declares. Terraform's daily scheduled query
# renders the same file with the same names, which is what keeps the scheduled
# run and this one from diverging.
PLACEHOLDERS = (
    "GCP_PROJECT_ID",
    "BIGQUERY_DATASET",
    "BIGQUERY_CDC_TABLE",
    "BIGQUERY_ORDERS_TABLE",
    "BIGQUERY_HISTORICAL_VIEW",
    "BIGQUERY_CHURN_MODEL",
)


class ChurnConfigError(ValueError):
    """A placeholder had no value, or the rendered SQL still has one.

    The CLI raised ``SystemExit`` here, which is correct for a script and wrong
    inside a server: it would take the worker down instead of returning an
    error to the caller.
    """


def render(template: str, context: Dict[str, str]) -> str:
    """Substitute ``${NAME}`` placeholders, failing loudly on anything missed."""
    rendered = template
    for key, value in context.items():
        if value in (None, ""):
            raise ChurnConfigError(
                f"Configuration value '{key}' is empty. Set it in the service's "
                f"environment."
            )
        rendered = rendered.replace(f"${{{key}}}", str(value))

    leftover = sorted(set(re.findall(r"\$\{([A-Z0-9_]+)\}", rendered)))
    if leftover:
        raise ChurnConfigError(f"Unresolved placeholders in SQL: {', '.join(leftover)}")

    return rendered


def build_context(env: Mapping[str, str]) -> Dict[str, str]:
    """Read the placeholders from the environment Cloud Run injects.

    ``BIGQUERY_ORDERS_TABLE`` falls back to ``{collection}_current``, the typed
    CDC mirror the feature view reads, because that is how the name is derived
    everywhere else in the system.
    """
    orders_table = env.get("BIGQUERY_ORDERS_TABLE") or (
        f"{env.get('FIRESTORE_COLLECTION', 'retail')}_current"
    )
    return {
        "GCP_PROJECT_ID": env.get("GCP_PROJECT_ID") or env.get("GCP_PROJECT", ""),
        "BIGQUERY_DATASET": env.get("BIGQUERY_DATASET", ""),
        "BIGQUERY_CDC_TABLE": env.get("BIGQUERY_CDC_TABLE", ""),
        "BIGQUERY_ORDERS_TABLE": orders_table,
        "BIGQUERY_HISTORICAL_VIEW": env.get("BIGQUERY_HISTORICAL_VIEW", ""),
        "BIGQUERY_CHURN_MODEL": env.get("BIGQUERY_CHURN_MODEL", ""),
    }


def split_statements(sql: str) -> List[str]:
    """Split a script on statement-terminating semicolons.

    Semicolons inside single, double or triple quotes, inside ``--`` and ``#``
    line comments, and inside ``/* */`` blocks are not terminators.
    """
    statements: List[str] = []
    buffer: List[str] = []

    i = 0
    length = len(sql)
    quote: Optional[str] = None  # active quote delimiter, if any
    in_line_comment = False
    in_block_comment = False

    while i < length:
        char = sql[i]
        pair = sql[i:i + 2]
        triple = sql[i:i + 3]

        if in_line_comment:
            buffer.append(char)
            if char == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            buffer.append(char)
            if pair == "*/":
                buffer.append(sql[i + 1])
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if quote:
            buffer.append(char)
            # Backslash escapes apply inside BigQuery string literals.
            if char == "\\" and i + 1 < length:
                buffer.append(sql[i + 1])
                i += 2
                continue
            if triple == quote:
                buffer.append(sql[i + 1])
                buffer.append(sql[i + 2])
                quote = None
                i += 3
                continue
            if char == quote:
                quote = None
            i += 1
            continue

        if triple in ('"""', "'''"):
            quote = triple
            buffer.append(triple)
            i += 3
            continue

        if char in ("'", '"'):
            quote = char
            buffer.append(char)
            i += 1
            continue

        if pair == "--" or char == "#":
            in_line_comment = True
            buffer.append(char)
            i += 1
            continue

        if pair == "/*":
            in_block_comment = True
            buffer.append(pair)
            i += 2
            continue

        if char == ";":
            statements.append("".join(buffer))
            buffer = []
            i += 1
            continue

        buffer.append(char)
        i += 1

    tail = "".join(buffer)
    if tail.strip():
        statements.append(tail)

    # Drop fragments that are only comments or blank lines.
    meaningful: List[str] = []
    for statement in statements:
        body = [
            line
            for line in statement.splitlines()
            if line.strip() and not line.strip().startswith(("--", "#"))
        ]
        if body:
            meaningful.append(statement.strip())
    return meaningful


def summarise(statement: str) -> str:
    """First meaningful line of a statement, for progress output."""
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("--", "#")):
            return stripped[:78]
    return statement[:78]


# Key pipeline steps tracked in output.
HEADLINE_STEPS = (
    (re.compile(r"^\s*CREATE\s+OR\s+REPLACE\s+MODEL", re.IGNORECASE),
     "Run customer_churn_model"),
    (re.compile(r"^\s*MERGE\b", re.IGNORECASE),
     "Merge customer_churn_risk"),
)


def headline_label(statement: str) -> Optional[str]:
    """The presenter-facing name for a statement, or None if it is scaffolding."""
    body = "\n".join(
        line for line in statement.splitlines()
        if line.strip() and not line.strip().startswith(("--", "#"))
    )
    for pattern, label in HEADLINE_STEPS:
        if pattern.match(body):
            return label
    return None


def is_rescore_statement(statement: str) -> bool:
    """Check if statement re-scores customers against the existing model."""
    return headline_label(statement) == "Merge customer_churn_risk"


def run(
    client: Any,
    sql_text: str,
    context: Dict[str, str],
    mode: str = FULL,
    report: bool = True,
    verbose: bool = False,
) -> Iterator[str]:
    """Execute the pipeline, yielding one log line at a time.

    The last line is always ``[exit N]``. That contract is what lets the
    Redwood Console tell success from failure without parsing the log text, and
    it is the same contract the subprocess had via its return code -- which is
    why replacing the transport did not change the console's control flow.

    Nothing in here raises. A caller is mid-stream by the time most failures
    happen, with its HTTP status already committed, so a failure has to be
    reportable in-band or not at all.
    """
    if mode not in MODES:
        yield f"[ERROR] Unknown mode {mode!r}; expected one of {', '.join(MODES)}."
        yield "[exit 1]"
        return

    try:
        rendered = render(sql_text, context)
        statements = split_statements(rendered)
    except ChurnConfigError as err:
        yield f"[ERROR] {err}"
        yield "[exit 1]"
        return

    if mode == RESCORE:
        statements = [s for s in statements if is_rescore_statement(s)]
        if not statements:
            yield (
                "[ERROR] No scoring MERGE found in the SQL template. The statement "
                "this mode looks for is the MERGE into customer_churn_risk."
            )
            yield "[exit 1]"
            return

    total_steps = sum(1 for s in statements if headline_label(s) is not None)
    step = 0

    yield f"Connecting to BigQuery in '{context['GCP_PROJECT_ID']}'..."
    if mode == RESCORE:
        yield "Re-scoring against the existing model."
    else:
        yield f"Running the churn pipeline: {total_steps} steps."
    yield ""

    for index, statement in enumerate(statements, 1):
        label = headline_label(statement)

        if label is not None:
            step += 1
            yield f"[{step}/{total_steps}] {label}"
        elif verbose:
            yield f"    ... {summarise(statement)}"

        try:
            job = client.query(statement)
            result = job.result()
        except Exception as err:  # noqa: BLE001 - report which statement failed
            yield f"[ERROR] Statement {index} failed: {summarise(statement)}"
            yield f"        {err}"
            yield "[exit 1]"
            return

        if label is None and not verbose:
            continue

        detail = []
        if getattr(job, "num_dml_affected_rows", None) is not None:
            detail.append(f"{job.num_dml_affected_rows} row(s) affected")
        elif getattr(result, "total_rows", None):
            detail.append(f"{result.total_rows} row(s)")
        if getattr(job, "total_bytes_processed", None):
            detail.append(f"{job.total_bytes_processed / 1e6:.1f} MB scanned")
        yield f"    done{' - ' + ', '.join(detail) if detail else ''}"

    yield ""
    yield "Churn scoring completed." if mode == RESCORE else "Churn pipeline completed."

    if report:
        for line in _report(
            client, context["GCP_PROJECT_ID"], context["BIGQUERY_DATASET"]
        ):
            yield line

    yield "[exit 0]"


def _report(client: Any, project: str, dataset: str) -> Iterator[str]:
    """Yield evaluation metrics and the resulting risk distribution.

    Every block is individually guarded. A missing evaluation view is not a
    reason to withhold the tier counts, and the persona rows at the end are the
    part of this output anybody actually reads.
    """
    yield ""
    yield "=" * 65
    yield " Model evaluation"
    yield "=" * 65

    try:
        rows = list(client.query(
            f"SELECT * FROM `{project}.{dataset}.customer_churn_model_evaluation` "
            f"ORDER BY evaluated_at DESC LIMIT 1"
        ).result())
        for row in rows:
            for key, value in dict(row).items():
                if key == "evaluated_at":
                    continue
                if isinstance(value, float):
                    yield f"  {key:<24} {value:.4f}"
                else:
                    yield f"  {key:<24} {value}"
    except Exception as err:  # noqa: BLE001
        yield f"  (unavailable: {err})"

    yield ""
    yield "=" * 65
    yield " Churn risk distribution"
    yield "=" * 65
    try:
        rows = client.query(
            f"SELECT churn_risk_tier, COUNT(*) AS customers, "
            f"ROUND(AVG(churn_probability), 4) AS avg_probability "
            f"FROM `{project}.{dataset}.customer_churn_risk` "
            f"GROUP BY churn_risk_tier ORDER BY avg_probability DESC"
        ).result()
        for row in rows:
            yield (f"  {row.churn_risk_tier:<10} {row.customers:>6} customers  "
                   f"avg p={row.avg_probability}")
    except Exception as err:  # noqa: BLE001
        yield f"  (unavailable: {err})"

    yield ""
    yield "=" * 65
    yield " Demo personas"
    yield "=" * 65
    try:
        rows = client.query(
            f"SELECT customer_id, churn_risk_tier, "
            f"ROUND(churn_probability, 4) AS p, days_since_last_purchase, "
            f"automated_retention_action "
            f"FROM `{project}.{dataset}.customer_churn_risk` "
            f"WHERE customer_id IN ('cust_demo1', 'cust_demo2') "
            f"ORDER BY customer_id"
        ).result()
        for row in rows:
            yield (f"  {row.customer_id:<12} {row.churn_risk_tier:<9} p={row.p:<8} "
                   f"dslp={row.days_since_last_purchase:<5} "
                   f"{row.automated_retention_action}")
    except Exception as err:  # noqa: BLE001
        yield f"  (unavailable: {err})"
