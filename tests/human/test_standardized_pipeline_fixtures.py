from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import Point, box


def _write_yaml(path: Path, payload: dict) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _source(url: str, status: str = "observed") -> dict[str, str]:
    return {
        "type": "fixture",
        "url": url,
        "provider": "fixture provider",
        "license": "fixture license",
        "attribution": "fixture attribution",
        "measurement_status": status,
    }


def test_places_download_build_inspect_fixture(tmp_path, monkeypatch) -> None:
    download_module = importlib.import_module(
        "human.demography_and_presence.places.download"
    )
    build_module = importlib.import_module(
        "human.demography_and_presence.places.build"
    )
    inspect_module = importlib.import_module(
        "human.demography_and_presence.places.inspect"
    )
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    report = tmp_path / "reports" / "places.html"
    source_names = (
        "wsdot_ferry_terminals",
        "wa_state_parks",
        "ecology_marinas",
        "openstreetmap_overpass",
        "natural_earth_ocean",
        "natural_earth_marine_polys",
    )
    config = _write_yaml(
        tmp_path / "places.yaml",
        {
            "schema_version": 1,
            "product": "places",
            "category": "demography_and_presence",
            "bbox": {"west": -124.0, "south": 47.0, "east": -122.0, "north": 49.0},
            "parameters": {
                "distance_crs": "EPSG:3857",
                "destination_score_threshold": 0.0,
                "include_osm": False,
                "include_ferries": False,
                "include_marinas": False,
            },
            "raw": {
                "cache_dir": str(raw),
                "inventory_path": str(raw / "inventory.json"),
                "manifest_path": str(raw / "manifest.json"),
            },
            "output": {
                "catalog_path": str(processed / "places.json"),
                "manifest_path": str(processed / "manifest.json"),
            },
            "inspection": {"report_path": str(report)},
            "sources": {name: _source(f"https://fixture.invalid/{name}") for name in source_names},
        },
    )

    def fake_materialize(_source_path, destination, *, overwrite=False):
        del overwrite
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture archive")
        return path

    def fake_arcgis(source, **_kwargs):
        if source.name == "wa_state_parks":
            return gpd.GeoDataFrame(
                {"ParkName": ["Fixture State Park"], "ParkID": ["1"]},
                geometry=[Point(-123.0, 48.0)],
                crs="EPSG:4326",
            )
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    monkeypatch.setattr(download_module, "materialize_source", fake_materialize)
    monkeypatch.setattr(download_module, "arcgis_query_first_available_geojson", fake_arcgis)
    monkeypatch.setattr(
        download_module,
        "overpass_query_batches_first_available_json",
        lambda *_args, **_kwargs: {"elements": []},
    )
    monkeypatch.setattr(
        build_module,
        "build_nearshore_mask",
        lambda **_kwargs: box(-20_000_000, -20_000_000, 20_000_000, 20_000_000),
    )

    inventory = download_module.download(config)
    # Acquisition and nearshore build must share filenames for offline replay.
    assert (raw / "ne_10m_ocean.zip").exists()
    assert (raw / "ne_10m_geography_marine_polys.zip").exists()
    catalog = build_module.build(config)
    reports = inspect_module.inspect(config)

    assert inventory.exists()
    assert catalog.exists()
    assert reports == [report]
    assert "Fixture State Park" in catalog.read_text(encoding="utf-8")


