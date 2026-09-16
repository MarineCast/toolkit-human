"""Configuration loading for the US 2020 Census population pipeline."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..common.config import (
    as_bool,
    as_tuple_float,
    as_tuple_str,
    country_population_config,
    require_keys,
    resolve_path,
    section,
    validate_distance_thresholds,
)


@dataclass(frozen=True)
class CensusConfig:
    """US Census 2020 PL API settings."""

    year: int
    dataset: str
    total_population_variable: str
    api_base_url: str
    use_api_key: bool
    api_key_env_var: str
    sleep_seconds: float
    max_retries: int
    timeout_seconds: int = 120


@dataclass(frozen=True)
class TigerConfig:
    """TIGER/Line source URLs for 2020 blocks and state boundaries."""

    blocks_urls: dict[str, str]
    states_url: str


@dataclass(frozen=True)
class PathsConfig:
    """US input, cache, and output paths."""

    data_dir: Path
    raw_dir: Path
    processed_dir: Path
    water_polygons_path: Path
    output_parquet: Path

    @property
    def block_population_cache(self) -> Path:
        return self.raw_dir / "block_population_2020.parquet"

    def blocks_zip(self, state_fips: str) -> Path:
        return self.raw_dir / f"tl_2020_{state_fips}_tabblock20.zip"

    @property
    def states_zip(self) -> Path:
        return self.raw_dir / "tl_2024_us_state.zip"


@dataclass(frozen=True)
class WaterDistanceConfig:
    """US water-distance feature settings."""

    max_distance_miles: float
    filter_output_to_water_buffer: bool
    distance_basis: str
    distance_threshold_miles: tuple[float, ...]
    decay: str


@dataclass(frozen=True)
class RuntimeConfig:
    """US pipeline overwrite and debug behavior."""

    overwrite_downloads: bool
    overwrite_raw_cache: bool
    overwrite_output: bool
    write_debug_geo: bool
    debug_output_path: Path


@dataclass(frozen=True)
class UsPopulationConfig:
    """Complete US pipeline configuration.

    The pipeline intentionally supports the 2020 Decennial PL block schema only.
    The explicit class name avoids suggesting that arbitrary Census vintages are
    interchangeable.
    """

    state_fips: tuple[str, ...]
    state_abbr: tuple[str, ...]
    h3_resolution: int
    area_crs: str
    output_crs: str
    census: CensusConfig
    tiger: TigerConfig
    paths: PathsConfig
    water_distance: WaterDistanceConfig
    runtime: RuntimeConfig
    config_path: Path

    @property
    def state_abbr_by_fips(self) -> dict[str, str]:
        return dict(zip(self.state_fips, self.state_abbr, strict=True))

    @property
    def state_label(self) -> str:
        return "_".join(self.state_abbr)


# Backward-compatible public name.
PopulationConfig = UsPopulationConfig


def load_us_config(path: str | Path) -> UsPopulationConfig:
    """Load and validate a US population pipeline YAML configuration."""
    from human.core.config import ConfigDocument
    from human.core.config.paths import resolve_config_path  # type: ignore[import-not-found]

    config_path = resolve_config_path(path)
    raw = dict(ConfigDocument.load(config_path).data)
    if not isinstance(raw, dict):
        raise ValueError("Config must parse to a mapping.")
    raw = country_population_config(raw, "us")

    require_keys(
        raw,
        "root",
        {
            "state_fips",
            "state_abbr",
            "h3_resolution",
            "area_crs",
            "output_crs",
            "census",
            "tiger",
            "paths",
            "water_distance",
            "runtime",
        },
    )
    census_raw = section(raw, "census")
    tiger_raw = section(raw, "tiger")
    paths_raw = section(raw, "paths")
    water_raw = section(raw, "water_distance")
    runtime_raw = section(raw, "runtime")

    require_keys(
        census_raw,
        "census",
        {
            "year",
            "dataset",
            "total_population_variable",
            "api_base_url",
            "use_api_key",
            "api_key_env_var",
            "sleep_seconds",
            "max_retries",
        },
    )
    if "blocks_urls" not in tiger_raw and "blocks_url" not in tiger_raw:
        raise ValueError("Missing required config key in tiger: blocks_urls")
    require_keys(tiger_raw, "tiger", {"states_url"})
    require_keys(
        paths_raw,
        "paths",
        {"data_dir", "raw_dir", "processed_dir", "water_polygons_path", "output_parquet"},
    )
    require_keys(
        water_raw,
        "water_distance",
        {"max_distance_miles", "distance_basis", "distance_threshold_miles", "decay"},
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

    state_fips = as_tuple_str(raw["state_fips"], "state_fips", zfill=2)
    state_abbr = as_tuple_str(raw["state_abbr"], "state_abbr")
    if len(state_fips) != len(state_abbr):
        raise ValueError("state_fips and state_abbr must have the same length.")
    if len(set(state_fips)) != len(state_fips):
        raise ValueError("state_fips values must be unique.")

    raw_blocks_urls = tiger_raw.get("blocks_urls")
    if raw_blocks_urls is None:
        if len(state_fips) != 1:
            raise ValueError("tiger.blocks_urls is required when multiple states are configured.")
        raw_blocks_urls = {state_fips[0]: tiger_raw["blocks_url"]}
    if not isinstance(raw_blocks_urls, dict):
        raise ValueError("tiger.blocks_urls must map state FIPS values to block URLs.")
    blocks_urls = {str(key).zfill(2): str(value) for key, value in raw_blocks_urls.items()}

    base_dir = config_path.parent
    thresholds = as_tuple_float(
        water_raw["distance_threshold_miles"],
        "water_distance.distance_threshold_miles",
    )
    cfg = UsPopulationConfig(
        state_fips=state_fips,
        state_abbr=tuple(value.upper() for value in state_abbr),
        h3_resolution=int(raw["h3_resolution"]),
        area_crs=str(raw["area_crs"]),
        output_crs=str(raw["output_crs"]),
        census=CensusConfig(
            year=int(census_raw["year"]),
            dataset=str(census_raw["dataset"]),
            total_population_variable=str(census_raw["total_population_variable"]),
            api_base_url=str(census_raw["api_base_url"]),
            use_api_key=as_bool(census_raw["use_api_key"], "census.use_api_key"),
            api_key_env_var=str(census_raw["api_key_env_var"]),
            sleep_seconds=float(census_raw["sleep_seconds"]),
            max_retries=int(census_raw["max_retries"]),
            timeout_seconds=int(census_raw.get("timeout_seconds", 120)),
        ),
        tiger=TigerConfig(blocks_urls=blocks_urls, states_url=str(tiger_raw["states_url"])),
        paths=PathsConfig(
            data_dir=resolve_path(paths_raw["data_dir"], base_dir),
            raw_dir=resolve_path(paths_raw["raw_dir"], base_dir),
            processed_dir=resolve_path(paths_raw["processed_dir"], base_dir),
            water_polygons_path=resolve_path(paths_raw["water_polygons_path"], base_dir),
            output_parquet=resolve_path(paths_raw["output_parquet"], base_dir),
        ),
        water_distance=WaterDistanceConfig(
            max_distance_miles=float(water_raw["max_distance_miles"]),
            filter_output_to_water_buffer=as_bool(
                water_raw.get("filter_output_to_water_buffer", False),
                "water_distance.filter_output_to_water_buffer",
            ),
            distance_basis=str(water_raw["distance_basis"]),
            distance_threshold_miles=thresholds,
            decay=str(water_raw["decay"]),
        ),
        runtime=RuntimeConfig(
            overwrite_downloads=as_bool(
                runtime_raw["overwrite_downloads"], "runtime.overwrite_downloads"
            ),
            overwrite_raw_cache=as_bool(
                runtime_raw["overwrite_raw_cache"], "runtime.overwrite_raw_cache"
            ),
            overwrite_output=as_bool(runtime_raw["overwrite_output"], "runtime.overwrite_output"),
            write_debug_geo=as_bool(runtime_raw["write_debug_geo"], "runtime.write_debug_geo"),
            debug_output_path=resolve_path(runtime_raw["debug_output_path"], base_dir),
        ),
        config_path=config_path,
    )
    validate_us_config(cfg)
    return cfg


def validate_us_config(cfg: UsPopulationConfig) -> None:
    """Validate coherent US 2020 pipeline settings before execution."""
    for state_fips in cfg.state_fips:
        if not state_fips.isdigit() or len(state_fips) != 2:
            raise ValueError("state_fips values must be two-digit FIPS strings.")
        if state_fips not in cfg.tiger.blocks_urls:
            raise ValueError(f"Missing TIGER blocks URL for state FIPS {state_fips}.")
    if any(not value for value in cfg.state_abbr):
        raise ValueError("state_abbr values must be non-empty.")
    if not 0 <= cfg.h3_resolution <= 15:
        raise ValueError("h3_resolution must be between 0 and 15.")
    if cfg.census.year != 2020 or cfg.census.dataset != "dec/pl":
        raise ValueError(
            "The US pipeline supports only the 2020 Decennial PL block schema "
            "(census.year=2020, census.dataset='dec/pl')."
        )
    if cfg.census.total_population_variable != "P1_001N":
        raise ValueError("The US pipeline supports only total population variable P1_001N.")
    if not math.isfinite(cfg.census.sleep_seconds) or cfg.census.sleep_seconds < 0:
        raise ValueError("census.sleep_seconds must be finite and >= 0.")
    if cfg.census.max_retries < 1:
        raise ValueError("census.max_retries must be >= 1.")
    if cfg.census.timeout_seconds <= 0:
        raise ValueError("census.timeout_seconds must be positive.")
    if cfg.water_distance.distance_basis != "centroid":
        raise ValueError("water_distance.distance_basis currently supports only 'centroid'.")
    if cfg.water_distance.decay != "linear":
        raise ValueError("water_distance.decay currently supports only 'linear'.")
    if (
        not math.isfinite(cfg.water_distance.max_distance_miles)
        or cfg.water_distance.max_distance_miles <= 0
    ):
        raise ValueError("water_distance.max_distance_miles must be finite and positive.")
    validate_distance_thresholds(
        cfg.water_distance.distance_threshold_miles,
        key="water_distance.distance_threshold_miles",
        maximum=cfg.water_distance.max_distance_miles,
    )
    if not cfg.census.api_base_url.strip():
        raise ValueError("census.api_base_url must be non-empty.")
    if not cfg.paths.water_polygons_path.exists():
        raise FileNotFoundError(
            f"Water polygons file does not exist: {cfg.paths.water_polygons_path}"
        )
    if cfg.paths.output_parquet == cfg.runtime.debug_output_path:
        raise ValueError("Standard and debug output paths must be different.")


# Backward-compatible public names.
UsPathsConfig = PathsConfig
load_config = load_us_config
validate_config = validate_us_config
