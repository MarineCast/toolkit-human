#!/usr/bin/env python3
"""Create an interactive map of daily ferry rider-minutes and route lines."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

import folium
import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from branca.colormap import linear
from folium.features import GeoJsonPopup, GeoJsonTooltip
from shapely import line_merge, unary_union
from shapely.geometry import Polygon

from human.activity_and_effort.ferry.pipeline import (
    RouteMapping,
    load_route_config,
)


def _h3_polygon(cell: str) -> Polygon:
    return Polygon([(longitude, latitude) for latitude, longitude in h3.cell_to_boundary(cell)])


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    frame.to_parquet(temporary, compression="zstd", index=False)
    os.replace(temporary, path)


def _active_route_lines(
    route_day: pd.DataFrame,
    route_segments: gpd.GeoDataFrame,
    mappings: Sequence[RouteMapping],
) -> gpd.GeoDataFrame:
    active_keys = set(route_day["route_key"].astype(str))
    active_mappings = [
        mapping for mapping in mappings if mapping.ridership_route_key in active_keys
    ]
    daily_route_totals = route_day.drop_duplicates(
        ["service_date", "jurisdiction", "route_key"]
    ).set_index("route_key")
    rows = []
    for geometry_id in sorted({mapping.route_geometry_id for mapping in active_mappings}):
        aliases = [
            mapping for mapping in active_mappings if mapping.route_geometry_id == geometry_id
        ]
        representative = aliases[0]
        selected = route_segments[
            route_segments["segment_id"].isin(representative.segment_ids)
        ].to_crs(4326)
        geometry = line_merge(unary_union(selected.geometry.array))
        route_keys = [mapping.ridership_route_key for mapping in aliases]
        selected_totals = daily_route_totals.loc[daily_route_totals.index.intersection(route_keys)]
        daily_riders = float(selected_totals["daily_riders"].sum(min_count=1))
        daily_sailings = selected_totals["daily_sailings"].sum(min_count=1)
        rider_duration = np.average(
            selected_totals["route_duration_minutes"],
            weights=selected_totals["daily_riders"],
        )
        platform_duration = np.average(
            selected_totals["platform_route_duration_minutes"],
            weights=selected_totals["daily_sailings"],
        )
        history_matches = selected_totals["history_matched_sailings"].sum(min_count=1)
        duration_sailings = selected_totals["duration_adjustment_sailings"].sum(min_count=1)
        history_coverage = (
            float(history_matches / duration_sailings)
            if pd.notna(duration_sailings) and duration_sailings > 0
            else np.nan
        )
        duration_sources = ", ".join(
            sorted(set(selected_totals["voyage_duration_source"].dropna().astype(str)))
        )
        assumption_notes = sorted(
            {
                mapping.traversal_assumption_note
                for mapping in aliases
                if mapping.traversal_assumption_note
            }
        )
        rows.append(
            {
                "route_geometry_id": geometry_id,
                "route_name": representative.route_name,
                "route_keys": ", ".join(route_keys),
                "rider_weighted_duration_minutes": float(rider_duration),
                "sailing_weighted_duration_minutes": float(platform_duration),
                "configured_duration_minutes": representative.route_duration_minutes,
                "duration_source": duration_sources,
                "history_matched_sailings": float(history_matches),
                "history_coverage_percent": 100.0 * history_coverage,
                "daily_riders": daily_riders,
                "daily_sailings": (float(daily_sailings) if pd.notna(daily_sailings) else np.nan),
                "full_route_assumption": any(
                    mapping.full_route_traversal_assumption for mapping in aliases
                ),
                "assumption": " | ".join(assumption_notes) or "Adjacent-leg traversal",
                "geometry": geometry,
            }
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)


def build_date_map(
    *,
    route_level_path: str | Path,
    route_segments_path: str | Path,
    route_config_path: str | Path,
    service_date: str | pd.Timestamp,
    output_html: str | Path,
) -> tuple[Path, Path, Path]:
    selected_date = pd.Timestamp(service_date).normalize()
    route_level = pd.read_parquet(route_level_path)
    route_level["service_date"] = pd.to_datetime(route_level["service_date"]).dt.normalize()
    route_day = route_level.loc[route_level["service_date"].eq(selected_date)].copy()
    if route_day.empty:
        minimum = route_level["service_date"].min().date()
        maximum = route_level["service_date"].max().date()
        raise ValueError(
            f"No ferry source weights for {selected_date.date()}; coverage is "
            f"{minimum} through {maximum}"
        )

    route_cell_names = (
        route_day.groupby("source_h3")["route_name"]
        .agg(lambda values: ", ".join(sorted(set(values.astype(str)))))
        .rename("routes")
    )
    cells = (
        route_day.groupby("source_h3", as_index=False)
        .agg(
            ferry_rider_minutes=("ferry_rider_minutes", "sum"),
            ferry_rider_hours=("ferry_rider_hours", "sum"),
            ferry_vessel_minutes=("ferry_vessel_minutes", lambda values: values.sum(min_count=1)),
            ferry_route_count=("route_geometry_id", "nunique"),
        )
        .merge(route_cell_names, on="source_h3", validate="one_to_one")
    )
    cells["service_date"] = selected_date.date().isoformat()
    cell_gdf = gpd.GeoDataFrame(
        cells,
        geometry=[_h3_polygon(cell) for cell in cells["source_h3"]],
        crs=4326,
    )

    mappings, _ = load_route_config(route_config_path)
    route_segments = gpd.read_parquet(route_segments_path)
    route_lines = _active_route_lines(route_day, route_segments, mappings)
    if route_lines.empty:
        raise ValueError(f"No configured route lines for {selected_date.date()}")

    bounds = np.vstack([cell_gdf.total_bounds, route_lines.total_bounds])
    west, south = bounds[:, [0, 1]].min(axis=0)
    east, north = bounds[:, [2, 3]].max(axis=0)
    center = [(south + north) / 2, (west + east) / 2]
    ferry_map = folium.Map(location=center, tiles="CartoDB positron", zoom_start=7)

    minimum = float(cell_gdf["ferry_rider_minutes"].min())
    maximum = float(cell_gdf["ferry_rider_minutes"].max())
    color_scale = linear.YlOrRd_09.scale(minimum, maximum)
    color_scale.caption = f"Total ferry rider-minutes on {selected_date:%B %d, %Y}"
    color_scale.add_to(ferry_map)

    cell_layer = folium.FeatureGroup(name="H3 rider-minutes", show=True)
    folium.GeoJson(
        json.loads(cell_gdf.to_json()),
        style_function=lambda feature: {
            "fillColor": color_scale(feature["properties"]["ferry_rider_minutes"]),
            "color": "#4a4a4a",
            "weight": 0.7,
            "fillOpacity": 0.72,
        },
        highlight_function=lambda _: {"weight": 2.5, "color": "#111111", "fillOpacity": 0.88},
        tooltip=GeoJsonTooltip(
            fields=[
                "source_h3",
                "ferry_rider_minutes",
                "ferry_rider_hours",
                "ferry_vessel_minutes",
                "ferry_route_count",
                "routes",
            ],
            aliases=[
                "H3 cell",
                "Total rider-minutes",
                "Total rider-hours",
                "Vessel-minutes",
                "Routes in cell",
                "Route names",
            ],
            localize=True,
            sticky=True,
        ),
    ).add_to(cell_layer)
    cell_layer.add_to(ferry_map)

    route_layer = folium.FeatureGroup(name="Ferry route centerlines", show=True)
    folium.GeoJson(
        json.loads(route_lines.to_json()),
        style_function=lambda _: {"color": "#145da0", "weight": 4, "opacity": 0.92},
        highlight_function=lambda _: {"color": "#001f3f", "weight": 7, "opacity": 1.0},
        tooltip=GeoJsonTooltip(
            fields=[
                "route_name",
                "route_keys",
                "rider_weighted_duration_minutes",
                "sailing_weighted_duration_minutes",
                "duration_source",
                "history_matched_sailings",
                "history_coverage_percent",
                "daily_riders",
                "daily_sailings",
                "full_route_assumption",
            ],
            aliases=[
                "Route",
                "Source route keys",
                "Rider-weighted duration (minutes)",
                "Sailing-weighted duration (minutes)",
                "Duration source",
                "WSDOT history matches",
                "History coverage (%)",
                "Daily riders",
                "Daily sailings",
                "Full-route assumption",
            ],
            localize=True,
            sticky=True,
        ),
        popup=GeoJsonPopup(fields=["route_name", "assumption"], aliases=["Route", "Assumption"]),
    ).add_to(route_layer)
    route_layer.add_to(ferry_map)

    route_day_unique = route_day.drop_duplicates(["service_date", "jurisdiction", "route_key"])
    total_riders = route_day_unique["daily_riders"].sum(min_count=1)
    total_rider_minutes = cells["ferry_rider_minutes"].sum(min_count=1)
    title = f"""
    <div style="position: fixed; top: 12px; left: 50px; z-index: 9999;
                background: rgba(255,255,255,.94); padding: 10px 14px;
                border: 1px solid #777; border-radius: 4px; font: 14px sans-serif;">
      <b>Daily ferry rider-minutes — {selected_date:%B %d, %Y}</b><br>
      {len(cell_gdf):,} H3 cells · {len(route_lines):,} route geometries ·
      {total_riders:,.0f} route riders · {total_rider_minutes:,.0f} rider-minutes<br>
      <span style="font-size: 12px;">BC contributes no records because its current artifact begins in 2026.</span>
    </div>
    """
    ferry_map.get_root().html.add_child(folium.Element(title))
    folium.LayerControl(collapsed=False).add_to(ferry_map)
    ferry_map.fit_bounds([[south, west], [north, east]], padding=(20, 20))

    output_path = Path(output_html)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.part")
    ferry_map.save(temporary)
    os.replace(temporary, output_path)
    cell_path = output_path.with_suffix(".cells.parquet")
    route_path = output_path.with_suffix(".routes.geojson")
    _atomic_parquet(cell_gdf, cell_path)
    route_lines.to_file(route_path, driver="GeoJSON")
    return output_path, cell_path, route_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route-level", required=True, type=Path)
    parser.add_argument("--route-segments", required=True, type=Path)
    parser.add_argument("--route-config", required=True, type=Path)
    parser.add_argument("--service-date", required=True)
    parser.add_argument("--output-html", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    html_path, cell_path, route_path = build_date_map(
        route_level_path=args.route_level,
        route_segments_path=args.route_segments,
        route_config_path=args.route_config,
        service_date=args.service_date,
        output_html=args.output_html,
    )
    print(f"Saved map: {html_path}")
    print(f"Saved date-cell subset: {cell_path}")
    print(f"Saved active route lines: {route_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
