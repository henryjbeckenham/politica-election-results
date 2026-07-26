from __future__ import annotations

import hashlib
import re
from collections import defaultdict

import duckdb


PARTY_ABBREVIATION_FIELDS = {"partyab", "partyabbr", "partyabbreviation"}
PARTY_NAME_FIELDS = {"partynm", "partyname", "party"}
CONSTITUENCY_CODE_FIELDS = {
    "divisionid",
    "constituencyid",
    "districtid",
    "electorateid",
    "divisioncode",
    "constituencycode",
}
CONSTITUENCY_NAME_FIELDS = {
    "divisionnm",
    "divisionname",
    "constituencynm",
    "constituencyname",
    "districtnm",
    "districtname",
    "electoratenm",
    "electoratename",
}
GIVEN_NAME_FIELDS = ("givennm", "givenname", "givennames", "firstname")
FAMILY_NAME_FIELDS = ("surname", "familyname", "lastname")


def normalise(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def issue_id(entity_type: str, field: str, source_value: str) -> str:
    digest = hashlib.sha256(
        f"{entity_type}\x1f{field}\x1f{normalise(source_value)}".encode("utf-8")
    ).hexdigest()
    return f"mapping_{digest[:24]}"


def _aliases(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip(" []\"'") for part in re.split(r"[|;,\n]+", value) if part.strip()]


class ReferenceMatcher:
    """Exact-only matcher over the read-only Grand Database snapshot."""

    TABLES = {
        "party": ("sync", "party", "party_id"),
        "constituency": ("sync", "constituency", "constituency_id"),
        "person": ("sync", "person", "person_id"),
    }

    def __init__(self, connection: duckdb.DuckDBPyConnection, authority_id: str | None = None):
        self.authority_id = authority_id
        self.lookups: dict[str, dict[str, set[str]]] = {
            "party": defaultdict(set),
            "constituency": defaultdict(set),
            "person": defaultdict(set),
        }
        self._load(connection)

    def _add(self, entity_type: str, canonical_id: str, *values: object) -> None:
        for value in values:
            key = normalise(value)
            if key:
                self.lookups[entity_type][key].add(str(canonical_id))

    def _load(self, connection: duckdb.DuckDBPyConnection) -> None:
        for party_id, name, short_name, abbreviation, aliases in connection.execute(
            "SELECT party_id, party_name, short_name, abbreviation, aliases FROM sync.party"
        ).fetchall():
            self._add("party", party_id, party_id, name, short_name, abbreviation, *_aliases(aliases))
        for constituency_id, name, official_code, aliases in connection.execute(
            """SELECT constituency_id, constituency_name, official_constituency_code, aliases
               FROM sync.constituency"""
        ).fetchall():
            self._add(
                "constituency",
                constituency_id,
                constituency_id,
                name,
                official_code,
                *_aliases(aliases),
            )
        for person_id, full_name, display_name, aliases in connection.execute(
            "SELECT person_id, full_name, display_name, aliases FROM sync.person"
        ).fetchall():
            self._add("person", person_id, person_id, full_name, display_name, *_aliases(aliases))
        query = """SELECT entity_type, canonical_id, external_id_value
                   FROM sync.external_identifier
                   WHERE record_status <> 'deleted'"""
        parameters: list[str] = []
        if self.authority_id:
            query += " AND authority_id=?"
            parameters.append(self.authority_id)
        for entity_type, canonical_id, external_value in connection.execute(query, parameters).fetchall():
            if entity_type in self.lookups:
                self._add(entity_type, canonical_id, external_value)

    def match(self, entity_type: str, value: object) -> str | None:
        candidates = self.lookups[entity_type].get(normalise(value), set())
        return next(iter(candidates)) if len(candidates) == 1 else None

    def canonical_exists(self, connection: duckdb.DuckDBPyConnection, entity_type: str, canonical_id: str) -> bool:
        schema, table, key = self.TABLES[entity_type]
        return (
            connection.execute(
                f'SELECT count(*) FROM "{schema}"."{table}" WHERE "{key}"=?', [canonical_id]
            ).fetchone()[0]
            == 1
        )


def _field_map(row: dict) -> dict[str, str]:
    return {normalise(key).replace(" ", ""): key for key in row}


def observations_for_row(
    row: dict, entity_types: set[str] | None = None
) -> list[dict]:
    enabled = {"party", "constituency", "person"} if entity_types is None else entity_types
    mapped_fields = _field_map(row)
    observations: list[dict] = []
    for normalised, field in mapped_fields.items():
        value = row.get(field)
        if value in (None, ""):
            continue
        if "party" in enabled and normalised in PARTY_ABBREVIATION_FIELDS | PARTY_NAME_FIELDS:
            observations.append(
                {
                    "entity_type": "party",
                    "field": field,
                    "source_value": str(value),
                    "source_label": str(value),
                }
            )
        elif (
            "constituency" in enabled
            and normalised in CONSTITUENCY_CODE_FIELDS | CONSTITUENCY_NAME_FIELDS
        ):
            observations.append(
                {
                    "entity_type": "constituency",
                    "field": field,
                    "source_value": str(value),
                    "source_label": str(value),
                }
            )
    given_field = next((mapped_fields[key] for key in GIVEN_NAME_FIELDS if key in mapped_fields), None)
    family_field = next((mapped_fields[key] for key in FAMILY_NAME_FIELDS if key in mapped_fields), None)
    if "person" in enabled and given_field and family_field:
        given = str(row.get(given_field) or "").strip()
        family = str(row.get(family_field) or "").strip()
        if given or family:
            label = " ".join(part for part in (given, family) if part)
            observations.append(
                {
                    "entity_type": "person",
                    "field": f"{given_field}+{family_field}",
                    "source_value": label,
                    "source_label": label,
                }
            )
    return observations


def map_row(
    row: dict,
    matcher: ReferenceMatcher,
    resolutions: dict[str, dict],
    entity_types: set[str] | None = None,
) -> tuple[dict, list[dict]]:
    canonical: dict[str, dict] = {}
    unresolved: list[dict] = []
    by_entity: dict[str, list[dict]] = defaultdict(list)
    for observation in observations_for_row(row, entity_types):
        observation = dict(observation)
        observation["issue_id"] = issue_id(
            observation["entity_type"], observation["field"], observation["source_value"]
        )
        resolution = resolutions.get(observation["issue_id"])
        if resolution and resolution.get("resolution_type") == "matched":
            observation["canonical_id"] = resolution["canonical_id"]
            observation["method"] = "operator_resolution"
        elif resolution and resolution.get("resolution_type") == "not_applicable":
            observation["not_applicable"] = True
        else:
            automatic = matcher.match(observation["entity_type"], observation["source_value"])
            if automatic:
                observation["canonical_id"] = automatic
                observation["method"] = "exact_reference_match"
        by_entity[observation["entity_type"]].append(observation)

    for observations in by_entity.values():
        resolved_ids = {
            item["canonical_id"] for item in observations if item.get("canonical_id") is not None
        }
        correlated_id = next(iter(resolved_ids)) if len(resolved_ids) == 1 else None
        for observation in observations:
            if observation.get("not_applicable"):
                canonical[observation["field"]] = {
                    "entity_type": observation["entity_type"],
                    "canonical_id": None,
                    "method": "operator_not_applicable",
                }
                continue
            canonical_id = observation.get("canonical_id") or correlated_id
            if canonical_id:
                canonical[observation["field"]] = {
                    "entity_type": observation["entity_type"],
                    "canonical_id": canonical_id,
                    "method": observation.get("method", "correlated_exact_match"),
                }
            else:
                unresolved.append(observation)
    return {"source": row, "canonical": canonical}, unresolved


def issue_document(observation: dict, dataset_id: str) -> dict:
    return {
        "issue_id": observation["issue_id"],
        "entity_type": observation["entity_type"],
        "field": observation["field"],
        "source_value": observation["source_value"],
        "source_label": observation["source_label"],
        "status": "unresolved",
        "occurrences": 1,
        "dataset_ids": [dataset_id],
    }


def merge_issue(existing: dict[str, dict], observation: dict, dataset_id: str) -> None:
    key = observation["issue_id"]
    if key not in existing:
        existing[key] = issue_document(observation, dataset_id)
        return
    existing[key]["occurrences"] += 1
    if dataset_id not in existing[key]["dataset_ids"]:
        existing[key]["dataset_ids"].append(dataset_id)
