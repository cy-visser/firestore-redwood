#!/usr/bin/env python3
"""
Renders and executes the BigQuery churn pipeline for Redwood Retail.

Substitutes ``${NAME}`` placeholders in the SQL template from .env, splits the
script into statements and runs them in order.

Splitting is done with a small scanner rather than ``sql.split(";")``. The
naive version breaks on any semicolon inside a string literal or a comment, and
the churn script contains both; a mis-split produces a syntax error whose
message points at a fragment that appears nowhere in the source file, which is
a miserable thing to debug.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

DEFAULT_PROJECT = os.getenv("GCP_PROJECT_ID") or os.getenv("GCP_PROJECT")
DEFAULT_DATASET = os.getenv("BIGQUERY_DATASET")
DEFAULT_CDC_TABLE = os.getenv("BIGQUERY_CDC_TABLE")
DEFAULT_HISTORICAL_VIEW = os.getenv("BIGQUERY_HISTORICAL_VIEW")
DEFAULT_CHURN_MODEL = os.getenv("BIGQUERY_CHURN_MODEL")

# The typed CDC mirror the feature view reads. Defaults to the orders
# collection name with a _current suffix, matching what the CDC service builds.
DEFAULT_ORDERS_TABLE = os.getenv("BIGQUERY_ORDERS_TABLE") or (
    f"{os.getenv('FIRESTORE_COLLECTION', 'retail')}_current"
)


def render(template: str, context: Dict[str, str]) -> str:
    """Substitute ``${NAME}`` placeholders, failing loudly on anything missed."""
    rendered = template
    for key, value in context.items():
        if value in (None, ""):
            raise SystemExit(
                f"Configuration value '{key}' is empty. Set it in .env or pass the "
                f"matching command-line flag."
            )
        rendered = rendered.replace(f"${{{key}}}", str(value))

    # An unresolved placeholder would otherwise reach BigQuery and fail with a
    # confusing syntax error.
    import re

    leftover = sorted(set(re.findall(r"\$\{([A-Z0-9_]+)\}", rendered)))
    if leftover:
        raise SystemExit(f"Unresolved placeholders in SQL: {', '.join(leftover)}")

    return rendered


def split_statements(sql: str) -> List[str]:
    """Split a script on statement-terminating semicolons.

    Semicolons inside single, double or triple quotes, inside ``--`` and ``#``
    line comments, and inside ``/* */`` blocks are not terminators.
    """
    statements: List[str] = []
    buffer: List[str] = []

    i = 0
    length = len(sql)
    quote: str | None = None  # active quote delimiter, if any
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render and execute the Redwood Retail churn pipeline"
    )
    parser.add_argument(
        "--sql-file",
        default=str(Path(__file__).parent / "bigquery_churn_sentiment_analysis.sql"),
        help="Path to the SQL template",
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--cdc-table", default=DEFAULT_CDC_TABLE)
    parser.add_argument("--orders-table", default=DEFAULT_ORDERS_TABLE,
                        help="Typed CDC mirror table the feature view reads")
    parser.add_argument("--historical-view", default=DEFAULT_HISTORICAL_VIEW)
    parser.add_argument("--churn-model", default=DEFAULT_CHURN_MODEL)
    parser.add_argument("--output-sql", help="Write the rendered SQL here")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render and print without executing")
    parser.add_argument("--execute", action="store_true",
                        help="Execute the statements against BigQuery")
    parser.add_argument("--report", action="store_true",
                        help="After executing, print model evaluation and tier counts")

    args = parser.parse_args()

    sql_path = Path(args.sql_file)
    if not sql_path.exists():
        print(f"SQL file '{sql_path}' not found.", file=sys.stderr)
        return 1

    context = {
        "GCP_PROJECT_ID": args.project,
        "BIGQUERY_DATASET": args.dataset,
        "BIGQUERY_CDC_TABLE": args.cdc_table,
        "BIGQUERY_ORDERS_TABLE": args.orders_table,
        "BIGQUERY_HISTORICAL_VIEW": args.historical_view,
        "BIGQUERY_CHURN_MODEL": args.churn_model,
    }

    rendered = render(sql_path.read_text(), context)
    statements = split_statements(rendered)

    if args.output_sql:
        Path(args.output_sql).write_text(rendered)
        print(f"Wrote rendered SQL to {args.output_sql}")

    if args.dry_run or (not args.execute and not args.output_sql):
        print("=" * 65)
        print(" Redwood Retail: churn pipeline (dry run)")
        print("=" * 65)
        for key, value in context.items():
            print(f"{key:<26} {value}")
        print(f"{'Statements':<26} {len(statements)}")
        print("=" * 65)
        for index, statement in enumerate(statements, 1):
            print(f"\n[{index}] {summarise(statement)}")
        return 0

    if not args.execute:
        return 0

    from google.cloud import bigquery

    print(f"Connecting to BigQuery in '{args.project}'...")
    client = bigquery.Client(project=args.project)
    print(f"Executing {len(statements)} statements.\n")

    for index, statement in enumerate(statements, 1):
        label = summarise(statement)
        print(f"[{index}/{len(statements)}] {label}")
        try:
            job = client.query(statement)
            result = job.result()
        except Exception as err:  # noqa: BLE001 - report which statement failed
            print(f"\nStatement {index} failed:\n  {label}\n\n{err}", file=sys.stderr)
            return 1

        detail = []
        if job.num_dml_affected_rows is not None:
            detail.append(f"{job.num_dml_affected_rows} row(s) affected")
        elif getattr(result, "total_rows", None):
            detail.append(f"{result.total_rows} row(s)")
        if job.total_bytes_processed:
            detail.append(f"{job.total_bytes_processed / 1e6:.1f} MB scanned")
        print(f"    done{' - ' + ', '.join(detail) if detail else ''}")

    print("\nChurn pipeline completed.")

    if args.report:
        _report(client, args.project, args.dataset)

    return 0


def _report(client, project: str, dataset: str) -> None:
    """Print evaluation metrics and the resulting risk distribution.

    Worth looking at every run. An AUC close to 1.0 on this problem is a sign
    that a feature has started leaking the label again, not that the model has
    improved.
    """
    print("\n" + "=" * 65)
    print(" Model evaluation")
    print("=" * 65)

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
                    print(f"  {key:<24} {value:.4f}")
                else:
                    print(f"  {key:<24} {value}")
    except Exception as err:  # noqa: BLE001
        print(f"  (unavailable: {err})")

    print("\n" + "=" * 65)
    print(" Churn risk distribution")
    print("=" * 65)
    try:
        rows = client.query(
            f"SELECT churn_risk_tier, COUNT(*) AS customers, "
            f"ROUND(AVG(churn_probability), 4) AS avg_probability "
            f"FROM `{project}.{dataset}.customer_churn_risk` "
            f"GROUP BY churn_risk_tier ORDER BY avg_probability DESC"
        ).result()
        for row in rows:
            print(f"  {row.churn_risk_tier:<10} {row.customers:>6} customers  "
                  f"avg p={row.avg_probability}")
    except Exception as err:  # noqa: BLE001
        print(f"  (unavailable: {err})")

    print("\n" + "=" * 65)
    print(" Demo personas")
    print("=" * 65)
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
            print(f"  {row.customer_id:<12} {row.churn_risk_tier:<9} p={row.p:<8} "
                  f"dslp={row.days_since_last_purchase:<5} {row.automated_retention_action}")
    except Exception as err:  # noqa: BLE001
        print(f"  (unavailable: {err})")


if __name__ == "__main__":
    sys.exit(main())
