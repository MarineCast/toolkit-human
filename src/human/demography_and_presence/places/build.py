"""Prepare normalized human places-of-interest domain outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, box
from shapely.ops import nearest_points, unary_union

from human.demography_and_presence.places.download import (
    ECOLOGY_MARINAS_LAYER,
    NE_MARINE_POLYS_ZIP_URL,
    NE_OCEAN_ZIP_URL,
    OSM_OVERPASS_URL,
    WA_STATE_PARKS_LAYER,
    WSDOT_FERRY_TERMINALS_LAYER,
    SourceRegistryEntry,
    arcgis_query_first_available_geojson,
    build_osm_destination_queries,
    download_if_needed,
    load_places_of_interest_sources,
    overpass_query_batches_first_available_json,
)
from human.demography_and_presence.places.schema import (
    normalize_place_of_interest_items,
)
from human.demography_and_presence.places.utils import write_places_of_interest

LOGGER = logging.getLogger(__name__)

MILES_TO_METERS = 1609.344
DEFAULT_NEARSHORE_MILES = 10.0
DEFAULT_DESTINATION_NEARSHORE_MILES = 2.0
DEFAULT_DESTINATION_SCORE_THRESHOLD = 5.0
DEFAULT_REQUIRE_NAMED_MARINAS = True
DEFAULT_WA_BBOX_4326 = box(-125.8, 45.4, -116.7, 49.2)
DEFAULT_DISTANCE_CRS = "EPSG:5070"

_OSM_OUTPUT_TAGS = (
    "tourism",
    "natural",
    "man_made",
    "tower:type",
    "leisure",
    "boundary",
    "protect_class",
    "protection_title",
    "amenity",
    "access",
    "opening_hours",
    "fee",
    "wheelchair",
    "direction",
    "operator",
    "website",
    "contact:website",
    "toilets",
    "parking",
    "description",
    "wikidata",
    "wikipedia",
)

_BASE_DESTINATION_SCORES = {
    "viewpoint": 6.0,
    "lighthouse": 5.5,
    "observation_tower": 5.0,
    "beach": 4.5,
    "pier": 4.5,
    "state_park": 4.0,
    "park": 3.5,
    "national_park": 3.5,
    "ferry_terminal": 3.5,
    "nature_reserve": 3.25,
    "protected_area": 2.5,
    "marina": 4.0,
}

_GENERIC_MARINA_NAME_KEYS = {
    "boat marina",
    "large boat facilities inventory",
    "marina",
    "n a",
    "na",
    "no name",
    "none",
    "not applicable",
    "private marina",
    "the marina",
    "tbd",
    "unknown",
    "unknown marina",
    "unnamed",
    "unnamed marina",
}


def _source_url(
    sources: Mapping[str, SourceRegistryEntry],
    name: str,
    fallback_url: str,
) -> str:
    source = sources.get(name)
    return source.url if source is not None else fallback_url


def _source_entry(
    sources: Mapping[str, SourceRegistryEntry],
    name: str,
    fallback_url: str,
    *,
    source_type: str = "arcgis_feature_layer",
) -> SourceRegistryEntry:
    source = sources.get(name)
    if source is not None:
        return source
    return SourceRegistryEntry(name=name, type=source_type, url=fallback_url)


def places_of_interest_metadata_path(output_path: str | Path) -> Path:
    path = Path(output_path)
    return path.with_suffix(path.suffix + ".metadata.json")


def build_source_metadata(
    sources: Mapping[str, SourceRegistryEntry],
    *,
    retrieved_at_utc: str | None = None,
    pipeline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "retrieved_at_utc": retrieved_at_utc
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sources": {name: source.to_metadata() for name, source in sorted(sources.items())},
    }
    if pipeline:
        metadata["pipeline"] = dict(pipeline)
    return metadata


def write_places_of_interest_metadata(
    output_path: str | Path,
    sources: Mapping[str, SourceRegistryEntry],
    *,
    metadata_path: str | Path | None = None,
    retrieved_at_utc: str | None = None,
    pipeline: Mapping[str, Any] | None = None,
) -> Path:
    out_path = (
        Path(metadata_path)
        if metadata_path is not None
        else places_of_interest_metadata_path(output_path)
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            build_source_metadata(
                sources,
                retrieved_at_utc=retrieved_at_utc,
                pipeline=pipeline,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return out_path


def build_nearshore_mask(
    *,
    bbox_4326: Any = DEFAULT_WA_BBOX_4326,
    cache_dir: str | Path,
    distance_crs: str = DEFAULT_DISTANCE_CRS,
    sources: Mapping[str, SourceRegistryEntry] | None = None,
) -> Any:
    """Build a projected ocean/marine mask used to keep nearshore POIs."""
    registry = sources or load_places_of_interest_sources()
    cache_path = Path(cache_dir)
    ocean_zip = download_if_needed(
        _source_url(registry, "natural_earth_ocean", NE_OCEAN_ZIP_URL),
        cache_path / "ne_10m_ocean.zip",
    )
    marine_zip = download_if_needed(
        _source_url(registry, "natural_earth_marine_polys", NE_MARINE_POLYS_ZIP_URL),
        cache_path / "ne_10m_geography_marine_polys.zip",
    )

    ocean = gpd.read_file(f"zip://{ocean_zip}").set_crs("EPSG:4326", allow_override=True)
    marine = gpd.read_file(f"zip://{marine_zip}").set_crs("EPSG:4326", allow_override=True)

    ocean_clip = ocean[ocean.intersects(bbox_4326)][["geometry"]]
    marine_clip = marine[marine.intersects(bbox_4326)][["geometry"]]
    combined = pd.concat([ocean_clip, marine_clip], ignore_index=True)
    if combined.empty:
        raise RuntimeError("Natural Earth marine mask is empty for the configured bounding box.")
    dissolved = unary_union(combined.geometry.values)
    return gpd.GeoSeries([dissolved], crs="EPSG:4326").to_crs(distance_crs).iloc[0]


def add_distance_to_mask_miles(
    points_4326: gpd.GeoDataFrame,
    mask_geom_m: Any,
    *,
    distance_crs: str = DEFAULT_DISTANCE_CRS,
) -> gpd.GeoDataFrame:
    """Add distance_to_marine_miles to a point GeoDataFrame."""
    points = points_4326.copy()
    if points.empty:
        points["distance_to_marine_miles"] = pd.Series(dtype=float)
        return points
    points_m = points.to_crs(distance_crs)
    distances = points_m.geometry.distance(mask_geom_m)
    points["distance_to_marine_miles"] = distances / MILES_TO_METERS
    return points


def filter_within_miles_of_mask(
    points_4326: gpd.GeoDataFrame,
    mask_geom_m: Any,
    *,
    miles: float,
    distance_crs: str = DEFAULT_DISTANCE_CRS,
) -> gpd.GeoDataFrame:
    """Keep only points within the requested distance of the projected mask."""
    points = add_distance_to_mask_miles(
        points_4326,
        mask_geom_m,
        distance_crs=distance_crs,
    )
    return points.loc[points["distance_to_marine_miles"] <= miles].copy()


def _marine_edge_points_for_geometries(
    features_4326: gpd.GeoDataFrame,
    mask_geom_m: Any,
    *,
    distance_crs: str = DEFAULT_DISTANCE_CRS,
) -> gpd.GeoDataFrame:
    """Represent polygons at the portion of the feature nearest marine water.

    A polygon centroid can be many miles inland. For a visitor destination, the
    marine-facing edge is a better automated first guess and keeps small sites
    such as Lime Kiln Point State Park at the coastline.
    """
    features_m = features_4326.to_crs(distance_crs).copy()

    def nearest_feature_point(geometry: Any) -> Any:
        if geometry is None or geometry.is_empty:
            return None
        try:
            return nearest_points(geometry, mask_geom_m)[0]
        except Exception:
            return geometry.representative_point()

    features_m["geometry"] = features_m.geometry.map(nearest_feature_point)
    features_m = features_m.dropna(subset=["geometry"])
    return features_m.to_crs("EPSG:4326")


def _clean_scalar(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _first_value(row: Mapping[str, Any], columns: tuple[str, ...]) -> str | None:
    for column in columns:
        if column in row:
            value = _clean_scalar(row[column])
            if value:
                return value
    return None


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
    return slug or "unnamed"


def _canonical_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", normalized.casefold()).strip()


def is_meaningful_marina_name(value: Any) -> bool:
    """Return whether a marina name identifies a place rather than a placeholder."""
    name = _clean_scalar(value)
    if not name:
        return False
    name_key = _canonical_name(name)
    if not name_key or name_key in _GENERIC_MARINA_NAME_KEYS:
        return False
    if name_key.isdigit():
        return False
    return re.fullmatch(r"(?:boat )?marina(?: \d+)?", name_key) is None


def points_to_items(
    points_4326: gpd.GeoDataFrame,
    *,
    type_name: str,
    name_col: str,
    source: str | None = None,
    source_prefix: str | None = None,
    source_id_columns: tuple[str, ...] = (),
    category: str | None = None,
    destination_type: str | None = None,
) -> list[dict[str, Any]]:
    """Convert point geometries and a name column to normalized POI records."""
    rows: list[dict[str, Any]] = []
    for _, row in points_4326.iterrows():
        geometry = row.geometry
        name = _clean_scalar(row.get(name_col))
        if geometry is None or geometry.is_empty or not name:
            continue
        source_key = _first_value(row, source_id_columns) or _slugify(name)
        source_id = f"{source_prefix}:{source_key}" if source_prefix else None
        item: dict[str, Any] = {
            "type": type_name,
            "name": name,
            "latitude": float(geometry.y),
            "longitude": float(geometry.x),
        }
        if source:
            item["source"] = source
        if source_id:
            item["id"] = source_id
            item["source_id"] = source_id
            item["source_ids"] = [source_id]
        if category:
            item["category"] = category
        if destination_type:
            item["destination_type"] = destination_type
        if "distance_to_marine_miles" in row:
            item["distance_to_marine_miles"] = float(row["distance_to_marine_miles"])
        rows.append(item)
    return rows


def _classify_osm_destination(tags: Mapping[str, str]) -> str | None:
    if tags.get("tourism") == "viewpoint":
        return "viewpoint"
    if tags.get("man_made") == "lighthouse":
        return "lighthouse"
    if tags.get("man_made") == "pier":
        return "pier"
    if tags.get("man_made") == "tower" and tags.get("tower:type") == "observation":
        return "observation_tower"
    if tags.get("natural") == "beach":
        return "beach"
    if tags.get("amenity") == "ferry_terminal":
        return "ferry_terminal"
    if tags.get("leisure") == "marina":
        return "marina"
    if tags.get("boundary") == "national_park":
        return "national_park"
    if tags.get("leisure") == "nature_reserve":
        return "nature_reserve"
    if tags.get("boundary") == "protected_area":
        return "protected_area"
    if tags.get("leisure") == "park":
        return "park"
    return None


def osm_payload_to_candidates(payload: Mapping[str, Any]) -> gpd.GeoDataFrame:
    """Convert an Overpass JSON response to named point candidates."""
    rows: list[dict[str, Any]] = []
    for element in payload.get("elements", []) or []:
        if not isinstance(element, Mapping):
            continue
        tags_raw = element.get("tags") or {}
        if not isinstance(tags_raw, Mapping):
            continue
        tags = {str(key): str(value) for key, value in tags_raw.items() if value is not None}
        name = _clean_scalar(tags.get("name"))
        destination_type = _classify_osm_destination(tags)
        if not name or destination_type is None:
            continue

        latitude = element.get("lat")
        longitude = element.get("lon")
        if latitude is None or longitude is None:
            center = element.get("center") or {}
            if isinstance(center, Mapping):
                latitude = center.get("lat")
                longitude = center.get("lon")
        if latitude is None or longitude is None:
            continue

        element_type = _clean_scalar(element.get("type"))
        element_id = _clean_scalar(element.get("id"))
        if not element_type or not element_id:
            continue
        rows.append(
            {
                "name": name,
                "destination_type": destination_type,
                "osm_element_type": element_type,
                "osm_id": element_id,
                "osm_tags": tags,
                "geometry": Point(float(longitude), float(latitude)),
            }
        )

    if not rows:
        return gpd.GeoDataFrame(
            columns=[
                "name",
                "destination_type",
                "osm_element_type",
                "osm_id",
                "osm_tags",
                "geometry",
            ],
            geometry="geometry",
            crs="EPSG:4326",
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def _distance_score(distance_miles: float) -> tuple[float, str]:
    if distance_miles <= 0.1:
        return 3.0, "marine_distance_le_0_1_mi"
    if distance_miles <= 0.5:
        return 2.5, "marine_distance_le_0_5_mi"
    if distance_miles <= 1.0:
        return 2.0, "marine_distance_le_1_mi"
    if distance_miles <= 2.0:
        return 1.0, "marine_distance_le_2_mi"
    if distance_miles <= 3.0:
        return 0.5, "marine_distance_le_3_mi"
    return 0.0, "marine_distance_gt_3_mi"


def score_destination_candidate(
    *,
    name: str,
    destination_type: str,
    distance_to_marine_miles: float,
    tags: Mapping[str, str] | None = None,
    official_source: bool = False,
) -> tuple[float | None, list[str]]:
    """Return a transparent heuristic destination score and reason codes.

    ``None`` means the feature should be excluded, currently because OSM marks
    access as private or closed. This score is a candidate-ranking heuristic,
    not a probability of seeing an orca.
    """
    tags = tags or {}
    access = str(tags.get("access", "")).strip().casefold()
    if access in {"no", "private", "customers", "members", "military"}:
        return None, [f"excluded_access_{access}"]

    score = _BASE_DESTINATION_SCORES.get(destination_type, 0.0)
    reasons = [f"base_{destination_type}"]

    distance_points, distance_reason = _distance_score(distance_to_marine_miles)
    score += distance_points
    reasons.append(distance_reason)

    normalized_name = _canonical_name(name)
    if re.search(r"\b(orca|whale|whales|whaling)\b", normalized_name):
        score += 3.0
        reasons.append("name_whale_or_orca")
    if re.search(r"\b(viewpoint|lookout|overlook|lighthouse)\b", normalized_name):
        score += 1.0
        reasons.append("name_viewing_feature")
    elif re.search(r"\b(point|cape|head|headland|beach|shore|waterfront)\b", normalized_name):
        score += 0.75
        reasons.append("name_coastal_feature")
    if re.search(r"\b(park|reserve|refuge)\b", normalized_name):
        score += 0.25
        reasons.append("name_managed_destination")
    if destination_type == "marina" and is_meaningful_marina_name(name):
        score += 0.5
        reasons.append("named_marina")

    if official_source:
        score += 0.75
        reasons.append("official_source")

    if access in {"yes", "public", "permissive", "designated"}:
        score += 0.75
        reasons.append("public_access_tag")
    elif access in {"permit", "seasonal"}:
        reasons.append(f"conditional_access_{access}")

    amenity_fields = (
        "opening_hours",
        "website",
        "contact:website",
        "operator",
        "wheelchair",
        "toilets",
        "parking",
    )
    amenity_count = sum(bool(_clean_scalar(tags.get(field))) for field in amenity_fields)
    if amenity_count:
        score += min(1.0, amenity_count * 0.2)
        reasons.append(f"metadata_fields_{amenity_count}")

    protect_class = str(tags.get("protect_class", "")).casefold()
    if destination_type in {"protected_area", "nature_reserve"} and protect_class in {
        "1",
        "1a",
        "1b",
    }:
        score -= 2.5
        reasons.append("strict_protection_penalty")

    return round(score, 3), reasons


def _select_osm_output_tags(tags: Mapping[str, str]) -> dict[str, str]:
    return {
        key: str(tags[key]) for key in _OSM_OUTPUT_TAGS if key in tags and _clean_scalar(tags[key])
    }


def prepare_osm_destination_items(
    payload: Mapping[str, Any],
    mask_geom_m: Any,
    *,
    destination_nearshore_miles: float = DEFAULT_DESTINATION_NEARSHORE_MILES,
    destination_score_threshold: float = DEFAULT_DESTINATION_SCORE_THRESHOLD,
    include_marinas: bool = True,
    require_named_marinas: bool = DEFAULT_REQUIRE_NAMED_MARINAS,
    distance_crs: str = DEFAULT_DISTANCE_CRS,
) -> list[dict[str, Any]]:
    """Build scored destination records from a cached/live Overpass response."""
    candidates = osm_payload_to_candidates(payload)
    candidates = filter_within_miles_of_mask(
        candidates,
        mask_geom_m,
        miles=destination_nearshore_miles,
        distance_crs=distance_crs,
    )

    items: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        if row["destination_type"] == "marina":
            if not include_marinas:
                continue
            if require_named_marinas and not is_meaningful_marina_name(row["name"]):
                continue
        tags = row["osm_tags"]
        score, reasons = score_destination_candidate(
            name=row["name"],
            destination_type=row["destination_type"],
            distance_to_marine_miles=float(row["distance_to_marine_miles"]),
            tags=tags,
        )
        if score is None or score < destination_score_threshold:
            continue

        source_id = f"osm:{row['osm_element_type']}/{row['osm_id']}"
        legacy_type = {
            "ferry_terminal": "Ferry",
            "marina": "Marina",
        }.get(row["destination_type"], "Park")
        website = tags.get("website") or tags.get("contact:website")
        item: dict[str, Any] = {
            "type": legacy_type,
            "name": row["name"],
            "latitude": float(row.geometry.y),
            "longitude": float(row.geometry.x),
            "id": source_id,
            "category": "Destination",
            "destination_type": row["destination_type"],
            "source": "OpenStreetMap",
            "source_id": source_id,
            "source_ids": [source_id],
            "source_url": (
                f"https://www.openstreetmap.org/{row['osm_element_type']}/{row['osm_id']}"
            ),
            "destination_score": score,
            "distance_to_marine_miles": float(row["distance_to_marine_miles"]),
            "destination_reasons": reasons,
            "osm_tags": _select_osm_output_tags(tags),
        }
        for output_field, tag_name in (
            ("website", "website"),
            ("operator", "operator"),
            ("access", "access"),
            ("fee", "fee"),
            ("opening_hours", "opening_hours"),
            ("wheelchair", "wheelchair"),
            ("direction", "direction"),
        ):
            value = website if output_field == "website" else tags.get(tag_name)
            if _clean_scalar(value):
                item[output_field] = value
        items.append(item)
    return items


def prepare_state_park_destination_items(
    parks: gpd.GeoDataFrame,
    mask_geom_m: Any,
    *,
    destination_nearshore_miles: float = DEFAULT_DESTINATION_NEARSHORE_MILES,
    destination_score_threshold: float = DEFAULT_DESTINATION_SCORE_THRESHOLD,
    distance_crs: str = DEFAULT_DISTANCE_CRS,
) -> list[dict[str, Any]]:
    """Convert official park polygons into scored marine-facing destinations."""
    parks = parks.dropna(subset=["geometry"]).copy()
    park_points = _marine_edge_points_for_geometries(
        parks,
        mask_geom_m,
        distance_crs=distance_crs,
    )
    park_points = filter_within_miles_of_mask(
        park_points,
        mask_geom_m,
        miles=destination_nearshore_miles,
        distance_crs=distance_crs,
    )
    name_col = "ParkName" if "ParkName" in park_points.columns else park_points.columns[0]

    items: list[dict[str, Any]] = []
    for _, row in park_points.iterrows():
        name = _clean_scalar(row.get(name_col))
        if not name:
            continue
        score, reasons = score_destination_candidate(
            name=name,
            destination_type="state_park",
            distance_to_marine_miles=float(row["distance_to_marine_miles"]),
            official_source=True,
        )
        if score is None or score < destination_score_threshold:
            continue
        raw_id = _first_value(
            row,
            ("ParkID", "Park_ID", "ParkCode", "OBJECTID", "ObjectID", "FID", "GlobalID"),
        )
        source_id = f"wa-state-parks:{raw_id or _slugify(name)}"
        items.append(
            {
                "type": "Park",
                "name": name,
                "latitude": float(row.geometry.y),
                "longitude": float(row.geometry.x),
                "id": source_id,
                "category": "Destination",
                "destination_type": "park",
                "source": "Washington State Parks",
                "source_id": source_id,
                "source_ids": [source_id],
                "source_url": WA_STATE_PARKS_LAYER,
                "destination_score": score,
                "distance_to_marine_miles": float(row["distance_to_marine_miles"]),
                "destination_reasons": reasons,
            }
        )
    return items


def prepare_ferry_destination_items(
    ferries: gpd.GeoDataFrame,
    mask_geom_m: Any,
    *,
    destination_nearshore_miles: float = DEFAULT_DESTINATION_NEARSHORE_MILES,
    destination_score_threshold: float = DEFAULT_DESTINATION_SCORE_THRESHOLD,
    distance_crs: str = DEFAULT_DISTANCE_CRS,
) -> list[dict[str, Any]]:
    """Convert ferry terminals to scored destination records."""
    ferries = ferries.dropna(subset=["geometry"]).copy()
    ferries = filter_within_miles_of_mask(
        ferries,
        mask_geom_m,
        miles=destination_nearshore_miles,
        distance_crs=distance_crs,
    )
    name_col = (
        "Display"
        if "Display" in ferries.columns
        else "Description" if "Description" in ferries.columns else "SR"
    )

    items: list[dict[str, Any]] = []
    for _, row in ferries.iterrows():
        name = _clean_scalar(row.get(name_col))
        if not name:
            continue
        score, reasons = score_destination_candidate(
            name=name,
            destination_type="ferry_terminal",
            distance_to_marine_miles=float(row["distance_to_marine_miles"]),
            official_source=True,
        )
        if score is None or score < destination_score_threshold:
            continue
        raw_id = _first_value(
            row,
            ("TerminalID", "Terminal_ID", "OBJECTID", "ObjectID", "FID", "GlobalID"),
        )
        source_id = f"wsdot-ferry:{raw_id or _slugify(name)}"
        items.append(
            {
                "type": "Ferry",
                "name": name,
                "latitude": float(row.geometry.y),
                "longitude": float(row.geometry.x),
                "id": source_id,
                "category": "Destination",
                "destination_type": "ferry_terminal",
                "source": "WSDOT",
                "source_id": source_id,
                "source_ids": [source_id],
                "source_url": WSDOT_FERRY_TERMINALS_LAYER,
                "destination_score": score,
                "distance_to_marine_miles": float(row["distance_to_marine_miles"]),
                "destination_reasons": reasons,
            }
        )
    return items


def prepare_marina_destination_items(
    marinas: gpd.GeoDataFrame,
    mask_geom_m: Any,
    *,
    nearshore_miles: float = DEFAULT_NEARSHORE_MILES,
    require_named_marinas: bool = DEFAULT_REQUIRE_NAMED_MARINAS,
    distance_crs: str = DEFAULT_DISTANCE_CRS,
    source_url: str = ECOLOGY_MARINAS_LAYER,
) -> list[dict[str, Any]]:
    """Convert Ecology marina points into named coastal destinations.

    The authoritative inventory is admitted independently of the broader OSM
    score threshold. A marina must still have usable geometry and a non-empty
    name; with ``require_named_marinas`` enabled, generic placeholders such as
    ``Marina`` or ``Unnamed Marina`` are removed.
    """
    marinas = marinas.dropna(subset=["geometry"]).copy()
    if marinas.empty:
        return []

    name_columns = tuple(
        column
        for column in (
            "FacilityName",
            "MarinaName",
            "Name",
            "NAME",
            # The legacy Ecology identify layer uses InventoryName for the
            # dataset title and NonGISAreaName for the actual facility name.
            "NonGISAreaName",
            "InventoryName",
        )
        if column in marinas.columns
    )
    if not name_columns:
        LOGGER.warning("Ecology marina layer has no recognized name column; skipping marinas.")
        return []

    marinas["_destination_name"] = marinas.apply(
        lambda row: _first_value(row, name_columns),
        axis=1,
    )
    marinas = marinas.loc[marinas["_destination_name"].notna()].copy()
    if require_named_marinas:
        marinas = marinas.loc[marinas["_destination_name"].map(is_meaningful_marina_name)].copy()
    if marinas.empty:
        return []

    source_id_columns = tuple(
        column
        for column in ("Marina_ID", "OBJECTID", "ObjectID", "FID", "GlobalID")
        if column in marinas.columns
    )
    dedupe_id_columns = ("Marina_ID",) if "Marina_ID" in marinas.columns else ()
    fallback_columns = tuple(
        column for column in ("WaterbodyName", "NonGISAreaName") if column in marinas.columns
    )
    keep_indices: list[Any] = []
    seen_keys: set[tuple[str, ...]] = set()
    for index, row in marinas.iterrows():
        marina_id = _first_value(row, dedupe_id_columns)
        if marina_id:
            dedupe_key = ("id", marina_id.casefold())
        else:
            fallback_values = tuple(
                _canonical_name(value)
                for value in (
                    row["_destination_name"],
                    *(_first_value(row, (column,)) or "" for column in fallback_columns),
                )
            )
            dedupe_key = ("name", *fallback_values)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        keep_indices.append(index)
    marinas = marinas.loc[keep_indices].copy()

    marinas = filter_within_miles_of_mask(
        marinas,
        mask_geom_m,
        miles=nearshore_miles,
        distance_crs=distance_crs,
    )

    items: list[dict[str, Any]] = []
    for _, row in marinas.iterrows():
        name = str(row["_destination_name"])
        distance = float(row["distance_to_marine_miles"])
        score, reasons = score_destination_candidate(
            name=name,
            destination_type="marina",
            distance_to_marine_miles=distance,
            official_source=True,
        )
        if score is None:
            continue
        raw_id = _first_value(row, source_id_columns)
        source_id = f"wa-ecology-marina:{raw_id or _slugify(name)}"
        items.append(
            {
                "type": "Marina",
                "name": name,
                "latitude": float(row.geometry.y),
                "longitude": float(row.geometry.x),
                "id": source_id,
                "category": "Destination",
                "destination_type": "marina",
                "source": "Washington Department of Ecology",
                "source_id": source_id,
                "source_ids": [source_id],
                "source_url": source_url,
                "destination_score": score,
                "distance_to_marine_miles": distance,
                "destination_reasons": reasons,
            }
        )
    return items


def _merge_duplicate_items(group: list[dict[str, Any]]) -> dict[str, Any]:
    source_priority = {
        "Washington State Parks": 3,
        "Washington Department of Ecology": 3,
        "WSDOT": 2,
        "OpenStreetMap": 1,
    }
    best = max(
        group,
        key=lambda item: (
            source_priority.get(str(item.get("source", "")), 0),
            float(item.get("destination_score", 0.0)),
        ),
    )
    merged = dict(best)
    merged["destination_score"] = max(float(item.get("destination_score", 0.0)) for item in group)

    source_ids: list[str] = []
    reasons: list[str] = []
    for item in group:
        for source_id in item.get("source_ids", []) or [item.get("source_id")]:
            if source_id and source_id not in source_ids:
                source_ids.append(str(source_id))
        for reason in item.get("destination_reasons", []) or []:
            if reason and reason not in reasons:
                reasons.append(str(reason))
        for field in (
            "website",
            "operator",
            "access",
            "fee",
            "opening_hours",
            "wheelchair",
            "direction",
            "osm_tags",
        ):
            if field not in merged and field in item:
                merged[field] = item[field]
    if source_ids:
        merged["source_ids"] = source_ids
    if reasons:
        merged["destination_reasons"] = reasons
    return merged


def deduplicate_destination_items(
    items: list[dict[str, Any]],
    *,
    distance_crs: str = DEFAULT_DISTANCE_CRS,
    max_distance_m: float = 3000.0,
) -> list[dict[str, Any]]:
    """Merge same-name records that refer to nearly the same destination."""
    if len(items) < 2:
        return items

    points = gpd.GeoDataFrame(
        {
            "index": range(len(items)),
            "name_key": [_canonical_name(item["name"]) for item in items],
            "legacy_type": [item["type"] for item in items],
        },
        geometry=[Point(item["longitude"], item["latitude"]) for item in items],
        crs="EPSG:4326",
    ).to_crs(distance_crs)

    output: list[dict[str, Any]] = []
    for _, same_name in points.groupby(["legacy_type", "name_key"], sort=True):
        remaining = list(same_name.index)
        while remaining:
            seed_index = remaining.pop(0)
            seed_geometry = points.loc[seed_index].geometry
            cluster = [seed_index]
            still_remaining: list[int] = []
            for candidate_index in remaining:
                if seed_geometry.distance(points.loc[candidate_index].geometry) <= max_distance_m:
                    cluster.append(candidate_index)
                else:
                    still_remaining.append(candidate_index)
            remaining = still_remaining
            output.append(_merge_duplicate_items([items[index] for index in cluster]))
    return output


def build_places_of_interest(
    *,
    cache_dir: str | Path,
    bbox_4326: Any = DEFAULT_WA_BBOX_4326,
    nearshore_miles: float = DEFAULT_NEARSHORE_MILES,
    destination_nearshore_miles: float = DEFAULT_DESTINATION_NEARSHORE_MILES,
    destination_score_threshold: float = DEFAULT_DESTINATION_SCORE_THRESHOLD,
    distance_crs: str = DEFAULT_DISTANCE_CRS,
    sources: Mapping[str, Any] | None = None,
    include_osm: bool = True,
    include_osm_broad_areas: bool = False,
    osm_element_types: tuple[str, ...] = ("node",),
    osm_refresh: bool = False,
    osm_required: bool = False,
    include_ferries: bool = True,
    include_marinas: bool = True,
    require_named_marinas: bool = DEFAULT_REQUIRE_NAMED_MARINAS,
    source_frames: Mapping[str, gpd.GeoDataFrame] | None = None,
    downloaded_osm_payload: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build an automated, scored catalog of nearshore visitor destinations.

    The four legacy fields remain in every record. Named marinas are included as
    recognizable coastal destinations, without claiming they are verified orca
    viewpoints.
    """
    registry = load_places_of_interest_sources(sources)
    mask_m = build_nearshore_mask(
        bbox_4326=bbox_4326,
        cache_dir=cache_dir,
        distance_crs=distance_crs,
        sources=registry,
    )

    items: list[dict[str, Any]] = []

    source_frames = source_frames or {}
    parks = source_frames.get("wa_state_parks")
    if parks is None:
        parks = arcgis_query_first_available_geojson(
            _source_entry(registry, "wa_state_parks", WA_STATE_PARKS_LAYER),
            bbox_4326=bbox_4326,
        )
    items.extend(
        prepare_state_park_destination_items(
            parks,
            mask_m,
            destination_nearshore_miles=destination_nearshore_miles,
            destination_score_threshold=destination_score_threshold,
            distance_crs=distance_crs,
        )
    )

    if include_osm:
        osm_source = _source_entry(
            registry,
            "openstreetmap_overpass",
            OSM_OVERPASS_URL,
            source_type="overpass_api",
        )
        osm_queries = build_osm_destination_queries(
            bbox_4326,
            include_marinas=include_marinas,
            include_broad_areas=include_osm_broad_areas,
            element_types=osm_element_types,
        )
        query_hash = hashlib.sha256(
            "\n-- batch --\n".join(osm_queries).encode("utf-8")
        ).hexdigest()[:16]
        osm_cache_path = Path(cache_dir) / f"osm_destinations_{query_hash}.json"
        try:
            osm_payload = downloaded_osm_payload
            if osm_payload is None:
                osm_payload = overpass_query_batches_first_available_json(
                    osm_source,
                    osm_queries,
                    cache_path=osm_cache_path,
                    refresh=osm_refresh,
                )
            items.extend(
                prepare_osm_destination_items(
                    osm_payload,
                    mask_m,
                    destination_nearshore_miles=destination_nearshore_miles,
                    destination_score_threshold=destination_score_threshold,
                    include_marinas=include_marinas,
                    require_named_marinas=require_named_marinas,
                    distance_crs=distance_crs,
                )
            )
        except Exception:
            if osm_required:
                raise
            LOGGER.exception(
                "OpenStreetMap destination discovery failed; continuing with official sources."
            )

    if include_ferries:
        ferries = source_frames.get("wsdot_ferry_terminals")
        if ferries is None:
            ferries = arcgis_query_first_available_geojson(
                _source_entry(registry, "wsdot_ferry_terminals", WSDOT_FERRY_TERMINALS_LAYER),
                bbox_4326=bbox_4326,
            )
        items.extend(
            prepare_ferry_destination_items(
                ferries,
                mask_m,
                destination_nearshore_miles=destination_nearshore_miles,
                destination_score_threshold=destination_score_threshold,
                distance_crs=distance_crs,
            )
        )

    if include_marinas:
        marina_source = _source_entry(
            registry,
            "ecology_marinas",
            ECOLOGY_MARINAS_LAYER,
        )
        marinas = source_frames.get("ecology_marinas")
        if marinas is None:
            marinas = arcgis_query_first_available_geojson(
                marina_source,
                bbox_4326=bbox_4326,
            )
        items.extend(
            prepare_marina_destination_items(
                marinas,
                mask_m,
                nearshore_miles=nearshore_miles,
                require_named_marinas=require_named_marinas,
                distance_crs=distance_crs,
                source_url=marina_source.url,
            )
        )

    items = deduplicate_destination_items(items, distance_crs=distance_crs)
    return normalize_place_of_interest_items(items)


