from dataclasses import replace

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import polars as pl
from shapely.geometry import LineString, Point

from human.activity_and_effort.land_reporting_opportunity.build import (
    build_target_components,
)
from human.activity_and_effort.land_reporting_opportunity.dynamic import (
    STREAM_COMPONENTS,
    _add_daily_v2_columns,
    aggregate_weekly,
)
from human.activity_and_effort.land_reporting_opportunity.sites import (
    build_observation_sites,
    sample_observation_sites,
)
from human.activity_and_effort.land_reporting_opportunity.support import (
    aggregate_supported_children,
    canonical_target_support,
)
from human.activity_and_effort.observation_opportunity_contract import (
    lineage_values,
)


def _facilities():
    return gpd.GeoDataFrame(
        {
            "SOURCE_DATASET": ["fixture"] * 3,
            "SOURCE_RECORD_ID": ["beach", "pier", "headland"],
            "PUBLIC_ACCESS_STATE": ["public", "public", "restricted"],
            "ACCESS_EVIDENCE_TIER": ["authoritative_verified", "osm_explicit_public", "restricted"],
            "H3_INDEX": [h3.latlng_to_cell(48, -123, 7)] * 3,
        },
        geometry=[
            LineString([(-123.001, 48), (-123, 48)]),
            Point(-123, 48.001),
            Point(-123.002, 48),
        ],
        crs=4326,
    )


def test_access_samples_exclude_inaccessible_headland_and_conserve_budget():
    sites = build_observation_sites(_facilities())
    assert sites.MAPPED_SITE_WEIGHT.sum() == 1
    assert sites.VERIFIED_SITE_WEIGHT.sum() == 1
    for count in (5, 20):
        samples = sample_observation_sites(sites, samples_per_site=count)
        assert not samples.PARENT_SITE_ID.str.contains("headland").any()
        np.testing.assert_allclose(samples.groupby("PARENT_SITE_ID").SAMPLE_WEIGHT.sum(), 1)
        assert np.isclose((samples.SAMPLE_WEIGHT * samples.MAPPED_SITE_WEIGHT).sum(), 1)
        # Constant kernel: sample allocation cannot multiply the source budget.
        assert np.isclose((7 * samples.SAMPLE_WEIGHT * samples.MAPPED_SITE_WEIGHT).sum(), 7)
        beach = samples.loc[samples.PARENT_SITE_ID == "fixture:beach"]
        assert beach.geometry.y.between(47.999999, 48.000001).all()


def test_duplicate_and_computational_split_preserve_parent_budget():
    original = _facilities()
    duplicated = pd.concat([original, original.iloc[[0]]], ignore_index=True)
    baseline = build_observation_sites(original)
    duplicate = build_observation_sites(duplicated)
    np.testing.assert_allclose(baseline.MAPPED_SITE_WEIGHT, duplicate.MAPPED_SITE_WEIGHT)
    split = original.iloc[[0, 0, 1, 2]].copy()
    split.geometry = [
        LineString([(-123.001, 48), (-123.0005, 48)]),
        LineString([(-123.0005, 48), (-123, 48)]),
        *list(original.geometry.iloc[1:]),
    ]
    split_sites = build_observation_sites(split)
    assert len(split_sites) == len(baseline)
    np.testing.assert_allclose(split_sites.MAPPED_SITE_WEIGHT, baseline.MAPPED_SITE_WEIGHT)


def test_water_area_aggregation_includes_explicit_zero_and_distinguishes_missing():
    parent = h3.latlng_to_cell(48, -123, 6)
    children = sorted(h3.cell_to_children(parent, 7))[:2]
    support = canonical_target_support(
        pd.DataFrame({"target_h3": children, "target_water_area_m2": [0.1, 0.9]})
    )
    values = pd.DataFrame({"target_h3": children, "OPPORTUNITY_RAW": [1.0, 0.0]})
    complete = aggregate_supported_children(values, support)
    partial = aggregate_supported_children(values.iloc[:1], support)
    assert np.isclose(complete.OPPORTUNITY_RAW.iloc[0], 0.1)
    assert complete.STATE.iloc[0] == "positive"
    assert partial.STATE.iloc[0] == "partial"
    assert np.isclose(partial.EVALUATED_GEOMETRIC_SUPPORT_FRACTION.iloc[0], 0.1)
    assert np.isclose(partial.OPPORTUNITY_RAW.iloc[0], 0.1)


