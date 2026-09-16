"""Download helpers for human places-of-interest source layers."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from shapely.geometry import box

from human.utils.acquisition import materialize_source
from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_json,
    atomic_write_parquet,
    manifest_payload,
    source_record,
    write_manifest,
)

from .config import DEFAULT_CONFIG_PATH, load_places_config

WSDOT_FERRY_TERMINALS_LAYER = (
    "https://data.wsdot.wa.gov/arcgis/rest/services/Shared/FerryRoutes/MapServer/0"
)
WA_STATE_PARKS_LAYER = (
    "https://services2.arcgis.com/6Miy5NqQWjMYTGFY/ArcGIS/rest/services/"
    "WA_State_Parks_WFL1/FeatureServer/3"
)
ECOLOGY_MARINAS_LAYER = (
    "https://gis.ecology.wa.gov/serverext/rest/services/GIS/CoastalAtlas/MapServer/116"
)
ECOLOGY_MARINAS_IDENTIFY_LAYER = (
    "https://gis.ecology.wa.gov/serverext/rest/services/GIS/ECY_Identify/MapServer/175"
)

OSM_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_OVERPASS_FALLBACK_URL = "https://overpass.private.coffee/api/interpreter"

NE_OCEAN_ZIP_URL = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_ocean.zip"
NE_MARINE_POLYS_ZIP_URL = (
    "https://naturalearth.s3.amazonaws.com/10m_physical/" "ne_10m_geography_marine_polys.zip"
)

DEFAULT_HTTP_HEADERS = {
    # Public services should be able to identify the application making requests.
    "User-Agent": "OrcaCastDataPrep/1.1 (+https://github.com/orcacast)",
    "Accept": "application/json, application/geo+json, text/plain, */*",
}


@dataclass(frozen=True)
class SourceRegistryEntry:
    """Configured external source used to build places-of-interest artifacts."""

    name: str
    type: str
    url: str
    version: str | None = None
    description: str | None = None
    attribution: str | None = None
    license: str | None = None
    fallback_urls: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(
        cls,
        name: str,
        values: Mapping[str, Any],
        *,
        default: "SourceRegistryEntry | None" = None,
    ) -> "SourceRegistryEntry":
        payload = asdict(default) if default is not None else {"name": name}
        payload.update(dict(values or {}))
        payload["name"] = str(payload.get("name") or name)
        if not payload.get("type"):
            raise ValueError(f"Source registry entry '{name}' is missing required field: type")
        if not payload.get("url"):
            raise ValueError(f"Source registry entry '{name}' is missing required field: url")
        configured_url = str(payload["url"])
        fallback_urls = payload.get("fallback_urls") or ()
        if isinstance(fallback_urls, str):
            fallback_urls = (fallback_urls,)
        else:
            fallback_urls = tuple(str(url) for url in fallback_urls)
        if default is not None and configured_url != default.url:
            fallback_urls = (*fallback_urls, default.url, *default.fallback_urls)
        return cls(
            name=payload["name"],
            type=str(payload["type"]),
            url=configured_url,
            version=str(payload["version"]) if payload.get("version") is not None else None,
            description=(
                str(payload["description"]) if payload.get("description") is not None else None
            ),
            attribution=(
                str(payload["attribution"]) if payload.get("attribution") is not None else None
            ),
            license=str(payload["license"]) if payload.get("license") is not None else None,
            fallback_urls=fallback_urls,
        )

    def urls(self) -> tuple[str, ...]:
        """Return primary and fallback URLs in query order without duplicates."""
        urls: list[str] = []
        for url in (self.url, *self.fallback_urls):
            if url and url not in urls:
                urls.append(url)
        return tuple(urls)

    def to_metadata(self) -> dict[str, Any]:
        metadata = {key: value for key, value in asdict(self).items() if value is not None}
        if not metadata.get("fallback_urls"):
            metadata.pop("fallback_urls", None)
        return metadata


DEFAULT_PLACES_OF_INTEREST_SOURCES: dict[str, SourceRegistryEntry] = {
    "wsdot_ferry_terminals": SourceRegistryEntry(
        name="wsdot_ferry_terminals",
        type="arcgis_feature_layer",
        url=WSDOT_FERRY_TERMINALS_LAYER,
        description="Washington State Department of Transportation ferry terminal layer.",
    ),
    "wa_state_parks": SourceRegistryEntry(
        name="wa_state_parks",
        type="arcgis_feature_layer",
        url=WA_STATE_PARKS_LAYER,
        description="Washington State Parks feature layer.",
    ),
    "ecology_marinas": SourceRegistryEntry(
        name="ecology_marinas",
        type="arcgis_feature_layer",
        url=ECOLOGY_MARINAS_LAYER,
        fallback_urls=(ECOLOGY_MARINAS_IDENTIFY_LAYER,),
        description=(
            "Washington Department of Ecology Coastal Atlas marina inventory layer. "
            "The older ECY_Identify layer is retained as a fallback only."
        ),
    ),
    "openstreetmap_overpass": SourceRegistryEntry(
        name="openstreetmap_overpass",
        type="overpass_api",
        url=OSM_OVERPASS_URL,
        fallback_urls=(OSM_OVERPASS_FALLBACK_URL,),
        version="live",
        description=(
            "OpenStreetMap destination-like features queried through the Overpass API. "
            "Responses are cached by query hash to minimize public API load."
        ),
        attribution="© OpenStreetMap contributors",
        license="Open Data Commons Open Database License (ODbL) 1.0",
    ),
    "natural_earth_ocean": SourceRegistryEntry(
        name="natural_earth_ocean",
        type="zip_shapefile",
        url=NE_OCEAN_ZIP_URL,
        version="10m",
        description="Natural Earth ocean polygons.",
    ),
    "natural_earth_marine_polys": SourceRegistryEntry(
        name="natural_earth_marine_polys",
        type="zip_shapefile",
        url=NE_MARINE_POLYS_ZIP_URL,
        version="10m",
        description="Natural Earth geography marine polygons.",
    ),
}


def load_places_of_interest_sources(
    configured_sources: Mapping[str, Any] | None = None,
) -> dict[str, SourceRegistryEntry]:
    """Return default POI sources with optional config overrides."""
    sources = dict(DEFAULT_PLACES_OF_INTEREST_SOURCES)
    for name, values in (configured_sources or {}).items():
        if isinstance(values, SourceRegistryEntry):
            sources[str(name)] = values
            continue
        if not isinstance(values, Mapping):
            raise ValueError(f"Source registry entry '{name}' must be a mapping.")
        sources[str(name)] = SourceRegistryEntry.from_mapping(
            str(name),
            values,
            default=sources.get(str(name)),
        )
    return sources


def ensure_arcgis_query_url(layer_url: str) -> str:
    """Return the ArcGIS REST query endpoint for a layer URL."""
    clean_url = layer_url.rstrip("/")
    return clean_url if clean_url.endswith("/query") else f"{clean_url}/query"


def _request_with_headers(method: str, url: str, **kwargs: Any) -> Any:
    """Issue an HTTP request with headers suitable for public GIS services."""
    import requests

    headers = dict(DEFAULT_HTTP_HEADERS)
    headers.update(kwargs.pop("headers", {}) or {})
    return requests.request(method, url, headers=headers, **kwargs)


def _get_with_headers(url: str, **kwargs: Any) -> Any:
    """Issue a GET request with headers compatible with public agency GIS servers."""
    return _request_with_headers("GET", url, **kwargs)


def arcgis_layer_meta(layer_url: str) -> dict[str, Any]:
    """Fetch ArcGIS layer metadata."""
    response = _get_with_headers(layer_url.rstrip("/"), params={"f": "pjson"}, timeout=60)
    response.raise_for_status()
    return response.json()


def arcgis_query_all_geojson(
    layer_url: str,
    *,
    where: str = "1=1",
    out_fields: str = "*",
    out_sr: int = 4326,
    bbox_4326: Any = None,
    max_record_count: int | None = None,
) -> Any:
    """Page through an ArcGIS REST layer and return a GeoDataFrame in EPSG:4326.

    The layer metadata endpoint is a convenience, not a hard dependency. A few
    public ArcGIS servers intermittently deny ``?f=pjson`` while the query
    endpoint remains usable, so metadata failures fall back to a safe page size.
    """
    import geopandas as gpd

    if max_record_count is None:
        try:
            meta = arcgis_layer_meta(layer_url)
            max_record_count = int(meta.get("maxRecordCount", 1000)) or 1000
        except Exception:
            max_record_count = 1000

    params: dict[str, Any] = {
        "f": "geojson",
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true",
        "outSR": out_sr,
        "resultOffset": 0,
        "resultRecordCount": max_record_count,
    }
    if bbox_4326 is not None:
        minx, miny, maxx, maxy = bbox_4326.bounds
        params.update(
            {
                "geometry": f"{minx},{miny},{maxx},{maxy}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
            }
        )

    features: list[dict[str, Any]] = []
    query_url = ensure_arcgis_query_url(layer_url)
    while True:
        response = _get_with_headers(query_url, params=params, timeout=120)
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            message = payload.get("error", {}).get("message") or payload["error"]
            raise RuntimeError(f"ArcGIS query failed for {query_url}: {message}")
        batch = payload.get("features", []) or []
        features.extend(batch)
        if len(batch) < max_record_count:
            break
        params["resultOffset"] += max_record_count

    if not features:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")


def arcgis_query_first_available_geojson(
    source: SourceRegistryEntry,
    *,
    where: str = "1=1",
    out_fields: str = "*",
    out_sr: int = 4326,
    bbox_4326: Any = None,
) -> Any:
    """Query a source's primary URL, then fallbacks, returning the first success."""
    errors: list[str] = []
    for url in source.urls():
        try:
            return arcgis_query_all_geojson(
                url,
                where=where,
                out_fields=out_fields,
                out_sr=out_sr,
                bbox_4326=bbox_4326,
            )
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    joined = "; ".join(errors)
    raise RuntimeError(f"All ArcGIS URLs failed for source '{source.name}': {joined}")


