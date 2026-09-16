from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

OwnerKind = Literal["individual", "entity", "trust"]
RepresentativeRole = Literal["member", "manager", "officer", "registered_agent", "unknown"]


@dataclass(frozen=True)
class PropertyOwnerRecord:
    source: str
    source_record_id: str
    county_fips: str
    apn: str
    address_line1: str
    unit: str
    city: str
    state: str
    postal_code: str
    property_type: str
    market: str
    owner_name: str
    owner_kind: OwnerKind
    owner_mailing_line1: str = ""
    owner_mailing_city: str = ""
    owner_mailing_state: str = ""
    owner_mailing_postal_code: str = ""
    owner_jurisdiction: str = ""
    company_number: str = ""
    owner_role: str = "recorded_owner"
    ownership_confidence: float = 1.0
    representative_name: str = ""
    representative_role: RepresentativeRole = "unknown"
    representative_confidence: float = 0.0
    observed_at: str = ""
    source_updated_at: str = ""
    rental_signal: bool = False
    rental_listed_date: str = ""
    days_on_market: int = 0
    unsolicited_email_allowed: bool = True
    absentee: bool = False
    current_client: bool = False


@dataclass(frozen=True)
class ImportStats:
    rows_seen: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    properties_inserted: int = 0
    owners_inserted: int = 0
    relationships_inserted: int = 0
    observations_inserted: int = 0
    rejects: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class EnrichmentRequest:
    request_id: str
    lead_id: str
    person_owner_id: str
    name: str
    mailing_line1: str
    mailing_city: str
    mailing_state: str
    mailing_postal_code: str


@dataclass(frozen=True)
class ContactCandidate:
    contact_type: Literal["phone", "email"]
    value: str
    association_confidence: float
    evidence: str
    validated: bool = False
    dnc_status: str = "unknown"


@dataclass(frozen=True)
class EnrichmentResult:
    request_id: str
    state: Literal["completed", "no_match", "failed", "timeout"]
    provider_request_id: str = ""
    contacts: tuple[ContactCandidate, ...] = field(default_factory=tuple)
    actual_charge: float = 0.0
    error: str = ""