def test_static_access_stays_positive_on_zero_opportunity_day():
    columns = {"DATE": [pd.Timestamp("2026-01-05")], "CALENDAR_EFFORT_WEIGHT": [1.0]}
    for stream, _ in STREAM_COMPONENTS:
        for suffix, value in (
            ("STATIC_RAW", 1.0),
            ("STATIC_INDEX", 1.0),
            ("STATIC_CONTEXT_FRACTION", 0.5),
            ("RAW", 0.0),
            ("INDEX", 0.0),
            ("DYNAMIC_CONTEXT_COVERAGE", 1.0),
        ):
            columns[f"{stream}_{suffix}"] = [value]
    lineage = lineage_values(
        generation_id="fixture",
        config_hash="a" * 64,
        source_hashes={"fixture": "b" * 64},
        source_vintages={},
        knowledge_time_utc="2026-01-01T00:00:00+00:00",
    )
    result = _add_daily_v2_columns(pd.DataFrame(columns), lineage=lineage)
    assert result.PUBLIC_SHORE_ACCESS_MAPPED_STATE.iloc[0] == "positive"
    assert result.PUBLIC_SHORE_ACCESS_MAPPED_STATIC_CONTEXT_FRACTION.iloc[0] == 0.5
    assert result.LAND_OBSERVATION_OPPORTUNITY_STATE.iloc[0] == "derived_zero"


def test_all_null_transport_raw_and_display_stay_null_and_valid_zero_counts():
    static = pl.DataFrame(
        {"source_h3": ["a"], "target_h3": ["t"], "weight_static_viewability": [1.0]}
    )
    source = pl.DataFrame(
        {
            "source_h3": ["a"],
            "LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX": [None],
            "VERIFIED_PUBLIC_ACCESS_SUPPORTED_INDEX": [None],
            "MAPPED_PUBLIC_ACCESS_SUPPORTED_INDEX": [None],
            "LAND_REACHABILITY_OPPORTUNITY_AVAILABLE": [False],
            "PUBLIC_SHORE_CONTEXT_MAPPED": [False],
        },
        schema_overrides={
            key: pl.Float64
            for key in [
                "LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX",
                "VERIFIED_PUBLIC_ACCESS_SUPPORTED_INDEX",
                "MAPPED_PUBLIC_ACCESS_SUPPORTED_INDEX",
            ]
        },
    )
    target, _ = build_target_components(static, source, scaling_quantile=0.99)
    assert target["LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_RAW"].item() is None
    assert target["LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_INDEX"].item() is None
    source = source.with_columns(pl.lit(0.0).alias("LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX"))
    target, _ = build_target_components(static, source, scaling_quantile=0.99)
    assert target["TRANSPORT_TRAVEL_AVAILABLE_SOURCE_COUNT"].item() == 1
    assert target["LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_RAW"].item() == 0


def test_seven_partial_days_remain_partial_for_both_names():
    daily = pl.DataFrame(
        {
            "DATE": pd.date_range("2026-01-05", periods=7),
            "H3_INDEX": ["t"] * 7,
            "H3_RESOLUTION": [6] * 7,
            "CALENDAR_EFFORT_WEIGHT": [1.0] * 7,
            "LAND_EFFORT_PROXY_RAW": [0.0] * 7,
            "LAND_EFFORT_PROXY_STATE": ["partial"] * 7,
            "LAND_OBSERVATION_OPPORTUNITY_RAW": [0.0] * 7,
            "LAND_OBSERVATION_OPPORTUNITY_STATE": ["partial"] * 7,
        }
    )
    weekly = aggregate_weekly(daily.lazy())
    assert weekly["LAND_EFFORT_PROXY_STATE"].item() == "partial"
    assert weekly["LAND_OBSERVATION_OPPORTUNITY_STATE"].item() == "partial"
    assert weekly["LAND_OBSERVATION_OPPORTUNITY_AVAILABLE_DAY_SUM"].item() == 0
    assert weekly["EXPECTED_DAY_COUNT"].item() == 7


