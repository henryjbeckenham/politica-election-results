from __future__ import annotations

import argparse
import csv
import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..build import PROJECT_ROOT


@dataclass(frozen=True)
class DatasetMatch:
    adapter_id: str
    dataset_key: str
    destination: str
    grain: str


def csv_headers(path: Path, encoding: str = "utf-8-sig", delimiter: str = ",") -> list[str]:
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        first = next(reader)
        if len(first) == 1 and "Event:" in first[0]:
            return next(reader)
        return first


def detect(path: Path, authority_id: str | None = None) -> list[DatasetMatch]:
    matches: list[DatasetMatch] = []
    for config_path in sorted((PROJECT_ROOT / "config" / "adapters").glob("*.yml")):
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if config.get("catalogue_visibility") == "batch_only":
            continue
        if authority_id and config["authority_id"] != authority_id:
            continue
        headers = csv_headers(path, config.get("encoding", "utf-8-sig"), config.get("delimiter", ","))
        header_set = set(headers)
        for dataset_key, dataset in config.get("datasets", {}).items():
            pattern_match = any(fnmatch.fnmatch(path.name, pattern) for pattern in dataset.get("filename_patterns", []))
            required_match = set(dataset.get("required_headers", [])).issubset(header_set)
            if pattern_match and required_match:
                matches.append(DatasetMatch(config["adapter_id"], dataset_key, dataset["destination"], dataset["grain"]))
    return matches


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--authority-id")
    args = parser.parse_args()
    matches = detect(args.file, args.authority_id)
    print(json.dumps([match.__dict__ for match in matches], indent=2))
    raise SystemExit(0 if len(matches) == 1 else 2)


if __name__ == "__main__":
    main()
