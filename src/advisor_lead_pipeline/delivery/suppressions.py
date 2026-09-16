from __future__ import annotations

import csv
from pathlib import Path

from ..db import transaction
from ..normalization import normalize_email, normalize_name, normalize_phone
from ..util import stable_id, utc_now


def _normalize(scope: str, value: str) -> str:
    if scope in {"owner", "company"}:
        if value.startswith("own_"):
            return value
        return normalize_name(value)
    if "@" in value:
        return normalize_email(value)
    return normalize_phone(value)


def import_suppressions(db_path: str | Path, input_path: str | Path) -> int:
    inserted = 0
    with Path(input_path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    with transaction(db_path) as conn:
        for row in rows:
            scope = (row.get("scope") or "").strip().lower()
            if scope not in {"owner", "company", "contact"}:
                raise ValueError(f"invalid suppression scope {scope!r}")
            normalized = _normalize(scope, (row.get("value") or "").strip())
            reason = (row.get("reason") or "unspecified").strip()
            suppression_id = stable_id("sup", scope, normalized, reason)
            exists = conn.execute(
                "SELECT 1 FROM suppressions WHERE id=?", (suppression_id,)
            ).fetchone()
            conn.execute(
                """
                INSERT INTO suppressions(
                    id, scope, normalized_value, reason, source, active, created_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(id) DO UPDATE SET active=1, source=excluded.source
                """,
                (
                    suppression_id,
                    scope,
                    normalized,
                    reason,
                    (row.get("source") or "manual").strip(),
                    utc_now(),
                ),
            )
            inserted += int(not exists)
            if scope == "contact":
                conn.execute(
                    """
                    UPDATE contacts SET suppression_status='suppressed', updated_at=?
                    WHERE normalized_value=?
                    """,
                    (utc_now(), normalized),
                )
    return inserted