def _conditioned_result(support, kernel, *, certify=True, missing_weather=(), activities=None):
    from scipy import sparse

    from human.activity_and_effort.land_reporting_opportunity.conditioned_basis import (
        condition_basis,
    )
    from human.activity_and_effort.land_reporting_opportunity.config import (
        load_land_reporting_config,
    )
    from human.activity_and_effort.land_reporting_opportunity.dynamic import (
        DynamicBasis,
        compute_dynamic_chunk,
    )

    source = h3.latlng_to_cell(48, -123, 7)
    weather_cell = h3.cell_to_parent(source, 5)
    daylight_cell = h3.cell_to_parent(source, 4)
    dates = pd.date_range("2026-01-05", periods=1)
    order = pd.Index(sorted(support.H3_INDEX.unique()), name="H3_INDEX")
    n = len(order)
    static = pd.DataFrame({"H3_INDEX": order})
    for stream, _ in STREAM_COMPONENTS:
        for suffix in ("STATIC_RAW", "CONTEXT_STATIC_SUPPORT", "STATIC_CONTEXT_FRACTION"):
            static[f"{stream}_{suffix}"] = 1.0
    basis = DynamicBasis(
        target_order=order,
        target_static=static,
        distance_km=np.array([0.0]),
        weather_bases=[
            {
                "weather_h3_r5": weather_cell,
                "daylight_h3_r4": daylight_cell,
                "matrix_transpose": sparse.csr_matrix(np.ones((len(STREAM_COMPONENTS) * n, 1))),
                "context_totals": np.ones(len(STREAM_COMPONENTS) * n),
            }
        ],
        weather_daily={
            weather_cell: pd.DataFrame(
                {
                    "VISIBILITY_KM_MEAN": 100.0,
                    "WIND_SPEED_10M_MS_MEAN": 2.0,
                    "PRECIP_MM_DAY_ESTIMATE": 0.0,
                    "SAMPLE_COVERAGE_FRAC": 1.0,
                    "QC_STATE": "COMPLETE",
                },
                index=dates,
            )
        },
        daylight_daily={daylight_cell: pd.DataFrame({"DAYLIGHT_FRACTION": 0.5}, index=dates)},
        calendar_daily=pd.DataFrame({"calendar_effort_weight": 1.0}, index=dates),
        dates=dates,
        native_pairs=len(kernel),
        grouped_rows=len(kernel),
    )
    cfg = load_land_reporting_config()
    source_cells = sorted(set(kernel.source_h3))
    source_frame = pd.DataFrame(
        {
            "source_h3": source_cells,
            "LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX": (
                activities if activities is not None else [1.0] * len(source_cells)
            ),
        }
    )
    for cell in source_cells:
        region = h3.cell_to_parent(cell, 5)
        basis.weather_daily[region] = basis.weather_daily[weather_cell].copy()
        basis.daylight_daily[h3.cell_to_parent(cell, 4)] = basis.daylight_daily[
            daylight_cell
        ].copy()
    for region in missing_weather:
        basis.weather_daily.pop(region, None)
    if certify:
        from human.activity_and_effort.land_reporting_opportunity.access_kernel import (
            kernel_digest,
            support_digest,
        )

        support = support.copy()
        support.attrs["evaluation_plan"] = {
            "state": "complete",
            "kernel_digest": kernel_digest(kernel),
            "support_digest": support_digest(support),
            "samples": [
                {
                    "source_h3": c,
                    "sample_id": c,
                    "SAMPLE_WEIGHT": 1.0,
                    "MAPPED_SITE_WEIGHT": 1.0,
                    "VERIFIED_SITE_WEIGHT": 0.0,
                }
                for c in source_cells
            ],
        }

    conditioned = condition_basis(cfg, basis, source_frame, kernel, support)
    return compute_dynamic_chunk(cfg, conditioned, dates), conditioned


def _kernel(children, values):
    from human.activity_and_effort.land_reporting_opportunity.access_kernel import (
        ACCESS_KERNEL_VERSION,
    )

    return pd.DataFrame(
        {
            "source_h3": h3.latlng_to_cell(48, -123, 7),
            "target_h3": children,
            "SCENARIO": "MAPPED",
            "DISTANCE_BIN_INDEX": 0,
            "canopy_kernel": values,
            "bare_earth_kernel": values,
            "evaluated_support": 1.0,
            "KERNEL_ALGORITHM_VERSION": ACCESS_KERNEL_VERSION,
        }
    )


