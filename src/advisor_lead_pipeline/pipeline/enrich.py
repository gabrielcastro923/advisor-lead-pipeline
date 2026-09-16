from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import EnrichmentConfig
from ..db import transaction
from ..enrichment.base import Enricher
from ..models import EnrichmentRequest, EnrichmentResult
from ..normalization import normalize_email, normalize_phone
from ..util import canonical_json, stable_id, utc_now
from .budget import can_reserve


@dataclass(frozen=True)
class EnrichmentStats:
    eligible: int
    submitted: int
    reused: int
    completed: int
    no_match: int
    needs_reconciliation: int
    budget_blocked: int
    contacts_added: int


def run_enrichment(
    db_path: str | Path, config: EnrichmentConfig, enricher: Enricher
) -> EnrichmentStats:
    pending: list[EnrichmentRequest] = []
    counters = {
        "eligible": 0,
        "submitted": 0,
        "reused": 0,
        "completed": 0,
        "no_match": 0,
        "needs_reconciliation": 0,
        "budget_blocked": 0,
        "contacts_added": 0,
    }
    with transaction(db_path) as conn:
        leads = conn.execute(
            """
            SELECT l.*, o.canonical_name, o.mailing_line1, o.mailing_city,
                   o.mailing_state, o.mailing_postal_code
            FROM leads l JOIN owners o ON o.id=l.target_person_id
            WHERE l.in_current_build=1 AND l.workflow_status='ready_for_enrichment'
            ORDER BY l.score DESC, l.id
            """
        ).fetchall()
        counters["eligible"] = len(leads)
        for lead in leads:
            normalized_input = canonical_json(
                {
                    "name": lead["canonical_name"],
                    "mailing_line1": lead["mailing_line1"],
                    "mailing_city": lead["mailing_city"],
                    "mailing_state": lead["mailing_state"],
                    "mailing_postal_code": lead["mailing_postal_code"],
                }
            )
            input_hash = stable_id("input", normalized_input, length=32)
            existing = conn.execute(
                """
                SELECT * FROM jobs WHERE provider=? AND operation=? AND adapter_version=?
                    AND normalized_input_hash=?
                """,
                (config.provider, config.operation, config.adapter_version, input_hash),
            ).fetchone()
            if existing and existing["state"] in {"completed", "no_match"}:
                counters["reused"] += 1
                continue
            if existing and existing["state"] == "needs_reconciliation":
                counters["needs_reconciliation"] += 1
                continue
            if not can_reserve(
                conn, config.estimated_cost_per_request, config.budget_cap, config.currency
            ):
                counters["budget_blocked"] += 1
                continue
            job_id = stable_id(
                "job", config.provider, config.operation, config.adapter_version, input_hash
            )
            now = utc_now()
            conn.execute(
                """
                INSERT INTO jobs(
                    id, lead_id, person_owner_id, provider, operation, adapter_version,
                    normalized_input_hash, state, reserved_amount, currency, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET state='reserved', reserved_amount=excluded.reserved_amount,
                    error='', updated_at=excluded.updated_at
                """,
                (
                    job_id,
                    lead["id"],
                    lead["target_person_id"],
                    config.provider,
                    config.operation,
                    config.adapter_version,
                    input_hash,
                    config.estimated_cost_per_request,
                    config.currency,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO spend_ledger(
                    id, job_id, entry_type, amount, currency, created_at
                ) VALUES (?, ?, 'reserve', ?, ?, ?)
                """,
                (
                    stable_id("spend", job_id, "reserve"),
                    job_id,
                    config.estimated_cost_per_request,
                    config.currency,
                    now,
                ),
            )
            pending.append(
                EnrichmentRequest(
                    request_id=job_id,
                    lead_id=lead["id"],
                    person_owner_id=lead["target_person_id"],
                    name=lead["canonical_name"],
                    mailing_line1=lead["mailing_line1"],
                    mailing_city=lead["mailing_city"],
                    mailing_state=lead["mailing_state"],
                    mailing_postal_code=lead["mailing_postal_code"],
                )
            )
    if not pending:
        return EnrichmentStats(**counters)

    results = list(enricher.enrich(pending))
    results_by_request = {result.request_id: result for result in results}
    counters["submitted"] = len(pending)
    with transaction(db_path) as conn:
        for request in pending:
            result = results_by_request.get(request.request_id)
            if result is None:
                result = EnrichmentResult(
                    request_id=request.request_id,
                    state="timeout",
                    error="provider omitted the original request ID from results",
                )
            now = utc_now()
            if result.state == "timeout":
                conn.execute(
                    """
                    UPDATE jobs SET state='needs_reconciliation', provider_request_id=?,
                        error=?, updated_at=? WHERE id=?
                    """,
                    (result.provider_request_id, result.error, now, request.request_id),
                )
                counters["needs_reconciliation"] += 1
                continue
            final_state = "completed" if result.state == "completed" else "no_match"
            conn.execute(
                """
                UPDATE jobs SET state=?, provider_request_id=?, actual_amount=?, error=?, updated_at=?
                WHERE id=?
                """,
                (
                    final_state,
                    result.provider_request_id,
                    result.actual_charge,
                    result.error,
                    now,
                    request.request_id,
                ),
            )
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (request.request_id,)).fetchone()
            conn.execute(
                """
                INSERT OR IGNORE INTO spend_ledger(
                    id, job_id, entry_type, amount, currency, created_at
                ) VALUES (?, ?, 'release', ?, ?, ?)
                """,
                (
                    stable_id("spend", request.request_id, "release"),
                    request.request_id,
                    float(job["reserved_amount"]),
                    job["currency"],
                    now,
                ),
            )
            if result.actual_charge:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO spend_ledger(
                        id, job_id, entry_type, amount, currency, created_at
                    ) VALUES (?, ?, 'charge', ?, ?, ?)
                    """,
                    (
                        stable_id("spend", request.request_id, "charge"),
                        request.request_id,
                        result.actual_charge,
                        job["currency"],
                        now,
                    ),
                )
            if final_state == "no_match":
                counters["no_match"] += 1
                continue
            counters["completed"] += 1
            for contact in result.contacts:
                normalized = (
                    normalize_phone(contact.value)
                    if contact.contact_type == "phone"
                    else normalize_email(contact.value)
                )
                if not normalized:
                    continue
                contact_id = stable_id(
                    "contact",
                    request.person_owner_id,
                    contact.contact_type,
                    normalized,
                    config.provider,
                )
                existed = conn.execute(
                    "SELECT 1 FROM contacts WHERE id=?", (contact_id,)
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO contacts(
                        id, person_owner_id, contact_type, contact_value, normalized_value,
                        provider, provider_request_id, association_confidence, evidence,
                        validated_at, dnc_checked_at, dnc_status, suppression_status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'clear', ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        contact_value=excluded.contact_value,
                        provider_request_id=excluded.provider_request_id,
                        association_confidence=excluded.association_confidence,
                        evidence=excluded.evidence, validated_at=excluded.validated_at,
                        dnc_checked_at=excluded.dnc_checked_at, dnc_status=excluded.dnc_status,
                        updated_at=excluded.updated_at
                    """,
                    (
                        contact_id,
                        request.person_owner_id,
                        contact.contact_type,
                        contact.value,
                        normalized,
                        config.provider,
                        result.provider_request_id,
                        contact.association_confidence,
                        contact.evidence,
                        now if contact.validated else None,
                        now
                        if contact.contact_type == "phone" and contact.dnc_status != "unknown"
                        else None,
                        contact.dnc_status,
                        now,
                        now,
                    ),
                )
                counters["contacts_added"] += int(not existed)
            usable = conn.execute(
                """
                SELECT 1 FROM contacts
                WHERE person_owner_id=? AND association_confidence>=0.8
                  AND validated_at IS NOT NULL AND suppression_status='clear'
                LIMIT 1
                """,
                (request.person_owner_id,),
            ).fetchone()
            if usable:
                conn.execute(
                    "UPDATE leads SET workflow_status='ready_for_review', updated_at=? WHERE id=?",
                    (now, request.lead_id),
                )
    return EnrichmentStats(**counters)
