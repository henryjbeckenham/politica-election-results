from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import date

NAMESPACE_UUID = uuid.UUID("908ec7be-31c0-5ce7-9042-22cb3e6c5d7e")


def canonical_component(value: object) -> str:
    """Return the deterministic component representation used by UUIDv5 IDs."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    return re.sub(r"\s+", " ", text)


def slug(value: object) -> str:
    text = canonical_component(value)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def deterministic_uuid(prefix: str, *components: object) -> uuid.UUID:
    name = "|".join([canonical_component(prefix), *map(canonical_component, components)])
    return uuid.uuid5(NAMESPACE_UUID, name)


def election_id(jurisdiction_code: str, election_date: date | str, election_type_code: str) -> str:
    date_token = election_date.isoformat() if isinstance(election_date, date) else str(election_date)
    return f"election_{slug(jurisdiction_code)}_{date_token.replace('-', '_')}_{slug(election_type_code)}"


def election_chamber_id(election_identifier: str, chamber_code: str) -> str:
    token = election_identifier.removeprefix("election_")
    return f"election_chamber_{token}_{slug(chamber_code)}"


def contest_id(
    election_identifier: str,
    chamber_code: str,
    official_constituency_code: str | None,
    constituency_name: str,
) -> str:
    election_token = election_identifier.removeprefix("election_")
    constituency_token = slug(official_constituency_code or constituency_name)
    return f"contest_{election_token}_{slug(chamber_code)}_{constituency_token}"


def candidacy_id(contest_identifier: str, authority_candidate_key: str) -> uuid.UUID:
    return deterministic_uuid("candidacy", contest_identifier, authority_candidate_key)


def reporting_unit_id(authority_id: str, official_code: str) -> uuid.UUID:
    return deterministic_uuid("reporting_unit", authority_id, official_code)


def source_revision_id(source_file_id: str, sha256: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise ValueError("sha256 must contain exactly 64 hexadecimal characters")
    return f"source_revision_{deterministic_uuid('source_revision', source_file_id, sha256.lower())}"


def fact_id(table_name: str, natural_grain: list[object] | tuple[object, ...], source_revision: str) -> uuid.UUID:
    return deterministic_uuid("fact", table_name, *natural_grain, source_revision)

