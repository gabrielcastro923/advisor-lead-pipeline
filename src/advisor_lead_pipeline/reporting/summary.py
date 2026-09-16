from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ..db import connect
from ..pipeline.budget import budget_snapshot
from ..util import utc_now


def _group_counts(conn, query: str) -> dict[str, int]:
    return {row[0]: int(row[1]) for row in conn.execute(query).fetchall()}


def build_summary(db_path: str | Path) -> dict:
    conn = connect(db_path)
    try:
        totals = {
            "properties_ingested": conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0],
            "unique_legal_owners": conn.execute(
                """
                SELECT COUNT(DISTINCT owner_id) FROM relationships WHERE property_id IS NOT NULL
                """
            ).fetchone()[0],
            "leads": conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
            "resolved_targets": conn.execute(
                "SELECT COUNT(*) FROM leads WHERE target_person_id IS NOT NULL"
            ).fetchone()[0],
            "enriched_targets": conn.execute(
                """
                SELECT COUNT(DISTINCT l.id) FROM leads l JOIN contacts c
                    ON c.person_owner_id=l.target_person_id
                WHERE c.association_confidence>=0.8 AND c.validated_at IS NOT NULL
                """
            ).fetchone()[0],
            "approved": conn.execute(
                "SELECT COUNT(*) FROM leads WHERE review_decision='approve'"
            ).fetchone()[0],
            "queued": conn.execute(
                "SELECT COUNT(*) FROM leads WHERE workflow_status='queued'"
            ).fetchone()[0],
            "attempted": conn.execute("SELECT COUNT(DISTINCT lead_id) FROM outcomes").fetchone()[0],
            "right_party": conn.execute(
                """
                SELECT COUNT(DISTINCT lead_id) FROM outcomes
                WHERE outcome IN ('right_party','qualified_conversation','appointment','won')
                """
            ).fetchone()[0],
            "qualified_conversation": conn.execute(
                "SELECT COUNT(DISTINCT lead_id) FROM outcomes WHERE outcome='qualified_conversation'"
            ).fetchone()[0],
            "appointment": conn.execute(
                "SELECT COUNT(DISTINCT lead_id) FROM outcomes WHERE outcome='appointment'"
            ).fetchone()[0],
            "won": conn.execute(
                "SELECT COUNT(DISTINCT lead_id) FROM outcomes WHERE outcome='won'"
            ).fetchone()[0],
        }
        spend = budget_snapshot(conn)
        by_cohort = _group_counts(conn, "SELECT cohort, COUNT(*) FROM leads GROUP BY cohort")
        by_status = _group_counts(
            conn, "SELECT workflow_status, COUNT(*) FROM leads GROUP BY workflow_status"
        )
        by_outcome = _group_counts(conn, "SELECT outcome, COUNT(*) FROM outcomes GROUP BY outcome")
        cohort_outcomes: dict[str, dict[str, int]] = defaultdict(dict)
        for row in conn.execute(
            """
            SELECT l.cohort, o.outcome, COUNT(DISTINCT o.lead_id)
            FROM outcomes o JOIN leads l ON l.id=o.lead_id
            GROUP BY l.cohort, o.outcome
            """
        ).fetchall():
            cohort_outcomes[row[0]][row[1]] = int(row[2])
        return {
            "generated_at": utc_now(),
            "totals": totals,
            "leads_by_cohort": by_cohort,
            "leads_by_status": by_status,
            "outcomes": by_outcome,
            "outcomes_by_cohort": dict(cohort_outcomes),
            "spend": {
                "charged": spend.charged,
                "open_reservations": spend.open_reservations,
                "committed": spend.committed,
                "currency": "USD",
            },
        }
    finally:
        conn.close()


def write_summary(db_path: str | Path, output_dir: str | Path) -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    summary = build_summary(db_path)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    totals = summary["totals"]
    spend = summary["spend"]
    lines = [
        "# Advisor lead pipeline summary",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Funnel",
        "",
        "| Stage | Count |",
        "|---|---:|",
    ]
    for label, count in totals.items():
        lines.append(f"| {label.replace('_', ' ').title()} | {count:,} |")
    lines.extend(["", "## Leads by cohort", "", "| Cohort | Count |", "|---|---:|"])
    for label, count in sorted(summary["leads_by_cohort"].items()):
        lines.append(f"| {label} | {count:,} |")
    lines.extend(["", "## Workflow status", "", "| Status | Count |", "|---|---:|"])
    for label, count in sorted(summary["leads_by_status"].items()):
        lines.append(f"| {label} | {count:,} |")
    lines.extend(
        [
            "",
            "## Spend",
            "",
            f"Charged: ${spend['charged']:.4f} {spend['currency']}",
            f"Open reservations: ${spend['open_reservations']:.4f} {spend['currency']}",
            f"Committed: ${spend['committed']:.4f} {spend['currency']}",
            "",
            "No outreach or CRM writes are performed by this pipeline.",
        ]
    )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
