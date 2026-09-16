"""Build land reporting-opportunity component and composite artifacts."""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import geopandas as gpd
import polars as pl

from human.activity_and_effort.observation_opportunity_contract import (
    SCHEMA_VERSION,
)
from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    load_manifest,
    manifest_payload,
    sha256_file,
)
from human.viewshed.config import load_app_config
from human.viewshed.finalize.final_artifacts import (
    static_scientific_config_hash,
    validate_static_artifact_metadata,
)

from .config import DEFAULT_CONFIG_PATH, load_land_reporting_config
from .dynamic import build_dynamic_products
from .generations import archive_legacy, new_generation, publish

STATIC_COLUMNS = {"source_h3", "target_h3", "weight_static_viewability"}


def build_source_components(
    source_cells: pl.DataFrame,
    transport: pl.DataFrame,
    population_travel: pl.DataFrame,
    public_shore: pl.DataFrame,
) -> pl.DataFrame:
    expected = set(source_cells.get_column("source_h3").to_list())
    for label, frame in (
        ("transport", transport),
        ("population travel", population_travel),
    ):
        observed = set(frame.get_column("H3_INDEX").to_list())
        if observed != expected:
            raise ValueError(
                f"Land {label} H3 universe differs from the static viewshed source universe: "
                f"missing={len(expected - observed)} extra={len(observed - expected)}"
            )
    shore = public_shore.select(
        "H3_INDEX",
        "PUBLIC_ACCESS_EVIDENCE_STATE",
        "VERIFIED_PUBLIC_ACCESS_SITE_COUNT",
        "OSM_EXPLICIT_PUBLIC_ACCESS_SITE_COUNT",
        "OSM_SHORE_CANDIDATE_COUNT",
        "SOURCE_COVERAGE_COMPLETE",
    ).with_columns(pl.lit(True).alias("PUBLIC_SHORE_CONTEXT_MAPPED"))
    source = (
        source_cells.join(
            transport.select(
                "H3_INDEX",
                "ROAD_PROXIMITY_COMPONENT",
                "CITY_TRAVEL_ACCESS_COMPONENT",
                "LAND_TRANSPORT_ACCESS_OPPORTUNITY_INDEX",
                "LAND_TRANSPORT_ACCESS_AVAILABLE",
                *(
                    ["CITY_EVALUATED_ORIGIN_FRACTION", "CITY_TRAVEL_CONTEXT_STATUS"]
                    if "CITY_EVALUATED_ORIGIN_FRACTION" in transport.columns
                    else []
                ),
            ),
            left_on="source_h3",
            right_on="H3_INDEX",
            how="left",
        )
        .join(
            population_travel.select(
                "H3_INDEX",
                "POPULATION_TRAVEL_OPPORTUNITY_INDEX",
                "POPULATION_TRAVEL_OPPORTUNITY_AVAILABLE",
                *(
                    ["POPULATION_TRAVEL_EVALUATED_SELECTED_POPULATION_FRACTION"]
                    if "POPULATION_TRAVEL_EVALUATED_SELECTED_POPULATION_FRACTION"
                    in population_travel.columns
                    else []
                ),
            ),
            left_on="source_h3",
            right_on="H3_INDEX",
            how="left",
        )
        .join(shore, left_on="source_h3", right_on="H3_INDEX", how="left")
        .with_columns(
            pl.col("PUBLIC_ACCESS_EVIDENCE_STATE").fill_null("unknown"),
            pl.col("PUBLIC_SHORE_CONTEXT_MAPPED").fill_null(False),
            pl.col("SOURCE_COVERAGE_COMPLETE").fill_null(False),
            (
                pl.col("ROAD_PROXIMITY_COMPONENT").is_not_null()
                & pl.col("POPULATION_TRAVEL_OPPORTUNITY_AVAILABLE").fill_null(False)
            ).alias("LAND_REACHABILITY_OPPORTUNITY_AVAILABLE"),
        )
        .with_columns(
            (pl.col("POPULATION_TRAVEL_OPPORTUNITY_INDEX") * pl.col("ROAD_PROXIMITY_COMPONENT"))
            .clip(0.0, 1.0)
            .alias("LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX")
        )
        .with_columns(
            pl.when(
                pl.col("LAND_REACHABILITY_OPPORTUNITY_AVAILABLE")
                & pl.col("PUBLIC_ACCESS_EVIDENCE_STATE").eq("verified_public")
            )
            .then(pl.col("LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX"))
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("VERIFIED_PUBLIC_ACCESS_SUPPORTED_INDEX"),
            pl.when(
                pl.col("LAND_REACHABILITY_OPPORTUNITY_AVAILABLE")
                & pl.col("PUBLIC_ACCESS_EVIDENCE_STATE").is_in(["verified_public", "mapped_public"])
            )
            .then(pl.col("LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX"))
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("MAPPED_PUBLIC_ACCESS_SUPPORTED_INDEX"),
            pl.when(pl.col("LAND_REACHABILITY_OPPORTUNITY_AVAILABLE"))
            .then(pl.lit(MeasurementStatus.DERIVED.value))
            .otherwise(pl.lit(MeasurementStatus.UNAVAILABLE.value))
            .alias("MEASUREMENT_STATUS"),
            pl.lit(7).cast(pl.Int8).alias("H3_RESOLUTION"),
        )
        .sort("source_h3")
    )
    if source.select(
        (
            pl.col("LAND_REACHABILITY_OPPORTUNITY_AVAILABLE")
            & pl.col("LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX").is_null()
        ).sum()
    ).item():
        raise ValueError("Available land transport/travel source components contain nulls.")
    return source


