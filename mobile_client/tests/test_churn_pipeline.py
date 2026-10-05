"""
Unit tests for the churn pipeline library that backs the redwood-churn
Cloud Run function.

These were written against ``run_bigquery_analysis.py`` when the pipeline ran
as a subprocess on the presenter's workstation. The parsing helpers moved to
``churn_service.pipeline`` unchanged, so their tests moved with them; what is
new is coverage of :func:`churn_service.pipeline.run`, which did not exist
before -- the CLI printed and exited, the library yields and terminates with
``[exit N]``. That final line is the contract the Redwood Console reads to
tell a successful recalculation from a failed one, so it is asserted on every
path below.
"""

import unittest

from churn_service import pipeline


class FakeResult:
    """Stands in for a ``RowIterator``."""

    def __init__(self, rows=(), total_rows=None):
        self._rows = list(rows)
        self.total_rows = total_rows if total_rows is not None else len(self._rows)

    def __iter__(self):
        return iter(self._rows)


class FakeJob:
    def __init__(self, result=None, num_dml_affected_rows=None,
                 total_bytes_processed=None):
        self._result = result if result is not None else FakeResult()
        self.num_dml_affected_rows = num_dml_affected_rows
        self.total_bytes_processed = total_bytes_processed

    def result(self):
        return self._result


class FakeBigQueryClient:
    """Records the statements it is handed and fails the ones told to fail.

    ``fail_on`` is a substring; the first statement containing it raises. That
    is enough to drive the error path without a real BigQuery, and asserting on
    ``statements`` proves the pipeline stopped where it said it did.
    """

    def __init__(self, fail_on=None, job_factory=None):
        self.fail_on = fail_on
        self.statements = []
        self._job_factory = job_factory or (lambda statement: FakeJob())

    def query(self, statement):
        self.statements.append(statement)
        if self.fail_on and self.fail_on in statement:
            raise RuntimeError("400 Syntax error: unexpected keyword")
        return self._job_factory(statement)


SAMPLE_SQL = """
CREATE OR REPLACE VIEW `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.order_facts` AS
SELECT 1;
CREATE OR REPLACE MODEL `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_CHURN_MODEL}`
OPTIONS(model_type='LOGISTIC_REG') AS SELECT 1;
MERGE INTO `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.customer_churn_risk` t
USING predictions s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.p = s.p;
"""

CONTEXT = {
    "GCP_PROJECT_ID": "test-prj",
    "BIGQUERY_DATASET": "redwood_retail",
    "BIGQUERY_CDC_TABLE": "retail_cdc",
    "BIGQUERY_ORDERS_TABLE": "retail_current",
    "BIGQUERY_HISTORICAL_VIEW": "customer_historical_data",
    "BIGQUERY_CHURN_MODEL": "customer_churn_model",
}


class TestRendering(unittest.TestCase):
    def test_render_substitutes_all_placeholders(self):
        template = "SELECT '${PROJECT}' AS p, '${DATASET}' AS d;"
        rendered = pipeline.render(template, {"PROJECT": "my-prj", "DATASET": "my_ds"})
        self.assertEqual(rendered, "SELECT 'my-prj' AS p, 'my_ds' AS d;")

    def test_render_fails_on_unresolved_or_empty(self):
        # The CLI raised SystemExit here, which would have taken a server
        # worker down with it; the library raises a plain ValueError subclass.
        with self.assertRaises(pipeline.ChurnConfigError):
            pipeline.render("SELECT '${UNKNOWN}' FROM t;", {"KNOWN": "val"})

        with self.assertRaises(pipeline.ChurnConfigError):
            pipeline.render("SELECT '${EMPTY}';", {"EMPTY": ""})

    def test_build_context_derives_orders_table_from_collection(self):
        context = pipeline.build_context({
            "GCP_PROJECT_ID": "test-prj",
            "FIRESTORE_COLLECTION": "retail",
            "BIGQUERY_DATASET": "redwood_retail",
            "BIGQUERY_CDC_TABLE": "retail_cdc",
            "BIGQUERY_HISTORICAL_VIEW": "customer_historical_data",
            "BIGQUERY_CHURN_MODEL": "customer_churn_model",
        })
        self.assertEqual(context["BIGQUERY_ORDERS_TABLE"], "retail_current")

    def test_build_context_prefers_explicit_orders_table(self):
        context = pipeline.build_context({
            "FIRESTORE_COLLECTION": "retail",
            "BIGQUERY_ORDERS_TABLE": "explicit_orders",
        })
        self.assertEqual(context["BIGQUERY_ORDERS_TABLE"], "explicit_orders")


