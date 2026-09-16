"""Session fixtures that keep the test suite independent of local research data."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from human.core.config.paths import project_root
import os
os.environ.setdefault("HUMAN_WORKSPACE", str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="session", autouse=True)
def public_source_contract_fixtures() -> Iterator[None]:
    """Create minimal synthetic files required only by shipped-config contract tests.

    Real source products remain excluded from Git. Existing local products always win;
    a clean checkout receives small valid stand-ins for path, checksum, and loader tests.
    The stand-ins are removed at the end of the test session and are never suitable for
    modeling, evaluation, or publication.
    """

    root = project_root()
    created: list[Path] = []

    water = (
        root / "data/processed/domain/environmental_layer/seascape/spatial_support/"
        "water_geometry/TERRITORIAL_WATER_POLYGON.parquet"
    )
    if not water.exists():
        water.parent.mkdir(parents=True, exist_ok=True)
        gpd.GeoDataFrame(
            {"SOURCE": ["SYNTHETIC_TEST_FIXTURE"]},
            geometry=[box(-125.9, 46.7, -121.5, 50.1)],
            crs="EPSG:4326",
        ).to_parquet(water)
        created.append(water)

    land = root / "data/raw/gis/land/ne_10m_land.shp"
    if not land.exists():
        land.parent.mkdir(parents=True, exist_ok=True)
        before = set(land.parent.glob(f"{land.stem}.*"))
        gpd.GeoDataFrame(
            {"SOURCE": ["SYNTHETIC_TEST_FIXTURE"]},
            geometry=[box(-130.0, 45.0, -126.0, 52.0)],
            crs="EPSG:4326",
        ).to_file(land)
        created.extend(sorted(set(land.parent.glob(f"{land.stem}.*")) - before))

    try:
        yield
    finally:
        for path in reversed(created):
            path.unlink(missing_ok=True)