def build_osm_destination_query(
    bbox_4326: Any,
    *,
    timeout_seconds: int = 180,
    include_marinas: bool = True,
    include_broad_areas: bool = False,
    element_type: str = "nwr",
) -> str:
    """Build a selective Overpass QL query for named, destination-like features.

    Named marinas are included by default as recognizable coastal destinations.
    Boat launches and other unnamed infrastructure remain excluded.
    """
    if hasattr(bbox_4326, "bounds"):
        minx, miny, maxx, maxy = bbox_4326.bounds
    else:
        minx, miny, maxx, maxy = bbox_4326
    bbox = f"{miny:.7f},{minx:.7f},{maxy:.7f},{maxx:.7f}"
    selectors = [
        '["name"]["tourism"="viewpoint"]',
        '["name"]["natural"="beach"]',
        '["name"]["man_made"~"^(lighthouse|pier)$"]',
        '["name"]["man_made"="tower"]["tower:type"="observation"]',
        '["name"]["amenity"="ferry_terminal"]',
    ]
    if include_broad_areas:
        selectors.extend(
            [
                '["name"]["leisure"~"^(park|nature_reserve)$"]',
                '["name"]["boundary"~"^(national_park|protected_area)$"]',
            ]
        )
    if include_marinas:
        selectors.append('["name"]["leisure"="marina"]')
    if element_type not in {"nwr", "node", "way", "relation"}:
        raise ValueError(f"Unsupported Overpass element type: {element_type}")
    statements = "\n".join(f"  {element_type}{selector}({bbox});" for selector in selectors)
    return (
        f"[out:json][timeout:{int(timeout_seconds)}];\n"
        "(\n"
        f"{statements}\n"
        ");\n"
        "out tags center qt;\n"
    )


