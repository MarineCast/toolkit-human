"""Observer-source context labels; these do not assign maritime jurisdiction."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Point


def target_context(root: Path) -> pd.DataFrame:
    from .config import load_land_reporting_config

    cfg = load_land_reporting_config(
        root / "config/data/human/activity_and_effort/land_reporting_opportunity.yaml"
    )
    pairs = pd.read_parquet(
        cfg.static_weights_path, columns=["source_h3", "target_h3", "weight_static_viewability"]
    )
    shore = pd.read_parquet(cfg.public_shore_path, columns=["H3_INDEX", "JURISDICTION"])
    jurisdiction = shore.set_index("H3_INDEX").JURISDICTION.to_dict()
    land = (
        gpd.read_file(root / "data/raw/gis/land/ne_10m_land.shp")
        .to_crs(4326)
        .explode(index_parts=False)
    )
    tree = shapely.STRtree(land.geometry.to_numpy())
    mainland = set(tree.query(Point(-122.3321, 47.6062), predicate="intersects"))
    labels = {}
    for cell in pairs.source_h3.unique():
        lat, lon = h3.cell_to_latlng(cell)
        parts = set(tree.query(Point(lon, lat), predicate="intersects"))
        labels[cell] = "mainland" if parts & mainland else "island" if parts else "unknown"
    pairs["h3"] = pairs.target_h3.map(lambda cell: h3.cell_to_parent(cell, 6))
    pairs["source_landmass"] = pairs.source_h3.map(labels)
    pairs["source_jurisdiction"] = pairs.source_h3.map(jurisdiction).fillna("unknown")
    result = pd.DataFrame({"h3": sorted(pairs.h3.unique())}).set_index("h3")
    for field in ("source_landmass", "source_jurisdiction"):
        mass = pairs.groupby(["h3", field]).weight_static_viewability.sum().unstack(fill_value=0)
        total = mass.sum(axis=1).replace(0, np.nan)
        fractions = mass.div(total, axis=0)
        dominant = fractions.fillna(0).idxmax(axis=1)
        result[field] = dominant.where(fractions.max(axis=1).ge(0.75), "mixed_or_unknown")
    return result.reset_index()
