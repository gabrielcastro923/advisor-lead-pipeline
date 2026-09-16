from __future__ import annotations

import csv
import hashlib
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection

from ..db import transaction
from ..models import ImportStats, PropertyOwnerRecord
from ..normalization import (
    normalize_address,
    normalize_name,
    owner_id_from_identity,
    owner_identity,
    property_id_from_identity,
    property_identity,
)
from ..util import as_bool, as_float, as_int, canonical_json, stable_id, utc_now

REQUIRED_COLUMNS = {
    "source",
    "source_record_id",
    "county_fips",
    "apn",
    "address_line1",
    "city",
    "state",
    "postal_code",
    "market",
    "owner_name",
    "owner_kind",
}
OWNER_KINDS = {"individual", "entity", "trust"}
REPRESENTATIVE_ROLES = {"member", "manager", "officer", "registered_agent", "unknown"}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _record_from_row(row: dict[str, str], line_number: int) -> PropertyOwnerRecord:
    missing = [name for name in REQUIRED_COLUMNS if name not in row]
    if missing:
        raise ValueError(f"missing columns: {', '.join(sorted(missing))}")
    blank = [
        name
        for name in ("source", "source_record_id", "address_line1", "owner_name", "market")
        if not (row.get(name) or "").strip()
    ]
    if blank:
        raise ValueError(f"blank required values: {', '.join(blank)}")
    owner_kind = (row.get("owner_kind") or "").strip().lower()
    if owner_kind not in OWNER_KINDS:
        raise ValueError(f"unsupported owner_kind {owner_kind!r}")
    representative_role = (row.get("representative_role") or "unknown").strip().lower()
    if representative_role not in REPRESENTATIVE_ROLES:
        raise ValueError(f"unsupported representative_role {representative_role!r}")
    source = (row.get("source") or "").strip()
    email_allowed = as_bool(row.get("unsolicited_email_allowed"), default=True)
    if source.lower().startswith("rentcast"):
        email_allowed = False
    observed_at = (row.get("observed_at") or "").strip() or utc_now()
    return PropertyOwnerRecord(
        source=source,
        source_record_id=(row.get("source_record_id") or f"line-{line_number}").strip(),
        county_fips=(row.get("county_fips") or "").strip(),
        apn=(row.get("apn") or "").strip(),
        address_line1=(row.get("address_line1") or "").strip(),
        unit=(row.get("unit") or "").strip(),
        city=(row.get("city") or "").strip(),
        state=(row.get("state") or "").strip().upper(),
        postal_code=(row.get("postal_code") or "").strip(),
        property_type=(row.get("property_type") or "residential").strip().lower(),
        market=(row.get("market") or "").strip(),
        owner_name=(row.get("owner_name") or "").strip(),
        owner_kind=owner_kind,  # type: ignore[arg-type]
        owner_mailing_line1=(row.get("owner_mailing_line1") or "").strip(),
        owner_mailing_city=(row.get("owner_mailing_city") or "").strip(),
        owner_mailing_state=(row.get("owner_mailing_state") or "").strip().upper(),
        owner_mailing_postal_code=(row.get("owner_mailing_postal_code") or "").strip(),
        owner_jurisdiction=(row.get("owner_jurisdiction") or "").strip().lower(),
        company_number=(row.get("company_number") or "").strip(),
        owner_role=(row.get("owner_role") or "recorded_owner").strip().lower(),
        ownership_confidence=as_float(row.get("ownership_confidence"), default=1.0),
        representative_name=(row.get("representative_name") or "").strip(),
        representative_role=representative_role,  # type: ignore[arg-type]
        representative_confidence=as_float(row.get("representative_confidence")),
        observed_at=observed_at,
        source_updated_at=(row.get("source_updated_at") or "").strip(),
        rental_signal=as_bool(row.get("rental_signal")),
        rental_listed_date=(row.get("rental_listed_date") or "").strip(),
        days_on_market=as_int(row.get("days_on_market")),
        unsolicited_email_allowed=email_allowed,
        absentee=as_bool(row.get("absentee")),
        current_client=as_bool(row.get("current_client")),
    )


