from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ..models import EnrichmentRequest, EnrichmentResult


class Enricher(Protocol):
    provider: str
    adapter_version: str

    def enrich(self, requests: Sequence[EnrichmentRequest]) -> Sequence[EnrichmentResult]:
        """Return results correlated by the original request_id, never by list position."""
        ...