def build_target_components(
    static: pl.DataFrame,
    source: pl.DataFrame,
    *,
    scaling_quantile: float,
) -> tuple[pl.DataFrame, float]:
    weighted = static.join(source, on="source_h3", how="left").with_columns(
        (
            pl.col("weight_static_viewability") * pl.col("LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX")
        ).alias("_transport_travel_contribution"),
        (
            pl.col("weight_static_viewability") * pl.col("VERIFIED_PUBLIC_ACCESS_SUPPORTED_INDEX")
        ).alias("_verified_access_contribution"),
        (
            pl.col("weight_static_viewability") * pl.col("MAPPED_PUBLIC_ACCESS_SUPPORTED_INDEX")
        ).alias("_mapped_access_contribution"),
    )
    target = (
        weighted.group_by("target_h3")
        .agg(
            pl.col("weight_static_viewability").sum().alias("LAND_STATIC_WEIGHT_SUM"),
            pl.col("source_h3").n_unique().alias("LAND_SOURCE_COUNT"),
            pl.col("_transport_travel_contribution")
            .sum()
            .alias("LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_RAW"),
            pl.col("_transport_travel_contribution")
            .count()
            .alias("TRANSPORT_TRAVEL_AVAILABLE_SOURCE_COUNT"),
            pl.col("weight_static_viewability")
            .filter(pl.col("LAND_REACHABILITY_OPPORTUNITY_AVAILABLE"))
            .sum()
            .alias("TRANSPORT_TRAVEL_AVAILABLE_STATIC_WEIGHT"),
            pl.col("_verified_access_contribution")
            .sum()
            .alias("VERIFIED_PUBLIC_ACCESS_SUPPORTED_RAW"),
            pl.col("_verified_access_contribution")
            .count()
            .alias("VERIFIED_PUBLIC_ACCESS_SOURCE_COUNT"),
            pl.col("_mapped_access_contribution").sum().alias("MAPPED_PUBLIC_ACCESS_SUPPORTED_RAW"),
            pl.col("_mapped_access_contribution")
            .count()
            .alias("MAPPED_PUBLIC_ACCESS_SOURCE_COUNT"),
            pl.col("weight_static_viewability")
            .filter(pl.col("PUBLIC_SHORE_CONTEXT_MAPPED"))
            .sum()
            .alias("MAPPED_ACCESS_CONTEXT_STATIC_WEIGHT"),
        )
        .with_columns(
            (
                pl.col("TRANSPORT_TRAVEL_AVAILABLE_STATIC_WEIGHT")
                / pl.col("LAND_STATIC_WEIGHT_SUM")
            ).alias("TRANSPORT_TRAVEL_CONTEXT_COVERAGE"),
            (
                pl.col("MAPPED_ACCESS_CONTEXT_STATIC_WEIGHT") / pl.col("LAND_STATIC_WEIGHT_SUM")
            ).alias("MAPPED_ACCESS_CONTEXT_FRACTION"),
            pl.when(pl.col("TRANSPORT_TRAVEL_AVAILABLE_SOURCE_COUNT") > 0)
            .then(pl.col("LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_RAW"))
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_RAW"),
            pl.when(pl.col("VERIFIED_PUBLIC_ACCESS_SOURCE_COUNT") > 0)
            .then(pl.col("VERIFIED_PUBLIC_ACCESS_SUPPORTED_RAW"))
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("VERIFIED_PUBLIC_ACCESS_SUPPORTED_RAW"),
            pl.when(pl.col("MAPPED_PUBLIC_ACCESS_SOURCE_COUNT") > 0)
            .then(pl.col("MAPPED_PUBLIC_ACCESS_SUPPORTED_RAW"))
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("MAPPED_PUBLIC_ACCESS_SUPPORTED_RAW"),
        )
    )
    log_cap = target.select(
        pl.col("LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_RAW")
        .filter(pl.col("TRANSPORT_TRAVEL_AVAILABLE_SOURCE_COUNT") > 0)
        .log1p()
        .quantile(scaling_quantile, interpolation="linear")
    ).item()
    if log_cap is None or float(log_cap) == 0.0:
        # No positive reference support: preserve nulls and valid zeros.
        log_cap = 1.0
    if not math.isfinite(float(log_cap)) or float(log_cap) < 0:
        raise ValueError("Land transport/travel target scaling cap is invalid.")
    target = (
        target.with_columns(
            (pl.col("LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_RAW").log1p() / float(log_cap))
            .clip(0.0, 1.0)
            .alias("LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_INDEX"),
            pl.lit(7).cast(pl.Int8).alias("H3_RESOLUTION"),
            pl.when(pl.col("TRANSPORT_TRAVEL_AVAILABLE_SOURCE_COUNT") > 0)
            .then(pl.lit("research_land_reachability_component"))
            .otherwise(pl.lit("unavailable_transport_or_population_routing"))
            .alias("LAND_REPORTING_OPPORTUNITY_STATUS"),
            pl.when(pl.col("TRANSPORT_TRAVEL_AVAILABLE_SOURCE_COUNT") > 0)
            .then(pl.lit(MeasurementStatus.DERIVED.value))
            .otherwise(pl.lit(MeasurementStatus.UNAVAILABLE.value))
            .alias("MEASUREMENT_STATUS"),
        )
        .rename({"target_h3": "H3_INDEX"})
        .sort("H3_INDEX")
    )
    return target, float(log_cap)


