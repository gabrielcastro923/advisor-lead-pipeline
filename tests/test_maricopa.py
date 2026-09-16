from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from advisor_lead_pipeline.db import connect, initialize
from advisor_lead_pipeline.sources.maricopa import import_maricopa


class MaricopaAdapterTest(unittest.TestCase):
    def test_streaming_adapter_filters_city_class_and_owner_kind(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "Residential_Master.txt"
            fields = [
                "ParcelNumber",
                "Class",
                "OwnerName",
                "OwnerAddr1stLine",
                "OwnerAddr2ndLine",
                "OwnerCity",
                "OwnerState",
                "OwnerZipCode",
                "SitusStreetNum",
                "SitusStreetDir",
                "SitusStreetName",
                "SitusStreetType",
                "SitusStreetPostDir",
                "SitusSuite",
                "SitusCity",
                "SitusZipCode",
            ]
            with source.open("w", newline="", encoding="latin-1") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, delimiter="|")
                writer.writeheader()
                writer.writerow(
                    {
                        "ParcelNumber": "A-1",
                        "Class": "CLASS R1",
                        "OwnerName": "Synthetic Homes LLC",
                        "OwnerAddr1stLine": "1 Owner Rd",
                        "OwnerCity": "Denver",
                        "OwnerState": "CO",
                        "OwnerZipCode": "80202",
                        "SitusStreetNum": "1",
                        "SitusStreetName": "Example",
                        "SitusStreetType": "Ave",
                        "SitusCity": "PHOENIX",
                        "SitusZipCode": "85001",
                    }
                )
                writer.writerow(
                    {
                        "ParcelNumber": "A-2",
                        "Class": "CLASS R1",
                        "OwnerName": "Individual Owner",
                        "SitusStreetNum": "2",
                        "SitusStreetName": "Example",
                        "SitusStreetType": "Ave",
                        "SitusCity": "PHOENIX",
                        "SitusZipCode": "85001",
                    }
                )
                writer.writerow(
                    {
                        "ParcelNumber": "A-3",
                        "Class": "CLASS R1",
                        "OwnerName": "Other City LLC",
                        "SitusCity": "MESA",
                        "SitusZipCode": "85201",
                    }
                )
            db = root / "pipeline.sqlite3"
            initialize(db)
            stats = import_maricopa(db, source, city="Phoenix", entity_only=True)
            self.assertEqual(stats.rows_accepted, 1)
            conn = connect(db)
            try:
                property_row = conn.execute("SELECT * FROM properties").fetchone()
                owner_row = conn.execute("SELECT * FROM owners").fetchone()
            finally:
                conn.close()
            self.assertEqual(property_row["apn"], "A-1")
            self.assertEqual(owner_row["owner_kind"], "entity")


if __name__ == "__main__":
    unittest.main()
