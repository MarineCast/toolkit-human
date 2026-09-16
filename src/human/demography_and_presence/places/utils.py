"""I/O helpers for normalized places-of-interest domain outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from human.demography_and_presence.places.schema import (
    normalize_place_of_interest_items,
)


def read_places_of_interest(path: str | Path) -> list[dict[str, Any]]:
    """Read normalized POI rows from a JSON list or app-style payload."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        rows = payload.get("items", [])
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError(f"POI file must contain a list or an object with an items list: {path}")
    return normalize_place_of_interest_items(rows)


def write_places_of_interest(rows: list[dict[str, Any]], path: str | Path) -> Path:
    """Write normalized POI rows as the domain-level preprocessed artifact."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    items = normalize_place_of_interest_items(rows)
    out_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return out_path
