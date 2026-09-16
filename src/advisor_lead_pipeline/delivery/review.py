from __future__ import annotations

import csv
import json
from pathlib import Path

from ..db import connect, transaction
from ..util import utc_now

REVIEW_FIELDS = [
    "lead_id",
    "cohort",
    "legal_owner_name",
    "owner_kind",
    "representative_name",
    "representative_role",
    "property_count",
    "properties",
    "score",
    "score_reasons",
    "resolution_status",
    "allowed_channels",
    "phone",
    "email",
    "shared_contact",
    "review_decision",
    "assigned_advisor",
    "reviewer",
    "review_notes",
]


def export_review(db_path: str | Path, output_path: str | Path) -> int:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        leads = conn.execute(
            """
            SELECT l.*, legal.canonical_name AS legal_owner_name, legal.owner_kind,
                   target.canonical_name AS representative_name
            FROM leads l
            JOIN owners legal ON legal.id=l.owner_id
            LEFT JOIN owners target ON target.id=l.target_person_id
            ORDER BY l.cohort, l.score DESC, l.id
            """
        ).fetchall()
        rows: list[dict[str, object]] = []
        for lead in leads:
            properties = conn.execute(
                """
                SELECT DISTINCT p.address_line1, p.unit, p.city, p.state, p.postal_code
                FROM properties p JOIN relationships r ON r.property_id=p.id
                WHERE r.owner_id=? ORDER BY p.address_line1
                """,
                (lead["owner_id"],),
            ).fetchall()
            contacts = conn.execute(
                """
                SELECT c.*,
                       (SELECT COUNT(DISTINCT c2.person_owner_id) FROM contacts c2
                        WHERE c2.contact_type=c.contact_type
                          AND c2.normalized_value=c.normalized_value) AS person_count
                FROM contacts c WHERE c.person_owner_id=?
                ORDER BY c.association_confidence DESC, c.id
                """,
                (lead["target_person_id"],),
            ).fetchall()
            phones = [row["contact_value"] for row in contacts if row["contact_type"] == "phone"]
            emails = [row["contact_value"] for row in contacts if row["contact_type"] == "email"]
            shared = any(row["person_count"] > 1 for row in contacts)
            representative_role = ""
            if lead["target_person_id"] and lead["target_person_id"] != lead["owner_id"]:
                role = conn.execute(
                    """
                    SELECT role FROM relationships
                    WHERE owner_id=? AND related_owner_id=?
                    ORDER BY confidence DESC LIMIT 1
                    """,
                    (lead["target_person_id"], lead["owner_id"]),
                ).fetchone()
                representative_role = role["role"] if role else ""
            formatted_properties = "; ".join(
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
            )
            rows.append(
                {
                    "lead_id": lead["id"],
                    "cohort": lead["cohort"],
                    "legal_owner_name": lead["legal_owner_name"],
                    "owner_kind": lead["owner_kind"],
                    "representative_name": lead["representative_name"] or "",
                    "representative_role": representative_role,
                    "property_count": lead["property_count"],
                    "properties": formatted_properties,
                    "score": lead["score"],
                    "score_reasons": "; ".join(json.loads(lead["reasons_json"])),
                    "resolution_status": lead["workflow_status"],
                    "allowed_channels": lead["allowed_channels"],
                    "phone": " | ".join(phones),
                    "email": " | ".join(emails),
                    "shared_contact": "yes" if shared else "",
                    "review_decision": lead["review_decision"],
                    "assigned_advisor": lead["assigned_advisor"],
                    "reviewer": lead["reviewer"],
                    "review_notes": lead["review_notes"],
                }
            )
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return len(rows)
    finally:
        conn.close()


def import_review(db_path: str | Path, input_path: str | Path) -> tuple[int, int]:
    approved = 0
    rejected = 0
    with Path(input_path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    with transaction(db_path) as conn:
        for row in rows:
            decision = (row.get("review_decision") or "").strip().lower()
            if not decision:
                continue
            if decision not in {"approve", "reject"}:
                raise ValueError(f"unsupported review_decision {decision!r}")
            lead_id = (row.get("lead_id") or "").strip()
            lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not lead:
                raise ValueError(f"unknown lead_id {lead_id!r}")
            advisor = (row.get("assigned_advisor") or "").strip()
            if decision == "approve":
                if lead["workflow_status"] != "ready_for_review":
                    raise ValueError(
                        f"lead {lead_id} cannot be approved from {lead['workflow_status']}"
                    )
                if not advisor:
                    raise ValueError(f"lead {lead_id} requires assigned_advisor")
                approved += 1
                status = "approved"
            else:
                rejected += 1
                status = "rejected"
            conn.execute(
                """
                UPDATE leads SET review_decision=?, review_notes=?, reviewer=?,
                    assigned_advisor=?, workflow_status=?, updated_at=? WHERE id=?
                """,
                (
                    decision,
                    (row.get("review_notes") or "").strip(),
                    (row.get("reviewer") or "").strip(),
                    advisor,
                    status,
                    utc_now(),
                    lead_id,
                ),
            )
    return approved, rejected
