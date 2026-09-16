# First iteration: Phoenix / Maricopa dry run

Run date: 2026-09-16

Source snapshot: local Maricopa County Residential Master file, modified 2026-09-01

Mode: Phoenix, `CLASS R1`/`R2`/`R3`, entity/trust names only, no paid enrichment

This report contains aggregates only. The local database and 100-row review file are ignored by
Git and were not published.

## Result

| Stage | Count |
|---|---:|
| Raw data rows scanned | 1,416,731 |
| Phoenix entity/trust-owned target properties accepted | 65,279 |
| Distinct candidate legal owners | 43,095 |
| Owners with 2–20 observed properties | 4,417 |
| Properties held by the 2–20 cohort | 14,305 |
| Current pilot review rows | 100 |
| Representatives resolved with supported evidence | 0 |
| Paid enrichment requests | 0 |
| Advisor-queue rows | 0 |
| Spend | $0 |

The owner-name classifier labeled the source population as 27,773 trusts and 15,322 entities.
These are deterministic name-pattern segments, not verified legal classifications.

## Portfolio distribution

| Observed properties | Owners |
|---:|---:|
| 1 | 38,507 |
| 2–20 | 4,417 |
| 21+ | 171 |

Within the 2–20 cohort, 2,704 owners have exactly two observed properties. The current top 100
have 13–20 properties, average 15.84, and span seven score bands from 46.5 to 55.0. They are 93
entity-name records and seven trust-name records.

## What the run tells us

- There is ample no-cost county inventory for a small-portfolio pilot. Acquisition volume is not
  the immediate constraint.
- The current county file establishes the recorded owner name, not a human member, manager, or
  authorized trust contact. All 100 rows correctly stopped at `needs_ownership_review`.
- The adapter does not infer company jurisdiction from the parcel's state. Jurisdiction and
  company number must come from actual filing evidence.
- No fresh CRM/current-client exclusion snapshot was provided in this run. The zero exclusion
  count is therefore not evidence that these are all net-new prospects.
- The first scoring pass exposed a broad tie among larger portfolios. The ranking was adjusted to
  preserve score separation across 2–20 properties, and reruns now mark old cohort rows outside
  the current build instead of leaving stale review candidates active.

## Recommendation

Proceed with the offline core, but do not pay to enrich or release this list yet. The next
iteration should:

1. import a fresh current-client, existing-lead, opt-out, and suppression snapshot;
2. resolve the 93 entity records in the top 100 to a documented member/manager using the correct
   company registry and preserve the filing evidence;
3. route the seven trust records to recorder/manual ownership review rather than treating a trust
   name as a person;
4. rerun the review export, then estimate the permitted enrichment batch under an explicit cap;
5. add rental-listing signals as the comparison cohort before making any source-performance claim.

Until those gates pass, this output is a research queue—not a call or email list.

## Reproduction

```bash
advisor-leads import-maricopa \
  --db var/maricopa-pilot-v1/pipeline.sqlite3 \
  --input /absolute/path/to/Residential_Master.txt \
  --city Phoenix \
  --entity-only
advisor-leads build-leads \
  --db var/maricopa-pilot-v1/pipeline.sqlite3 \
  --config config/pilot.example.toml
advisor-leads export-review \
  --db var/maricopa-pilot-v1/pipeline.sqlite3 \
  --output var/maricopa-pilot-v1/review.csv
advisor-leads report \
  --db var/maricopa-pilot-v1/pipeline.sqlite3 \
  --output-dir var/maricopa-pilot-v1
```
