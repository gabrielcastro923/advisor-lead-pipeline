from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ..db import transaction
from ..normalization import normalize_address
from ..util import canonical_json, stable_id, utc_now

SOURCE = "zillow_authorized_snapshot"
INACTIVE_STATUSES = {"FOR_SALE", "OFF_MARKET", "PENDING", "SOLD"}


@dataclass(frozen=True)
class ZillowListing:
    source_record_id: str
    address_line1: str
    unit: str
    city: str
    state: str
    postal_code: str
    status: str
    is_rental: bool
    is_active: bool
    price: float | None
    days_on_market: int
    detail_url: str


@dataclass(frozen=True)
class ZillowSnapshotStats:
    listings_parsed: int
    active_rentals: int
    skipped_non_rental: int
    matched: int
    unmatched: int
    ambiguous: int
    observations_inserted: int
    observations_updated: int


class _ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._attrs: dict[str, str] | None = None
        self._chunks: list[str] = []
        self.payloads: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        self._attrs = {key.lower(): value or "" for key, value in attrs}
        self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._attrs is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or self._attrs is None:
            return
        script_type = self._attrs.get("type", "").lower()
        script_id = self._attrs.get("id", "").lower()
        content = html.unescape("".join(self._chunks)).strip()
        if content and (
            "json" in script_type
            or script_id == "__next_data__"
            or "searchpagestate" in content.lower()
        ):
            self.payloads.append(content)
        self._attrs = None
        self._chunks = []


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _full_address_parts(value: str) -> tuple[str, str, str, str] | None:
    match = re.match(
        r"^\s*(?P<street>.+?),\s*(?P<city>[^,]+),\s*(?P<state>[A-Za-z]{2})\s+"
        r"(?P<zip>\d{5}(?:-\d{4})?)\s*$",
        value,
    )
    if not match:
        return None
    return (
        match.group("street"),
        match.group("city"),
        match.group("state").upper(),
        match.group("zip"),
    )


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _integer(value: object) -> int:
    number = _number(value)
    return max(0, int(number)) if number is not None else 0


def _listing_from_dict(item: dict[str, Any]) -> ZillowListing | None:
    status = str(
        item.get("homeStatus")
        or item.get("listingStatus")
        or item.get("statusText")
        or item.get("status")
        or ""
    ).strip()
    listing_type = str(
        item.get("listingType") or item.get("homeType") or item.get("marketingStatus") or ""
    ).strip()
    is_rental = (
        item.get("isForRent") is True or "RENT" in status.upper() or "RENT" in listing_type.upper()
    )
    if not status and is_rental:
        status = "FOR_RENT"
    normalized_status = status.upper().replace("-", "_")

    address = item.get("address")
    if isinstance(address, dict):
        line1 = str(
            address.get("streetAddress")
            or address.get("addressLine1")
            or address.get("street")
            or ""
        ).strip()
        unit = str(address.get("addressLine2") or address.get("unit") or "").strip()
        city = str(address.get("city") or address.get("addressLocality") or "").strip()
        state = str(address.get("state") or address.get("addressRegion") or "").strip().upper()
        postal_code = str(
            address.get("zipcode") or address.get("postalCode") or address.get("zip") or ""
        ).strip()
    else:
        line1 = str(item.get("addressStreet") or item.get("addressLine1") or "").strip()
        unit = str(item.get("addressLine2") or item.get("unit") or "").strip()
        city = str(item.get("addressCity") or item.get("city") or "").strip()
        state = str(item.get("addressState") or item.get("state") or "").strip().upper()
        postal_code = str(
            item.get("addressZipcode") or item.get("zipcode") or item.get("postalCode") or ""
        ).strip()
        if not line1 and isinstance(address, str):
            parts = _full_address_parts(address)
            if parts:
                line1, city, state, postal_code = parts

    record_id = str(item.get("zpid") or item.get("id") or "").strip()
    if not line1 or not city or not state or not postal_code:
        return None
    if not record_id:
        record_id = stable_id(
            "zillow", normalize_address(line1, unit, city, state, postal_code), status
        )
    detail_url = str(item.get("detailUrl") or item.get("url") or "").strip()
    if detail_url.startswith("/"):
        detail_url = "https://www.zillow.com" + detail_url
    return ZillowListing(
        source_record_id=record_id,
        address_line1=line1,
        unit=unit,
        city=city,
        state=state,
        postal_code=postal_code,
        status=normalized_status,
        is_rental=is_rental,
        is_active=is_rental and normalized_status not in INACTIVE_STATUSES,
        price=_number(item.get("unformattedPrice") or item.get("price")),
        days_on_market=_integer(item.get("daysOnZillow") or item.get("daysOnMarket")),
        detail_url=detail_url,
    )


