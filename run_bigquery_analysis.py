#!/usr/bin/env python3
"""
BigQuery Analytics & ML Pipeline Runner for Redwood Retail.
Reads configuration from .env, renders `bigquery_churn_sentiment_analysis.sql`
with target project, dataset, and table names, and can dry-run (render) or execute
the queries against BigQuery.
"""

import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

# Load environment configuration from .env if present
load_dotenv(find_dotenv(usecwd=True))

DEFAULT_PROJECT = os.getenv("GCP_PROJECT_ID") or os.getenv("GCP_PROJECT")
DEFAULT_DATASET = os.getenv("BIGQUERY_DATASET")
DEFAULT_CDC_TABLE = os.getenv("BIGQUERY_CDC_TABLE")
DEFAULT_HISTORICAL_VIEW = os.getenv("BIGQUERY_HISTORICAL_VIEW")
DEFAULT_CHURN_MODEL = os.getenv("BIGQUERY_CHURN_MODEL")


def get_rendered_sql(sql_template: str, context: dict) -> str:
    """Substitutes environment placeholders into the SQL script template."""
    rendered = sql_template
    for key, val in context.items():
        rendered = rendered.replace(f"${{{key}}}", str(val))
    return rendered


def main():
    parser = argparse.ArgumentParser(description="Render and execute BigQuery Churn Sentiment Analysis SQL using .env configuration")
    parser.add_argument("--sql-file", default=str(Path(__file__).parent / "bigquery_churn_sentiment_analysis.sql"), help="Path to SQL template file")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Google Cloud project ID")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="BigQuery dataset ID")
    parser.add_argument("--cdc-table", default=DEFAULT_CDC_TABLE, help="BigQuery CDC table name")
    parser.add_argument("--historical-view", default=DEFAULT_HISTORICAL_VIEW, help="BigQuery feature view name")
    parser.add_argument("--churn-model", default=DEFAULT_CHURN_MODEL, help="BigQuery ML model name")
    parser.add_argument("--output-sql", help="Path to write rendered SQL file (e.g. bigquery_churn_sentiment_analysis.rendered.sql)")
    parser.add_argument("--dry-run", action="store_true", help="Print rendered SQL statements without executing")
    parser.add_argument("--execute", action="store_true", help="Execute the SQL script queries against BigQuery")

    args = parser.parse_args()

    sql_path = Path(args.sql_file)
    if not sql_path.exists():
        print(f"Error: SQL file '{sql_path}' not found.", file=sys.stderr)
        sys.exit(1)

    template_content = sql_path.read_text()

    context = {
        "GCP_PROJECT_ID": args.project,
        "BIGQUERY_DATASET": args.dataset,
        "BIGQUERY_CDC_TABLE": args.cdc_table,
        "BIGQUERY_HISTORICAL_VIEW": args.historical_view,
        "BIGQUERY_CHURN_MODEL": args.churn_model,
    }

    rendered_sql = get_rendered_sql(template_content, context)

    if args.output_sql:
        out_path = Path(args.output_sql)
        out_path.write_text(rendered_sql)
        print(f"✅ Successfully wrote rendered SQL to: {out_path}")

    if args.dry_run or (not args.execute and not args.output_sql):
        print("=================================================================")
        print(" Redwood Retail: BigQuery Rendered SQL Script (Dry-Run)")
        print("=================================================================")
        print(f"Target Project:   {args.project}")
        print(f"Target Dataset:   {args.dataset}")
        print(f"Target CDC Table: {args.cdc_table}")
        print(f"Historical View:  {args.historical_view}")
        print(f"Churn Model:      {args.churn_model}")
        print("=================================================================\n")
        print(rendered_sql)

    if args.execute:
        try:
            from google.cloud import bigquery
            print(f"🚀 Connecting to BigQuery in project '{args.project}'...")
            client = bigquery.Client(project=args.project)
            
            # Split SQL statements by semicolon
            statements = [s.strip() for s in rendered_sql.split(";") if s.strip()]
            print(f"Found {len(statements)} SQL statements to execute.\n")

            for idx, stmt in enumerate(statements, 1):
                # Filter out pure comment blocks
                lines = [l for l in stmt.splitlines() if l.strip() and not l.strip().startswith("--")]
                if not lines:
                    continue
                first_line = lines[0][:60]
                print(f"[{idx}/{len(statements)}] Executing: {first_line}...")
                query_job = client.query(stmt)
                results = query_job.result()
                if results.total_rows is not None:
                    print(f"    ↳ Completed. Returned {results.total_rows} row(s).")
                else:
                    print("    ↳ Completed successfully.")

            print("\n🎉 All BigQuery SQL statements executed successfully!")
        except Exception as e:
            print(f"❌ Error executing BigQuery queries: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
