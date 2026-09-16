# Pilot operations

## Stage gates

1. Import the property source and a fresh current-client/suppression snapshot.
2. Inspect import rejects and source counts before building leads.
3. Resolve company member/manager evidence. A registered agent alone is insufficient.
4. Review the dry-run cohort and estimated enrichment cost.
5. Configure an explicit non-zero budget only for a verified provider adapter.
6. Reconcile ambiguous paid timeouts before rerunning them.
7. Import human review decisions before producing the advisor queue.
8. Import dispositions using unique event IDs; apply opt-outs immediately.

## Paid-run stop conditions

Stop the run when the next reservation would exceed the cap, the provider schema differs from a
frozen fixture, response rows lack request IDs, billing is ambiguous, or source/channel terms
cannot be represented. Do not retry an ambiguous charged request. Leave it in
`needs_reconciliation` until the provider job or request ID is checked.

## First live-adapter verification

Use at most a small, explicitly approved batch. Record the endpoint/version, normalized input,
provider request ID, returned association evidence, charge rule, actual charge, and retention
limit. Freeze a redacted synthetic response fixture before increasing volume.