def _upsert_record(conn: Connection, record: PropertyOwnerRecord) -> dict[str, int]:
    now = utc_now()
    property_key = property_identity(
        record.county_fips,
        record.apn,
        record.address_line1,
        record.unit,
        record.city,
        record.state,
        record.postal_code,
    )
    property_id = property_id_from_identity(property_key)
    property_exists = conn.execute(
        "SELECT 1 FROM properties WHERE id = ?", (property_id,)
    ).fetchone()
    conn.execute(
        """
        INSERT INTO properties(
            id, identity_key, county_fips, apn, address_line1, unit, city, state,
            postal_code, normalized_address, property_type, market, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            address_line1=excluded.address_line1, unit=excluded.unit, city=excluded.city,
            state=excluded.state, postal_code=excluded.postal_code,
            normalized_address=excluded.normalized_address,
            property_type=excluded.property_type, market=excluded.market, updated_at=excluded.updated_at
        """,
        (
            property_id,
            property_key,
            record.county_fips,
            record.apn,
            record.address_line1,
            record.unit,
            record.city,
            record.state,
            record.postal_code,
            normalize_address(
                record.address_line1, record.unit, record.city, record.state, record.postal_code
            ),
            record.property_type,
            record.market,
            now,
            now,
        ),
    )

    owner_key = owner_identity(
        record.owner_kind,
        record.owner_name,
        record.owner_jurisdiction,
        record.company_number,
        record.owner_mailing_line1,
        record.owner_mailing_city,
        record.owner_mailing_state,
        record.owner_mailing_postal_code,
    )
    owner_id = owner_id_from_identity(owner_key)
    owner_exists = conn.execute("SELECT 1 FROM owners WHERE id = ?", (owner_id,)).fetchone()
    conn.execute(
        """
        INSERT INTO owners(
            id, identity_key, owner_kind, canonical_name, normalized_name, mailing_line1,
            mailing_city, mailing_state, mailing_postal_code, jurisdiction, company_number,
            current_client, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            canonical_name=excluded.canonical_name,
            mailing_line1=excluded.mailing_line1,
            mailing_city=excluded.mailing_city,
            mailing_state=excluded.mailing_state,
            mailing_postal_code=excluded.mailing_postal_code,
            current_client=MAX(owners.current_client, excluded.current_client),
            updated_at=excluded.updated_at
        """,
        (
            owner_id,
            owner_key,
            record.owner_kind,
            record.owner_name,
            normalize_name(record.owner_name),
            record.owner_mailing_line1,
            record.owner_mailing_city,
            record.owner_mailing_state,
            record.owner_mailing_postal_code,
            record.owner_jurisdiction,
            record.company_number,
            int(record.current_client),
            now,
            now,
        ),
    )

    relationship_id = stable_id(
        "rel", owner_id, property_id, record.owner_role, record.source, record.source_record_id
    )
    relationship_exists = conn.execute(
        "SELECT 1 FROM relationships WHERE id = ?", (relationship_id,)
    ).fetchone()
    conn.execute(
        """
        INSERT INTO relationships(
            id, owner_id, property_id, related_owner_id, role, evidence_source,
            source_record_id, confidence, valid_from, valid_to, created_at
        ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?)
        ON CONFLICT(id) DO UPDATE SET confidence=excluded.confidence, valid_from=excluded.valid_from
        """,
        (
            relationship_id,
            owner_id,
            property_id,
            record.owner_role,
            record.source,
            record.source_record_id,
            record.ownership_confidence,
            record.source_updated_at or None,
            now,
        ),
    )

    owner_inserted = int(not owner_exists)
    relationship_inserted = int(not relationship_exists)
    if record.representative_name:
        representative_key = (
            f"representative-candidate|{normalize_name(record.representative_name)}|{owner_id}"
        )
        representative_id = owner_id_from_identity(representative_key)
        representative_exists = conn.execute(
            "SELECT 1 FROM owners WHERE id = ?", (representative_id,)
        ).fetchone()
        conn.execute(
            """
            INSERT INTO owners(
                id, identity_key, owner_kind, canonical_name, normalized_name, created_at, updated_at
            ) VALUES (?, ?, 'individual', ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET canonical_name=excluded.canonical_name,
                updated_at=excluded.updated_at
            """,
            (
                representative_id,
                representative_key,
                record.representative_name,
                normalize_name(record.representative_name),
                now,
                now,
            ),
        )
        rep_relationship_id = stable_id(
            "rel",
            representative_id,
            owner_id,
            record.representative_role,
            record.source,
            record.source_record_id,
        )
        rep_relationship_exists = conn.execute(
            "SELECT 1 FROM relationships WHERE id = ?", (rep_relationship_id,)
        ).fetchone()
        conn.execute(
            """
            INSERT INTO relationships(
                id, owner_id, property_id, related_owner_id, role, evidence_source,
                source_record_id, confidence, valid_from, valid_to, created_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(id) DO UPDATE SET confidence=excluded.confidence, valid_from=excluded.valid_from
            """,
            (
                rep_relationship_id,
                representative_id,
                owner_id,
                record.representative_role,
                record.source,
                record.source_record_id,
                record.representative_confidence,
                record.source_updated_at or None,
                now,
            ),
        )
        owner_inserted += int(not representative_exists)
        relationship_inserted += int(not rep_relationship_exists)

    observation_payload = {
        "absentee": record.absentee,
        "owner_role": record.owner_role,
        "ownership_confidence": record.ownership_confidence,
    }
    observation_id = stable_id("obs", record.source, record.source_record_id, "ownership")
    observation_exists = conn.execute(
        "SELECT 1 FROM observations WHERE id = ?", (observation_id,)
    ).fetchone()
    conn.execute(
        """
        INSERT INTO observations(
            id, property_id, owner_id, source, source_record_id, observation_type,
            observed_at, source_updated_at, unsolicited_email_allowed, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, 'ownership', ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            observed_at=excluded.observed_at, source_updated_at=excluded.source_updated_at,
            unsolicited_email_allowed=excluded.unsolicited_email_allowed,
            payload_json=excluded.payload_json
        """,
        (
            observation_id,
            property_id,
            owner_id,
            record.source,
            record.source_record_id,
            record.observed_at,
            record.source_updated_at or None,
            int(record.unsolicited_email_allowed),
            canonical_json(observation_payload),
            now,
        ),
    )
    observation_inserted = int(not observation_exists)

    if record.rental_signal:
        rental_id = stable_id("obs", record.source, record.source_record_id, "rental_listing")
        rental_exists = conn.execute(
            "SELECT 1 FROM observations WHERE id = ?", (rental_id,)
        ).fetchone()
        conn.execute(
            """
            INSERT INTO observations(
                id, property_id, owner_id, source, source_record_id, observation_type,
                observed_at, source_updated_at, unsolicited_email_allowed, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, 'rental_listing', ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                observed_at=excluded.observed_at, source_updated_at=excluded.source_updated_at,
                unsolicited_email_allowed=excluded.unsolicited_email_allowed,
                payload_json=excluded.payload_json
            """,
            (
                rental_id,
                property_id,
                owner_id,
                record.source,
                record.source_record_id,
                record.observed_at,
                record.source_updated_at or None,
                int(record.unsolicited_email_allowed),
                canonical_json(
                    {
                        "listed_date": record.rental_listed_date,
                        "days_on_market": record.days_on_market,
                    }
                ),
                now,
            ),
        )
        observation_inserted += int(not rental_exists)

    return {
        "properties_inserted": int(not property_exists),
        "owners_inserted": owner_inserted,
        "relationships_inserted": relationship_inserted,
        "observations_inserted": observation_inserted,
    }


