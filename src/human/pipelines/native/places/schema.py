"""Schema contract for normalized places-of-interest records."""

from __future__ import annotations

import math
from typing import Any, Mapping

PLACE_OF_INTEREST_COLUMNS = ("type", "name", "latitude", "longitude")

_OPTIONAL_TEXT_FIELDS = (
    "id",
    "category",
    "destination_type",
    "source",
    "source_id",
    "source_url",
    "website",
    "operator",
    "access",
    "fee",
    "opening_hours",
    "wheelchair",
    "direction",
)
_OPTIONAL_FLOAT_FIELDS = (
    "destination_score",
    "distance_to_marine_miles",
)
_OPTIONAL_LIST_FIELDS = (
    "destination_reasons",
    "source_ids",
)


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        values = [value]
    else:
        try:
            values = list(value)
        except TypeError as exc:
            raise ValueError(f"expected a string list, received {type(value).__name__}") from exc

    normalized: list[str] = []
    for item in values:
        text = _normalize_optional_text(item)
        if text and text not in normalized:
            normalized.append(text)
    return normalized or None


def _normalize_string_mapping(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"expected a mapping, received {type(value).__name__}")
    normalized: dict[str, str] = {}
    for key, raw_value in value.items():
        normalized_key = _normalize_optional_text(key)
        normalized_value = _normalize_optional_text(raw_value)
        if normalized_key and normalized_value:
            normalized[normalized_key] = normalized_value
    return dict(sorted(normalized.items())) or None


def normalize_place_of_interest_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Return one normalized POI item or raise if required fields are invalid.

    The original four-field contract remains required so existing clients can
    continue to render the artifact. Destination and provenance fields are
    optional extensions and are preserved when present.
    """
    item_type = str(raw.get("type", "")).strip()
    name = str(raw.get("name", "")).strip()
    if not item_type:
        raise ValueError("place-of-interest item missing non-empty type")
    if not name:
        raise ValueError("place-of-interest item missing non-empty name")

    latitude = float(raw["latitude"])
    longitude = float(raw["longitude"])
    if not math.isfinite(latitude) or not (-90.0 <= latitude <= 90.0):
        raise ValueError(f"invalid latitude for POI {name!r}: {latitude}")
    if not math.isfinite(longitude) or not (-180.0 <= longitude <= 180.0):
        raise ValueError(f"invalid longitude for POI {name!r}: {longitude}")

    item: dict[str, Any] = {
        "type": item_type,
        "name": name,
        "latitude": latitude,
        "longitude": longitude,
    }

    for field in _OPTIONAL_TEXT_FIELDS:
        value = _normalize_optional_text(raw.get(field))
        if value is not None:
            item[field] = value

    for field in _OPTIONAL_FLOAT_FIELDS:
        if raw.get(field) is None:
            continue
        value = float(raw[field])
        if not math.isfinite(value):
            raise ValueError(f"invalid {field} for POI {name!r}: {value}")
        if field == "distance_to_marine_miles" and value < 0:
            raise ValueError(f"negative distance_to_marine_miles for POI {name!r}: {value}")
        item[field] = round(value, 6)

    for field in _OPTIONAL_LIST_FIELDS:
        value = _normalize_string_list(raw.get(field))
        if value is not None:
            item[field] = value

    osm_tags = _normalize_string_mapping(raw.get("osm_tags"))
    if osm_tags is not None:
        item["osm_tags"] = osm_tags

    return item


def normalize_place_of_interest_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize, validate, and deterministically order POI records."""
    items = [normalize_place_of_interest_item(row) for row in rows]
    return sorted(
        items,
        key=lambda row: (
            row["type"],
            row["name"].casefold(),
            row["latitude"],
            row["longitude"],
            row.get("source_id", ""),
        ),
    )
