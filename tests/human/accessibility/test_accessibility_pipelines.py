from __future__ import annotations

import importlib
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import LineString, Point


def _write_yaml(path: Path, payload: dict) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _source(source_type: str, jurisdiction: str) -> dict[str, str]:
    return {
        "type": source_type,
        "url": "https://fixture.invalid/source",
        "provider": "fixture provider",
        "license": "fixture license",
        "attribution": "fixture attribution",
        "jurisdiction": jurisdiction,
    }


def test_boat_launch_download_build_inspect_with_osm(tmp_path, monkeypatch) -> None:
    download_module = importlib.import_module(
        "human.accessibility.boat_launch_access.download"
    )
    build_module = importlib.import_module(
        "human.accessibility.boat_launch_access.build"
    )
    inspect_module = importlib.import_module(
        "human.accessibility.boat_launch_access.inspect"
    )
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    config = _write_yaml(
        tmp_path / "boat_launch.yaml",
        {
            "schema_version": 1,
            "product": "boat_launch_access",
            "category": "accessibility",
            "bbox": {"west": -124, "south": 47, "east": -122, "north": 50},
            "sources": {
                "wa_public_access_points": _source("arcgis", "WA"),
                "bc_coastal_boat_launches": _source("arcgis", "BC"),
                "osm_slipways": _source("overpass", "cross_border"),
            },
            "parameters": {"h3_resolution": 7, "source_completeness": "partial"},
            "raw": {
                "snapshot_dir": str(raw),
                "source_availability_path": str(raw / "availability.json"),
                "manifest_path": str(raw / "manifest.json"),
            },
            "output": {
                "facilities_path": str(processed / "facilities.parquet"),
                "h3_path": str(processed / "h3.parquet"),
                "manifest_path": str(processed / "manifest.json"),
            },
            "inspection": {"report_path": str(tmp_path / "reports" / "boat.html")},
        },
    )

    frames = {
        "wa_public_access_points": gpd.GeoDataFrame(
            {
                "ECYBEACHID": ["wa-1", "wa-2"],
                "Beach_Name": ["Public ramp", "No launch"],
                "Boat_Launch": ["Yes", "No"],
                "Primary_Acccess_Type": ["Concrete boat ramp", "Beach trail"],
            },
            geometry=[Point(-122.50, 48.50), Point(-122.55, 48.52)],
            crs="EPSG:4326",
        ),
        "bc_coastal_boat_launches": gpd.GeoDataFrame(
            {"BOAT_LAUNCH_ID": ["bc-1"], "LOCATION": ["Legacy launch"]},
            geometry=[Point(-123.00, 49.00)],
            crs="EPSG:4326",
        ),
        "osm_slipways": gpd.GeoDataFrame(
            {
                "OSM_TYPE": ["node"],
                "OSM_ID": ["42"],
                "name": ["Mapped slipway"],
                "access": [None],
                "opening_hours": [None],
            },
            geometry=[Point(-122.51, 48.51)],
            crs="EPSG:4326",
        ),
    }
    monkeypatch.setattr(
        download_module,
        "_acquire",
        lambda source, _bbox, **_kwargs: frames[source.name].copy(),
    )

    download_module.download(config)
    with pytest.raises(ValueError, match="allow_partial=True"):
        build_module.build(config)
    h3_path = build_module.build(config, allow_partial=True)
    facilities = gpd.read_parquet(processed / "facilities.parquet")
    h3_output = pd.read_parquet(h3_path)
    assert set(facilities["SOURCE_DATASET"]) == {
        "wa_public_access_points",
        "bc_coastal_boat_launches",
        "osm_slipways",
    }
    assert len(facilities) == 3
    assert (
        facilities.loc[
            facilities["SOURCE_DATASET"].eq("bc_coastal_boat_launches"),
            "PUBLIC_ACCESS_STATE",
        ].item()
        == "unknown"
    )
    assert (
        facilities.loc[
            facilities["SOURCE_DATASET"].eq("osm_slipways"), "PUBLIC_ACCESS_STATE"
        ].item()
        == "unknown"
    )
    assert (
        facilities.loc[
            facilities["SOURCE_DATASET"].eq("wa_public_access_points"),
            "RAMP_CAPABILITY_STATE",
        ].item()
        == "ramp"
    )
    assert set(h3_output["H3_RESOLUTION"]) == {7}
    assert not h3_output["SOURCE_COVERAGE_COMPLETE"].any()
    assert int(h3_output["BOAT_LAUNCH_COUNT"].sum()) == len(facilities)
    reports = inspect_module.inspect(config)
    assert reports[0].is_file()