def ingest_records(
    db_path: str | Path,
    records: Iterable[PropertyOwnerRecord],
    *,
    source: str,
    input_path: str,
    input_sha256: str,
) -> ImportStats:
    counts = {
        "rows_seen": 0,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "properties_inserted": 0,
        "owners_inserted": 0,
        "relationships_inserted": 0,
        "observations_inserted": 0,
    }
    rejects: list[dict[str, str]] = []
    import_id = stable_id("imp", source, input_sha256)
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO imports(id, source, input_path, input_sha256, started_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source, input_sha256) DO UPDATE SET started_at=excluded.started_at,
                completed_at=NULL
            """,
            (import_id, source, input_path, input_sha256, utc_now()),
        )
        for line_number, item in enumerate(records, start=2):
            counts["rows_seen"] += 1
            try:
                record = item
                if not record.source_record_id:
                    record = replace(record, source_record_id=f"line-{line_number}")
                delta = _upsert_record(conn, record)
                counts["rows_accepted"] += 1
                for key, value in delta.items():
                    counts[key] += value
            except (ValueError, TypeError, sqlite3.IntegrityError, sqlite3.DataError) as exc:
                counts["rows_rejected"] += 1
                source_record_id = getattr(item, "source_record_id", f"line-{line_number}")
                reject = {
                    "line": str(line_number),
                    "source_record_id": str(source_record_id),
                    "reason": str(exc),
                }
                rejects.append(reject)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO quarantine(
                        id, stage, source, source_record_id, reason, payload_json, created_at
                    ) VALUES (?, 'import', ?, ?, ?, ?, ?)
                    """,
                    (
                        stable_id("qua", "import", source, source_record_id, str(exc)),
                        source,
                        source_record_id,
                        str(exc),
                        canonical_json(reject),
                        utc_now(),
                    ),
                )
        conn.execute(
            """
            UPDATE imports SET completed_at=?, rows_seen=?, rows_accepted=?, rows_rejected=?
            WHERE id=?
            """,
            (
                utc_now(),
                counts["rows_seen"],
                counts["rows_accepted"],
                counts["rows_rejected"],
                import_id,
            ),
        )
    return ImportStats(**counts, rejects=tuple(rejects))


