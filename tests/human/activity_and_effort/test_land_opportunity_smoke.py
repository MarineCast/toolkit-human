"""GDAL-backed production smoke; skips only when GDAL bindings are unavailable."""

import json
from dataclasses import replace
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest

from human.activity_and_effort.land_reporting_opportunity.access_kernel import (
    build_access_kernel,
)
from human.activity_and_effort.land_reporting_opportunity.config import (
    load_land_reporting_config,
)
from human.activity_and_effort.land_reporting_opportunity.smoke import run_smoke
from human.viewshed.config import load_app_config


def test_offline_production_smoke_and_batch_invariance(tmp_path: Path):
    pytest.importorskip("osgeo.gdal", reason="GDAL bindings required for production land smoke")
    config = run_smoke(tmp_path)
    cfg = load_land_reporting_config(config)
    manifest = json.loads(cfg.manifest_path.read_text())
    from human.activity_and_effort.land_reporting_opportunity.modeling import (
        load_weekly,
    )

    with pytest.raises(ValueError, match="Access-conditioned land schema v4"):
        load_weekly(cfg.weekly_output_path)
    outputs = {a["dataset_id"].split(".")[-1]: Path(a["path"]) for a in manifest["artifacts"]}
    daily = pd.read_parquet(cfg.daily_output_path)
    weekly = pd.read_parquet(cfg.weekly_output_path)
    assert daily.DATE.nunique() == 7
    coverage = daily.LAND_EFFORT_PROXY_DYNAMIC_CONTEXT_COVERAGE
    assert daily.loc[coverage.eq(0), "LAND_OBSERVATION_OPPORTUNITY_RAW"].isna().all()
    assert (
        daily.loc[coverage.between(0, 1, inclusive="neither"), "LAND_OBSERVATION_OPPORTUNITY_STATE"]
        .eq("partial")
        .all()
    )
    assert (
        daily.loc[coverage.eq(1), "LAND_OBSERVATION_OPPORTUNITY_STATE"]
        .isin(["positive", "derived_zero"])
        .all()
    )
    assert set(weekly.LAND_OBSERVATION_OPPORTUNITY_STATE) == set(
        daily.LAND_OBSERVATION_OPPORTUNITY_STATE
    )
    assert daily.LAND_OBSERVATION_OPPORTUNITY_STATE.eq("partial").any()
    np.testing.assert_allclose(daily.DAYLIGHT_WEIGHT.dropna(), 0.5, rtol=1e-6, atol=1e-8)
    np.testing.assert_allclose(daily.DAYLIGHT_COVERAGE, 1)
    assert daily.ACCESS_MAPPING_COMPLETENESS.isna().all()
    assert daily.LAND_OBSERVATION_OPPORTUNITY_RAW.isna().any()
    assert daily.LAND_OBSERVATION_OPPORTUNITY_RAW.gt(0).any()
    expected = daily.groupby("H3_INDEX").LAND_OBSERVATION_OPPORTUNITY_RAW.sum(min_count=1)
    np.testing.assert_allclose(
        weekly.set_index("H3_INDEX").LAND_OBSERVATION_OPPORTUNITY_AVAILABLE_DAY_SUM.sort_index(),
        expected.sort_index(),
        rtol=1e-6,
        atol=1e-8,
    )
    assert weekly.LAND_OBSERVATION_OPPORTUNITY_AVAILABLE_DAY_SUM_STATE.eq("partial").any()
    support = pd.read_parquet(outputs["target_water_support"])
    assert support.target_water_area_m2.nunique() > 1
    assert outputs["inspection_html"].exists()
    sites = gpd.read_parquet(outputs["observation_sites"])
    samples = gpd.read_parquet(outputs["observer_samples"])
    assert not samples.PARENT_SITE_ID.str.contains("headland").any()
    assert (
        sites.loc[sites.PARENT_SITE_ID.str.contains("headland"), "MAPPED_ELIGIBLE"].eq(False).all()
    )
    convergence = json.loads((tmp_path / "sampling_convergence.json").read_text())
    assert convergence["5"]["maximum_absolute_discrepancy_from_80"] > 0
    assert convergence["20"]["maximum_absolute_discrepancy_from_80"] >= 0
    # Fixed A kernel: adding/reordering a zero-budget B must not clear B's obstruction.
    a = samples.iloc[[0]].copy()
    a["SAMPLE_WEIGHT"] = 1.0
    b = samples.iloc[[1]].copy()
    b["SAMPLE_WEIGHT"] = 0.0
    app = load_app_config(cfg.viewshed_config_path)
    baseline, water = build_access_kernel(app, a, distance_bin_km=0.25)
    for design in (pd.concat([a, b]), pd.concat([b, a])):
        actual, other_water = build_access_kernel(app, design, distance_bin_km=0.25)
        pd.testing.assert_frame_equal(water, other_water)
        keys = ["source_h3", "target_h3", "DISTANCE_BIN_INDEX", "SCENARIO"]
        # Extra empty distance bins are structural zeros and do not change fixed-pair totals.
        cols = ["bare_earth_kernel", "canopy_kernel", "evaluated_support"]
        left = baseline.groupby(keys)[cols].sum()
        right = actual.groupby(keys)[cols].sum()
        left, right = left.align(right, fill_value=0)
        np.testing.assert_allclose(left, right, rtol=1e-6, atol=1e-8)


