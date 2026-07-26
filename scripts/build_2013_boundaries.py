#!/usr/bin/env python3
"""Build the governed browser geometry for the 2013 House map."""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
import zipfile
from pathlib import Path

import shapefile
from shapely import force_2d
from shapely.geometry import mapping, shape


def rounded(value):
    if isinstance(value, (list, tuple)):
        return [rounded(item) for item in value]
    return round(float(value), 4)


def division_numbers(source: Path) -> dict[str, int]:
    """Read the election-specific division identifiers from the official result."""

    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        next(handle, None)
        rows = csv.DictReader(handle)
        result: dict[str, int] = {}
        for row in rows:
            name = str(row.get("DivisionNm") or "").strip()
            number = str(row.get("DivisionID") or "").strip()
            if not name or not number:
                continue
            key = name.casefold()
            observed = int(number)
            if key in result and result[key] != observed:
                raise RuntimeError(f"Conflicting AEC division identifiers for {name}")
            result[key] = observed
    if len(result) != 150:
        raise RuntimeError("The 2013 candidate source must identify 150 divisions")
    return result


def build(source: Path, candidates: Path, output: Path, tolerance: float) -> dict:
    identifiers = division_numbers(candidates)
    with tempfile.TemporaryDirectory(prefix="politica-boundaries-2013-") as temporary:
        with zipfile.ZipFile(source) as archive:
            archive.extractall(temporary)
        reader = shapefile.Reader(str(Path(temporary) / "COM20111216_ELB_region.shp"))
        features = []
        for item in reader.iterShapeRecords():
            record = item.record.as_dict()
            electorate = str(record["ELECT_DIV"]).strip()
            geometry = force_2d(shape(item.shape.__geo_interface__)).simplify(
                tolerance, preserve_topology=True
            )
            if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                raise RuntimeError(
                    f"Unsupported simplified geometry for {electorate}: {geometry.geom_type}"
                )
            try:
                division_number = identifiers[electorate.casefold()]
            except KeyError as exc:
                raise RuntimeError(
                    f"The AEC boundary division is absent from the 2013 result: {electorate}"
                ) from exc
            payload = mapping(geometry)
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "electorate": electorate,
                        "sort_name": str(record["SORTNAME"]).strip(),
                        "division_number": division_number,
                        "area_sq_km": float(record["AREA_SQKM"]),
                    },
                    "geometry": {
                        "type": payload["type"],
                        "coordinates": rounded(payload["coordinates"]),
                    },
                }
            )
    features.sort(key=lambda item: item["properties"]["sort_name"].casefold())
    names = [item["properties"]["electorate"].casefold() for item in features]
    if len(features) != 150 or len(names) != len(set(names)):
        raise RuntimeError("The 2013 boundary source must contain 150 unique divisions")
    if set(names) != set(identifiers):
        missing = sorted(set(identifiers) - set(names))
        extra = sorted(set(names) - set(identifiers))
        raise RuntimeError(
            f"The 2013 result and boundary divisions differ; missing={missing}, extra={extra}"
        )
    document = {"type": "FeatureCollection", "features": features}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return {"feature_count": len(features), "size_bytes": output.stat().st_size}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--tolerance", type=float, default=0.001)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.candidates, args.output, args.tolerance), indent=2))


if __name__ == "__main__":
    main()