def import_csv(
    db_path: str | Path, input_path: str | Path, rejects_path: str | Path | None = None
) -> ImportStats:
    path = Path(input_path)
    digest = _file_sha256(path)
    rejects: list[dict[str, str]] = []

    def rows() -> Iterator[PropertyOwnerRecord]:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("CSV has no header")
            missing = REQUIRED_COLUMNS - set(reader.fieldnames)
            if missing:
                raise ValueError(f"missing columns: {', '.join(sorted(missing))}")
            for line_number, row in enumerate(reader, start=2):
                try:
                    yield _record_from_row(row, line_number)
                except (ValueError, TypeError) as exc:
                    reject = {
                        "line": str(line_number),
                        "source_record_id": (row.get("source_record_id") or "").strip(),
                        "reason": str(exc),
                    }
                    rejects.append(reject)

    stats = ingest_records(
        db_path,
        rows(),
        source="canonical_csv",
        input_path=str(path.resolve()),
        input_sha256=digest,
    )
    combined_rejects = list(stats.rejects) + rejects
    if rejects_path and combined_rejects:
        output = Path(rejects_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["line", "source_record_id", "reason"])
            writer.writeheader()
            writer.writerows(combined_rejects)
    return ImportStats(
        rows_seen=stats.rows_seen + len(rejects),
        rows_accepted=stats.rows_accepted,
        rows_rejected=stats.rows_rejected + len(rejects),
        properties_inserted=stats.properties_inserted,
        owners_inserted=stats.owners_inserted,
        relationships_inserted=stats.relationships_inserted,
        observations_inserted=stats.observations_inserted,
        rejects=tuple(combined_rejects),
    )