def build_osm_destination_queries(
    bbox_4326: Any,
    *,
    timeout_seconds: int = 180,
    include_marinas: bool = True,
    include_broad_areas: bool = False,
    element_types: tuple[str, ...] = ("node",),
) -> tuple[str, ...]:
    """Build independently executable queries for the requested OSM primitives.

    Point markers only require nodes by default. Ways and relations may be
    requested explicitly if a future consumer needs their representative
    centers, without restoring the expensive monolithic ``nwr`` query.
    """
    return tuple(
        build_osm_destination_query(
            bbox_4326,
            timeout_seconds=timeout_seconds,
            include_marinas=include_marinas,
            include_broad_areas=include_broad_areas,
            element_type=element_type,
        )
        for element_type in element_types
    )


def overpass_query_json(
    endpoint_url: str,
    query: str,
    *,
    timeout_seconds: int = 240,
) -> dict[str, Any]:
    """Execute one Overpass QL query and validate the JSON response."""
    response = _request_with_headers(
        "POST",
        endpoint_url,
        data={"data": query},
        timeout=(30, timeout_seconds),
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        snippet = response.text[:500].replace("\n", " ")
        raise RuntimeError(f"Overpass returned non-JSON content: {snippet}") from exc

    elements = payload.get("elements")
    if not isinstance(elements, list):
        remark = payload.get("remark") or "response has no elements list"
        raise RuntimeError(f"Invalid Overpass response from {endpoint_url}: {remark}")
    remark = payload.get("remark")
    if remark:
        raise RuntimeError(f"Incomplete Overpass response from {endpoint_url}: {remark}")
    return payload


def overpass_query_first_available_json(
    source: SourceRegistryEntry,
    query: str,
    *,
    cache_path: str | Path | None = None,
    refresh: bool = False,
    timeout_seconds: int = 240,
) -> dict[str, Any]:
    """Return cached Overpass JSON or query primary/fallback endpoints.

    Cache files are written atomically. Callers should include a query hash in
    ``cache_path`` so a changed bbox or selector set cannot reuse stale results.
    """
    path = Path(cache_path) if cache_path is not None else None
    if path is not None and path.exists() and path.stat().st_size > 0 and not refresh:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload.get("elements"), list) and not payload.get("remark"):
            return payload

    errors: list[str] = []
    for url in source.urls():
        try:
            payload = overpass_query_json(url, query, timeout_seconds=timeout_seconds)
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = path.with_suffix(path.suffix + ".tmp")
                temp_path.write_text(
                    json.dumps(payload, separators=(",", ":")),
                    encoding="utf-8",
                )
                temp_path.replace(path)
            return payload
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    joined = "; ".join(errors)
    raise RuntimeError(f"All Overpass URLs failed for source '{source.name}': {joined}")


