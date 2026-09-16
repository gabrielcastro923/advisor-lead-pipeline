# Advisor Lead Pipeline

A small, review-first property-to-owner pipeline for producing advisor lead queues without
putting personal data, credentials, or paid-provider responses in source control.

The pilot is intentionally boring infrastructure: Python, SQLite, CSVs, deterministic rules,
and one worker. It does not send outreach or write to a CRM. It keeps parcel ownership,
company representatives, contacts, source permissions, spend, and outcomes as distinct records
so a phone match never becomes proof of ownership or consent.

## What the first version does

- imports a documented canonical property/ownership CSV idempotently;
- streams the Maricopa County Residential Master file through a dedicated adapter;
- preserves source lineage and channel restrictions;
- separates legal owners from LLC representatives and blocks registered-agent-only records;
- ranks rental-signal and small-portfolio cohorts with explainable rules;
- reserves a spend cap before enrichment and deduplicates paid operations;
- includes an offline fixture-based enrichment adapter for end-to-end testing;
- exports a human review file and an advisor queue only after approval;
- imports idempotent dispositions and reports the measurable funnel by cohort.

## Safe quick start

The demo uses synthetic people, addresses, contacts, and outcomes. It makes no network calls.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
advisor-leads demo --workspace var/demo
python -m unittest discover -s tests -v
```

The demo writes:

- `var/demo/pipeline.sqlite3` — local pipeline state;
- `var/demo/review.csv` — all ranked leads and resolution issues;
- `var/demo/advisor_queue.csv` — approved, contactable leads only;
- `var/demo/summary.md` and `summary.json` — funnel and spend report.

All of `var/` is ignored by Git.

## Run a dry Maricopa iteration

Use a local copy of Maricopa County's Residential Master dataset. The source file and all
resulting personal information must stay outside Git.

```bash
advisor-leads init --db var/maricopa/pipeline.sqlite3
advisor-leads import-maricopa \
  --db var/maricopa/pipeline.sqlite3 \
  --input /absolute/path/to/Residential_Master.txt \
  --city Phoenix \
  --entity-only
advisor-leads build-leads \
  --db var/maricopa/pipeline.sqlite3 \
  --config config/pilot.example.toml
advisor-leads export-review \
  --db var/maricopa/pipeline.sqlite3 \
  --output var/maricopa/review.csv
advisor-leads report \
  --db var/maricopa/pipeline.sqlite3 \
  --output-dir var/maricopa
```

This run performs no paid enrichment and creates no outreach. Entity records without supported
member/manager evidence remain in `needs_ownership_review`.

## Canonical import

Start from [`fixtures/demo_properties.csv`](fixtures/demo_properties.csv). Required columns are
`source`, `source_record_id`, `county_fips`, `apn`, `address_line1`, `city`, `state`,
`postal_code`, `market`, `owner_name`, and `owner_kind`. Optional fields carry units, mailing
addresses, company identity, representative evidence, rental signals, client exclusions, and
source channel permissions.

```bash
advisor-leads import-csv \
  --db var/pilot/pipeline.sqlite3 \
  --input /absolute/path/to/properties.csv \
  --rejects var/pilot/rejects.csv
```

## Review workflow

1. Run `export-review`.
2. Fill `review_decision` with `approve` or `reject`; add `assigned_advisor` when approved.
3. Import the file with `import-review`.
4. Run `export-queue`. A row is released only when owner resolution, a usable contact,
   suppression state, source permissions, and advisor approval all pass.
5. Later, import advisor results using the stable `event_id` format in
   [`fixtures/outcomes_template.csv`](fixtures/outcomes_template.csv).

## Live adapters

The repository deliberately contains no guessed BatchData, Tracerfy, or RentCast payloads.
`enrich-demo` proves the contract, request correlation, deduplication, timeout reconciliation,
and budget ledger offline. A live adapter should be added only after its exact account endpoint,
response schema, retention terms, and billing behavior are verified with a permitted small test.

See [`docs/operations.md`](docs/operations.md) and [`SECURITY.md`](SECURITY.md) before a real pilot.