@pytest.mark.parametrize(
    ("source_value", "expected_capability"),
    [
        ("Both", "motorized_and_non_motorized"),
        ("Motorized", "motorized"),
        ("Non-motorized", "non_motorized"),
        ("Yes", "present_type_unknown"),
    ],
)
def test_wa_boat_launch_source_domain(source_value, expected_capability) -> None:
    build_module = importlib.import_module(
        "human.accessibility.boat_launch_access.build"
    )
    assert build_module._boolean_state(source_value) is True
    assert build_module._ramp_state(source_value) == expected_capability


def test_public_shore_keeps_unmapped_access_null(tmp_path, monkeypatch) -> None:
    download_module = importlib.import_module(
        "human.accessibility.public_shore_access.download"
    )
    build_module = importlib.import_module(
        "human.accessibility.public_shore_access.build"
    )
    inspect_module = importlib.import_module(
        "human.accessibility.public_shore_access.inspect"
    )
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    config = _write_yaml(
        tmp_path / "public_shore.yaml",
        {
            "schema_version": 1,
            "product": "public_shore_access",
            "category": "accessibility",
            "bbox": {"west": -124, "south": 47, "east": -122, "north": 50},
            "sources": {
                "wa_public_access_lines": _source("arcgis", "WA"),
                "wa_public_access_points": _source("arcgis", "WA"),
                "wa_marine_shoreline": _source("arcgis", "WA"),
                "osm_shore_access": _source("overpass", "cross_border"),
            },
            "parameters": {
                "h3_resolution": 7,
                "source_completeness": "partial",
                "length_crs": "EPSG:32610",
                "sample_spacing_m": 100,
            },
            "raw": {
                "snapshot_dir": str(raw),
                "source_availability_path": str(raw / "availability.json"),
                "manifest_path": str(raw / "manifest.json"),
            },
            "output": {
                "facilities_path": str(processed / "facilities.parquet"),
                "h3_path": str(processed / "h3.parquet"),
                "manifest_path": str(processed / "manifest.json"),
            },
            "inspection": {"report_path": str(tmp_path / "reports" / "shore.html")},
        },
    )
    frames = {
        "wa_public_access_lines": gpd.GeoDataFrame(
            {"ECYBEACHID": ["line-1"]},
            geometry=[LineString([(-122.55, 48.50), (-122.45, 48.50)])],
            crs="EPSG:4326",
        ),
        "wa_public_access_points": gpd.GeoDataFrame(
            {
                "ECYBEACHID": ["site-1"],
                "Beach_Name": ["Public shore"],
                "Primary_Acccess_Type": ["Trail"],
            },
            geometry=[Point(-122.50, 48.50)],
            crs="EPSG:4326",
        ),
        "wa_marine_shoreline": gpd.GeoDataFrame(
            {"OBJECTID": [1]},
            geometry=[LineString([(-122.70, 48.50), (-122.30, 48.50)])],
            crs="EPSG:4326",
        ),
        "osm_shore_access": gpd.GeoDataFrame(
            {
                "OSM_TYPE": ["way"],
                "OSM_ID": ["99"],
                "name": ["Community beach"],
                "access": [None],
                "natural": ["beach"],
            },
            geometry=[Point(-122.50, 48.5001)],
            crs="EPSG:4326",
        ),
    }
    monkeypatch.setattr(
        download_module,
        "_acquire",
        lambda source, _bbox, **_kwargs: frames[source.name].copy(),
    )

    download_module.download(config)
    with pytest.raises(ValueError, match="allow_partial=True"):
        build_module.build(config)
    h3_path = build_module.build(config, allow_partial=True)
    h3_output = pd.read_parquet(h3_path)
    facilities = gpd.read_parquet(processed / "facilities.parquet")
    assert set(h3_output["H3_RESOLUTION"]) == {7}
    assert h3_output["ACCESSIBLE_WATERFRONT_FRACTION"].dropna().between(0, 1).all()
    assert h3_output["ACCESSIBLE_WATERFRONT_FRACTION"].notna().any()
    total_only = h3_output[
        h3_output["TOTAL_MARINE_SHORELINE_M"].notna()
        & h3_output["PUBLIC_ACCESSIBLE_SHORELINE_M"].isna()
    ]
    assert not total_only.empty
    assert total_only["ACCESSIBLE_WATERFRONT_FRACTION"].isna().all()
    assert total_only["PUBLIC_ACCESS_STATE"].eq("unknown").all()
    assert (
        facilities.loc[
            facilities["SOURCE_DATASET"].eq("osm_shore_access"), "PUBLIC_ACCESS_STATE"
        ].item()
        == "unknown"
    )
    assert not h3_output["SOURCE_COVERAGE_COMPLETE"].any()
    reports = inspect_module.inspect(config)
    assert reports[0].is_file()


