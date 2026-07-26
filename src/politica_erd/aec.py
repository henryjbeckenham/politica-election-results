from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


def normalise_label(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.casefold().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def row_hash(row: dict[str, str]) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceRow:
    data: dict[str, str]
    locator: str
    row_number: int
    source_row_hash: str
    member: str | None = None


def _dict_rows(reader: csv.reader, source_name: str, member: str | None = None) -> Iterator[SourceRow]:
    first = next(reader)
    has_metadata = len(first) == 1 and "Event:" in first[0]
    headers = next(reader) if has_metadata else first
    first_data_row = 3 if has_metadata else 2
    for row_number, raw in enumerate(reader, start=first_data_row):
        padded = list(raw) + [""] * (len(headers) - len(raw))
        record = dict(zip(headers, padded[: len(headers)], strict=True))
        locator = f"row:{row_number}"
        if member:
            locator = f"member:{member};{locator}"
        yield SourceRow(record, locator, row_number, row_hash(record), member)


def read_aec_csv(path: Path) -> Iterator[SourceRow]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from _dict_rows(csv.reader(handle), path.name)


def read_aec_zip(path: Path) -> Iterator[SourceRow]:
    with zipfile.ZipFile(path) as archive:
        for member in sorted(name for name in archive.namelist() if name.lower().endswith(".csv")):
            with archive.open(member) as raw:
                with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as handle:
                    yield from _dict_rows(csv.reader(handle), path.name, member)


def source_rows(path: Path) -> Iterator[SourceRow]:
    if path.suffix.lower() == ".zip":
        yield from read_aec_zip(path)
    else:
        yield from read_aec_csv(path)


def integer(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return int(value.replace(",", "").strip())


def decimal_text(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    return value.replace(",", "").strip()


def yes(value: str | None) -> bool:
    return (value or "").strip().upper() in {"Y", "YES", "TRUE", "1"}
