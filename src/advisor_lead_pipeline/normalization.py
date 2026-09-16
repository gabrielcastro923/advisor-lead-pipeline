from __future__ import annotations

import re
import unicodedata

from .util import compact_whitespace, stable_id

ENTITY_PATTERN = re.compile(
    r"\b(LLC|L\.?L\.?C\.?|INC(?:ORPORATED)?|CORP(?:ORATION)?|LTD|LP|LLP|"
    r"PARTNERS(?:HIP)?|HOLDINGS?|PROPERTIES|INVESTMENTS?|VENTURES?|CAPITAL|REALTY|"
    r"MANAGEMENT|MGMT)\b",
    re.IGNORECASE,
)


def ascii_fold(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value or "")
        if not unicodedata.combining(char)
    )


def normalize_name(value: str) -> str:
    value = ascii_fold(compact_whitespace(value)).upper()
    return re.sub(r"[^A-Z0-9 ]+", "", value)


def normalize_address(
    line1: str,
    unit: str = "",
    city: str = "",
    state: str = "",
    postal_code: str = "",
) -> str:
    parts = [line1, unit, city, state, postal_code[:5]]
    joined = " | ".join(compact_whitespace(ascii_fold(part)).upper() for part in parts)
    return re.sub(r"[^A-Z0-9| ]+", "", joined)


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def normalize_email(value: str) -> str:
    return compact_whitespace(value).lower()


def classify_owner_kind(name: str) -> str:
    upper = normalize_name(name)
    if "TRUST" in upper:
        return "trust"
    if ENTITY_PATTERN.search(upper):
        return "entity"
    return "individual"


def property_identity(
    county_fips: str,
    apn: str,
    address_line1: str,
    unit: str,
    city: str,
    state: str,
    postal_code: str,
) -> str:
    county = compact_whitespace(county_fips)
    parcel = re.sub(r"[^A-Z0-9]", "", (apn or "").upper())
    unit_key = compact_whitespace(unit).upper()
    if county and parcel:
        return f"parcel|{county}|{parcel}|{unit_key}"
    return "address|" + normalize_address(address_line1, unit, city, state, postal_code)


def owner_identity(
    owner_kind: str,
    name: str,
    jurisdiction: str,
    company_number: str,
    mailing_line1: str,
    mailing_city: str,
    mailing_state: str,
    mailing_postal_code: str,
) -> str:
    normalized_name = normalize_name(name)
    normalized_jurisdiction = compact_whitespace(jurisdiction).lower()
    normalized_company = re.sub(r"[^A-Z0-9]", "", (company_number or "").upper())
    if owner_kind == "entity" and normalized_jurisdiction and normalized_company:
        return f"company|{normalized_jurisdiction}|{normalized_company}"
    mailing = normalize_address(mailing_line1, "", mailing_city, mailing_state, mailing_postal_code)
    return f"candidate|{owner_kind}|{normalized_name}|{normalized_jurisdiction}|{mailing}"


def owner_id_from_identity(identity_key: str) -> str:
    return stable_id("own", identity_key)


def property_id_from_identity(identity_key: str) -> str:
    return stable_id("prop", identity_key)
