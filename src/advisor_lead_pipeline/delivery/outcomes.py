from __future__ import annotations

import csv
from pathlib import Path

from ..db import transaction
from ..util import stable_id, utc_now

VALID_OUTCOMES = {
    "wrong_person",
    "unreachable",
    "right_party",
    "not_interested",
    "qualified_conversation",
    "appointment",
    "won",
    "opt_out",
}


def import_outcomes(db_path: str | Path, input_path: str | Path) -> tuple[int, int]:
    inserted = 0
    duplicates = 0
    with Path(input_path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    with transaction(db_path) as conn:
        for row in rows:
            event_id = (row.get("event_id") or "").strip()
            lead_id = (row.get("lead_id") or "").strip()
            outcome = (row.get("outcome") or "").strip().lower()
            if not event_id:
                raise ValueError("outcome event_id is required")
            if outcome not in VALID_OUTCOMES:
                raise ValueError(f"invalid outcome {outcome!r}")
            if not conn.execute("SELECT 1 FROM leads WHERE id=?", (lead_id,)).fetchone():
                raise ValueError(f"unknown lead_id {lead_id!r}")
            exists = conn.execute("SELECT 1 FROM outcomes WHERE event_id=?", (event_id,)).fetchone()
            if exists:
                duplicates += 1
                continue
            conn.execute(
                """
                INSERT INTO outcomes(event_id, lead_id, outcome, occurred_at, advisor, notes, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    lead_id,
                    outcome,
                    (row.get("occurred_at") or utc_now()).strip(),
                    (row.get("advisor") or "").strip(),
                    (row.get("notes") or "").strip(),
                    utc_now(),
                ),
            )
            inserted += 1
            if outcome == "opt_out":
                lead = conn.execute(
                    "SELECT owner_id, target_person_id FROM leads WHERE id=?", (lead_id,)
                ).fetchone()
                suppression_id = stable_id("sup", "owner", lead["owner_id"], "opt_out")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO suppressions(
                        id, scope, normalized_value, reason, source, active, created_at
                    ) VALUES (?, 'owner', ?, 'opt_out', 'outcome_import', 1, ?)
                    """,
                    (suppression_id, lead["owner_id"], utc_now()),
                )
                conn.execute(
                    """
                    UPDATE contacts SET suppression_status='suppressed', updated_at=?
                    WHERE person_owner_id=?
                    """,
                    (utc_now(), lead["target_person_id"]),
                )
                conn.execute(
                    """
                    UPDATE leads SET workflow_status='excluded_suppressed', updated_at=? WHERE id=?
                    """,
                    (utc_now(), lead_id),
                )
    return inserted, duplicates