def write_preprocessed_places_of_interest(
    *,
    output_path: str | Path,
    cache_dir: str | Path,
    bbox_4326: Any = DEFAULT_WA_BBOX_4326,
    nearshore_miles: float = DEFAULT_NEARSHORE_MILES,
    destination_nearshore_miles: float = DEFAULT_DESTINATION_NEARSHORE_MILES,
    destination_score_threshold: float = DEFAULT_DESTINATION_SCORE_THRESHOLD,
    distance_crs: str = DEFAULT_DISTANCE_CRS,
    sources: Mapping[str, Any] | None = None,
    metadata_path: str | Path | None = None,
    include_osm: bool = True,
    include_osm_broad_areas: bool = False,
    osm_element_types: tuple[str, ...] = ("node",),
    osm_refresh: bool = False,
    osm_required: bool = False,
    include_ferries: bool = True,
    include_marinas: bool = True,
    require_named_marinas: bool = DEFAULT_REQUIRE_NAMED_MARINAS,
) -> Path:
    """Build and write the domain-level preprocessed POI file."""
    registry = load_places_of_interest_sources(sources)
    items = build_places_of_interest(
        cache_dir=cache_dir,
        bbox_4326=bbox_4326,
        nearshore_miles=nearshore_miles,
        destination_nearshore_miles=destination_nearshore_miles,
        destination_score_threshold=destination_score_threshold,
        distance_crs=distance_crs,
        sources=registry,
        include_osm=include_osm,
        include_osm_broad_areas=include_osm_broad_areas,
        osm_element_types=osm_element_types,
        osm_refresh=osm_refresh,
        osm_required=osm_required,
        include_ferries=include_ferries,
        include_marinas=include_marinas,
        require_named_marinas=require_named_marinas,
    )
    out_path = write_places_of_interest(items, output_path)

    used_source_names = {
        "natural_earth_ocean",
        "natural_earth_marine_polys",
        "wa_state_parks",
    }
    if include_osm:
        used_source_names.add("openstreetmap_overpass")
    if include_ferries:
        used_source_names.add("wsdot_ferry_terminals")
    if include_marinas:
        used_source_names.add("ecology_marinas")
    used_registry = {name: source for name, source in registry.items() if name in used_source_names}

    write_places_of_interest_metadata(
        out_path,
        used_registry,
        metadata_path=metadata_path,
        pipeline={
            "nearshore_miles": nearshore_miles,
            "destination_nearshore_miles": destination_nearshore_miles,
            "destination_score_threshold": destination_score_threshold,
            "distance_crs": distance_crs,
            "include_osm": include_osm,
            "include_osm_broad_areas": include_osm_broad_areas,
            "osm_element_types": list(osm_element_types),
            "osm_refresh": osm_refresh,
            "osm_required": osm_required,
            "include_ferries": include_ferries,
            "include_marinas": include_marinas,
            "require_named_marinas": require_named_marinas,
            "osm_cache": "query-hash keyed; reused unless osm_refresh=true",
            "score_semantics": "heuristic candidate quality, not orca sighting probability",
        },
    )
    return out_path