def overpass_query_batches_first_available_json(
    source: SourceRegistryEntry,
    queries: tuple[str, ...],
    *,
    cache_path: str | Path | None = None,
    refresh: bool = False,
    timeout_seconds: int = 240,
) -> dict[str, Any]:
    """Execute complete Overpass batches and atomically cache their union."""
    path = Path(cache_path) if cache_path is not None else None
    if path is not None and path.exists() and path.stat().st_size > 0 and not refresh:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload.get("elements"), list) and not payload.get("remark"):
            return payload

    merged_elements: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    osm3s: dict[str, Any] | None = None
    generator: str | None = None
    for query in queries:
        payload = overpass_query_first_available_json(
            source,
            query,
            timeout_seconds=timeout_seconds,
        )
        if isinstance(payload.get("osm3s"), dict):
            osm3s = dict(payload["osm3s"])
        if payload.get("generator"):
            generator = str(payload["generator"])
        for element in payload["elements"]:
            if not isinstance(element, dict):
                continue
            key = (str(element.get("type", "")), str(element.get("id", "")))
            if key in seen:
                continue
            seen.add(key)
            merged_elements.append(element)

    merged: dict[str, Any] = {"elements": merged_elements}
    if generator:
        merged["generator"] = generator
    if osm3s:
        merged["osm3s"] = osm3s
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(merged, separators=(",", ":")),
            encoding="utf-8",
        )
        temp_path.replace(path)
    return merged


def download_if_needed(url: str, dest: str | Path) -> Path:
    """Download a remote file unless a non-empty cached copy already exists."""
    out_path = Path(dest)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    with _get_with_headers(url, stream=True, timeout=180) as response:
        response.raise_for_status()
        with out_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    return out_path