def test_population_download_build_inspect_fixture(tmp_path, monkeypatch) -> None:
    download_module = importlib.import_module(
        "human.demography_and_presence.population.download"
    )
    build_module = importlib.import_module(
        "human.demography_and_presence.population.build"
    )
    inspect_module = importlib.import_module(
        "human.demography_and_presence.population.inspect"
    )
    base = yaml.safe_load(
        Path("config/data/human/demography_and_presence/population.yaml").read_text(
            encoding="utf-8"
        )
    )
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    base["raw"]["manifest_path"] = str(raw / "manifest.json")
    base["output"] = {
        "us_path": str(processed / "us.parquet"),
        "canada_path": str(processed / "canada.parquet"),
        "cross_border_path": str(processed / "cross_border.parquet"),
        "context_path": str(processed / "context.parquet"),
        "manifest_path": str(processed / "manifest.json"),
    }
    base["inspection"]["report_path"] = str(tmp_path / "reports" / "population.html")
    base["population"]["us"]["paths"].update(
        {
            "data_dir": str(tmp_path / "population"),
            "raw_dir": str(raw / "us"),
            "processed_dir": str(processed),
            "output_parquet": base["output"]["us_path"],
        }
    )
    base["population"]["us"]["runtime"]["debug_output_path"] = str(processed / "us.geo.parquet")
    base["population"]["canada"]["canada_paths"].update(
        {
            "raw_dir": str(raw / "canada"),
            "processed_dir": str(processed),
            "population_api_cache": str(raw / "canada" / "population.parquet"),
            "output_parquet": base["output"]["canada_path"],
        }
    )
    base["population"]["canada"]["runtime"]["debug_output_path"] = str(
        processed / "canada.geo.parquet"
    )
    config = _write_yaml(tmp_path / "population.yaml", base)

    def fake_us_boundaries(cfg, **_kwargs):
        cfg.paths.raw_dir.mkdir(parents=True, exist_ok=True)
        (cfg.paths.raw_dir / "states.fixture").write_text("states", encoding="utf-8")
        return pd.DataFrame({"state": ["WA"]})

    def fake_us_blocks(cfg, **_kwargs):
        (cfg.paths.raw_dir / "blocks.fixture").write_text("blocks", encoding="utf-8")
        return pd.DataFrame({"GEOID20": ["1"]})

    def fake_us_population(cfg, _blocks, **_kwargs):
        (cfg.paths.raw_dir / "population.fixture").write_text("1", encoding="utf-8")
        return pd.DataFrame({"GEOID20": ["1"], "population": [10]})

    def fake_canada_geography(cfg, **_kwargs):
        cfg.paths.raw_dir.mkdir(parents=True, exist_ok=True)
        (cfg.paths.raw_dir / "geography.fixture").write_text("geography", encoding="utf-8")
        return pd.DataFrame({"DAUID": ["1"]}), "DAUID"

    def fake_canada_population(cfg, _geographies, _join_column, **_kwargs):
        (cfg.paths.raw_dir / "population.fixture").write_text("10", encoding="utf-8")
        return pd.DataFrame({"DAUID": ["1"], "population": [10]})

    monkeypatch.setattr(download_module, "load_dotenv", lambda *_args: None)
    monkeypatch.setattr(download_module, "load_state_boundaries", fake_us_boundaries)
    monkeypatch.setattr(download_module, "load_state_blocks", fake_us_blocks)
    monkeypatch.setattr(download_module, "fetch_block_population", fake_us_population)
    monkeypatch.setattr(download_module, "load_bc_source_geography", fake_canada_geography)
    monkeypatch.setattr(download_module, "join_candidate_population", fake_canada_population)

    us_frame = pd.DataFrame(
        {
            "h3": ["872830828ffffff"],
            "h3_resolution": [7],
            "state_abbr": ["WA"],
            "population_2020": [10.0],
            "population_2020_round": [10],
            "population_density_2020_per_km2": [2.0],
        }
    )
    canada_frame = pd.DataFrame(
        {
            "h3": ["872830828ffffff"],
            "h3_resolution": [7],
            "province_abbr": ["BC"],
            "population_2021": [20.0],
            "population_2021_round": [20],
            "population_density_2021_per_km2": [4.0],
        }
    )

    build_runtime_flags = []

    def fake_us_build(cfg, **_kwargs):
        build_runtime_flags.append((cfg.runtime.overwrite_downloads, cfg.runtime.overwrite_output))
        cfg.paths.output_parquet.parent.mkdir(parents=True, exist_ok=True)
        us_frame.to_parquet(cfg.paths.output_parquet, index=False)
        return SimpleNamespace(output_path=cfg.paths.output_parquet)

    def fake_canada_build(cfg, **_kwargs):
        build_runtime_flags.append((cfg.runtime.overwrite_downloads, cfg.runtime.overwrite_output))
        cfg.paths.output_parquet.parent.mkdir(parents=True, exist_ok=True)
        canada_frame.to_parquet(cfg.paths.output_parquet, index=False)
        return SimpleNamespace(output_path=cfg.paths.output_parquet)

    monkeypatch.setattr(build_module, "run_us_population_pipeline", fake_us_build)
    monkeypatch.setattr(build_module, "run_canada_population_pipeline_result", fake_canada_build)

    raw_manifest = download_module.download(config)
    with pytest.raises(ValueError, match="partial"):
        build_module.build(config, allow_partial=True)
    cross_border = build_module.build(config)
    build_module.build(config, overwrite=True)
    reports = inspect_module.inspect(config)

    frame = pd.read_parquet(cross_border)
    context = pd.read_parquet(base["output"]["context_path"])
    assert raw_manifest.exists()
    assert build_runtime_flags[-2:] == [(False, True), (False, True)]
    assert reports[0].exists()
    assert len(frame) == 2
    assert not frame.duplicated(["COUNTRY_CODE", "H3_INDEX"]).any()
    assert frame["POPULATION"].sum() == 30.0
    assert len(context) == 1
    assert not context["H3_INDEX"].duplicated().any()
    assert context["POPULATION"].sum() == 30.0
    assert context.loc[0, "COUNTRY_CODES"] == "CA|US"
    assert bool(context.loc[0, "CENSUS_VINTAGE_MIXED_QC"])
    assert not bool(context.loc[0, "MARINE_TRANSFER_APPLIED"])