def test_public_shore_denominator_mismatch_is_null_not_clipped() -> None:
    build_module = importlib.import_module(
        "human.accessibility.public_shore_access.build"
    )
    facilities = gpd.GeoDataFrame(
        {
            "H3_INDEX": ["fixture-cell"],
            "PUBLIC_ACCESS_STATE": ["public"],
        },
        geometry=[Point(-122.5, 48.5)],
        crs="EPSG:4326",
    )
    output = build_module.aggregate_h3(
        facilities,
        {"fixture-cell": 100.0},
        {"fixture-cell": 125.0},
        source_complete=False,
    )
    row = output.iloc[0]
    assert pd.isna(row["ACCESSIBLE_WATERFRONT_FRACTION"])
    assert row["ACCESSIBLE_WATERFRONT_RAW_RATIO_QC"] == pytest.approx(1.25)
    assert bool(row["FRACTION_DENOMINATOR_MISMATCH_QC"])
    assert row["PUBLIC_ACCESS_STATE"] == "observed_access"


@pytest.mark.parametrize(
    ("access", "foot", "expected"),
    [
        (None, None, "unknown"),
        ("yes", None, "public"),
        ("private", None, "restricted"),
        ("private", "yes", "public"),
        ("yes", "private", "restricted"),
    ],
)
def test_osm_pedestrian_access_uses_explicit_foot_override(access, foot, expected) -> None:
    build_module = importlib.import_module(
        "human.accessibility.public_shore_access.build"
    )
    assert build_module.pedestrian_access_state(access, foot) == expected


def test_osm_conditional_access_is_not_promoted_to_public() -> None:
    build_module = importlib.import_module(
        "human.accessibility.public_shore_access.build"
    )
    assert (
        build_module.pedestrian_access_state("yes", None, "no @ (sunset-sunrise)", None)
        == "conditional"
    )


def test_bc_recreation_sites_require_shorezone_proximity_and_no_closure() -> None:
    build_module = importlib.import_module(
        "human.accessibility.public_shore_access.build"
    )
    wa = gpd.GeoDataFrame({"ECYBEACHID": []}, geometry=[], crs="EPSG:4326")
    osm = gpd.GeoDataFrame({"OSM_TYPE": [], "OSM_ID": []}, geometry=[], crs="EPSG:4326")
    bc = gpd.GeoDataFrame(
        {
            "FOREST_FILE_ID": ["near-open", "near-closed", "inland"],
            "PROJECT_NAME": ["Open coast", "Closed coast", "Inland"],
            "CLOSURE_DESCRIPTION": [None, "Seasonal closure", None],
        },
        geometry=[
            Point(-123.0001, 49.0),
            Point(-123.0002, 49.0),
            Point(-122.9, 49.0),
        ],
        crs="EPSG:4326",
    )
    shore = gpd.GeoDataFrame(
        {"OBJECTID": [1]},
        geometry=[LineString([(-123.0, 48.99), (-123.0, 49.01)])],
        crs="EPSG:4326",
    )
    result = build_module.normalize_facilities(
        wa,
        osm,
        h3_resolution=7,
        bc_recreation_sites=bc,
        bc_shorezone_lines=shore,
        bc_shore_connection_distance_m=500,
    )
    bc_result = result.loc[result["SOURCE_DATASET"].eq("bc_recreation_sites")]
    assert set(bc_result["SOURCE_RECORD_ID"]) == {"near-open", "near-closed"}
    assert (
        bc_result.set_index("SOURCE_RECORD_ID").loc["near-open", "PUBLIC_ACCESS_STATE"] == "unknown"
    )
    assert (
        bc_result.set_index("SOURCE_RECORD_ID").loc["near-closed", "PUBLIC_ACCESS_STATE"]
        == "restricted"
    )