def _write_parquet(frame: pl.DataFrame, path: Path, *, overwrite: bool) -> Path:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        frame.write_parquet(temporary, compression="zstd", statistics=True)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    allow_partial: bool = False,
    *,
    overwrite: bool = False,
) -> Path:
    cfg = load_land_reporting_config(config_path, resolve_generation=False)
    if cfg.manifest_path.exists() and not overwrite:
        raise FileExistsError(f"Land product already exists: {cfg.manifest_path}")
    raw_manifest = load_manifest(cfg.raw_manifest_path)
    if raw_manifest["config_hash"] != cfg.human.config_hash:
        raise ValueError("Land input inventory configuration is stale; rerun download")
    import io

    from human.utils.artifacts import validate_manifest

    from .inputs import (
        read_pinned_bytes,
        read_pinned_json,
        read_pinned_parquet,
        validate_pinned_inputs,
    )

    inventory_identity = validate_pinned_inputs(cfg)
    if cfg.source_completeness != "complete" and not allow_partial:
        raise ValueError(
            "Land reporting opportunity uses partial research inputs; rerun with allow_partial=True."
        )
    upstream = []
    for name in ("transport_manifest", "population_travel_manifest", "public_shore_manifest"):
        manifest = read_pinned_json(cfg, name)
        validate_manifest(manifest)
        upstream.append(manifest)
    policies = {item.get("routing_policy", "legacy_routing_unverified") for item in upstream[:2]}
    if len(policies) != 1:
        raise ValueError("Transport and population must use the same routing policy")
    routing_policy = policies.pop()
    graph_identities = {item.get("routing_graph_manifest_sha256") for item in upstream[:2]}
    if len(graph_identities) != 1 or (
        routing_policy != "legacy_routing_unverified" and None in graph_identities
    ):
        raise ValueError("Transport and population must use the same identified routing graph")
    app = load_app_config(cfg.viewshed_config_path)
    static_metadata = validate_static_artifact_metadata(
        cfg.static_weights_path,
        raw=app.raw_config,
        source_type="land",
    )
    artifact_scientific_hash = str(static_metadata.get("scientific_config_hash", "unknown"))
    current_scientific_hash = static_scientific_config_hash(app.raw_config)
    if artifact_scientific_hash != current_scientific_hash:
        raise ValueError("Land static viewshed scientific configuration is stale.")
    static = pl.from_pandas(read_pinned_parquet(cfg, "land_static_weights", exact=True))
    missing = sorted(STATIC_COLUMNS.difference(static.columns))
    if missing:
        raise ValueError(f"Land static viewshed artifact is missing columns: {missing}")
    source_cells = (
        pl.from_pandas(read_pinned_parquet(cfg, "land_transport_access", exact=True))
        .select(pl.col("H3_INDEX").alias("source_h3"))
        .unique()
        .sort("source_h3")
    )
    source = build_source_components(
        source_cells,
        pl.from_pandas(read_pinned_parquet(cfg, "land_transport_access", exact=True)),
        pl.from_pandas(read_pinned_parquet(cfg, "population_travel_time", exact=True)),
        pl.from_pandas(read_pinned_parquet(cfg, "public_shore_access", exact=True)),
    )
    target, log_cap = build_target_components(
        static.select("source_h3", "target_h3", "weight_static_viewability"),
        source,
        scaling_quantile=cfg.scaling_quantile,
    )
    archive_legacy(cfg.manifest_path)
    cfg = new_generation(cfg)
    # Resolve access geometry through the source manifest, never an all-land fallback.
    facility_artifacts = [
        item for item in upstream[2]["artifacts"] if item["dataset_id"].endswith(".facilities_r7")
    ]
    if len(facility_artifacts) != 1:
        raise ValueError(
            "Strict access-conditioned output unsupported: facilities_r7 geometry unavailable"
        )
    facility_artifact = facility_artifacts[0]
    if sha256_file(facility_artifact["path"]) != facility_artifact["sha256"]:
        raise ValueError("Public-access facilities checksum does not match its manifest")
    from .access_kernel import build_access_kernel
    from .sites import build_observation_sites, sample_observation_sites

    sites = build_observation_sites(
        gpd.read_parquet(io.BytesIO(read_pinned_bytes(cfg, "public_access_facilities")))
    )
    # Restrict only to the declared activity domain; retain inaccessible sites for inspection.
    sites = sites.loc[
        sites.source_h3.isna() | sites.source_h3.isin(source.get_column("source_h3").to_list())
    ].copy()
    if sites.empty or not sites.MAPPED_ELIGIBLE.any():
        raise ValueError(
            "Strict access-conditioned output unsupported: no represented public geometry"
        )
    samples = sample_observation_sites(
        sites,
        samples_per_site=cfg.samples_per_site,
        max_design_points=cfg.max_site_design_points,
        projected_crs=app.viewshed.crs_projected,
    )
    canopy_app = replace(app, viewshed=replace(app.viewshed, surface_model="canopy"))
    access_kernel, target_support = build_access_kernel(
        canopy_app,
        samples,
        distance_bin_km=cfg.dynamic_distance_bin_km,
    )
    preparation = {
        "observation_sites": sites,
        "observer_samples": samples,
        "access_kernel": access_kernel,
        "target_water_support": target_support,
    }
    for role, frame in preparation.items():
        frame.to_parquet(cfg.source_output_path.parent / f"{role}.parquet", index=False)
    _write_parquet(source, cfg.source_output_path, overwrite=False)
    _write_parquet(target, cfg.target_output_path, overwrite=overwrite)
    dynamic = build_dynamic_products(
        cfg,
        static.select(
            "source_h3",
            "target_h3",
            "weight_distance",
            "weight_static_viewability",
        ).to_pandas(),
        source.to_pandas(),
        overwrite=overwrite,
        routing_policy=routing_policy,
        access_kernel=access_kernel,
        target_support=target_support,
    )
    from .inspect import write_access_generation_report

    report_paths = write_access_generation_report(cfg, sites, samples, target_support)
    artifacts = (
        [
            artifact_record(
                path,
                dataset_id=f"human.activity_and_effort.land_reporting_opportunity.inspection_{path.suffix[1:]}",
            )
            for path in report_paths
        ]
        + [
            artifact_record(
                cfg.source_output_path.parent / f"{role}.parquet",
                dataset_id=f"human.activity_and_effort.land_reporting_opportunity.{role}",
            )
            for role in preparation
        ]
        + [
            artifact_record(
                cfg.source_output_path,
                dataset_id="human.activity_and_effort.land_reporting_opportunity.source_h3_r7",
                frame=source.to_pandas(),
                h3_resolution=7,
            ),
            artifact_record(
                cfg.target_output_path,
                dataset_id="human.activity_and_effort.land_reporting_opportunity.target_h3_r7",
                frame=target.to_pandas(),
                h3_resolution=7,
            ),
            artifact_record(
                dynamic.daily_path,
                dataset_id="human.activity_and_effort.land_reporting_opportunity.daily_h3_r6",
                h3_resolution=6,
            ),
            artifact_record(
                dynamic.weekly_path,
                dataset_id="human.activity_and_effort.land_reporting_opportunity.weekly_h3_r6",
                h3_resolution=6,
            ),
            artifact_record(
                dynamic.metadata_path,
                dataset_id="human.activity_and_effort.land_reporting_opportunity.dynamic_metadata",
            ),
        ]
    )
    supplement = cfg.daily_output_path.parent / "daylight_support_supplement.parquet"
    if supplement.exists():
        artifacts.append(
            artifact_record(
                supplement,
                dataset_id="human.activity_and_effort.land_reporting_opportunity.daylight_supplement",
                h3_resolution=4,
            )
        )
    payload = manifest_payload(
        config=cfg.human,
        stage="build",
        artifacts=artifacts,
        inputs=raw_manifest["inputs"],
        source_completeness=cfg.source_completeness,
        measurement_statuses=[MeasurementStatus.DERIVED, MeasurementStatus.UNAVAILABLE],
        attribution=raw_manifest["attribution"],
        licenses=raw_manifest["licenses"],
        h3_resolution=None,
        limitations=[
            "The transport/travel index is relative reporting opportunity, not observed effort or detection probability.",
            "The road-adjusted population travel formula and target q99 scaling are uncalibrated research choices.",
            "Verified and mapped-public access streams are scenarios, not calibrated uncertainty bounds; mapping completeness is unknown.",
            "The primary uses the population/travel times road source budget allocated only over represented access sites.",
            "All physical, population-travel, road, city, transport, reachability, mapped-access, and verified-access streams remain separately published.",
            f"Routing policy: {routing_policy}; road-only disconnections refer to the pinned graph, not real-world absence of roads.",
            f"Target log1p scaling cap: {log_cap}.",
        ],
    )
    import platform
    from dataclasses import asdict
    from importlib.metadata import version

    try:
        from osgeo import gdal

        gdal_version = gdal.VersionInfo()
    except ImportError:
        gdal_version = "CLI backend; Python binding version unavailable"

    payload["runtime_versions"] = {
        "python": platform.python_version(),
        "GDAL": gdal_version,
        **{
            name: version(name)
            for name in (
                "numpy",
                "pandas",
                "scipy",
                "rasterio",
                "h3",
                "shapely",
                "geopandas",
                "pyproj",
                "polars",
                "pyarrow",
            )
        },
    }
    payload["canopy_distance_parameters"] = {
        "requested_viewshed": asdict(app.viewshed),
        "effective_viewshed": asdict(canopy_app.viewshed),
        "distance": app.raw_config.get("distance_weight", {}),
    }
    payload["land_product_schema_version"] = "4.1.0-research"
    payload["static_reference_scope"] = static_metadata.get("coverage", {}).get(
        "reference", "legacy_all_land_reference"
    )
    payload["land_semantic_version"] = "source_cell_common_reference_v1"
    payload["software_content_hashes"] = {
        path.name: sha256_file(path) for path in sorted(Path(__file__).parent.glob("*.py"))
    }
    viewshed_root = Path(__file__).parents[2] / "viewshed"
    payload["software_content_hashes"].update(
        {
            f"viewshed/{path.relative_to(viewshed_root)}": sha256_file(path)
            for path in sorted(viewshed_root.rglob("*.py"))
        }
    )
    import shutil
    import subprocess

    executable = shutil.which("gdal_viewshed")
    payload["runtime_versions"]["gdal_viewshed_cli"] = (
        subprocess.run(
            [executable, "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
        if executable
        else "unavailable"
    )
    payload["kernel_evaluation_plan"] = target_support.attrs["evaluation_plan"]
    payload["actual_los_execution_modes"] = target_support.attrs["evaluation_plan"]["profile"][
        "execution_modes"
    ]
    payload["land_formula_version"] = cfg.composite_formula_version
    payload["access_conditioning"] = {
        "site_algorithm": "source_cell_local_destinations_v3",
        "sample_algorithm": "parent_budget_nested_samples_v1",
        "kernel_algorithm": "source_cell_radius_reference_kernel_v3",
        "site_allocation": cfg.site_allocation,
        "samples_per_site": cfg.samples_per_site,
        "max_design_points": cfg.max_site_design_points,
        "activity_budget": "population/travel opportunity times road proximity; relative scenario budget",
        "facility_input": facility_artifact,
        "geometry": "represented source geometry; point-only facilities remain points; supplied line geometry requires explicit upstream source evidence",
        "geometry_type_counts": sites.GEOMETRY_TYPE.value_counts().to_dict(),
        "cross_cell_parent_allocation": "source-cell budget divided among eligible local destinations; global geometry shares are descriptive only",
        "sample_weights": "within-parent clipped polygon Voronoi area, line Voronoi length, or equal represented points",
        "mapping_completeness": "unknown",
        "water_support": "canonical water-mask pixels assigned by center to R7 and logical R6 parent",
        "timezone": "Source-local calendar days; Monday-start weeks; see calendar_timezone",
        "broad_reachability": "alternate general-support scenario, not access-confirmed observation",
        "canopy_observer_algorithm": "observer_isolated_canopy_surface_v2",
    }
    payload["routing_policy"] = routing_policy
    dynamic_metadata = json.loads(dynamic.metadata_path.read_text(encoding="utf-8"))
    payload["calendar_timezone"] = dynamic_metadata["spatial_support"]["calendar_timezone"]
    payload["schema_version"] = SCHEMA_VERSION
    payload["model_eligible"] = False
    payload["composite_contract"] = dynamic_metadata["formula"]
    payload["component_provenance"] = dynamic_metadata["component_provenance"]
    payload["deprecated_compatibility_aliases"] = dynamic_metadata[
        "deprecated_compatibility_aliases"
    ]
    payload["routing_graph_manifest_sha256"] = upstream[0].get("routing_graph_manifest_sha256")
    payload["partial_evaluated_population_lower_bound"] = upstream[1].get(
        "partial_evaluated_population_lower_bound", False
    )
    validate_pinned_inputs(cfg, expected_identity=inventory_identity)
    payload["consumed_input_inventory_sha256"] = inventory_identity
    publish(cfg, payload)
    return cfg.target_output_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    print(build(args.config, allow_partial=args.allow_partial, overwrite=args.overwrite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
