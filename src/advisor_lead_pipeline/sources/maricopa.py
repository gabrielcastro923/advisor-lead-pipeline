from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from ..models import ImportStats, PropertyOwnerRecord
from ..normalization import classify_owner_kind, normalize_address
from .csv_source import ingest_records

TARGET_CLASSES = {
    "CLASS R1": "single_family",
    "CLASS R2": "condo",
    "CLASS R3": "townhouse",
}


def _source_fingerprint(path: Path) -> str:
    stat = path.stat()
    material = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _as_of(path: Path, provided: str | None) -> str:
    if provided:
        return provided
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(timespec="seconds")


def import_maricopa(
    db_path: str | Path,
    input_path: str | Path,
    *,
    city: str,
    entity_only: bool = False,
    limit: int | None = None,
    as_of: str | None = None,
) -> ImportStats:
    path = Path(input_path)
    observed_at = _as_of(path, as_of)
    desired_city = city.strip().upper()

    def records() -> Iterator[PropertyOwnerRecord]:
        accepted = 0
        with path.open(newline="", encoding="latin-1") as handle:
            reader = csv.DictReader(handle, delimiter="|")
            expected = {
                "ParcelNumber",
                "Class",
                "OwnerName",
                "OwnerAddr1stLine",
                "OwnerCity",
                "OwnerState",
                "OwnerZipCode",
                "SitusStreetNum",
                "SitusStreetName",
                "SitusCity",
                "SitusZipCode",
            }
            missing = expected - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"Maricopa file missing columns: {', '.join(sorted(missing))}")
            for row in reader:
                prop_class = (row.get("Class") or "").strip().upper()
                if prop_class not in TARGET_CLASSES:
                    continue
                row_city = (row.get("SitusCity") or "").strip().upper()
                if row_city != desired_city:
                    continue
                owner_name = (row.get("OwnerName") or "").strip()
                if not owner_name:
                    continue
                owner_kind = classify_owner_kind(owner_name)
                if entity_only and owner_kind == "individual":
                    continue
                address_parts = [
                    row.get("SitusStreetNum") or "",
                    row.get("SitusStreetDir") or "",
                    row.get("SitusStreetName") or "",
                    row.get("SitusStreetType") or "",
                    row.get("SitusStreetPostDir") or "",
                ]
                address_line1 = " ".join(part.strip() for part in address_parts if part.strip())
                unit = (row.get("SitusSuite") or "").strip()
                owner_line1 = " ".join(
                    part.strip()
                    for part in (
                        row.get("OwnerAddr1stLine") or "",
                        row.get("OwnerAddr2ndLine") or "",
                    )
                    if part.strip()
                )
                postal_code = (row.get("SitusZipCode") or "").strip()
                owner_postal = (row.get("OwnerZipCode") or "").strip()
                situs_normalized = normalize_address(address_line1, unit, city, "AZ", postal_code)
                mailing_normalized = normalize_address(
                    owner_line1,
                    "",
                    row.get("OwnerCity") or "",
                    row.get("OwnerState") or "",
                    owner_postal,
                )
                absentee = situs_normalized != mailing_normalized
                accepted += 1
                yield PropertyOwnerRecord(
                    source="maricopa_county_assessor",
                    source_record_id=(row.get("ParcelNumber") or "").strip(),
                    county_fips="04013",
                    apn=(row.get("ParcelNumber") or "").strip(),
                    address_line1=address_line1,
                    unit=unit,
                    city=city.strip().title(),
                    state="AZ",
                    postal_code=postal_code,
                    property_type=TARGET_CLASSES[prop_class],
                    market=f"{city.strip().title()}, AZ",
                    owner_name=owner_name,
                    owner_kind=owner_kind,  # type: ignore[arg-type]
                    owner_mailing_line1=owner_line1,
                    owner_mailing_city=(row.get("OwnerCity") or "").strip(),
                    owner_mailing_state=(row.get("OwnerState") or "").strip().upper(),
                    owner_mailing_postal_code=owner_postal,
                    owner_jurisdiction="us_az" if owner_kind == "entity" else "",
                    ownership_confidence=1.0,
                    observed_at=observed_at,
                    source_updated_at=observed_at,
                    unsolicited_email_allowed=True,
                    absentee=absentee,
                )
                if limit and accepted >= limit:
                    return

    return ingest_records(
        db_path,
        records(),
        source="maricopa_county_assessor",
        input_path=str(path.resolve()),
        input_sha256=_source_fingerprint(path),
    )