def parse_zillow_snapshot(path: str | Path) -> list[ZillowListing]:
    snapshot = Path(path)
    content = snapshot.read_text(encoding="utf-8", errors="replace").strip()
    payloads: list[str]
    if content.startswith(("{", "[")):
        payloads = [content]
    else:
        collector = _ScriptCollector()
        collector.feed(content)
        payloads = collector.payloads

    listings: dict[str, ZillowListing] = {}
    for payload in payloads:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for item in _walk(data):
            listing = _listing_from_dict(item)
            if listing is not None:
                listings[listing.source_record_id] = listing
    return sorted(listings.values(), key=lambda item: item.source_record_id)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _observed_at(path: Path, provided: str | None) -> str:
    if provided:
        return provided
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(timespec="seconds")


def import_zillow_snapshot(
    db_path: str | Path,
    input_path: str | Path,
    *,
    authorization_reference: str,
    observed_at: str | None = None,
) -> ZillowSnapshotStats:
    if not authorization_reference.strip():
        raise ValueError("authorization_reference is required")
    path = Path(input_path)
    listings = parse_zillow_snapshot(path)
    observed = _observed_at(path, observed_at)
    input_hash = _file_sha256(path)
    counts = {
        "listings_parsed": len(listings),
        "active_rentals": 0,
        "skipped_non_rental": 0,
        "matched": 0,
        "unmatched": 0,
        "ambiguous": 0,
        "observations_inserted": 0,
        "observations_updated": 0,
    }
    import_id = stable_id("imp", SOURCE, input_hash)
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO imports(id, source, input_path, input_sha256, started_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source, input_sha256) DO UPDATE SET
                started_at=excluded.started_at, completed_at=NULL
            """,
            (import_id, SOURCE, str(path.resolve()), input_hash, utc_now()),
        )
        for listing in listings:
            if not listing.is_active:
                counts["skipped_non_rental"] += 1
                continue
            counts["active_rentals"] += 1
            normalized = normalize_address(
                listing.address_line1,
                listing.unit,
                listing.city,
                listing.state,
                listing.postal_code,
            )
            properties = conn.execute(
                "SELECT id FROM properties WHERE normalized_address=?", (normalized,)
            ).fetchall()
            if not properties:
                counts["unmatched"] += 1
                _quarantine(conn, listing, "no matching imported property", authorization_reference)
                continue
            if len(properties) != 1:
                counts["ambiguous"] += 1
                _quarantine(conn, listing, "ambiguous property address", authorization_reference)
                continue
            property_id = properties[0]["id"]
            owners = conn.execute(
                """
                SELECT DISTINCT owner_id FROM relationships
                WHERE property_id=? AND role='recorded_owner'
                """,
                (property_id,),
            ).fetchall()
            if len(owners) != 1:
                counts["ambiguous"] += 1
                _quarantine(conn, listing, "ambiguous recorded owner", authorization_reference)
                continue
            counts["matched"] += 1
            observation_id = stable_id("obs", SOURCE, listing.source_record_id, "rental_listing")
            exists = conn.execute(
                "SELECT 1 FROM observations WHERE id=?", (observation_id,)
            ).fetchone()
            conn.execute(
                """
                INSERT INTO observations(
                    id, property_id, owner_id, source, source_record_id, observation_type,
                    observed_at, source_updated_at, unsolicited_email_allowed,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 'rental_listing', ?, ?, 0, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    property_id=excluded.property_id, owner_id=excluded.owner_id,
                    observed_at=excluded.observed_at, source_updated_at=excluded.source_updated_at,
                    unsolicited_email_allowed=0, payload_json=excluded.payload_json
                """,
                (
                    observation_id,
                    property_id,
                    owners[0]["owner_id"],
                    SOURCE,
                    listing.source_record_id,
                    observed,
                    observed,
                    canonical_json(
                        {
                            "authorization_reference": authorization_reference.strip(),
                            "days_on_market": listing.days_on_market,
                            "detail_url": listing.detail_url,
                            "price": listing.price,
                            "snapshot_sha256": input_hash,
                            "status": listing.status,
                        }
                    ),
                    utc_now(),
                ),
            )
            if exists:
                counts["observations_updated"] += 1
            else:
                counts["observations_inserted"] += 1
        conn.execute(
            """
            UPDATE imports SET completed_at=?, rows_seen=?, rows_accepted=?, rows_rejected=?
            WHERE id=?
            """,
            (
                utc_now(),
                counts["listings_parsed"],
                counts["matched"],
                counts["unmatched"] + counts["ambiguous"],
                import_id,
            ),
        )
    return ZillowSnapshotStats(**counts)


def _quarantine(conn, listing: ZillowListing, reason: str, authorization_reference: str) -> None:
    payload = {
        "address": normalize_address(
            listing.address_line1,
            listing.unit,
            listing.city,
            listing.state,
            listing.postal_code,
        ),
        "authorization_reference": authorization_reference.strip(),
        "status": listing.status,
    }
    conn.execute(
        """
        INSERT OR IGNORE INTO quarantine(
            id, stage, source, source_record_id, reason, payload_json, created_at
        ) VALUES (?, 'source_signal', ?, ?, ?, ?, ?)
        """,
        (
            stable_id("qua", "source_signal", SOURCE, listing.source_record_id, reason),
            SOURCE,
            listing.source_record_id,
            reason,
            canonical_json(payload),
            utc_now(),
        ),
    )
