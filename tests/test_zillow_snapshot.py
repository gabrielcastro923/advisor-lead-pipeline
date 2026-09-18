from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from advisor_lead_pipeline.db import connect, initialize
from advisor_lead_pipeline.sources.csv_source import import_csv
from advisor_lead_pipeline.sources.zillow_snapshot import (
    import_zillow_snapshot,
    parse_zillow_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]


class ZillowSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db = self.root / "pipeline.sqlite3"
        initialize(self.db)
        import_csv(self.db, ROOT / "fixtures" / "demo_properties.csv")
        self.snapshot = ROOT / "fixtures" / "zillow_authorized_snapshot.html"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_parses_embedded_json_without_network_access(self) -> None:
        listings = parse_zillow_snapshot(self.snapshot)
        self.assertEqual(len(listings), 3)
        rental = next(item for item in listings if item.source_record_id == "synthetic-zillow-200")
        self.assertTrue(rental.is_rental)
        self.assertTrue(rental.is_active)
        self.assertEqual(rental.days_on_market, 25)
        sale = next(item for item in listings if item.source_record_id == "synthetic-zillow-sale")
        self.assertFalse(sale.is_rental)
        self.assertFalse(sale.is_active)

    def test_import_matches_county_property_and_is_idempotent(self) -> None:
        first = import_zillow_snapshot(
            self.db,
            self.snapshot,
            authorization_reference="LEGAL-APPROVAL-123",
            observed_at="2026-09-18T12:00:00+00:00",
        )
        self.assertEqual(first.listings_parsed, 3)
        self.assertEqual(first.active_rentals, 2)
        self.assertEqual(first.skipped_non_rental, 1)
        self.assertEqual(first.matched, 1)
        self.assertEqual(first.unmatched, 1)
        self.assertEqual(first.ambiguous, 0)
        self.assertEqual(first.observations_inserted, 1)

        second = import_zillow_snapshot(
            self.db,
            self.snapshot,
            authorization_reference="LEGAL-APPROVAL-123",
            observed_at="2026-09-18T12:00:00+00:00",
        )
        self.assertEqual(second.observations_inserted, 0)
        self.assertEqual(second.observations_updated, 1)

        conn = connect(self.db)
        try:
            row = conn.execute(
                """
                SELECT obs.observation_type, obs.unsolicited_email_allowed, obs.payload_json,
                       own.canonical_name
                FROM observations obs JOIN owners own ON own.id=obs.owner_id
                WHERE obs.source='zillow_authorized_snapshot'
                """
            ).fetchone()
            quarantine_count = conn.execute(
                "SELECT COUNT(*) FROM quarantine WHERE source='zillow_authorized_snapshot'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["canonical_name"], "Sunny Door LLC")
        self.assertEqual(row["observation_type"], "rental_listing")
        self.assertEqual(row["unsolicited_email_allowed"], 0)
        self.assertEqual(
            json.loads(row["payload_json"])["authorization_reference"], "LEGAL-APPROVAL-123"
        )
        self.assertEqual(quarantine_count, 1)

    def test_import_requires_an_authorization_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "authorization_reference is required"):
            import_zillow_snapshot(self.db, self.snapshot, authorization_reference="  ")


if __name__ == "__main__":
    unittest.main()
