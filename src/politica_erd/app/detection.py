from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path

import yaml


class DatasetSelectionError(ValueError):
    pass


class AdapterCatalogue:
    def __init__(self, adapters_root: Path):
        self.adapters_root = adapters_root

    def catalogue(self) -> list[dict]:
        adapters: list[dict] = []
        for path in sorted(self.adapters_root.glob("*.yml")):
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            if config.get("catalogue_visibility") == "batch_only":
                continue
            datasets = []
            for key, dataset in config.get("datasets", {}).items():
                datasets.append(
                    {
                        "dataset_key": key,
                        "destination": dataset.get("destination"),
                        "grain": dataset.get("grain"),
                        "dataset_family": dataset.get("dataset_family"),
                        "chamber_code": dataset.get("chamber_code"),
                        "geographic_scope": dataset.get("geographic_scope"),
                        "mapping_entities": dataset.get("mapping_entities"),
                        "required_headers": dataset.get("required_headers", []),
                        "filename_patterns": dataset.get("filename_patterns", []),
                    }
                )
            adapters.append(
                {
                    "adapter_id": config["adapter_id"],
                    "authority_id": config["authority_id"],
                    "adapter_version": str(config["adapter_version"]),
                    "status": config.get("status", "unknown"),
                    "signature_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "datasets": datasets,
                }
            )
        return adapters

    def _configs(self) -> list[dict]:
        configs = [
            yaml.safe_load(path.read_text(encoding="utf-8"))
            for path in sorted(self.adapters_root.glob("*.yml"))
        ]
        return [
            config
            for config in configs
            if config.get("catalogue_visibility") != "batch_only"
        ]

    def detect(self, virtual_name: str, headers: list[str], authority_id: str | None) -> dict:
        header_set = set(headers)
        exact: list[dict] = []
        header_only: list[dict] = []
        for config in self._configs():
            if authority_id and config["authority_id"] != authority_id:
                continue
            for key, dataset in config.get("datasets", {}).items():
                required = set(dataset.get("required_headers", []))
                if not required.issubset(header_set):
                    continue
                candidate = {
                    "adapter_id": config["adapter_id"],
                    "adapter_version": str(config["adapter_version"]),
                    "authority_id": config["authority_id"],
                    "dataset_key": key,
                    "destination": dataset.get("destination"),
                    "grain": dataset.get("grain"),
                    "dataset_family": dataset.get("dataset_family"),
                    "chamber_code": dataset.get("chamber_code"),
                    "geographic_scope": dataset.get("geographic_scope"),
                    "mapping_entities": dataset.get("mapping_entities"),
                    "required_headers": sorted(required),
                }
                if any(
                    fnmatch.fnmatch(Path(virtual_name).name, pattern)
                    for pattern in dataset.get("filename_patterns", [])
                ):
                    candidate["match_method"] = "filename_and_headers"
                    exact.append(candidate)
                else:
                    candidate["match_method"] = "headers_only"
                    header_only.append(candidate)
        candidates = exact or header_only
        if len(exact) == 1:
            return {"status": "matched", "selection": exact[0], "candidates": exact}
        if candidates:
            return {"status": "needs_selection", "selection": None, "candidates": candidates}
        return {"status": "unknown", "selection": None, "candidates": []}

    def validate_selection(
        self,
        adapter_id: str,
        dataset_key: str,
        headers: list[str],
        authority_id: str | None,
    ) -> dict:
        for config in self._configs():
            if config["adapter_id"] != adapter_id:
                continue
            if authority_id and config["authority_id"] != authority_id:
                raise DatasetSelectionError("The adapter belongs to a different authority")
            dataset = config.get("datasets", {}).get(dataset_key)
            if dataset is None:
                raise DatasetSelectionError("The dataset key is not registered for this adapter")
            missing = sorted(set(dataset.get("required_headers", [])) - set(headers))
            if missing:
                raise DatasetSelectionError(
                    "The selected dataset is missing required headers: " + ", ".join(missing)
                )
            return {
                "adapter_id": config["adapter_id"],
                "adapter_version": str(config["adapter_version"]),
                "authority_id": config["authority_id"],
                "dataset_key": dataset_key,
                "destination": dataset.get("destination"),
                "grain": dataset.get("grain"),
                "dataset_family": dataset.get("dataset_family"),
                "chamber_code": dataset.get("chamber_code"),
                "geographic_scope": dataset.get("geographic_scope"),
                "mapping_entities": dataset.get("mapping_entities"),
                "required_headers": dataset.get("required_headers", []),
                "match_method": "operator_selected",
            }
        raise DatasetSelectionError(f"Unknown adapter: {adapter_id}")


def schema_signature(headers: list[str]) -> str:
    serialised = json.dumps(headers, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()