def test_osm_shore_candidates_require_reference_shoreline_proximity() -> None:
    build_module = importlib.import_module(
        "human.accessibility.public_shore_access.build"
    )
    wa = gpd.GeoDataFrame({"ECYBEACHID": []}, geometry=[], crs="EPSG:4326")
    osm = gpd.GeoDataFrame(
        {
            "OSM_TYPE": ["node", "node"],
            "OSM_ID": ["coastal", "inland"],
            "tourism": ["viewpoint", "viewpoint"],
            "access": [None, "yes"],
        },
        geometry=[Point(-123.0001, 49.0), Point(-122.9, 49.0)],
        crs="EPSG:4326",
    )
    shore = gpd.GeoDataFrame(
        {"OBJECTID": [1]},
        geometry=[LineString([(-123.0, 48.99), (-123.0, 49.01)])],
        crs="EPSG:4326",
    )
    result = build_module.normalize_facilities(
        wa,
        osm,
        h3_resolution=7,
        reference_shoreline_lines=shore,
        osm_shore_connection_distance_m=500,
    )
    osm_result = result.loc[result["SOURCE_DATASET"].eq("osm_shore_access")]
    assert osm_result["SOURCE_RECORD_ID"].tolist() == ["node/coastal"]
    assert osm_result["SHORE_DISTANCE_M"].item() < 500


def test_optional_osm_outage_is_unavailable_not_zero(tmp_path, monkeypatch) -> None:
    download_module = importlib.import_module(
        "human.accessibility.boat_launch_access.download"
    )
    raw = tmp_path / "raw"
    osm_source = _source("overpass", "cross_border")
    osm_source["required"] = False
    config = _write_yaml(
        tmp_path / "boat_launch_outage.yaml",
        {
            "schema_version": 1,
            "product": "boat_launch_access",
            "category": "accessibility",
            "bbox": {"west": -124, "south": 47, "east": -122, "north": 50},
            "sources": {
                "wa_public_access_points": _source("arcgis", "WA"),
                "bc_coastal_boat_launches": _source("arcgis", "BC"),
                "osm_slipways": osm_source,
            },
            "parameters": {"h3_resolution": 7, "source_completeness": "partial"},
            "raw": {
                "snapshot_dir": str(raw),
                "source_availability_path": str(raw / "availability.json"),
                "manifest_path": str(raw / "manifest.json"),
            },
            "output": {
                "facilities_path": str(tmp_path / "processed" / "facilities.parquet"),
                "h3_path": str(tmp_path / "processed" / "h3.parquet"),
                "manifest_path": str(tmp_path / "processed" / "manifest.json"),
            },
            "inspection": {"report_path": str(tmp_path / "reports" / "boat.html")},
        },
    )
    frames = {
        "wa_public_access_points": gpd.GeoDataFrame(
            {"ECYBEACHID": ["wa-1"], "Boat_Launch": ["Yes"]},
            geometry=[Point(-122.5, 48.5)],
            crs="EPSG:4326",
        ),
        "bc_coastal_boat_launches": gpd.GeoDataFrame(
            {"BOAT_LAUNCH_ID": ["bc-1"]},
            geometry=[Point(-123.0, 49.0)],
            crs="EPSG:4326",
        ),
    }

    def fake_acquire(source, _bbox, **_kwargs):
        if source.name == "osm_slipways":
            raise RuntimeError("fixture gateway timeout")
        return frames[source.name].copy()

    monkeypatch.setattr(download_module, "_acquire", fake_acquire)
    manifest_path = download_module.download(config)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    osm_record = next(source for source in manifest["sources"] if source["name"] == "osm_slipways")
    availability = json.loads((raw / "availability.json").read_text(encoding="utf-8"))
    assert osm_record["measurement_status"] == "unavailable"
    assert "unavailable" in manifest["measurement_statuses"]
    assert availability["sources"]["osm_slipways"]["status"] == "unavailable"
    assert availability["sources"]["osm_slipways"]["records"] == 0
    assert "gateway timeout" in availability["sources"]["osm_slipways"]["error"]
    assert gpd.read_parquet(raw / "osm_slipways.parquet").empty
