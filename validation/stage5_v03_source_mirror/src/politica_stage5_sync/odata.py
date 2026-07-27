from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlencode, urljoin

from .models import Candidate, DiscoveryWindow


DateTimeLiteralStyle = Literal["utc_z", "utc_without_designator"]


@dataclass(frozen=True)
class EntitySpec:
    entity_set: str
    entity_type: str
    identifier_field: str
    discovery_field: str
    secondary_order_field: str
    datetime_literal_style: DateTimeLiteralStyle


TITLE_SPEC = EntitySpec(
    "Titles", "Title", "id", "asMadeRegisteredAt", "id", "utc_z"
)
VERSION_SPEC = EntitySpec(
    "Versions", "Version", "registerId", "registeredAt", "registerId", "utc_without_designator"
)
ENTITY_SPECS = (TITLE_SPEC, VERSION_SPEC)


def odata_datetime(value: datetime, *, style: DateTimeLiteralStyle) -> str:
    if value.tzinfo is None:
        raise ValueError("OData datetime must be timezone-aware")
    utc = value.astimezone(timezone.utc).replace(tzinfo=None)
    encoded = utc.isoformat(timespec="seconds")
    if style == "utc_z":
        return f"{encoded}Z"
    if style == "utc_without_designator":
        return encoded
    raise ValueError(f"unsupported OData datetime literal style: {style}")


def build_collection_url(
    base_url: str,
    spec: EntitySpec,
    window: DiscoveryWindow,
    *,
    page_size: int,
    skip: int,
) -> str:
    if page_size < 1 or skip < 0:
        raise ValueError("invalid page size or skip")
    start = odata_datetime(window.start, style=spec.datetime_literal_style)
    end = odata_datetime(window.end, style=spec.datetime_literal_style)
    filter_expression = (
        f"{spec.discovery_field} ge {start} and "
        f"{spec.discovery_field} le {end}"
    )
    query = urlencode(
        {
            "$filter": filter_expression,
            "$orderby": f"{spec.discovery_field} asc,{spec.secondary_order_field} asc",
            "$top": str(page_size),
            "$skip": str(skip),
        }
    )
    return f"{urljoin(base_url, spec.entity_set)}?{query}"


def parse_collection_payload(body: bytes) -> list[dict[str, Any]]:
    import json

    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("value"), list):
        raise ValueError("collection response does not contain an OData value list")
    values: list[dict[str, Any]] = []
    for item in parsed["value"]:
        if not isinstance(item, dict):
            raise ValueError("collection item is not an object")
        values.append(item)
    return values


def validate_collection_order(spec: EntitySpec, values: list[dict[str, Any]]) -> None:
    keys: list[tuple[str, str]] = []
    identifiers: list[str] = []
    for item in values:
        discovery_value = item.get(spec.discovery_field)
        secondary_value = item.get(spec.secondary_order_field)
        identifier = item.get(spec.identifier_field)
        if not isinstance(discovery_value, str) or not discovery_value.strip():
            raise ValueError(f"{spec.entity_set} item lacks {spec.discovery_field}")
        if not isinstance(secondary_value, str) or not secondary_value.strip():
            raise ValueError(f"{spec.entity_set} item lacks {spec.secondary_order_field}")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"{spec.entity_set} item lacks {spec.identifier_field}")
        keys.append((discovery_value, secondary_value))
        identifiers.append(identifier)
    if keys != sorted(keys):
        raise ValueError(
            f"{spec.entity_set} response is not ordered by "
            f"{spec.discovery_field}, {spec.secondary_order_field}"
        )
    if len(keys) != len(set(keys)):
        raise ValueError(f"{spec.entity_set} response contains duplicate ordering tuples")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{spec.entity_set} response contains duplicate external identifiers")


def candidate_from_item(spec: EntitySpec, item: dict[str, Any], source_url: str) -> Candidate:
    external_identifier = item.get(spec.identifier_field)
    if not isinstance(external_identifier, str) or not external_identifier.strip():
        raise ValueError(f"{spec.entity_set} item lacks {spec.identifier_field}")
    discovery_value = item.get(spec.discovery_field)
    if not isinstance(discovery_value, str) or not discovery_value.strip():
        raise ValueError(f"{spec.entity_set} item lacks {spec.discovery_field}")
    from .strategy import canonical_json_hash

    return Candidate(
        entity_type=spec.entity_type,
        external_identifier=external_identifier,
        source_url=source_url,
        observed_fingerprint=canonical_json_hash(item),
    )
