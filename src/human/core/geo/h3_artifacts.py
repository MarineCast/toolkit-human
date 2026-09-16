"""Persisted H3 relationship artifacts for parent maps, neighbors, and water masks."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from human.core.artifacts.paths import PathManager
from human.core.geo.h3 import grid_disk, to_parent
from human.core.geo.water_universe import (
    load_water_h3_universe,
    resolve_water_h3_universe_path,
)

_PARENT_MAP_CACHE: dict[str, pd.DataFrame] = {}
_NEIGHBOR_EDGE_CACHE: dict[str, pd.DataFrame] = {}
_WATER_UNIVERSE_CACHE: dict[str, pd.DataFrame] = {}
_DEFAULT_MAX_CACHE_ENTRIES = 8

logger = logging.getLogger(__name__)


def _cache_get(cache: dict[str, pd.DataFrame], key: str) -> pd.DataFrame | None:
    value = cache.get(key)
    if value is None:
        return None
    # Preserve simple LRU-ish behavior without changing the public cache type.
    cache.pop(key, None)
    cache[key] = value
    return value


def _cache_set(
    cache: dict[str, pd.DataFrame],
    key: str,
    value: pd.DataFrame,
    *,
    max_entries: int = _DEFAULT_MAX_CACHE_ENTRIES,
) -> None:
    if max_entries <= 0:
        return
    cache[key] = value
    while len(cache) > int(max_entries):
        cache.pop(next(iter(cache)))


def clear_h3_artifact_caches() -> None:
    _PARENT_MAP_CACHE.clear()
    _NEIGHBOR_EDGE_CACHE.clear()
    _WATER_UNIVERSE_CACHE.clear()


def h3_artifact_cache_sizes() -> dict[str, int]:
    return {
        "parent_maps": len(_PARENT_MAP_CACHE),
        "neighbor_edges": len(_NEIGHBOR_EDGE_CACHE),
        "water_universe": len(_WATER_UNIVERSE_CACHE),
    }


def _canonical_cells(cells: list[str] | pd.Series | Any) -> list[str]:
    values = pd.Series(list(cells), dtype="object").dropna().astype(str).str.strip()
    values = values[values != ""]
    return sorted(values.drop_duplicates().tolist())


def _cells_signature(cells: list[str]) -> str:
    digest = hashlib.sha1()
    for value in cells:
        digest.update(value.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()[:16]


def _safe_namespace(namespace: str | None) -> str:
    raw = str(namespace or "default").strip().lower()
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)


def h3_artifact_root(path_manager: PathManager) -> Path:
    return path_manager.static_maps_dir() / "h3"


def _parent_map_path(
    path_manager: PathManager,
    *,
    parent_res: int,
    namespace: str,
    cells_sig: str,
) -> Path:
    return (
        h3_artifact_root(path_manager)
        / "parent_maps"
        / f"parent_r{int(parent_res)}"
        / f"{_safe_namespace(namespace)}__{cells_sig}.parquet"
    )


def _neighbor_edge_path(
    path_manager: PathManager,
    *,
    k_ring: int,
    include_self: bool,
    namespace: str,
    cells_sig: str,
) -> Path:
    return (
        h3_artifact_root(path_manager)
        / "neighbor_edges"
        / f"k{int(k_ring)}"
        / f"include_self_{int(bool(include_self))}"
        / f"{_safe_namespace(namespace)}__{cells_sig}.parquet"
    )


def _water_universe_path(path_manager: PathManager, source_path: Path) -> Path:
    resolved = source_path.expanduser().resolve()
    src_sig = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:16]
    stem = _safe_namespace(resolved.stem)
    return h3_artifact_root(path_manager) / "water_universe" / f"{stem}__{src_sig}.parquet"


def load_or_build_water_universe_df(
    *,
    config: dict[str, Any],
    path_manager: PathManager,
    h3_col: str = "H3_INDEX",
) -> pd.DataFrame | None:
    source_path = resolve_water_h3_universe_path(config)
    if source_path is None:
        return None
    artifact_path = _water_universe_path(path_manager, source_path)
    cache_key = str(artifact_path.resolve())
    max_cache_entries = int(config.get("h3_artifact_cache_max_entries", _DEFAULT_MAX_CACHE_ENTRIES))
    cached = _cache_get(_WATER_UNIVERSE_CACHE, cache_key)
    if cached is not None:
        return cached.copy()
    if artifact_path.exists():
        df = pd.read_parquet(artifact_path)
        _cache_set(_WATER_UNIVERSE_CACHE, cache_key, df, max_entries=max_cache_entries)
        return df.copy()
    cells = sorted(load_water_h3_universe(source_path, h3_col=h3_col))
    df = pd.DataFrame({h3_col: cells})
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(artifact_path, index=False)
    _cache_set(_WATER_UNIVERSE_CACHE, cache_key, df, max_entries=max_cache_entries)
    return df.copy()


def filter_h3_values_by_cached_water_universe(
    h3_values: list[str] | pd.Series | Any,
    *,
    config: dict[str, Any],
    path_manager: PathManager,
    h3_col: str = "H3_INDEX",
) -> list[str]:
    values = _canonical_cells(h3_values)
    universe_df = load_or_build_water_universe_df(
        config=config,
        path_manager=path_manager,
        h3_col=h3_col,
    )
    if universe_df is None:
        return values
    universe = set(universe_df[h3_col].astype(str).tolist())
    return [value for value in values if value in universe]


def load_or_build_parent_map(
    *,
    cells: list[str] | pd.Series | Any,
    parent_res: int,
    path_manager: PathManager,
    namespace: str | None = None,
    child_col: str = "H3_INDEX",
    parent_col: str = "PARENT_H3",
) -> pd.DataFrame:
    unique_cells = _canonical_cells(cells)
    if not unique_cells:
        return pd.DataFrame(columns=[child_col, parent_col])
    cells_sig = _cells_signature(unique_cells)
    path = _parent_map_path(
        path_manager,
        parent_res=int(parent_res),
        namespace=str(namespace or "default"),
        cells_sig=cells_sig,
    )
    cache_key = str(path.resolve())
    cached = _cache_get(_PARENT_MAP_CACHE, cache_key)
    if cached is not None:
        return cached.rename(columns={"H3_INDEX": child_col, "PARENT_H3": parent_col}).copy()
    if path.exists():
        base = pd.read_parquet(path)
        _cache_set(_PARENT_MAP_CACHE, cache_key, base)
        return base.rename(columns={"H3_INDEX": child_col, "PARENT_H3": parent_col}).copy()

    base = pd.DataFrame({"H3_INDEX": unique_cells})
    base["PARENT_H3"] = base["H3_INDEX"].map(lambda x: to_parent(x, int(parent_res)))
    path.parent.mkdir(parents=True, exist_ok=True)
    base.to_parquet(path, index=False)
    _cache_set(_PARENT_MAP_CACHE, cache_key, base)
    return base.rename(columns={"H3_INDEX": child_col, "PARENT_H3": parent_col}).copy()


def attach_parent_column(
    df: pd.DataFrame,
    *,
    path_manager: PathManager,
    h3_col: str,
    parent_col: str,
    parent_res: int,
    namespace: str | None = None,
) -> pd.DataFrame:
    if df.empty:
        out = df.copy()
        out[parent_col] = pd.Series(dtype="object")
        return out
    work = df.copy()
    work[h3_col] = work[h3_col].astype(str)
    parent_map = load_or_build_parent_map(
        cells=work[h3_col].dropna().unique().tolist(),
        parent_res=int(parent_res),
        path_manager=path_manager,
        namespace=namespace,
        child_col=h3_col,
        parent_col=parent_col,
    )
    return work.merge(parent_map, on=h3_col, how="left", copy=False)


def load_or_build_neighbor_edges(
    *,
    cells: list[str] | pd.Series | Any,
    k_ring: int,
    include_self: bool,
    path_manager: PathManager,
    namespace: str | None = None,
    water_universe: set[str] | None = None,
    anchor_col: str = "anchor_h3",
    neighbor_col: str = "neighbor_h3",
) -> pd.DataFrame:
    unique_cells = _canonical_cells(cells)
    if not unique_cells:
        return pd.DataFrame(columns=[anchor_col, neighbor_col])
    water_suffix = "all"
    if water_universe is not None:
        water_suffix = hashlib.sha1("\n".join(sorted(water_universe)).encode("utf-8")).hexdigest()[
            :12
        ]
    cells_sig = f"{_cells_signature(unique_cells)}__{water_suffix}"
    path = _neighbor_edge_path(
        path_manager,
        k_ring=int(k_ring),
        include_self=bool(include_self),
        namespace=str(namespace or "default"),
        cells_sig=cells_sig,
    )
    cache_key = str(path.resolve())
    cached = _cache_get(_NEIGHBOR_EDGE_CACHE, cache_key)
    if cached is not None:
        return cached.rename(columns={"anchor_h3": anchor_col, "neighbor_h3": neighbor_col}).copy()
    if path.exists():
        base = pd.read_parquet(path)
        _cache_set(_NEIGHBOR_EDGE_CACHE, cache_key, base)
        return base.rename(columns={"anchor_h3": anchor_col, "neighbor_h3": neighbor_col}).copy()

    rows: list[dict[str, str]] = []
    for anchor in unique_cells:
        neighbors = [str(x) for x in grid_disk(anchor, int(k_ring))]
        if not include_self:
            neighbors = [value for value in neighbors if value != anchor]
        elif anchor not in neighbors:
            neighbors.append(anchor)
        if water_universe is not None:
            neighbors = [value for value in neighbors if value in water_universe]
        rows.extend({"anchor_h3": anchor, "neighbor_h3": value} for value in neighbors)
    logger.info(
        "Built H3 neighbor edge artifact namespace=%s cells=%d edges=%d k_ring=%d include_self=%s",
        str(namespace or "default"),
        len(unique_cells),
        len(rows),
        int(k_ring),
        bool(include_self),
    )
    base = pd.DataFrame(rows, columns=["anchor_h3", "neighbor_h3"]).drop_duplicates()
    path.parent.mkdir(parents=True, exist_ok=True)
    base.to_parquet(path, index=False)
    _cache_set(_NEIGHBOR_EDGE_CACHE, cache_key, base)
    return base.rename(columns={"anchor_h3": anchor_col, "neighbor_h3": neighbor_col}).copy()


def load_or_build_neighbor_reverse_map(
    *,
    cells: list[str] | pd.Series | Any,
    k_ring: int,
    include_self: bool,
    path_manager: PathManager,
    namespace: str | None = None,
    water_universe: set[str] | None = None,
    source_col: str = "source_h3",
    anchor_col: str = "anchor_h3",
) -> pd.DataFrame:
    edges = load_or_build_neighbor_edges(
        cells=cells,
        k_ring=int(k_ring),
        include_self=bool(include_self),
        path_manager=path_manager,
        namespace=namespace,
        water_universe=water_universe,
        anchor_col=anchor_col,
        neighbor_col=source_col,
    )
    return edges[[source_col, anchor_col]].drop_duplicates()
