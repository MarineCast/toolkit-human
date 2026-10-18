"""Shared input, download, and atomic output helpers."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import geopandas as gpd
import requests

LOGGER = logging.getLogger(__name__)


def ensure_dirs(paths: Iterable[Path]) -> None:
    """Create required pipeline directories."""
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


def download_file(url: str, out_path: Path, overwrite: bool = False) -> Path:
    """Download a URL atomically and reuse a cached file unless overwrite is true."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not overwrite:
        LOGGER.info("Using cached download: %s", out_path)
        return out_path

    temp_path = out_path.with_name(f".{out_path.name}.{uuid.uuid4().hex}.part")
    LOGGER.info("Downloading %s to %s", url, out_path)
    try:
        with requests.get(url, stream=True, timeout=180) as response:
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise RuntimeError(
                    f"Download failed with HTTP {response.status_code}: {url}\n"
                    f"Response preview:\n{response.text[:1000]}"
                ) from exc
            with temp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        os.replace(temp_path, out_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return out_path


def read_zipped_vector(zip_path: Path) -> gpd.GeoDataFrame:
    """Read a zipped vector dataset with GeoPandas-compatible fallbacks."""
    zip_path = Path(zip_path).resolve()
    try:
        return gpd.read_file(f"zip://{zip_path}")
    except Exception:
        return gpd.read_file(zip_path)


def read_vector(path: str | Path) -> gpd.GeoDataFrame:
    """Read supported vector formats, including GeoParquet."""
    resolved = Path(path).expanduser().resolve()
    suffixes = "".join(resolved.suffixes).lower()
    suffix = resolved.suffix.lower()
    if suffixes.endswith(".geo.parquet") or suffix in {".parquet", ".pq"}:
        return gpd.read_parquet(resolved)
    if suffix == ".zip":
        return read_zipped_vector(resolved)
    if suffix in {".geojson", ".gpkg", ".shp", ".fgb"}:
        return gpd.read_file(resolved)
    raise ValueError(f"Unsupported vector format: {resolved}")


def atomic_write_parquet(
    frame: Any,
    path: str | Path,
    *,
    overwrite: bool,
    index: bool = False,
) -> Path:
    """Write a DataFrame or GeoDataFrame to Parquet using an atomic rename."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output exists and overwrite is false: {output}")
    temp_path = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temp_path, index=index)
        os.replace(temp_path, output)
    finally:
        temp_path.unlink(missing_ok=True)
    return output
