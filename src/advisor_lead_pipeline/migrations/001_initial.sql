CREATE TABLE imports (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    input_path TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    rows_seen INTEGER NOT NULL DEFAULT 0,
    rows_accepted INTEGER NOT NULL DEFAULT 0,
    rows_rejected INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, input_sha256)
);

CREATE TABLE properties (
    id TEXT PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    county_fips TEXT NOT NULL,
    apn TEXT NOT NULL,
    address_line1 TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    postal_code TEXT NOT NULL,
    normalized_address TEXT NOT NULL,
    property_type TEXT NOT NULL,
    market TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE owners (
    id TEXT PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    owner_kind TEXT NOT NULL CHECK(owner_kind IN ('individual', 'entity', 'trust')),
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    mailing_line1 TEXT NOT NULL DEFAULT '',
    mailing_city TEXT NOT NULL DEFAULT '',
    mailing_state TEXT NOT NULL DEFAULT '',
    mailing_postal_code TEXT NOT NULL DEFAULT '',
    jurisdiction TEXT NOT NULL DEFAULT '',
    company_number TEXT NOT NULL DEFAULT '',
    current_client INTEGER NOT NULL DEFAULT 0 CHECK(current_client IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE relationships (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id),
    property_id TEXT REFERENCES properties(id),
    related_owner_id TEXT REFERENCES owners(id),
    role TEXT NOT NULL,
    evidence_source TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    valid_from TEXT,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    CHECK((property_id IS NOT NULL) != (related_owner_id IS NOT NULL)),
    UNIQUE(owner_id, property_id, related_owner_id, role, evidence_source, source_record_id)
);

CREATE TABLE observations (
    id TEXT PRIMARY KEY,
    property_id TEXT NOT NULL REFERENCES properties(id),
    owner_id TEXT NOT NULL REFERENCES owners(id),
    source TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    observation_type TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_updated_at TEXT,
    unsolicited_email_allowed INTEGER NOT NULL CHECK(unsolicited_email_allowed IN (0, 1)),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source, source_record_id, observation_type)
);

CREATE TABLE leads (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id),
    target_person_id TEXT REFERENCES owners(id),
    cohort TEXT NOT NULL,
    score REAL NOT NULL,
    reasons_json TEXT NOT NULL,
    property_count INTEGER NOT NULL,
    workflow_status TEXT NOT NULL,
    allowed_channels TEXT NOT NULL DEFAULT '',
    review_decision TEXT NOT NULL DEFAULT '',
    review_notes TEXT NOT NULL DEFAULT '',
    reviewer TEXT NOT NULL DEFAULT '',
    assigned_advisor TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(owner_id, cohort)
);

CREATE TABLE contacts (
    id TEXT PRIMARY KEY,
    person_owner_id TEXT NOT NULL REFERENCES owners(id),
    contact_type TEXT NOT NULL CHECK(contact_type IN ('phone', 'email')),
    contact_value TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_request_id TEXT NOT NULL,
    association_confidence REAL NOT NULL CHECK(association_confidence >= 0 AND association_confidence <= 1),
    evidence TEXT NOT NULL,
    validated_at TEXT,
    dnc_checked_at TEXT,
    dnc_status TEXT NOT NULL DEFAULT 'unknown',
    suppression_status TEXT NOT NULL DEFAULT 'clear',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(person_owner_id, contact_type, normalized_value, provider)
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads(id),
    person_owner_id TEXT NOT NULL REFERENCES owners(id),
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    normalized_input_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    provider_job_id TEXT NOT NULL DEFAULT '',
    provider_request_id TEXT NOT NULL DEFAULT '',
    reserved_amount REAL NOT NULL DEFAULT 0,
    actual_amount REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(provider, operation, adapter_version, normalized_input_hash)
);

CREATE TABLE spend_ledger (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    entry_type TEXT NOT NULL CHECK(entry_type IN ('reserve', 'charge', 'release', 'refund')),
    amount REAL NOT NULL CHECK(amount >= 0),
    currency TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, entry_type)
);

CREATE TABLE suppressions (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN ('owner', 'contact', 'company')),
    normalized_value TEXT NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(scope, normalized_value, reason)
);

CREATE TABLE outcomes (
    event_id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads(id),
    outcome TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    advisor TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL
);

CREATE TABLE quarantine (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    source TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(stage, source, source_record_id, reason)
);

CREATE INDEX idx_relationships_property ON relationships(property_id);
CREATE INDEX idx_relationships_related_owner ON relationships(related_owner_id);
CREATE INDEX idx_observations_owner ON observations(owner_id);
CREATE INDEX idx_leads_status ON leads(workflow_status, review_decision);
CREATE INDEX idx_contacts_person ON contacts(person_owner_id);
CREATE INDEX idx_outcomes_lead ON outcomes(lead_id, outcome);
