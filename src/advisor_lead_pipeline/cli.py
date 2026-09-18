from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .config import load_config
from .db import initialize
from .delivery.outcomes import import_outcomes
from .delivery.queue import export_queue
from .delivery.review import export_review, import_review
from .delivery.suppressions import import_suppressions
from .enrichment.demo import DemoEnricher
from .pipeline.builder import build_leads
from .pipeline.enrich import run_enrichment
from .reporting.summary import write_summary
from .sources.csv_source import import_csv
from .sources.maricopa import import_maricopa
from .sources.zillow_snapshot import import_zillow_snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]


def _print(value) -> None:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    print(json.dumps(value, indent=2, sort_keys=True))


def _demo_review(input_path: Path) -> None:
    with input_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    fieldnames = list(rows[0].keys()) if rows else []
    for row in rows:
        if row["resolution_status"] == "ready_for_review" and not row["shared_contact"]:
            row["review_decision"] = "approve"
            row["assigned_advisor"] = "Demo Advisor"
            row["reviewer"] = "Synthetic Demo"
            row["review_notes"] = "Synthetic fixture approval; not a production decision."
    with input_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_demo(workspace: Path) -> dict:
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "pipeline.sqlite3"
    fixtures = REPO_ROOT / "fixtures"
    config_path = REPO_ROOT / "config" / "pilot.example.toml"
    initialize(db_path)
    imported = import_csv(db_path, fixtures / "demo_properties.csv", workspace / "rejects.csv")
    config = load_config(config_path)
    built = build_leads(db_path, config)
    enriched = run_enrichment(
        db_path, config.enrichment, DemoEnricher(fixtures / "demo_contacts.csv")
    )
    review_path = workspace / "review.csv"
    export_review(db_path, review_path)
    _demo_review(review_path)
    approved, rejected = import_review(db_path, review_path)
    queue_count = export_queue(db_path, workspace / "advisor_queue.csv")
    summary = write_summary(db_path, workspace)
    return {
        "workspace": str(workspace.resolve()),
        "import": asdict(imported),
        "build": asdict(built),
        "enrichment": asdict(enriched),
        "review": {"approved": approved, "rejected": rejected},
        "advisor_queue": queue_count,
        "summary": summary,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="advisor-leads")
    commands = root.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Initialize or migrate a local SQLite database")
    init.add_argument("--db", required=True)

    canonical = commands.add_parser("import-csv", help="Import canonical property-owner CSV")
    canonical.add_argument("--db", required=True)
    canonical.add_argument("--input", required=True)
    canonical.add_argument("--rejects")

    maricopa = commands.add_parser("import-maricopa", help="Stream Maricopa Residential Master")
    maricopa.add_argument("--db", required=True)
    maricopa.add_argument("--input", required=True)
    maricopa.add_argument("--city", required=True)
    maricopa.add_argument("--entity-only", action="store_true")
    maricopa.add_argument("--limit", type=int)
    maricopa.add_argument("--as-of")

    zillow = commands.add_parser(
        "import-zillow-snapshot",
        help="Import an authorized Zillow HTML/JSON snapshot without making network requests",
    )
    zillow.add_argument("--db", required=True)
    zillow.add_argument("--input", required=True)
    zillow.add_argument("--authorization-reference", required=True)
    zillow.add_argument("--observed-at")

    build = commands.add_parser("build-leads", help="Build deterministic lead cohorts")
    build.add_argument("--db", required=True)
    build.add_argument("--config", required=True)

    enrich = commands.add_parser("enrich-demo", help="Run offline fixture enrichment")
    enrich.add_argument("--db", required=True)
    enrich.add_argument("--config", required=True)
    enrich.add_argument("--fixture", required=True)

    review_export = commands.add_parser("export-review", help="Export human review sheet")
    review_export.add_argument("--db", required=True)
    review_export.add_argument("--output", required=True)

    review_import = commands.add_parser("import-review", help="Import review decisions")
    review_import.add_argument("--db", required=True)
    review_import.add_argument("--input", required=True)

    queue = commands.add_parser("export-queue", help="Export approved advisor queue")
    queue.add_argument("--db", required=True)
    queue.add_argument("--output", required=True)

    suppressions = commands.add_parser(
        "import-suppressions", help="Import owner/contact exclusions"
    )
    suppressions.add_argument("--db", required=True)
    suppressions.add_argument("--input", required=True)

    outcomes = commands.add_parser("import-outcomes", help="Import idempotent advisor dispositions")
    outcomes.add_argument("--db", required=True)
    outcomes.add_argument("--input", required=True)

    report = commands.add_parser("report", help="Write funnel and spend summaries")
    report.add_argument("--db", required=True)
    report.add_argument("--output-dir", required=True)

    demo = commands.add_parser("demo", help="Run the synthetic end-to-end pilot")
    demo.add_argument("--workspace", default="var/demo")
    return root


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.command == "init":
        initialize(args.db)
        _print({"database": str(Path(args.db).resolve()), "status": "ready"})
    elif args.command == "import-csv":
        initialize(args.db)
        _print(import_csv(args.db, args.input, args.rejects))
    elif args.command == "import-maricopa":
        initialize(args.db)
        _print(
            import_maricopa(
                args.db,
                args.input,
                city=args.city,
                entity_only=args.entity_only,
                limit=args.limit,
                as_of=args.as_of,
            )
        )
    elif args.command == "import-zillow-snapshot":
        initialize(args.db)
        _print(
            import_zillow_snapshot(
                args.db,
                args.input,
                authorization_reference=args.authorization_reference,
                observed_at=args.observed_at,
            )
        )
    elif args.command == "build-leads":
        initialize(args.db)
        _print(build_leads(args.db, load_config(args.config)))
    elif args.command == "enrich-demo":
        initialize(args.db)
        config = load_config(args.config)
        _print(run_enrichment(args.db, config.enrichment, DemoEnricher(args.fixture)))
    elif args.command == "export-review":
        _print({"rows": export_review(args.db, args.output), "output": args.output})
    elif args.command == "import-review":
        approved, rejected = import_review(args.db, args.input)
        _print({"approved": approved, "rejected": rejected})
    elif args.command == "export-queue":
        _print({"rows": export_queue(args.db, args.output), "output": args.output})
    elif args.command == "import-suppressions":
        _print({"inserted": import_suppressions(args.db, args.input)})
    elif args.command == "import-outcomes":
        inserted, duplicates = import_outcomes(args.db, args.input)
        _print({"inserted": inserted, "duplicates": duplicates})
    elif args.command == "report":
        _print(write_summary(args.db, args.output_dir))
    elif args.command == "demo":
        _print(run_demo(Path(args.workspace)))
    else:
        raise AssertionError(args.command)


if __name__ == "__main__":
    main(sys.argv[1:])
