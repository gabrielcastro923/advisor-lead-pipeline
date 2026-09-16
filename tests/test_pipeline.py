from __future__ import annotations

import csv
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from advisor_lead_pipeline.config import load_config
from advisor_lead_pipeline.db import connect, initialize
from advisor_lead_pipeline.delivery.outcomes import import_outcomes
from advisor_lead_pipeline.delivery.queue import export_queue
from advisor_lead_pipeline.delivery.review import export_review, import_review
from advisor_lead_pipeline.enrichment.demo import DemoEnricher
from advisor_lead_pipeline.normalization import property_identity
from advisor_lead_pipeline.pipeline.budget import budget_snapshot, can_reserve
from advisor_lead_pipeline.pipeline.builder import build_leads
from advisor_lead_pipeline.pipeline.enrich import run_enrichment
from advisor_lead_pipeline.reporting.summary import build_summary
from advisor_lead_pipeline.sources.csv_source import import_csv

ROOT = Path(__file__).resolve().parents[1]


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db = self.root / "pipeline.sqlite3"
        initialize(self.db)
        self.config = load_config(ROOT / "config" / "pilot.example.toml")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def import_demo(self):
        return import_csv(self.db, ROOT / "fixtures" / "demo_properties.csv")

    def test_import_is_idempotent_and_identity_keeps_units_and_counties_distinct(self) -> None:
        first = self.import_demo()
        second = self.import_demo()
        self.assertEqual(first.properties_inserted, 12)
        self.assertEqual(second.properties_inserted, 0)
        conn = connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0], 12)
            same_apn = conn.execute(
                "SELECT COUNT(*) FROM properties WHERE apn='700-00-001'"
            ).fetchone()[0]
            self.assertEqual(same_apn, 2)
            same_name_entities = conn.execute(
                "SELECT COUNT(*) FROM owners WHERE canonical_name='Same Name Holdings LLC'"
            ).fetchone()[0]
            self.assertEqual(same_name_entities, 2)
        finally:
            conn.close()
        self.assertNotEqual(
            property_identity("04013", "123", "1 Main", "", "Phoenix", "AZ", "85001"),
            property_identity("06037", "123", "1 Main", "", "Phoenix", "AZ", "85001"),
        )

    def test_resolution_gates_registered_agents_and_current_clients(self) -> None:
        self.import_demo()
        stats = build_leads(self.db, self.config)
        self.assertEqual(stats.ready_for_enrichment, 3)
        self.assertEqual(stats.needs_ownership_review, 2)
        self.assertEqual(stats.excluded, 1)
        conn = connect(self.db)
        try:
            statuses = {
                row["canonical_name"]: row["workflow_status"]
                for row in conn.execute(
                    """
                    SELECT o.canonical_name, l.workflow_status
                    FROM leads l JOIN owners o ON o.id=l.owner_id
                    """
                ).fetchall()
            }
        finally:
            conn.close()
        self.assertEqual(statuses["Agent Only LLC"], "needs_ownership_review")
        self.assertEqual(statuses["Existing Client LLC"], "excluded_current_client")
        self.assertEqual(statuses["Sunny Door LLC"], "ready_for_enrichment")

    def test_rerun_marks_old_cohort_rows_outside_the_current_build(self) -> None:
        self.import_demo()
        build_leads(self.db, self.config)
        smaller = replace(
            self.config,
            pipeline=replace(self.config.pipeline, cohort_limit=1),
        )
        build_leads(self.db, smaller)
        conn = connect(self.db)
        try:
            current = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE in_current_build=1"
            ).fetchone()[0]
            stale = conn.execute("SELECT COUNT(*) FROM leads WHERE in_current_build=0").fetchone()[
                0
            ]
        finally:
            conn.close()
        self.assertEqual(current, 2)
        self.assertGreater(stale, 0)

    def test_enrichment_correlates_requests_deduplicates_and_holds_timeout_reservation(
        self,
    ) -> None:
        self.import_demo()
        build_leads(self.db, self.config)
        enricher = DemoEnricher(ROOT / "fixtures" / "demo_contacts.csv")
        first = run_enrichment(self.db, self.config.enrichment, enricher)
        self.assertEqual(first.submitted, 3)
        self.assertEqual(first.completed, 2)
        self.assertEqual(first.needs_reconciliation, 1)
        self.assertEqual(first.contacts_added, 4)
        build_leads(self.db, self.config)
        second = run_enrichment(self.db, self.config.enrichment, enricher)
        self.assertEqual(second.reused, 2)
        self.assertEqual(second.needs_reconciliation, 1)
        conn = connect(self.db)
        try:
            budget = budget_snapshot(conn)
            self.assertAlmostEqual(budget.charged, 0.04)
            self.assertAlmostEqual(budget.open_reservations, 0.02)
            self.assertFalse(can_reserve(conn, 0.01, 0.06))
            owner_contacts = {
                row["canonical_name"]: row["contact_value"]
                for row in conn.execute(
                    """
                    SELECT o.canonical_name, c.contact_value
                    FROM contacts c JOIN owners o ON o.id=c.person_owner_id
                    WHERE c.contact_type='phone'
                    """
                ).fetchall()
            }
        finally:
            conn.close()
        self.assertEqual(owner_contacts["Jane Sample"], "+1 202-555-0101")
        self.assertEqual(owner_contacts["Morgan Reyes"], "+1 202-555-0102")

    def _approve_ready(self, review_path: Path) -> list[str]:
        export_review(self.db, review_path)
        with review_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fields = reader.fieldnames or []
        approved_ids: list[str] = []
        for row in rows:
            if row["resolution_status"] == "ready_for_review":
                row["review_decision"] = "approve"
                row["assigned_advisor"] = "Test Advisor"
                row["reviewer"] = "Unit Test"
                approved_ids.append(row["lead_id"])
        with review_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        import_review(self.db, review_path)
        return approved_ids

    def test_rentcast_email_restriction_survives_queue_export(self) -> None:
        self.import_demo()
        build_leads(self.db, self.config)
        run_enrichment(
            self.db,
            self.config.enrichment,
            DemoEnricher(ROOT / "fixtures" / "demo_contacts.csv"),
        )
        self._approve_ready(self.root / "review.csv")
        queue_path = self.root / "queue.csv"
        self.assertEqual(export_queue(self.db, queue_path), 2)
        with queue_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        jane = next(row for row in rows if row["contact_name"] == "Jane Sample")
        self.assertEqual(jane["email"], "")
        self.assertEqual(jane["allowed_channels"], "phone")

    def test_outcomes_are_idempotent_and_opt_out_suppresses(self) -> None:
        self.import_demo()
        build_leads(self.db, self.config)
        run_enrichment(
            self.db,
            self.config.enrichment,
            DemoEnricher(ROOT / "fixtures" / "demo_contacts.csv"),
        )
        approved = self._approve_ready(self.root / "review.csv")
        outcomes_path = self.root / "outcomes.csv"
        with outcomes_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["event_id", "lead_id", "outcome", "occurred_at", "advisor", "notes"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "event_id": "event-1",
                    "lead_id": approved[0],
                    "outcome": "opt_out",
                    "occurred_at": "2026-09-16T12:00:00+00:00",
                    "advisor": "Test Advisor",
                    "notes": "synthetic",
                }
            )
        self.assertEqual(import_outcomes(self.db, outcomes_path), (1, 0))
        self.assertEqual(import_outcomes(self.db, outcomes_path), (0, 1))
        summary = build_summary(self.db)
        self.assertEqual(summary["totals"]["attempted"], 1)
        self.assertEqual(summary["outcomes"]["opt_out"], 1)
        conn = connect(self.db)
        try:
            status = conn.execute(
                "SELECT workflow_status FROM leads WHERE id=?", (approved[0],)
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(status, "excluded_suppressed")


if __name__ == "__main__":
    unittest.main()
