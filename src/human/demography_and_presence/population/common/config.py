"""Shared configuration objects and strict parsing helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class H3Config:
    """Shared H3 and coordinate-reference-system settings."""

    resolution: int
    area_crs: str
    output_crs: str


@dataclass(frozen=True)
class RuntimeConfig:
    """Shared cache, overwrite, and debug-output behavior."""

    overwrite_downloads: bool
    overwrite_raw_cache: bool
    overwrite_output: bool
    write_debug_geo: bool
    debug_output_path: Path


def require_keys(raw: dict[str, Any], section_name: str, keys: Iterable[str]) -> None:
    """Raise when a mapping is missing required keys."""
    missing = set(keys) - set(raw)
    if missing:
        raise ValueError(f"Missing required config keys in {section_name}: {sorted(missing)}")


def section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a required mapping section."""
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Missing or invalid config section: {key}")
    return value


def merged_section(
    parent: dict[str, Any],
    child: dict[str, Any],
    key: str,
    *,
    child_path: str | None = None,
) -> dict[str, Any]:
    """Merge a shared section with a country override section."""
    parent_value = parent.get(key, {})
    child_value = child.get(key, {})
    if not isinstance(parent_value, dict):
        raise ValueError(f"Config section '{key}' must be a mapping.")
    if not isinstance(child_value, dict):
        location = child_path or "country"
        raise ValueError(f"Config section '{location}.{key}' must be a mapping.")
    return {**parent_value, **child_value}


def country_population_config(raw: dict[str, Any], country: str) -> dict[str, Any]:
    """Extract one country config while preserving the legacy flat layout.

    Supported layouts are either the original flat country mapping or a shared
    project config with ``population.<country>``. In the nested layout, root
    ``paths`` and ``runtime`` values are merged with country overrides.
    """
    population_raw = raw.get("population")
    if population_raw is None:
        return dict(raw)
    if not isinstance(population_raw, dict):
        raise ValueError("Config section 'population' must be a mapping.")
    country_raw = population_raw.get(country)
    if not isinstance(country_raw, dict):
        raise ValueError(f"Missing or invalid config section: population.{country}")

    merged = dict(country_raw)
    for key in ("h3_resolution", "output_crs", "area_crs", "canada_area_crs"):
        if key in raw and key not in merged:
            merged[key] = raw[key]
    merged["paths"] = merged_section(
        raw,
        country_raw,
        "paths",
        child_path=f"population.{country}",
    )
    merged["runtime"] = merged_section(
        raw,
        country_raw,
        "runtime",
        child_path=f"population.{country}",
    )
    return merged


def resolve_path(value: str | Path, base_dir: Path) -> Path:
    """Resolve canonical paths relative to the repository root.

    ``base_dir`` remains in the signature for country-loader compatibility but
    is intentionally not used by the reorganized human configuration contract.
    """
    from human.core.config.paths import project_root

    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (project_root() / path).resolve()


def resolve_optional_path(value: Any, base_dir: Path) -> Path | None:
    """Resolve an optional path relative to the configuration file directory."""
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "null", "none"}):
        return None
    return resolve_path(str(value), base_dir)


def as_bool(value: Any, key: str) -> bool:
    """Parse a strict boolean while accepting conventional string spellings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1", "on"}:
            return True
        if normalized in {"false", "no", "n", "0", "off"}:
            return False
    raise ValueError(f"{key} must be a boolean.")


def as_tuple_str(value: Any, key: str, *, zfill: int | None = None) -> tuple[str, ...]:
    """Normalize one or many values to a non-empty tuple of stripped strings."""
    if value is None:
        raise ValueError(f"{key} must not be empty.")
    values = value if isinstance(value, (list, tuple)) else [value]
    output: list[str] = []
    for item in values:
        text = str(item).strip()
        if zfill is not None:
            text = text.zfill(zfill)
        output.append(text)
    result = tuple(output)
    if not result or any(item == "" for item in result):
        raise ValueError(f"{key} must not be empty.")
    return result


def as_optional_tuple_str(
    value: Any,
    key: str,
    *,
    zfill: int | None = None,
) -> tuple[str, ...]:
    """Normalize an optional scalar/list to a tuple of strings."""
    if value is None or value == () or value == []:
        return ()
    return as_tuple_str(value, key, zfill=zfill)


def as_tuple_float(value: Any, key: str) -> tuple[float, ...]:
    """Normalize one or many finite numeric values to a non-empty tuple."""
    if value is None:
        raise ValueError(f"{key} must not be empty.")
    values = value if isinstance(value, (list, tuple)) else [value]
    try:
        output = tuple(float(item) for item in values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must contain only numbers.") from exc
    if not output or any(not math.isfinite(item) for item in output):
        raise ValueError(f"{key} must contain finite numbers and must not be empty.")
    return output


def validate_thresholds(
    thresholds: tuple[float, ...],
    *,
    key: str,
    maximum: float | None = None,
) -> None:
    """Validate positive, unique water-distance thresholds."""
    if not thresholds:
        raise ValueError(f"{key} must not be empty.")
    if any(value <= 0 or not math.isfinite(value) for value in thresholds):
        raise ValueError(f"{key} values must be finite and positive.")
    if len(set(thresholds)) != len(thresholds):
        raise ValueError(f"{key} values must be unique.")
    if tuple(sorted(thresholds)) != thresholds:
        raise ValueError(f"{key} values must be in ascending order.")
    if maximum is not None:
        if not math.isfinite(maximum) or maximum <= 0:
            raise ValueError(f"The maximum distance for {key} must be finite and positive.")
        if max(thresholds) > maximum:
            raise ValueError(f"{key} values must not exceed the configured maximum distance.")


# Descriptive alias retained for country config modules.
validate_distance_thresholds = validate_thresholds
