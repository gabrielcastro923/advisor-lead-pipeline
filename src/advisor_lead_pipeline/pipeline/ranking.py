from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class RankInput:
    property_count: int
    rental_signal: bool
    max_days_on_market: int
    absentee: bool
    newest_observation: str


@dataclass(frozen=True)
class RankResult:
    score: float
    reasons: tuple[str, ...]


def _age_days(value: str) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0, (datetime.now(UTC) - parsed).days)
    except ValueError:
        return None


def rank_owner(item: RankInput, *, fresh_days: int) -> RankResult:
    score = 0.0
    reasons: list[str] = []
    if item.rental_signal:
        score += 50
        reasons.append("current rental signal +50")
    if item.max_days_on_market >= 21:
        score += 15
        reasons.append("listing active 21+ days +15")
    if item.absentee:
        score += 15
        reasons.append("absentee ownership +15")
    if item.property_count > 1:
        portfolio_points = min(30.0, round(5 + (item.property_count - 2) * 1.5, 1))
        score += portfolio_points
        reasons.append(f"{item.property_count}-property observed portfolio +{portfolio_points}")
    age = _age_days(item.newest_observation)
    if age is not None and age <= fresh_days:
        score += 10
        reasons.append(f"source observed {age} days ago +10")
    return RankResult(round(min(score, 100.0), 1), tuple(reasons))


def observation_flags(rows: list[dict]) -> tuple[bool, int, bool, bool, str]:
    rental_signal = False
    max_days = 0
    absentee = False
    unsolicited_email_allowed = True
    newest = ""
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        rental_signal = rental_signal or row["observation_type"] == "rental_listing"
        max_days = max(max_days, int(payload.get("days_on_market") or 0))
        absentee = absentee or bool(payload.get("absentee"))
        unsolicited_email_allowed = unsolicited_email_allowed and bool(
            row["unsolicited_email_allowed"]
        )
        observed_at = row["observed_at"] or ""
        newest = max(newest, observed_at)
    return rental_signal, max_days, absentee, unsolicited_email_allowed, newest
