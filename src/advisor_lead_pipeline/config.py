from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MarketConfig:
    allowed_markets: tuple[str, ...] = ()
    allowed_property_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineConfig:
    min_ownership_confidence: float = 0.80
    min_representative_confidence: float = 0.80
    portfolio_min_properties: int = 2
    portfolio_max_properties: int = 20
    cohort_limit: int = 100
    fresh_days: int = 45


@dataclass(frozen=True)
class EnrichmentConfig:
    provider: str = "demo"
    operation: str = "person_skip_trace"
    adapter_version: str = "1"
    estimated_cost_per_request: float = 0.02
    budget_cap: float = 0.0
    currency: str = "USD"


@dataclass(frozen=True)
class AppConfig:
    market: MarketConfig = MarketConfig()
    pipeline: PipelineConfig = PipelineConfig()
    enrichment: EnrichmentConfig = EnrichmentConfig()


def load_config(path: str | Path) -> AppConfig:
    with Path(path).open("rb") as handle:
        raw = tomllib.load(handle)
    market = raw.get("market", {})
    pipeline = raw.get("pipeline", {})
    enrichment = raw.get("enrichment", {})
    return AppConfig(
        market=MarketConfig(
            allowed_markets=tuple(market.get("allowed_markets", ())),
            allowed_property_types=tuple(market.get("allowed_property_types", ())),
        ),
        pipeline=PipelineConfig(**pipeline),
        enrichment=EnrichmentConfig(**enrichment),
    )
