#!/usr/bin/env python3
"""Build the governed browser geometry for the 2022 House map."""

from __future__ import annotations

import argparse
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


def build(source: Path, output: Path, tolerance: float) -> dict:
    with tempfile.TemporaryDirectory(prefix="politica-boundaries-2022-") as temporary:
        with zipfile.ZipFile(source) as archive:
            archive.extractall(temporary)
        reader = shapefile.Reader(str(Path(temporary) / "2021_ELB_region.shp"))
        features = []
        for item in reader.iterShapeRecords():
            record = item.record.as_dict()
            geometry = force_2d(shape(item.shape.__geo_interface__)).simplify(
                tolerance, preserve_topology=True
            )
            if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                raise RuntimeError(
                    f"Unsupported simplified geometry for {record['Elect_div']}: {geometry.geom_type}"
                )
            payload = mapping(geometry)
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "electorate": record["Elect_div"],
                        "sort_name": record["Sortname"],
                        "division_number": int(record["E_div_numb"]),
                        "area_sq_km": float(record["Area_SqKm"]),
                    },
                    "geometry": {
                        "type": payload["type"],
                        "coordinates": rounded(payload["coordinates"]),
                    },
                }
            )
    features.sort(key=lambda item: item["properties"]["sort_name"].casefold())
    names = [item["properties"]["electorate"].casefold() for item in features]
    if len(features) != 151 or len(names) != len(set(names)):
        raise RuntimeError("The 2022 boundary source must contain 151 unique divisions")
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
    parser.add_argument("output", type=Path)
    parser.add_argument("--tolerance", type=float, default=0.001)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output, args.tolerance), indent=2))


if __name__ == "__main__":
    main()