def test_production_basis_rejects_missing_geometry_and_retains_zero_water_denominator():
    import pytest

    children = sorted(h3.cell_to_children(h3.latlng_to_cell(48, -123, 6), 7))[:2]
    support = canonical_target_support(
        pd.DataFrame({"target_h3": children, "target_water_area_m2": [0.1, 0.9]})
    )
    kernel = _kernel(children, [1.0, 0.0])
    complete, basis = _conditioned_result(support, kernel)
    # A missing computation is rejected, rather than being certified as zero.
    with pytest.raises(ValueError, match="geometry remains unresolved"):
        _conditioned_result(support, kernel.iloc[:1], certify=False)
    np.testing.assert_allclose(basis.target_static.PHYSICAL_VIEWABILITY_STATIC_RAW, 0.1)
    np.testing.assert_allclose(basis.target_static.EVALUATED_GEOMETRIC_SUPPORT_FRACTION, 1.0)
    np.testing.assert_allclose(complete["DAYLIGHT_WEIGHT"], 0.5)
    np.testing.assert_allclose(complete["LAND_EFFORT_PROXY_DYNAMIC_CONTEXT_COVERAGE"], 1.0)


def test_adding_another_target_does_not_reduce_existing_target_kernel():
    children = [h3.latlng_to_cell(48, -123, 7), h3.latlng_to_cell(49, -123, 7)]
    support = canonical_target_support(
        pd.DataFrame({"target_h3": children, "target_water_area_m2": [1.0, 1.0]})
    )
    full, basis = _conditioned_result(support, _kernel(children, [1.0, 1.0]))
    one, _ = _conditioned_result(support.iloc[:1], _kernel([support.target_h3.iloc[0]], [1.0]))
    index = list(basis.target_order).index(support.H3_INDEX.iloc[0])
    np.testing.assert_allclose(
        full["LAND_EFFORT_PROXY_RAW"][:, index],
        one["LAND_EFFORT_PROXY_RAW"][:, 0],
        rtol=1e-6,
        atol=1e-8,
    )


def test_incompatible_kernel_and_support_versions_are_rejected():
    import pytest

    child = h3.latlng_to_cell(48, -123, 7)
    support = canonical_target_support(
        pd.DataFrame({"target_h3": [child], "target_water_area_m2": [1.0]})
    )
    kernel = _kernel([child], [1.0])
    with pytest.raises(ValueError, match="Incompatible access kernel"):
        _conditioned_result(support, kernel.assign(KERNEL_ALGORITHM_VERSION="old"))
    with pytest.raises(ValueError, match="Incompatible canonical water support"):
        _conditioned_result(support.assign(WATER_SUPPORT_VERSION="old"), kernel)


def test_irrelevant_source_weather_does_not_change_local_conditions():
    child = h3.latlng_to_cell(48, -123, 7)
    remote = h3.latlng_to_cell(49, -123, 7)
    support = canonical_target_support(
        pd.DataFrame({"target_h3": [child], "target_water_area_m2": [1.0]})
    )
    a = _kernel([child], [1.0])
    b = _kernel([child], [0.0]).assign(source_h3=remote, DISTANCE_BIN_INDEX=80)
    baseline, _ = _conditioned_result(support, a)
    for missing in ((), (h3.cell_to_parent(remote, 5),)):
        actual, _ = _conditioned_result(support, pd.concat([a, b]), missing_weather=missing)
        for name in [
            "LAND_EFFORT_PROXY_RAW",
            "LAND_EFFORT_PROXY_DYNAMIC_CONTEXT_COVERAGE",
            "DAYLIGHT_WEIGHT",
            "ATMOSPHERIC_VISIBILITY_WEIGHT",
            "DYNAMIC_CONDITION_COVERAGE",
        ]:
            np.testing.assert_allclose(actual[name], baseline[name])