class TestStatementScanning(unittest.TestCase):
    def test_split_statements_handles_quotes_and_comments(self):
        sql = """
        -- Comment with ; semicolon
        CREATE OR REPLACE VIEW v AS
        SELECT '; inside string' AS val, "; double quote ;" AS d;
        /* Block comment with ; */
        MERGE target USING source ON target.id = source.id
        WHEN MATCHED THEN UPDATE SET val = 'done;';
        """
        statements = pipeline.split_statements(sql)
        self.assertEqual(len(statements), 2)
        self.assertIn("CREATE OR REPLACE VIEW v", statements[0])
        self.assertIn("MERGE target USING source", statements[1])

    def test_headline_label_classification(self):
        model_stmt = "CREATE OR REPLACE MODEL `proj.ds.churn_model` OPTIONS(...) AS SELECT 1"
        merge_stmt = "MERGE INTO `proj.ds.customer_churn_risk` t USING s ON t.id = s.id ..."
        view_stmt = "CREATE OR REPLACE VIEW `proj.ds.v` AS SELECT 1"
        commented_merge = "-- comment\nMERGE INTO `proj.ds.customer_churn_risk` t ..."

        self.assertEqual(pipeline.headline_label(model_stmt), "Run customer_churn_model")
        self.assertEqual(pipeline.headline_label(merge_stmt), "Merge customer_churn_risk")
        self.assertIsNone(pipeline.headline_label(view_stmt))
        self.assertEqual(pipeline.headline_label(commented_merge),
                         "Merge customer_churn_risk")

    def test_is_rescore_statement(self):
        self.assertTrue(pipeline.is_rescore_statement("MERGE INTO `customer_churn_risk` ..."))
        self.assertFalse(pipeline.is_rescore_statement("CREATE OR REPLACE MODEL ..."))


class TestRun(unittest.TestCase):
    """The streaming contract: every run ends in exactly one ``[exit N]``."""

    def _run(self, client, mode=pipeline.FULL, report=False, verbose=False):
        return list(pipeline.run(client, SAMPLE_SQL, CONTEXT, mode=mode,
                                 report=report, verbose=verbose))

    def test_full_run_executes_every_statement_and_exits_zero(self):
        client = FakeBigQueryClient()
        lines = self._run(client)

        self.assertEqual(len(client.statements), 3)
        self.assertEqual(lines[-1], "[exit 0]")
        self.assertEqual(sum(1 for line in lines if line.startswith("[exit ")), 1)
        self.assertIn("[1/2] Run customer_churn_model", lines)
        self.assertIn("[2/2] Merge customer_churn_risk", lines)

    def test_rescore_runs_only_the_scoring_merge(self):
        # A demo reset does not change what churn looks like, so retraining
        # would spend a minute arriving at the same coefficients.
        client = FakeBigQueryClient()
        lines = self._run(client, mode=pipeline.RESCORE)

        self.assertEqual(len(client.statements), 1)
        self.assertIn("MERGE INTO", client.statements[0])
        self.assertEqual(lines[-1], "[exit 0]")
        self.assertIn("Re-scoring against the existing model.", lines)

    def test_failing_statement_stops_the_run_and_exits_one(self):
        client = FakeBigQueryClient(fail_on="CREATE OR REPLACE MODEL")
        lines = self._run(client)

        # The MERGE after the failure must not have been attempted.
        self.assertEqual(len(client.statements), 2)
        self.assertEqual(lines[-1], "[exit 1]")
        self.assertTrue(any(line.startswith("[ERROR] Statement 2 failed") for line in lines))
        self.assertTrue(any("Syntax error" in line for line in lines))

    def test_unknown_mode_exits_one_without_touching_bigquery(self):
        client = FakeBigQueryClient()
        lines = self._run(client, mode="sideways")

        self.assertEqual(client.statements, [])
        self.assertEqual(lines[-1], "[exit 1]")
        self.assertTrue(lines[0].startswith("[ERROR] Unknown mode"))

    def test_unresolved_placeholder_exits_one_instead_of_raising(self):
        client = FakeBigQueryClient()
        lines = list(pipeline.run(
            client, "SELECT '${NOT_PROVIDED}';", CONTEXT, mode=pipeline.FULL,
            report=False,
        ))

        self.assertEqual(client.statements, [])
        self.assertEqual(lines[-1], "[exit 1]")
        self.assertTrue(any("Unresolved placeholders" in line for line in lines))

    def test_rescore_without_a_merge_exits_one(self):
        client = FakeBigQueryClient()
        lines = list(pipeline.run(
            client, "CREATE OR REPLACE VIEW `v` AS SELECT 1;", CONTEXT,
            mode=pipeline.RESCORE, report=False,
        ))

        self.assertEqual(client.statements, [])
        self.assertEqual(lines[-1], "[exit 1]")
        self.assertTrue(any("No scoring MERGE" in line for line in lines))

    def test_dml_row_counts_are_reported(self):
        def job_factory(statement):
            if statement.strip().upper().startswith("MERGE"):
                return FakeJob(num_dml_affected_rows=42, total_bytes_processed=2_500_000)
            return FakeJob()

        client = FakeBigQueryClient(job_factory=job_factory)
        lines = self._run(client)

        self.assertIn("    done - 42 row(s) affected, 2.5 MB scanned", lines)

    def test_report_failures_do_not_sink_the_run(self):
        # The report queries hit views that may not exist yet on a fresh
        # project. That is a reason to print "(unavailable)", not to tell the
        # console the recalculation failed.
        class ReportFailsClient(FakeBigQueryClient):
            def query(self, statement):
                if "customer_churn_model_evaluation" in statement or \
                        "GROUP BY churn_risk_tier" in statement or \
                        "cust_demo1" in statement:
                    raise RuntimeError("404 Not found: Table")
                return super().query(statement)

        lines = list(pipeline.run(ReportFailsClient(), SAMPLE_SQL, CONTEXT,
                                  report=True))

        self.assertEqual(lines[-1], "[exit 0]")
        self.assertTrue(any("(unavailable:" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
