from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..config import AppConfig
from ..db import transaction
from ..ownership.resolver import resolve_target_person
from ..util import canonical_json, stable_id, utc_now
from .ranking import RankInput, observation_flags, rank_owner


@dataclass(frozen=True)
class BuildStats:
    candidates: int
    leads_upserted: int
    excluded: int
    needs_ownership_review: int
    ready_for_enrichment: int


def build_leads(db_path: str | Path, config: AppConfig) -> BuildStats:
    candidates: list[dict] = []
    with transaction(db_path) as conn:
        conn.execute("UPDATE leads SET in_current_build=0")
        owner_rows = conn.execute(
            """
            SELECT o.*, COUNT(DISTINCT r.property_id) AS property_count,
                   MIN(r.confidence) AS ownership_confidence
            FROM owners o
            JOIN relationships r ON r.owner_id=o.id AND r.property_id IS NOT NULL
            GROUP BY o.id
            """
        ).fetchall()
        for owner in owner_rows:
            property_rows = conn.execute(
                """
                SELECT DISTINCT p.*
                FROM properties p
                JOIN relationships r ON r.property_id=p.id
                WHERE r.owner_id=?
                """,
                (owner["id"],),
            ).fetchall()
            property_count = len(property_rows)
            markets = {row["market"] for row in property_rows}
            property_types = {row["property_type"] for row in property_rows}
            if config.market.allowed_markets and not markets.intersection(
                config.market.allowed_markets
            ):
                continue
            if config.market.allowed_property_types and not property_types.intersection(
                config.market.allowed_property_types
            ):
                continue
            observations = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM observations WHERE owner_id=?", (owner["id"],)
                ).fetchall()
            ]
            rental_signal, max_days, absentee, email_allowed, newest = observation_flags(
                observations
            )
            portfolio_eligible = (
                config.pipeline.portfolio_min_properties
                <= property_count
                <= config.pipeline.portfolio_max_properties
            )
            if rental_signal:
                cohort = "rental_signal"
            elif portfolio_eligible:
                cohort = "small_portfolio"
            else:
                continue
            ranking = rank_owner(
                RankInput(
                    property_count=property_count,
                    rental_signal=rental_signal,
                    max_days_on_market=max_days,
                    absentee=absentee,
                    newest_observation=newest,
                ),
                fresh_days=config.pipeline.fresh_days,
            )
            resolution = resolve_target_person(
                conn,
                owner["id"],
                owner["owner_kind"],
                float(owner["ownership_confidence"] or 0),
                min_ownership_confidence=config.pipeline.min_ownership_confidence,
                min_representative_confidence=config.pipeline.min_representative_confidence,
            )
            status = resolution.status
            if owner["current_client"]:
                status = "excluded_current_client"
            suppressed = conn.execute(
                """
                SELECT 1 FROM suppressions
                WHERE active=1 AND scope IN ('owner', 'company')
                  AND normalized_value IN (?, ?)
                LIMIT 1
                """,
                (owner["id"], owner["normalized_name"]),
            ).fetchone()
            if suppressed:
                status = "excluded_suppressed"
            candidates.append(
                {
                    "owner_id": owner["id"],
                    "target_person_id": resolution.target_person_id,
                    "cohort": cohort,
                    "score": ranking.score,
                    "reasons": ranking.reasons + (resolution.reason,),
                    "property_count": property_count,
                    "workflow_status": status,
                    "allowed_channels": "phone,direct_mail" + (",email" if email_allowed else ""),
                }
            )

        selected: list[dict] = []
        grouped: dict[str, list[dict]] = defaultdict(list)
        for candidate in candidates:
            grouped[candidate["cohort"]].append(candidate)
        for cohort_items in grouped.values():
            cohort_items.sort(key=lambda item: (-item["score"], item["owner_id"]))
            selected.extend(cohort_items[: config.pipeline.cohort_limit])

        counts = defaultdict(int)
        now = utc_now()
        for candidate in selected:
            lead_id = stable_id("lead", candidate["owner_id"], candidate["cohort"])
            existed = conn.execute("SELECT 1 FROM leads WHERE id=?", (lead_id,)).fetchone()
            conn.execute(
                """
                INSERT INTO leads(
                    id, owner_id, target_person_id, cohort, score, reasons_json,
                    property_count, workflow_status, allowed_channels, in_current_build,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    target_person_id=excluded.target_person_id, score=excluded.score,
                    reasons_json=excluded.reasons_json, property_count=excluded.property_count,
                    workflow_status=CASE
                        WHEN leads.review_decision='approve' THEN leads.workflow_status
                        ELSE excluded.workflow_status
                    END,
                    allowed_channels=excluded.allowed_channels, in_current_build=1,
                    updated_at=excluded.updated_at
                """,
                (
                    lead_id,
                    candidate["owner_id"],
                    candidate["target_person_id"],
                    candidate["cohort"],
                    candidate["score"],
                    canonical_json(candidate["reasons"]),
                    candidate["property_count"],
                    candidate["workflow_status"],
                    candidate["allowed_channels"],
                    now,
                    now,
                ),
            )
            counts["leads_upserted"] += int(not existed)
            if candidate["workflow_status"].startswith("excluded_"):
                counts["excluded"] += 1
            elif candidate["workflow_status"] == "needs_ownership_review":
                counts["needs_ownership_review"] += 1
            elif candidate["workflow_status"] == "ready_for_enrichment":
                counts["ready_for_enrichment"] += 1

    return BuildStats(
        candidates=len(candidates),
        leads_upserted=counts["leads_upserted"],
        excluded=counts["excluded"],
        needs_ownership_review=counts["needs_ownership_review"],
        ready_for_enrichment=counts["ready_for_enrichment"],
    )
