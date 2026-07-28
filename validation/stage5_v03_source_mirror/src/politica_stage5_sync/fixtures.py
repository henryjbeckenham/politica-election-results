from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .http import FixtureTransport, HttpResponse
from .odata import ENTITY_SPECS, build_collection_url
from .strategy import build_discovery_window


def load_fixture_transport(
    fixture_root: str | Path,
    *,
    base_url: str,
    page_size: int,
    previous_end,
    now,
    overlap_hours: int,
) -> FixtureTransport:
    root = Path(fixture_root)
    responses: dict[str, HttpResponse] = {}
    for spec in ENTITY_SPECS:
        window = build_discovery_window(previous_end, now, overlap_hours, spec.discovery_field)
        entity_dir = root / spec.entity_set.lower()
        page_files = sorted(entity_dir.glob("page_*.json"))
        for index, page_path in enumerate(page_files):
            skip = index * page_size
            url = build_collection_url(base_url, spec, window, page_size=page_size, skip=skip)
            body = page_path.read_bytes()
            responses[url] = HttpResponse(
                requested_url=url,
                final_url=url,
                status=200,
                headers=(("Content-Type", "application/json; odata.metadata=minimal; charset=utf-8"),),
                body=body,
                attempt=1,
                duration_ms=1,
            )
    return FixtureTransport(responses)


def load_baseline_contract(root: str | Path) -> dict[str, Any]:
    return json.loads((Path(root) / "openapi.json").read_text(encoding="utf-8"))
