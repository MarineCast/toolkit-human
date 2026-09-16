"""Build bounded road and city-travel accessibility challenger components."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_parquet,
    load_manifest,
    manifest_payload,
    write_manifest,
)

from .config import DEFAULT_CONFIG_PATH, load_land_transport_config


def build_components(
    frame: pd.DataFrame,
    *,
    road_distance_decay_km: float,
    city_travel_time_decay_minutes: float,
) -> pd.DataFrame:
    output = frame.copy()
    output = output.rename(columns={"source_h3": "H3_INDEX"})
    if output["H3_INDEX"].isna().any() or output["H3_INDEX"].duplicated().any():
        raise ValueError("Land-transport H3_INDEX must be non-null and unique.")
    road_available = (
        output["ROAD_ROUTING_AVAILABLE"].eq(True)
        & output["ROAD_SNAP_DISTANCE_FROM_SOURCE_CENTROID_M"].notna()
    )
    city_available = (
        output["CITY_TRAVEL_ROUTING_AVAILABLE"].eq(True)
        & output["MIN_CITY_TRAVEL_TIME_MIN"].notna()
    )
    city_disconnected = output.get(
        "CITY_KNOWN_DISCONNECTED", pd.Series(False, index=output.index)
    ).eq(True)
    city_available = city_available | city_disconnected
    complete = road_available & city_available
    output.insert(1, "H3_RESOLUTION", 7)
    output["ROAD_PROXIMITY_COMPONENT"] = pd.Series(
        np.exp(
            -output["ROAD_SNAP_DISTANCE_FROM_SOURCE_CENTROID_M"].astype(float)
            / (road_distance_decay_km * 1000.0)
        ),
        index=output.index,
    ).where(road_available)
    output["CITY_TRAVEL_ACCESS_COMPONENT"] = pd.Series(
        np.exp(-output["MIN_CITY_TRAVEL_TIME_MIN"].astype(float) / city_travel_time_decay_minutes),
        index=output.index,
    ).where(city_available)
    output.loc[city_disconnected, "CITY_TRAVEL_ACCESS_COMPONENT"] = 0.0
    output["LAND_TRANSPORT_ACCESS_OPPORTUNITY_INDEX"] = (
        output["ROAD_PROXIMITY_COMPONENT"] * output["CITY_TRAVEL_ACCESS_COMPONENT"]
    ).where(complete)
    output["LAND_TRANSPORT_ACCESS_AVAILABLE"] = complete
    output["LAND_TRANSPORT_ACCESS_STATUS"] = np.where(
        complete, "complete_prototype_routing", "routing_unavailable"
    )
    if "CITY_EVALUATED_ORIGIN_FRACTION" in output:
        evaluated = output["CITY_EVALUATED_ORIGIN_FRACTION"].astype(float)
        if not evaluated.dropna().between(0, 1).all():
            raise ValueError("City routing coverage must be in [0, 1]")
        output.loc[complete & evaluated.eq(1), "LAND_TRANSPORT_ACCESS_STATUS"] = (
            "complete_evaluated_road_only_routing"
        )
        output.loc[complete & evaluated.lt(1), "LAND_TRANSPORT_ACCESS_STATUS"] = (
            "partial_evaluated_city_lower_bound"
        )
    output["MEASUREMENT_STATUS"] = np.where(
        complete, MeasurementStatus.DERIVED.value, MeasurementStatus.UNAVAILABLE.value
    )
    return output.sort_values("H3_INDEX").reset_index(drop=True)


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    allow_partial: bool = False,
    *,
    overwrite: bool = False,
) -> Path:
    cfg = load_land_transport_config(config_path)
    if cfg.source_completeness != "complete" and not allow_partial:
        raise ValueError(
            "Land-transport routing is partial research evidence; rerun with " "allow_partial=True."
        )
    raw_manifest = load_manifest(cfg.raw_manifest_path)
    routing_metadata = json.loads(cfg.snapshot_metadata_path.read_text())
    source = pd.read_parquet(cfg.snapshot_path)
    output = build_components(
        source,
        road_distance_decay_km=cfg.road_distance_decay_km,
        city_travel_time_decay_minutes=cfg.city_travel_time_decay_minutes,
    )
    atomic_write_parquet(output, cfg.output_path, overwrite=overwrite)
    artifact = artifact_record(
        cfg.output_path,
        dataset_id="human.accessibility.land_transport_access.h3_r7",
        frame=output,
        h3_resolution=7,
    )
    payload = manifest_payload(
        config=cfg.human,
        stage="build",
        artifacts=[artifact],
        sources=raw_manifest["sources"],
        inputs=raw_manifest["artifacts"],
        source_completeness=cfg.source_completeness,
        measurement_statuses=[MeasurementStatus.DERIVED, MeasurementStatus.UNAVAILABLE],
        attribution=raw_manifest["attribution"],
        licenses=raw_manifest["licenses"],
        h3_resolution=7,
        limitations=[
            "The index is theoretical road/city accessibility, not observed travel or effort.",
            (
                "Routing uses a pinned local road-only graph with explicit unavailable snaps."
                if routing_metadata.get("routing_policy") == "road_only_no_ferry_no_shuttle_train"
                else "The adopted legacy routing snapshot cannot be rebuilt from a pinned OSRM graph version."
            ),
            "Centroid-to-road snapping can be misleading for islands and irregular coastal cells.",
            "Ferry schedules, seasonal closures, parking, trails, and legal shore access are separate evidence.",
        ],
    )
    payload["routing_policy"] = routing_metadata.get("routing_policy", "legacy_routing_unverified")
    payload["routing_graph_manifest_sha256"] = routing_metadata.get("graph_manifest_sha256")
    write_manifest(cfg.manifest_path, payload)
    return cfg.output_path


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
