"""Inspect land reporting-opportunity component and composite coverage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import polars as pl

from human.activity_and_effort.observation_opportunity_contract import (
    validate_product_contract,
)
from human.utils.artifacts import load_manifest
from human.utils.inspection import write_html_report

from .config import DEFAULT_CONFIG_PATH, load_land_reporting_config
from .dynamic import STREAM_COMPONENTS


def _dynamic_summary(path: Path, *, period_column: str) -> dict[str, Any]:
    scan = pl.scan_parquet(path)
    schema = scan.collect_schema()
    required = {
        period_column,
        "H3_INDEX",
        "LAND_EFFORT_PROXY_RAW",
        "LAND_EFFORT_PROXY_INDEX",
        "LAND_EFFORT_PROXY_STATUS",
        "LAND_OBSERVATION_OPPORTUNITY_RAW",
        "LAND_OBSERVATION_OPPORTUNITY_INDEX",
        "COMPONENT_PROVENANCE_JSON",
        *(f"{stream}_INDEX" for stream, _component in STREAM_COMPONENTS),
    }
    missing = sorted(required.difference(schema.names()))
    if missing:
        raise ValueError(f"Dynamic artifact {path} is missing columns: {missing}")
    sample = scan.head(100_000).collect(engine="streaming")
    validate_product_contract(
        sample,
        "land_daily_r6" if period_column == "DATE" else "land_weekly_r6",
    )
    alias_mismatch = sample.filter(
        (pl.col("LAND_EFFORT_PROXY_RAW") != pl.col("LAND_OBSERVATION_OPPORTUNITY_RAW"))
        | (
            pl.col("VERIFIED_LAND_EFFORT_PROXY_RAW")
            != pl.col("VERIFIED_LAND_OBSERVATION_OPPORTUNITY_RAW")
        )
    ).height
    if alias_mismatch:
        raise ValueError(f"Dynamic artifact {path} changed deprecated compatibility values.")
    metrics = scan.select(
        pl.len().alias("rows"),
        pl.col(period_column).min().alias("start"),
        pl.col(period_column).max().alias("end"),
        pl.col(period_column).n_unique().alias("periods"),
        pl.col("H3_INDEX").n_unique().alias("target_cells"),
        pl.struct(period_column, "H3_INDEX").n_unique().alias("unique_keys"),
        pl.col("LAND_EFFORT_PROXY_RAW").null_count().alias("primary_null_rows"),
        pl.col("LAND_EFFORT_PROXY_INDEX").min().alias("primary_index_minimum"),
        pl.col("LAND_EFFORT_PROXY_INDEX").max().alias("primary_index_maximum"),
    ).collect()
    result = metrics.row(0, named=True)
    for name in ("start", "end"):
        result[name] = result[name].isoformat()
    result["duplicate_keys"] = int(result["rows"] - result.pop("unique_keys"))
    result["status_counts"] = dict(
        scan.group_by("LAND_EFFORT_PROXY_STATUS").len().collect().iter_rows()
    )
    result["published_streams"] = [stream for stream, _component in STREAM_COMPONENTS]
    return result


def inspect(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    output_dir: str | Path | None = None,
) -> list[Path]:
    cfg = load_land_reporting_config(config_path)
    manifest = load_manifest(cfg.manifest_path)
    metadata = json.loads(cfg.dynamic_metadata_path.read_text(encoding="utf-8"))
    source = pd.read_parquet(cfg.source_output_path)
    target = pd.read_parquet(cfg.target_output_path)
    summary = {
        "source_rows": len(source),
        "target_rows": len(target),
        "source_completeness": manifest["source_completeness"],
        "primary_observation_opportunity": metadata["primary_observation_opportunity"],
        "land_reachability_available_sources": int(
            source["LAND_REACHABILITY_OPPORTUNITY_AVAILABLE"].sum()
        ),
        "verified_public_access_sources": int(
            source["VERIFIED_PUBLIC_ACCESS_SUPPORTED_INDEX"].notna().sum()
        ),
        "mapped_public_access_sources": int(
            source["MAPPED_PUBLIC_ACCESS_SUPPORTED_INDEX"].notna().sum()
        ),
        "access_evidence_states": source["PUBLIC_ACCESS_EVIDENCE_STATE"]
        .value_counts(dropna=False)
        .to_dict(),
        "target_reachability_index_minimum": target[
            "LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_INDEX"
        ].min(),
        "target_reachability_index_maximum": target[
            "LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_INDEX"
        ].max(),
        "duplicate_source_h3": int(source["source_h3"].duplicated().sum()),
        "duplicate_target_h3": int(target["H3_INDEX"].duplicated().sum()),
        "daily": _dynamic_summary(cfg.daily_output_path, period_column="DATE"),
        "weekly": _dynamic_summary(cfg.weekly_output_path, period_column="WEEK_START"),
    }
    summary["access_conditioning"] = manifest.get("access_conditioning", "legacy source support")
    access_reports = [
        Path(item["path"])
        for item in manifest["artifacts"]
        if item["dataset_id"].endswith(".inspection_html")
    ]
    report = Path(output_dir) / cfg.report_path.name if output_dir else cfg.report_path
    return access_reports + [
        write_html_report(
            report,
            title="Land reporting-opportunity component and composite inspection",
            summary=summary,
            frame=target,
        )
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir")
    args = parser.parse_args(argv)
    for path in inspect(args.config, args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def write_access_generation_report(cfg, sites, samples, support) -> list[Path]:
    """Publish a local, self-contained view of geometry, values, and missingness."""
    import geopandas as gpd
    import matplotlib.pyplot as plt
    import polars as pl

    from human.core.geo.h3 import cell_to_polygon

    scan = pl.scan_parquet(cfg.daily_output_path)
    coverage_columns = [c for c in scan.collect_schema().names() if c.endswith("_COVERAGE")]
    columns = [
        "H3_INDEX",
        "DATE",
        "LAND_OBSERVATION_OPPORTUNITY_RAW",
        "LAND_OBSERVATION_OPPORTUNITY_STATE",
        *coverage_columns,
    ]
    daily = scan.select(columns).head(100).collect(engine="streaming").to_pandas()
    means = (
        scan.group_by("H3_INDEX")
        .agg(pl.col("LAND_OBSERVATION_OPPORTUNITY_RAW").mean())
        .collect(engine="streaming")
        .to_pandas()
    )
    states = scan.group_by("LAND_OBSERVATION_OPPORTUNITY_STATE").len().collect(engine="streaming")
    coverages = (
        scan.select(pl.col(coverage_columns).mean()).collect(engine="streaming").to_dicts()[0]
    )
    null_rows = (
        scan.select(pl.col("LAND_OBSERVATION_OPPORTUNITY_RAW").is_null().sum())
        .collect(engine="streaming")
        .item()
    )
    water = gpd.GeoDataFrame(means, geometry=[cell_to_polygon(c) for c in means.H3_INDEX], crs=4326)
    image_path = cfg.source_output_path.parent / "access_placement.png"
    figure, axes = plt.subplots(1, 2, figsize=(13, 5))
    water.plot(
        column="LAND_OBSERVATION_OPPORTUNITY_RAW",
        ax=axes[0],
        legend=True,
        missing_kwds={"color": "lightgray"},
        cmap="viridis",
    )
    axes[0].set_title("Mean available daily raw opportunity")
    water.boundary.plot(ax=axes[1], color="lightgray", linewidth=0.6)
    sites.loc[sites.MAPPED_ELIGIBLE].plot(
        ax=axes[1], color="green", markersize=55, label="Mapped public sites"
    )
    if (~sites.MAPPED_ELIGIBLE).any():
        sites.loc[~sites.MAPPED_ELIGIBLE & sites.geometry.notna()].plot(
            ax=axes[1], color="red", markersize=55, label="Unsupported / restricted"
        )
    samples.plot(ax=axes[1], color="black", markersize=12, label="Actual observer samples")
    axes[1].set_title("Represented access and sample placement")
    handles, labels = axes[1].get_legend_handles_labels()
    unique_handles = dict(zip(labels, handles))
    axes[1].legend(unique_handles.values(), unique_handles.keys(), loc="upper right", fontsize=8)
    first_parent = samples.PARENT_SITE_ID.iloc[0]
    detail = samples.loc[samples.PARENT_SITE_ID == first_parent].to_crs(32610)
    inset = axes[1].inset_axes([0.04, 0.62, 0.44, 0.30])
    sites.loc[sites.PARENT_SITE_ID == first_parent].to_crs(32610).plot(
        ax=inset, color="green", linewidth=2
    )
    detail.plot(ax=inset, color="black", markersize=18)
    xmin, ymin, xmax, ymax = detail.total_bounds
    padding = max(30.0, (xmax - xmin) * 0.6, (ymax - ymin) * 0.6)
    inset.set_xlim(xmin - padding, xmax + padding)
    inset.set_ylim(ymin - padding, ymax + padding)
    inset.set_xticks([])
    inset.set_yticks([])
    inset.set_title("First site: actual sample detail", fontsize=8)
    for axis in axes:
        axis.set_xlabel("Longitude")
        axis.set_ylabel("Latitude")
    figure.tight_layout()
    figure.savefig(image_path, dpi=140)
    plt.close(figure)
    summary = {
        "sites": len(sites),
        "samples": len(samples),
        "target_children": len(support),
        "access_classes": sites.ACCESS_EVIDENCE_TIER.value_counts().to_dict(),
        "geometry_states": sites.GEOMETRY_STATE.value_counts().to_dict(),
        "daily_states": dict(states.iter_rows()),
        "condition_coverage": coverages,
        "raw_null_rows": int(null_rows),
        "assumptions": [
            "Relative source-cell population/travel times road budget; equal parent-site allocation.",
            "Site and sample allocations are scenario assumptions, not measured visitation.",
            "Mapped and verified scenarios are not calibrated uncertainty bounds.",
            "Water-area average uses canonical modeled R7 water pixels and logical R6 parents.",
            "Distance attenuation is integrated once; daily atmosphere is applied within distance bins.",
            "Known-site geometric evaluation does not establish real-world mapping completeness.",
            "Daily dates and Monday-start weeks use UTC; no hourly viewing precision is inferred.",
            "General-land static visibility and broad reachability remain alternate diagnostics.",
        ],
    }
    report = write_html_report(
        cfg.source_output_path.parent / "inspection.html",
        title="Access-conditioned land observation opportunity",
        summary=summary,
        frame=daily.head(100),
    )
    content = report.read_text()
    content = content.replace(
        "</body>",
        '<h2>Placement and raw opportunity</h2><img alt="Access sites and raw opportunity" src="access_placement.png" style="max-width:100%">'
        + "<h2>Represented sites</h2>"
        + sites.drop(columns="geometry").to_html(index=False)
        + "<h2>Observer samples</h2>"
        + samples.assign(longitude=samples.geometry.x, latitude=samples.geometry.y)
        .drop(columns="geometry")
        .head(200)
        .to_html(index=False)
        + "</body>",
    )
    report.write_text(content)
    return [report, image_path]
