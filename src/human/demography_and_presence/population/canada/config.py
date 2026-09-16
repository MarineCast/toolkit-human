"""Configuration objects for the British Columbia population pipeline."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..common.config import (
    as_bool,
    as_optional_tuple_str,
    as_tuple_float,
    as_tuple_str,
    country_population_config,
    require_keys,
    resolve_optional_path,
    resolve_path,
    section,
    validate_distance_thresholds,
)


@dataclass(frozen=True)
class CanadaSourceConfig:
    """Statistics Canada source and schema settings."""

    enabled: bool
    census_year: int
    province_name: str
    province_abbr: str
    province_code: str
    geography_level: str
    population_variable_candidates: tuple[str, ...]
    source: str
    api_or_download_mode: str
    statcan_profile_wds_url: str
    statcan_sdmx_base_url: str
    statcan_dataflow: str
    statcan_frequency: str
    statcan_gender_code: str
    statcan_population_characteristic_code: str
    statcan_statistic_code: str
    statcan_request_chunk_size: int
    statcan_timeout_seconds: int
    statcan_dguid_prefix: str
    geography_path: Path | None
    population_table_path: Path | None
    population_download_url: str | None
    population_download_filename: str
    geography_download_url: str | None
    geography_download_filename: str
    geography_join_column_candidates: tuple[str, ...]
    population_join_column_candidates: tuple[str, ...]
    population_geoid_col: str | None
    population_value_col: str | None


@dataclass(frozen=True)
class CanadaPathsConfig:
    """Canada input, cache, and output paths."""

    raw_dir: Path
    processed_dir: Path
    output_parquet: Path
    population_api_cache: Path


@dataclass(frozen=True)
class CanadaWaterDistanceConfig:
    """Canada candidate-domain and water-distance settings."""

    candidate_distance_miles: float
    final_max_distance_miles: float
    h3_prefilter_distance_miles: float
    filter_h3_to_source_geographies: bool
    distance_basis: str
    distance_threshold_miles: tuple[float, ...]
    decay: str


@dataclass(frozen=True)
class CanadaRuntimeConfig:
    """Canada overwrite, debug, and allocation chunk settings."""

    overwrite_downloads: bool
    overwrite_raw_cache: bool
    overwrite_output: bool
    write_debug_geo: bool
    debug_output_path: Path
    allocation_chunk_parent_resolution: int


@dataclass(frozen=True)
class WaterAttributeRule:
    """Case-insensitive exact-match rule for scoping water features."""

    column: str
    values: tuple[str, ...]


# Internal/legacy descriptive name used by geography helpers.
WaterAttributeFilter = WaterAttributeRule


@dataclass(frozen=True)
class CanadaWaterScopeConfig:
    """Deterministic ordered rules for Canada water-polygon selection."""

    include_rules: tuple[WaterAttributeRule, ...]
    exclude_rules: tuple[WaterAttributeRule, ...]
    require_include_match: bool

    @property
    def filters(self) -> tuple[WaterAttributeRule, ...]:
        """Compatibility alias for ordered include rules."""
        return self.include_rules

    @property
    def excludes(self) -> tuple[WaterAttributeRule, ...]:
        """Compatibility alias for exclusion rules."""
        return self.exclude_rules

    @property
    def allow_unfiltered(self) -> bool:
        """Whether unmatched include rules may fall back to all non-excluded water."""
        return not self.require_include_match


@dataclass(frozen=True)
class CanadaPopulationConfig:
    """Complete British Columbia Census 2021 pipeline configuration."""

    canada_area_crs: str
    output_crs: str
    h3_resolution: int
    water_polygons_path: Path
    canada: CanadaSourceConfig
    canada_paths: CanadaPathsConfig
    canada_water_distance: CanadaWaterDistanceConfig
    water_scope: CanadaWaterScopeConfig
    runtime: CanadaRuntimeConfig
    config_path: Path

    @property
    def area_crs(self) -> str:
        """Canonical internal name used by shared geospatial operations."""
        return self.canada_area_crs

    @property
    def source(self) -> CanadaSourceConfig:
        """Canonical source configuration alias."""
        return self.canada

    @property
    def paths(self) -> CanadaPathsConfig:
        """Canonical paths configuration alias."""
        return self.canada_paths

    @property
    def water_distance(self) -> CanadaWaterDistanceConfig:
        """Canonical water-distance configuration alias."""
        return self.canada_water_distance


def _parse_rule(raw: Any, *, key: str) -> WaterAttributeRule:
    if not isinstance(raw, dict):
        raise ValueError(f"{key} entries must be mappings.")
    column = str(raw.get("column", "")).strip()
    values = as_tuple_str(raw.get("values"), f"{key}.values")
    if not column:
        raise ValueError(f"{key}.column must be non-empty.")
    return WaterAttributeRule(column=column, values=values)


def _parse_rules(raw: Any, *, key: str) -> tuple[WaterAttributeRule, ...]:
    if raw is None:
        return ()
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be a mapping or list of mappings.")
    return tuple(_parse_rule(item, key=key) for item in raw)


def _default_water_scope(
    province_name: str,
    province_abbr: str,
) -> CanadaWaterScopeConfig:
    """Return safe defaults that must match an explicit water attribute."""
    return CanadaWaterScopeConfig(
        include_rules=(
            WaterAttributeRule(
                column="AREA",
                values=(province_name, province_abbr, "BRITISH_COLUMBIA"),
            ),
            WaterAttributeRule(column="NAME", values=("CANADA",)),
        ),
        exclude_rules=(WaterAttributeRule(column="AREA", values=("ALASKA",)),),
        require_include_match=True,
    )


def _scope_config(
    scope_raw: dict[str, Any],
    *,
    province_name: str,
    province_abbr: str,
) -> CanadaWaterScopeConfig:
    defaults = _default_water_scope(province_name, province_abbr)

    include_present = "include_rules" in scope_raw or "filters" in scope_raw
    exclude_present = "exclude_rules" in scope_raw or "excludes" in scope_raw
    include_raw = scope_raw.get("include_rules", scope_raw.get("filters"))
    exclude_raw = scope_raw.get("exclude_rules", scope_raw.get("excludes"))
    include_rules = (
        _parse_rules(include_raw, key="canada_water_scope.include_rules")
        if include_present
        else defaults.include_rules
    )
    exclude_rules = (
        _parse_rules(exclude_raw, key="canada_water_scope.exclude_rules")
        if exclude_present
        else defaults.exclude_rules
    )

    if "require_include_match" in scope_raw:
        require_include_match = as_bool(
            scope_raw["require_include_match"],
            "canada_water_scope.require_include_match",
        )
    elif "allow_unfiltered" in scope_raw:
        require_include_match = not as_bool(
            scope_raw["allow_unfiltered"],
            "canada_water_scope.allow_unfiltered",
        )
    else:
        require_include_match = defaults.require_include_match

    return CanadaWaterScopeConfig(
        include_rules=include_rules,
        exclude_rules=exclude_rules,
        require_include_match=require_include_match,
    )


def load_canada_config(path: str | Path) -> CanadaPopulationConfig:
    """Load and validate the British Columbia population configuration."""
    from human.core.config import ConfigDocument
    from human.core.config.paths import resolve_config_path  # type: ignore[import-not-found]

    config_path = resolve_config_path(path)
    raw = dict(ConfigDocument.load(config_path).data)
    if not isinstance(raw, dict):
        raise ValueError("Canada config must parse to a mapping.")
    raw = country_population_config(raw, "canada")
    base_dir = config_path.parent

    canada_raw = section(raw, "canada")
    paths_raw = section(raw, "canada_paths")
    water_raw = section(raw, "canada_water_distance")
    runtime_raw = section(raw, "runtime")
    shared_paths = section(raw, "paths")

    require_keys(
        canada_raw,
        "canada",
        {"province_name", "province_abbr", "province_code"},
    )
    require_keys(
        paths_raw,
        "canada_paths",
        {"raw_dir", "processed_dir", "output_parquet"},
    )
    require_keys(shared_paths, "paths", {"water_polygons_path"})
    require_keys(
        water_raw,
        "canada_water_distance",
        {
            "candidate_distance_miles",
            "final_max_distance_miles",
            "distance_basis",
            "distance_threshold_miles",
            "decay",
        },
    )
    require_keys(
        runtime_raw,
        "runtime",
        {
            "overwrite_downloads",
            "overwrite_raw_cache",
            "overwrite_output",
            "write_debug_geo",
            "debug_output_path",
        },
    )

    scope_raw = raw.get("canada_water_scope", canada_raw.get("water_scope", {}))
    if not isinstance(scope_raw, dict):
        raise ValueError("canada_water_scope must be a mapping.")

    province_name = str(canada_raw["province_name"]).strip()
    province_abbr = str(canada_raw["province_abbr"]).strip().upper()
    thresholds = as_tuple_float(
        water_raw["distance_threshold_miles"],
        "canada_water_distance.distance_threshold_miles",
    )

    cfg = CanadaPopulationConfig(
        canada_area_crs=str(raw.get("canada_area_crs", "EPSG:3005")),
        output_crs=str(raw.get("output_crs", "EPSG:4326")),
        h3_resolution=int(raw.get("h3_resolution", 7)),
        water_polygons_path=resolve_path(shared_paths["water_polygons_path"], base_dir),
        canada=CanadaSourceConfig(
            enabled=as_bool(canada_raw.get("enabled", True), "canada.enabled"),
            census_year=int(canada_raw.get("census_year", 2021)),
            province_name=province_name,
            province_abbr=province_abbr,
            province_code=str(canada_raw["province_code"]).strip().zfill(2),
            geography_level=str(canada_raw.get("geography_level", "DA")).strip(),
            population_variable_candidates=as_optional_tuple_str(
                canada_raw.get("population_variable_candidates", ()),
                "canada.population_variable_candidates",
            ),
            source=str(canada_raw.get("source", "statscan")).strip().lower(),
            api_or_download_mode=str(canada_raw.get("api_or_download_mode", "auto"))
            .strip()
            .lower(),
            statcan_profile_wds_url=str(canada_raw.get("statcan_profile_wds_url", "")).strip(),
            statcan_sdmx_base_url=str(
                canada_raw.get(
                    "statcan_sdmx_base_url",
                    "https://api.statcan.gc.ca/census-recensement/profile/sdmx/rest",
                )
            )
            .strip()
            .rstrip("/"),
            statcan_dataflow=str(canada_raw.get("statcan_dataflow", "DF_DA")).strip(),
            statcan_frequency=str(canada_raw.get("statcan_frequency", "A5")).strip(),
            statcan_gender_code=str(canada_raw.get("statcan_gender_code", "1")).strip(),
            statcan_population_characteristic_code=str(
                canada_raw.get("statcan_population_characteristic_code", "1")
            ).strip(),
            statcan_statistic_code=str(canada_raw.get("statcan_statistic_code", "1")).strip(),
            statcan_request_chunk_size=int(canada_raw.get("statcan_request_chunk_size", 200)),
            statcan_timeout_seconds=int(canada_raw.get("statcan_timeout_seconds", 120)),
            statcan_dguid_prefix=str(canada_raw.get("statcan_dguid_prefix", "2021S0512")).strip(),
            geography_path=resolve_optional_path(canada_raw.get("geography_path"), base_dir),
            population_table_path=resolve_optional_path(
                canada_raw.get("population_table_path"), base_dir
            ),
            population_download_url=(
                str(canada_raw["population_download_url"]).strip()
                if canada_raw.get("population_download_url")
                else None
            ),
            population_download_filename=str(
                canada_raw.get(
                    "population_download_filename",
                    "bc_census_profile_2021.csv.zip",
                )
            ).strip(),
            geography_download_url=(
                str(canada_raw["geography_download_url"]).strip()
                if canada_raw.get("geography_download_url")
                else None
            ),
            geography_download_filename=str(
                canada_raw.get("geography_download_filename", "bc_da_geography.zip")
            ).strip(),
            geography_join_column_candidates=as_optional_tuple_str(
                canada_raw.get(
                    "geography_join_column_candidates",
                    ("DGUID", "DAUID"),
                ),
                "canada.geography_join_column_candidates",
            ),
            population_join_column_candidates=as_optional_tuple_str(
                canada_raw.get(
                    "population_join_column_candidates",
                    ("DGUID", "DAUID", "REF_AREA", "GEO"),
                ),
                "canada.population_join_column_candidates",
            ),
            population_geoid_col=(
                str(canada_raw["population_geoid_col"]).strip()
                if canada_raw.get("population_geoid_col")
                else None
            ),
            population_value_col=(
                str(canada_raw["population_value_col"]).strip()
                if canada_raw.get("population_value_col")
                else None
            ),
        ),
        canada_paths=CanadaPathsConfig(
            raw_dir=resolve_path(paths_raw["raw_dir"], base_dir),
            processed_dir=resolve_path(paths_raw["processed_dir"], base_dir),
            output_parquet=resolve_path(paths_raw["output_parquet"], base_dir),
            population_api_cache=resolve_path(
                paths_raw.get(
                    "population_api_cache",
                    "data/raw/population/canada/bc_da_population_2021.parquet",
                ),
                base_dir,
            ),
        ),
        canada_water_distance=CanadaWaterDistanceConfig(
            candidate_distance_miles=float(water_raw["candidate_distance_miles"]),
            final_max_distance_miles=float(water_raw["final_max_distance_miles"]),
            h3_prefilter_distance_miles=float(
                water_raw.get(
                    "h3_prefilter_distance_miles",
                    water_raw["final_max_distance_miles"],
                )
            ),
            filter_h3_to_source_geographies=as_bool(
                water_raw.get("filter_h3_to_source_geographies", False),
                "canada_water_distance.filter_h3_to_source_geographies",
            ),
            distance_basis=str(water_raw["distance_basis"]).strip().lower(),
            distance_threshold_miles=thresholds,
            decay=str(water_raw["decay"]).strip().lower(),
        ),
        water_scope=_scope_config(
            scope_raw,
            province_name=province_name,
            province_abbr=province_abbr,
        ),
        runtime=CanadaRuntimeConfig(
            overwrite_downloads=as_bool(
                runtime_raw["overwrite_downloads"], "runtime.overwrite_downloads"
            ),
            overwrite_raw_cache=as_bool(
                runtime_raw["overwrite_raw_cache"], "runtime.overwrite_raw_cache"
            ),
            overwrite_output=as_bool(runtime_raw["overwrite_output"], "runtime.overwrite_output"),
            write_debug_geo=as_bool(runtime_raw["write_debug_geo"], "runtime.write_debug_geo"),
            debug_output_path=resolve_path(runtime_raw["debug_output_path"], base_dir),
            allocation_chunk_parent_resolution=int(
                runtime_raw.get("allocation_chunk_parent_resolution", 4)
            ),
        ),
        config_path=config_path,
    )
    validate_canada_config(cfg)
    return cfg


def validate_canada_config(cfg: CanadaPopulationConfig) -> None:
    """Validate Canada settings before pipeline execution."""
    if not cfg.source.province_name or not cfg.source.province_abbr:
        raise ValueError("Canada province_name and province_abbr must be non-empty.")
    if cfg.h3_resolution != 7:
        raise ValueError("Canada pipeline currently requires h3_resolution == 7.")
    if cfg.source.census_year != 2021:
        raise ValueError("Canada pipeline currently supports Census 2021 only.")
    if cfg.source.province_code != "59":
        raise ValueError("Canada pipeline currently supports BC province_code '59'.")
    if cfg.source.geography_level.upper() != "DA":
        raise ValueError("Canada pipeline currently expects DA geography.")
    if not cfg.source.geography_join_column_candidates:
        raise ValueError("canada.geography_join_column_candidates must not be empty.")
    if cfg.water_distance.distance_basis != "centroid":
        raise ValueError("Canada water distance currently supports only centroid basis.")
    if cfg.water_distance.decay != "linear":
        raise ValueError("Canada water distance currently supports only linear decay.")

    final_max = cfg.water_distance.final_max_distance_miles
    candidate = cfg.water_distance.candidate_distance_miles
    prefilter = cfg.water_distance.h3_prefilter_distance_miles
    for label, value in {
        "final_max_distance_miles": final_max,
        "candidate_distance_miles": candidate,
        "h3_prefilter_distance_miles": prefilter,
    }.items():
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{label} must be finite and positive.")
    if candidate < final_max:
        raise ValueError("candidate_distance_miles must be >= final_max_distance_miles.")
    if prefilter < final_max:
        raise ValueError("h3_prefilter_distance_miles must be >= final_max_distance_miles.")
    if prefilter > candidate:
        raise ValueError("h3_prefilter_distance_miles must be <= candidate_distance_miles.")
    validate_distance_thresholds(
        cfg.water_distance.distance_threshold_miles,
        key="canada_water_distance.distance_threshold_miles",
        maximum=final_max,
    )

    if cfg.source.source != "statscan":
        raise ValueError("Canada pipeline currently supports source: statscan.")
    if cfg.source.api_or_download_mode not in {"auto", "api", "local"}:
        raise ValueError("canada.api_or_download_mode must be auto, api, or local.")
    if cfg.source.statcan_dataflow != "DF_DA":
        raise ValueError("Canada pipeline currently expects StatsCan dataflow DF_DA.")
    if not cfg.source.statcan_sdmx_base_url:
        raise ValueError("canada.statcan_sdmx_base_url must be non-empty.")
    if cfg.source.statcan_request_chunk_size <= 0:
        raise ValueError("statcan_request_chunk_size must be positive.")
    if cfg.source.statcan_timeout_seconds <= 0:
        raise ValueError("statcan_timeout_seconds must be positive.")
    if cfg.source.api_or_download_mode == "local":
        if cfg.source.population_table_path is None and cfg.source.population_download_url is None:
            raise ValueError(
                "canada.population_table_path or canada.population_download_url is required "
                "when api_or_download_mode=local."
            )
        has_join = bool(
            cfg.source.population_geoid_col or cfg.source.population_join_column_candidates
        )
        has_value = bool(
            cfg.source.population_value_col or cfg.source.population_variable_candidates
        )
        if not has_join or not has_value:
            raise ValueError(
                "Local Canada mode requires population join and value column candidates."
            )

    if not 0 <= cfg.runtime.allocation_chunk_parent_resolution < cfg.h3_resolution:
        raise ValueError(
            "allocation_chunk_parent_resolution must be >= 0 and less than h3_resolution."
        )
    if cfg.water_scope.require_include_match and not cfg.water_scope.include_rules:
        raise ValueError("Canada water scope requires a match but has no include_rules configured.")
    for rule in (*cfg.water_scope.include_rules, *cfg.water_scope.exclude_rules):
        if not rule.column or not rule.values:
            raise ValueError("Canada water scope rules require a column and values.")
    if cfg.source.enabled and not cfg.water_polygons_path.exists():
        raise FileNotFoundError(
            f"Configured water polygons path does not exist: {cfg.water_polygons_path}"
        )
    if cfg.paths.output_parquet == cfg.runtime.debug_output_path:
        raise ValueError("Standard and debug output paths must be different.")


# Compatibility aliases used by the original package.
load_config = load_canada_config
validate_config = validate_canada_config