def test_proven_zero_survives_missing_weather_but_unknown_activity_does_not():
    child = h3.latlng_to_cell(48, -123, 7)
    support = canonical_target_support(
        pd.DataFrame({"target_h3": [child], "target_water_area_m2": [1.0]})
    )
    missing = (h3.cell_to_parent(child, 5),)
    result, _ = _conditioned_result(support, _kernel([child], [0.0]), missing_weather=missing)
    np.testing.assert_allclose(result["LAND_EFFORT_PROXY_RAW"], 0)
    np.testing.assert_allclose(result["LAND_EFFORT_PROXY_DYNAMIC_CONTEXT_COVERAGE"], 1)
    for options in ({"missing_weather": missing}, {"activities": [np.nan]}):
        unresolved, _ = _conditioned_result(support, _kernel([child], [1.0]), **options)
        assert np.isnan(unresolved["LAND_EFFORT_PROXY_RAW"]).all()
        np.testing.assert_allclose(unresolved["LAND_EFFORT_PROXY_DYNAMIC_CONTEXT_COVERAGE"], 0)


def test_missing_contributing_weather_keeps_partial_sum_without_inflation():
    child = h3.latlng_to_cell(48, -123, 7)
    remote = h3.latlng_to_cell(49, -123, 7)
    support = canonical_target_support(
        pd.DataFrame({"target_h3": [child], "target_water_area_m2": [1.0]})
    )
    kernel = pd.concat([_kernel([child], [1.0]), _kernel([child], [1.0]).assign(source_h3=remote)])
    full, _ = _conditioned_result(support, kernel)
    partial, _ = _conditioned_result(
        support, kernel, missing_weather=(h3.cell_to_parent(remote, 5),)
    )
    np.testing.assert_allclose(partial["LAND_EFFORT_PROXY_RAW"], full["LAND_EFFORT_PROXY_RAW"] / 2)
    np.testing.assert_allclose(partial["LAND_EFFORT_PROXY_DYNAMIC_CONTEXT_COVERAGE"], 0.5)
    np.testing.assert_allclose(partial["DAYLIGHT_WEIGHT"], 0.5)


def test_parent_spanning_source_cells_retains_identity_and_conserves_fragment_budget():
    facilities = _facilities().iloc[[1, 1]].copy()
    facilities["H3_INDEX"] = [h3.latlng_to_cell(48, -123, 7), h3.latlng_to_cell(49, -123, 7)]
    facilities.geometry = [Point(-123, 48), Point(-123, 49)]
    sites = build_observation_sites(facilities)
    assert len(sites) == 2 and sites.PARENT_SITE_ID.nunique() == 1
    assert sites.SITE_ID.nunique() == 2
    np.testing.assert_allclose(sites.PARENT_FRAGMENT_SHARE.sum(), 1)
    samples = sample_observation_sites(sites)
    np.testing.assert_allclose(
        (samples.SAMPLE_WEIGHT * samples.MAPPED_SITE_WEIGHT).groupby(samples.source_h3).sum(), 1
    )


def test_restricted_remote_fragment_cannot_reduce_public_cell_budget():
    a = _facilities().iloc[[1]].copy()
    a["PARENT_SITE_ID"] = "shared"
    b = a.copy()
    b["H3_INDEX"] = h3.latlng_to_cell(49, -123, 7)
    b["PUBLIC_ACCESS_STATE"] = "restricted"
    b.geometry = [Point(-123, 49)]
    baseline = build_observation_sites(a)
    combined = build_observation_sites(pd.concat([a, b], ignore_index=True))
    public = combined.loc[combined.MAPPED_ELIGIBLE]
    np.testing.assert_allclose(public.MAPPED_SITE_WEIGHT, baseline.MAPPED_SITE_WEIGHT)
    assert public.MAPPED_SITE_WEIGHT.iloc[0] == 1


def test_verified_local_reference_support_differs_by_target():
    children = [h3.latlng_to_cell(48, -123, 7), h3.latlng_to_cell(49, -123, 7)]
    support = canonical_target_support(
        pd.DataFrame({"target_h3": children, "target_water_area_m2": [1.0, 1.0]})
    )
    mapped = _kernel(children, [0.5, 0.5])
    subset = _kernel(children, [0.5, 0.0]).assign(SCENARIO="VERIFIED_REFERENCE")
    _, basis = _conditioned_result(support, pd.concat([mapped, subset]))
    np.testing.assert_allclose(
        basis.target_static.TARGET_LOCAL_VERIFIED_REFERENCE_FRACTION, [1.0, 0.0]
    )
    assert basis.target_static.TARGET_LOCAL_VERIFIED_REFERENCE_STATE.tolist() == [
        "positive",
        "derived_zero",
    ]