def test_sparse_dense_reference_remote_extension_and_partition_resume(tmp_path, monkeypatch):
    import rasterio

    from human.activity_and_effort.land_reporting_opportunity import (
        access_kernel as module,
    )
    from human.viewshed.prepare.area.raster_stack import (
        ensure_canonical_raster_stack,
    )

    pytest.importorskip("osgeo.gdal")
    config = run_smoke(tmp_path)
    cfg = load_land_reporting_config(config)
    manifest = json.loads(cfg.manifest_path.read_text())
    sample_path = next(
        a["path"] for a in manifest["artifacts"] if a["dataset_id"].endswith(".observer_samples")
    )
    samples = gpd.read_parquet(sample_path).iloc[:1].copy()
    app = load_app_config(cfg.viewshed_config_path)
    baseline, support = build_access_kernel(app, samples, distance_bin_km=0.25)
    resumed, resumed_support = build_access_kernel(app, samples, distance_bin_km=0.25)
    pd.testing.assert_frame_equal(baseline, resumed)
    assert resumed_support.attrs["evaluation_plan"]["profile"]["resumed_partitions"] == 1
    assert resumed_support.attrs["evaluation_plan"]["profile"]["processed_pixels"] == 0

    class DenseReferenceIndex:
        def __init__(self, xy):
            self.xy = xy

        def query_ball_point(self, point, radius):
            # Deliberately scan all water pixels, independently of the spatial index.
            return np.flatnonzero(np.hypot(*(self.xy - point).T) <= radius)

    with monkeypatch.context() as patch:
        patch.setattr(module, "cKDTree", DenseReferenceIndex)
        dense_app = replace(app, raw_config={**app.raw_config, "test_reference": "dense"})
        dense, _ = build_access_kernel(dense_app, samples, distance_bin_km=0.25)
    columns = ["canopy_kernel", "bare_earth_kernel", "evaluated_support"]
    np.testing.assert_allclose(baseline[columns], dense[columns], rtol=1e-6, atol=1e-8)

    stack = ensure_canonical_raster_stack(app, include_canopy=True)
    remote_path = tmp_path / "extended_water.tif"
    with rasterio.open(stack.water_mask_path) as src:
        water = src.read(1)
        remote = np.pad(water, ((0, 0), (0, 700)))
        remote[:, -100:] = 1
        with rasterio.open(remote_path, "w", **{**src.profile, "width": remote.shape[1]}) as dst:
            dst.write(remote, 1)
    with monkeypatch.context() as patch:
        patch.setattr(
            module,
            "ensure_canonical_raster_stack",
            lambda *a, **kw: replace(stack, water_mask_path=remote_path),
        )
        extended, extended_support = build_access_kernel(app, samples, distance_bin_km=0.25)
    np.testing.assert_allclose(baseline[columns], extended[columns], rtol=1e-6, atol=1e-8)
    first = support.attrs["evaluation_plan"]["profile"]
    other = extended_support.attrs["evaluation_plan"]["profile"]
    assert other["regional_water_pixels"] > first["regional_water_pixels"]
    assert other["max_observer_pixels"] == first["max_observer_pixels"]
    assert first["max_observer_pixels"] < first["regional_water_pixels"]
    assert len(extended_support) > len(support)