def download(config_path: str | Path = DEFAULT_CONFIG_PATH, overwrite: bool = False) -> Path:
    """Snapshot all configured GIS and OpenStreetMap inputs."""
    cfg = load_places_config(config_path)
    bbox = box(*cfg.bbox)
    registry = load_places_of_interest_sources(cfg.sources)
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    inventory: dict[str, str] = {}
    source_rows: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    for name, filename in (
        ("natural_earth_ocean", "natural_earth_ocean.zip"),
        ("natural_earth_marine_polys", "natural_earth_marine_polys.zip"),
    ):
        entry = registry[name]
        path = materialize_source(entry.url, cfg.cache_dir / filename, overwrite=overwrite)
        inventory[name] = str(path)
        metadata = cfg.sources[name]
        source_rows.append(
            source_record(
                path,
                name=name,
                provider=str(metadata.get("provider", name)),
                license_name=str(metadata.get("license", "unresolved")),
                attribution=str(metadata.get("attribution", metadata.get("provider", name))),
                url=entry.url,
            )
        )
        artifacts.append(artifact_record(path, dataset_id=f"human.places.raw.{name}"))

    for name in ("wa_state_parks", "wsdot_ferry_terminals", "ecology_marinas"):
        path = cfg.cache_dir / f"{name}.parquet"
        if overwrite or not path.exists():
            frame = arcgis_query_first_available_geojson(registry[name], bbox_4326=bbox)
            atomic_write_parquet(frame, path, overwrite=overwrite)
        else:
            import geopandas as gpd

            frame = gpd.read_parquet(path)
        inventory[name] = str(path)
        metadata = cfg.sources[name]
        source_rows.append(
            source_record(
                path,
                name=name,
                provider=str(metadata.get("provider", name)),
                license_name=str(metadata.get("license", "unresolved")),
                attribution=str(metadata.get("attribution", metadata.get("provider", name))),
                url=registry[name].url,
            )
        )
        artifacts.append(artifact_record(path, dataset_id=f"human.places.raw.{name}", frame=frame))

    osm_path = cfg.cache_dir / "openstreetmap_overpass.json"
    if overwrite or not osm_path.exists():
        parameters = cfg.parameters
        queries = build_osm_destination_queries(
            bbox,
            include_marinas=bool(parameters.get("include_marinas", True)),
            include_broad_areas=False,
            element_types=("node",),
        )
        payload = overpass_query_batches_first_available_json(
            registry["openstreetmap_overpass"], queries
        )
        atomic_write_json(osm_path, payload, overwrite=overwrite)
    inventory["openstreetmap_overpass"] = str(osm_path)
    metadata = cfg.sources["openstreetmap_overpass"]
    source_rows.append(
        source_record(
            osm_path,
            name="openstreetmap_overpass",
            provider=str(metadata.get("provider", "OpenStreetMap contributors")),
            license_name=str(metadata.get("license", "ODbL-1.0")),
            attribution=str(metadata.get("attribution", "OpenStreetMap contributors")),
            url=registry["openstreetmap_overpass"].url,
        )
    )
    artifacts.append(artifact_record(osm_path, dataset_id="human.places.raw.openstreetmap"))
    atomic_write_json(cfg.inventory_path, inventory, overwrite=True)
    artifacts.append(artifact_record(cfg.inventory_path, dataset_id="human.places.raw.inventory"))
    manifest = manifest_payload(
        config=cfg.human,
        stage="download",
        artifacts=artifacts,
        sources=source_rows,
        source_completeness="complete",
        measurement_statuses=[MeasurementStatus.OBSERVED],
        attribution=[row["attribution"] for row in source_rows],
        licenses=[row["license"] for row in source_rows],
        spatial_bounds={
            "west": cfg.bbox[0],
            "south": cfg.bbox[1],
            "east": cfg.bbox[2],
            "north": cfg.bbox[3],
        },
    )
    write_manifest(cfg.raw_manifest_path, manifest)
    return cfg.inventory_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    print(download(args.config, overwrite=args.overwrite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