def build(
    config_path: str | Path = "config/data/human/demography_and_presence/places.yaml",
    allow_partial: bool = False,
    *,
    overwrite: bool = False,
) -> Path:
    """Build the app-only normalized catalog from downloaded snapshots."""
    del allow_partial
    from human.utils.artifacts import (
        MeasurementStatus,
        artifact_record,
        atomic_write_json,
        load_manifest,
        manifest_payload,
        write_manifest,
    )

    from .config import load_places_config

    cfg = load_places_config(config_path)
    raw_manifest = load_manifest(cfg.raw_manifest_path)
    inventory = json.loads(cfg.inventory_path.read_text(encoding="utf-8"))
    source_frames = {
        name: gpd.read_parquet(inventory[name])
        for name in ("wa_state_parks", "wsdot_ferry_terminals", "ecology_marinas")
    }
    osm_payload = json.loads(Path(inventory["openstreetmap_overpass"]).read_text(encoding="utf-8"))
    params = cfg.parameters
    items = build_places_of_interest(
        cache_dir=cfg.cache_dir,
        bbox_4326=box(*cfg.bbox),
        nearshore_miles=float(params.get("nearshore_miles", DEFAULT_NEARSHORE_MILES)),
        destination_nearshore_miles=float(
            params.get("destination_nearshore_miles", DEFAULT_DESTINATION_NEARSHORE_MILES)
        ),
        destination_score_threshold=float(
            params.get("destination_score_threshold", DEFAULT_DESTINATION_SCORE_THRESHOLD)
        ),
        distance_crs=str(params.get("distance_crs", DEFAULT_DISTANCE_CRS)),
        sources=cfg.sources,
        include_osm=bool(params.get("include_osm", True)),
        include_ferries=bool(params.get("include_ferries", True)),
        include_marinas=bool(params.get("include_marinas", True)),
        require_named_marinas=bool(params.get("require_named_marinas", True)),
        source_frames=source_frames,
        downloaded_osm_payload=osm_payload,
    )
    if cfg.catalog_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {cfg.catalog_path}")
    atomic_write_json(cfg.catalog_path, items, overwrite=overwrite)
    frame = pd.DataFrame(items)
    artifact = artifact_record(cfg.catalog_path, dataset_id="human.places.catalog", frame=frame)
    manifest = manifest_payload(
        config=cfg.human,
        stage="build",
        artifacts=[artifact],
        sources=raw_manifest["sources"],
        inputs=raw_manifest["artifacts"],
        source_completeness="complete",
        measurement_statuses=[MeasurementStatus.OBSERVED, MeasurementStatus.DERIVED],
        attribution=raw_manifest["attribution"],
        licenses=raw_manifest["licenses"],
        spatial_bounds={
            "west": cfg.bbox[0],
            "south": cfg.bbox[1],
            "east": cfg.bbox[2],
            "north": cfg.bbox[3],
        },
        limitations=["Destination scores are app-ranking heuristics and are not model predictors."],
    )
    write_manifest(cfg.manifest_path, manifest)
    return cfg.catalog_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/data/human/demography_and_presence/places.yaml")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    print(build(args.config, allow_partial=args.allow_partial, overwrite=args.overwrite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
