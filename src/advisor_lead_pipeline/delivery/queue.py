from __future__ import annotations

import csv
import json
from pathlib import Path

from ..db import connect, transaction
from ..util import utc_now

QUEUE_FIELDS = [
    "lead_id",
    "assigned_advisor",
    "cohort",
    "legal_owner_name",
    "contact_name",
    "contact_role",
    "property_count",
    "properties",
    "score",
    "reasons",
    "phone",
    "email",
    "allowed_channels",
    "source_dates",
    "association_confidence",
]


def export_queue(db_path: str | Path, output_path: str | Path) -> int:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    exported_ids: list[str] = []
    try:
        leads = conn.execute(
            """
            SELECT l.*, legal.canonical_name AS legal_owner_name,
                   target.canonical_name AS contact_name
            FROM leads l
            JOIN owners legal ON legal.id=l.owner_id
            JOIN owners target ON target.id=l.target_person_id
            WHERE l.in_current_build=1 AND l.review_decision='approve' AND l.assigned_advisor<>''
            ORDER BY l.score DESC, l.id
            """
        ).fetchall()
        output_rows: list[dict[str, object]] = []
        for lead in leads:
            contacts = conn.execute(
                """
                SELECT c.*,
                       (SELECT COUNT(DISTINCT c2.person_owner_id) FROM contacts c2
                        WHERE c2.contact_type=c.contact_type
                          AND c2.normalized_value=c.normalized_value) AS person_count
                FROM contacts c
                WHERE c.person_owner_id=? AND c.association_confidence>=0.8
                  AND c.validated_at IS NOT NULL AND c.suppression_status='clear'
                ORDER BY c.association_confidence DESC, c.id
                """,
                (lead["target_person_id"],),
            ).fetchall()
            usable = [row for row in contacts if row["person_count"] == 1]
            phones = [
                row
                for row in usable
                if row["contact_type"] == "phone" and row["dnc_status"] == "clear"
            ]
            emails = [
                row
                for row in usable
                if row["contact_type"] == "email" and "email" in lead["allowed_channels"].split(",")
            ]
            allowed_channels: list[str] = []
            if phones:
                allowed_channels.append("phone")
            if emails:
                allowed_channels.append("email")
            if not allowed_channels:
                continue
            properties = conn.execute(
                """
                SELECT DISTINCT p.address_line1, p.unit, p.city, p.state, p.postal_code
                FROM properties p JOIN relationships r ON r.property_id=p.id
                WHERE r.owner_id=? ORDER BY p.address_line1
                """,
                (lead["owner_id"],),
            ).fetchall()
            observations = conn.execute(
                "SELECT source, observed_at FROM observations WHERE owner_id=? ORDER BY observed_at DESC",
                (lead["owner_id"],),
            ).fetchall()
            role = "recorded_owner"
            if lead["target_person_id"] != lead["owner_id"]:
                relation = conn.execute(
                    """
                    SELECT role FROM relationships WHERE owner_id=? AND related_owner_id=?
                    ORDER BY confidence DESC LIMIT 1
                    """,
                    (lead["target_person_id"], lead["owner_id"]),
                ).fetchone()
                role = relation["role"] if relation else "unknown"
            confidence_values = [float(row["association_confidence"]) for row in usable]
            output_rows.append(
                {
                    "lead_id": lead["id"],
                    "assigned_advisor": lead["assigned_advisor"],
                    "cohort": lead["cohort"],
                    "legal_owner_name": lead["legal_owner_name"],
                    "contact_name": lead["contact_name"],
                    "contact_role": role,
                    "property_count": lead["property_count"],
                    "properties": "; ".join(
                        " ".join(
                            part
                            for part in (
                                prop["address_line1"],
                                prop["unit"],
                                prop["city"],
                                prop["state"],
                                prop["postal_code"],
                            )
                            if part
                        )
                        for prop in properties
                    ),
                    "score": lead["score"],
                    "reasons": "; ".join(json.loads(lead["reasons_json"])),
                    "phone": phones[0]["contact_value"] if phones else "",
                    "email": emails[0]["contact_value"] if emails else "",
                    "allowed_channels": ",".join(allowed_channels),
                    "source_dates": "; ".join(
                        f"{row['source']}:{row['observed_at']}" for row in observations[:5]
                    ),
                    "association_confidence": max(confidence_values) if confidence_values else 0,
                }
            )
            exported_ids.append(lead["id"])
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=QUEUE_FIELDS)
            writer.writeheader()
            writer.writerows(output_rows)
    finally:
        conn.close()
    if exported_ids:
        with transaction(db_path) as conn:
            conn.executemany(
                "UPDATE leads SET workflow_status='queued', updated_at=? WHERE id=?",
                [(utc_now(), lead_id) for lead_id in exported_ids],
            )
    return len(exported_ids)
