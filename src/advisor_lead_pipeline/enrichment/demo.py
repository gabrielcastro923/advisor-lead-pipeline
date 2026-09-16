from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

from ..models import ContactCandidate, EnrichmentRequest, EnrichmentResult
from ..normalization import normalize_name


class DemoEnricher:
    provider = "demo"
    adapter_version = "1"

    def __init__(self, fixture_path: str | Path):
        self.fixture_path = Path(fixture_path)
        self._rows: dict[str, dict[str, str]] = {}
        with self.fixture_path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                self._rows[normalize_name(row.get("match_name") or "")] = row

    def enrich(self, requests: Sequence[EnrichmentRequest]) -> Sequence[EnrichmentResult]:
        results: list[EnrichmentResult] = []
        for request in reversed(requests):
            row = self._rows.get(normalize_name(request.name))
            if row is None:
                results.append(
                    EnrichmentResult(
                        request_id=request.request_id,
                        state="no_match",
                        provider_request_id=f"demo-{request.request_id}",
                        actual_charge=0.0,
                    )
                )
                continue
            state = (row.get("state") or "completed").strip().lower()
            if state == "timeout":
                results.append(
                    EnrichmentResult(
                        request_id=request.request_id,
                        state="timeout",
                        provider_request_id=(row.get("provider_request_id") or "").strip(),
                        error="synthetic ambiguous timeout",
                    )
                )
                continue
            contacts: list[ContactCandidate] = []
            for contact_type in ("phone", "email"):
                value = (row.get(contact_type) or "").strip()
                if not value:
                    continue
                contacts.append(
                    ContactCandidate(
                        contact_type=contact_type,  # type: ignore[arg-type]
                        value=value,
                        association_confidence=float(row.get("association_confidence") or 0),
                        evidence=(row.get("evidence") or "synthetic fixture").strip(),
                        validated=(row.get("validated") or "").strip().lower()
                        in {"1", "true", "yes"},
                        dnc_status=(row.get("dnc_status") or "unknown").strip().lower(),
                    )
                )
            results.append(
                EnrichmentResult(
                    request_id=request.request_id,
                    state="completed" if contacts else "no_match",
                    provider_request_id=(row.get("provider_request_id") or "").strip()
                    or f"demo-{request.request_id}",
                    contacts=tuple(contacts),
                    actual_charge=float(row.get("actual_charge") or 0),
                )
            )
        return results
