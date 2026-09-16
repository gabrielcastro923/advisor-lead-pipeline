from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

SUPPORTED_REPRESENTATIVE_ROLES = {"member", "manager", "officer"}


@dataclass(frozen=True)
class Resolution:
    status: str
    target_person_id: str | None
    reason: str


def resolve_target_person(
    conn: Connection,
    owner_id: str,
    owner_kind: str,
    ownership_confidence: float,
    *,
    min_ownership_confidence: float,
    min_representative_confidence: float,
) -> Resolution:
    if ownership_confidence < min_ownership_confidence:
        return Resolution("needs_ownership_review", None, "low ownership confidence")

    if owner_kind == "individual":
        return Resolution("ready_for_enrichment", owner_id, "recorded individual owner")

    representatives = conn.execute(
        """
        SELECT r.owner_id, r.role, MAX(r.confidence) AS confidence
        FROM relationships r
        WHERE r.related_owner_id = ?
        GROUP BY r.owner_id, r.role
        ORDER BY confidence DESC, r.owner_id
        """,
        (owner_id,),
    ).fetchall()
    supported = [
        row
        for row in representatives
        if row["role"] in SUPPORTED_REPRESENTATIVE_ROLES
        and row["confidence"] >= min_representative_confidence
    ]
    if len(supported) == 1:
        return Resolution(
            "ready_for_enrichment", supported[0]["owner_id"], f"supported {supported[0]['role']}"
        )
    if len(supported) > 1:
        return Resolution("needs_ownership_review", None, "multiple supported representatives")
    if representatives and all(row["role"] == "registered_agent" for row in representatives):
        return Resolution("needs_ownership_review", None, "registered-agent-only evidence")
    return Resolution("needs_ownership_review", None, "no supported representative evidence")
