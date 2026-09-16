from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

_GEOSPATIAL_SUFFIXES = {".gpkg", ".geojson", ".json"}


def load_water_h3_universe(path: str | Path, h3_col: str = "H3_INDEX") -> set[str]:
    resolved = Path(path).expanduser()
    suffix = resolved.suffix.lower()

    if suffix == ".parquet":
        df = pd.read_parquet(resolved, columns=[h3_col])
    elif suffix == ".csv":
        df = pd.read_csv(resolved, usecols=[h3_col])
    elif suffix in _GEOSPATIAL_SUFFIXES:
        try:
            import geopandas as gpd  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency at runtime
            raise ImportError(
                f"geopandas is required to load geospatial water universe file: {resolved}"
            ) from exc
        df = gpd.read_file(resolved)
        if h3_col not in df.columns:
            raise KeyError(
                f"H3 column '{h3_col}' not found in geospatial universe file {resolved}. "
                f"Available columns: {list(df.columns)}"
            )
        df = pd.DataFrame(df[[h3_col]])
    else:
        raise ValueError(
            f"Unsupported water universe file type '{suffix}' for path {resolved}. "
            "Supported: parquet, csv, gpkg, geojson, json."
        )

    if h3_col not in df.columns:
        raise KeyError(
            f"H3 column '{h3_col}' not found in water universe file {resolved}. "
            f"Available columns: {list(df.columns)}"
        )

    values = df[h3_col].dropna().astype(str).str.strip()
    values = values[values != ""]
    return set(values.tolist())


def resolve_water_h3_universe_path(config: dict[str, Any]) -> Path | None:
    geo_cfg = config.get("geo") or {}
    raw_path = geo_cfg.get("water_h3_universe_path")
    if not raw_path:
        return None

    h3_res = (
        config.get("h3_resolution")
        or config.get("project", {}).get("h3_resolution")
        or geo_cfg.get("resolution")
    )
    path_str = str(raw_path)
    if "{RES}" in path_str:
        if h3_res is None:
            raise ValueError(
                "geo.water_h3_universe_path uses '{RES}' but no h3_resolution was found in config."
            )
        path_str = path_str.replace("{RES}", str(int(h3_res)))
    return Path(path_str).expanduser()


def load_water_h3_universe_from_config(config: dict[str, Any]) -> set[str] | None:
    geo_cfg = config.get("geo") or {}
    if not bool(geo_cfg.get("enforce_water_universe", False)):
        return None

    path = resolve_water_h3_universe_path(config)
    if path is None:
        return None

    h3_col = str(geo_cfg.get("water_h3_col", "H3_INDEX"))
    return load_water_h3_universe(path, h3_col=h3_col)


def filter_h3_values_by_water_universe(
    h3_values: list[str] | pd.Series | Any,
    *,
    config: dict[str, Any],
) -> list[str]:
    universe = load_water_h3_universe_from_config(config)
    values = [str(v).strip() for v in h3_values if pd.notna(v) and str(v).strip()]
    if universe is None:
        return values
    return [value for value in values if value in universe]