def test_ferry_download_build_inspect_fixture(tmp_path, monkeypatch) -> None:
    download_module = importlib.import_module(
        "human.activity_and_effort.ferry.download"
    )
    build_module = importlib.import_module("human.activity_and_effort.ferry.build")
    config_module = importlib.import_module(
        "human.activity_and_effort.ferry.config"
    )
    inspect_module = importlib.import_module(
        "human.activity_and_effort.ferry.inspect"
    )
    supplied = tmp_path / "supplied"
    supplied.mkdir()
    source_specs = {
        "wsf_ridership": ("wsf.parquet", "observed"),
        "bc_ridership": ("bc.parquet", "estimated"),
        "route_segments": ("routes.parquet", "derived"),
        "route_mapping": ("routes.yaml", "derived"),
        "wsf_vessel_history": ("vessels.parquet", "observed"),
    }
    sources = {}
    for name, (filename, status) in source_specs.items():
        path = supplied / filename
        if path.suffix == ".yaml":
            path.write_text("routes: {}\n", encoding="utf-8")
        else:
            pd.DataFrame({"fixture": [name]}).to_parquet(path, index=False)
        sources[name] = {
            "supplied_path": str(path),
            "provider": "fixture provider",
            "license": "fixture license",
            "attribution": "fixture attribution",
            "measurement_status": status,
        }
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    config = _write_yaml(
        tmp_path / "ferry.yaml",
        {
            "schema_version": 1,
            "product": "ferry",
            "category": "activity_and_effort",
            "source": {"source_completeness": "partial", **sources},
            "parameters": {"native_h3_resolution": 7, "model_h3_resolution": 6},
            "raw": {
                "snapshot_dir": str(raw),
                "manifest_path": str(raw / "manifest.json"),
            },
            "output": {
                "directory": str(processed),
                "route_daily_path": str(processed / "route_daily.parquet"),
                "daily_path": str(processed / "daily.parquet"),
                "weekly_path": str(processed / "weekly.parquet"),
                "manifest_path": str(processed / "manifest.json"),
            },
            "inspection": {"report_path": str(tmp_path / "reports" / "ferry.html")},
        },
    )

    route_daily = pd.DataFrame(
        {
            "service_date": ["2025-01-06"],
            "source_h3": ["872830828ffffff"],
            "route_geometry_id": ["fixture-route"],
            "operator": ["WSF"],
            "ferry_rider_minutes": [120.0],
            "ferry_rider_hours": [2.0],
            "ferry_rider_km": [24.0],
            "ferry_vessel_minutes": [60.0],
            "ferry_vessel_hours": [1.0],
            "ferry_vessel_km": [12.0],
            "ridership_is_estimated": [False],
            "platform_effort_available": [True],
            "voyage_duration_source": ["observed"],
        }
    )
    daily = route_daily.groupby(["service_date", "source_h3"], as_index=False)[
        [
            "ferry_rider_minutes",
            "ferry_rider_hours",
            "ferry_rider_km",
            "ferry_vessel_minutes",
            "ferry_vessel_hours",
            "ferry_vessel_km",
        ]
    ].sum()

    def fake_pipeline(**kwargs):
        output_dir = Path(kwargs["output_dir"])
        route_path = output_dir / "route.parquet"
        daily_path = output_dir / "daily.parquet"
        route_daily.to_parquet(route_path, index=False)
        daily.to_parquet(daily_path, index=False)
        return SimpleNamespace(route_level_path=route_path, collapsed_path=daily_path)

    monkeypatch.setattr(build_module, "build_ferry_daily_source_weights", fake_pipeline)

    raw_manifest = download_module.download(config)
    with pytest.raises(ValueError, match="partial"):
        build_module.build(config)
    daily_path = build_module.build(config, allow_partial=True)
    reports = inspect_module.inspect(config)

    assert raw_manifest.exists()
    assert daily_path.exists()
    assert reports[0].exists()
    built = config_module.load_ferry_config(config)
    assert built.weekly_path.parent.parent.name == "generations"
    assert pd.read_parquet(built.weekly_path)["ferry_rider_minutes"].sum() == 120.0
