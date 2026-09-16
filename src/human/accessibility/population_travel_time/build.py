"""Build population-weighted travel-opportunity challenger components."""

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

from .config import DEFAULT_CONFIG_PATH, load_population_travel_config


def build_components(
    frame: pd.DataFrame,
    *,
    decay_minutes: tuple[int, ...],
    primary_decay_minutes: int,
    cap_quantile: float,
    minimum_routed_population_fraction: float,
    allow_partial_evaluated_lower_bound: bool = False,
) -> tuple[pd.DataFrame, dict[int, float]]:
    output = frame.copy().rename(columns={"source_h3": "H3_INDEX"})
    if output["H3_INDEX"].isna().any() or output["H3_INDEX"].duplicated().any():
        raise ValueError("Population-travel H3_INDEX must be non-null and unique.")
    context_available = output["POPULATION_TRAVEL_CONTEXT_AVAILABLE"].eq(True)
    # Road-only disconnected OD pairs are successfully evaluated, not missing.
    # Legacy snapshots lack this distinction and retain their original gate.
    evaluated_column = "POPULATION_TRAVEL_EVALUATED_SELECTED_POPULATION_FRACTION"
    fraction_column = (
        evaluated_column
        if evaluated_column in output
        else "POPULATION_TRAVEL_ROUTED_SELECTED_POPULATION_FRACTION"
    )
    routed_fraction = output[fraction_column].astype(float)
    if not routed_fraction.dropna().between(0, 1).all():
        raise ValueError("Population routing coverage must be in [0, 1]")
    routing_complete = context_available & routed_fraction.ge(minimum_routed_population_fraction)
    routing_usable = routing_complete
    if allow_partial_evaluated_lower_bound:
        if evaluated_column not in output:
            raise ValueError("Partial lower bounds require explicit evaluated-population coverage")
        routing_usable = context_available & routed_fraction.gt(0)
    output.insert(1, "H3_RESOLUTION", 7)
    caps: dict[int, float] = {}
    for minutes in decay_minutes:
        raw_column = f"POPULATION_TRAVEL_DEMAND_{minutes}_MIN"
        available = routing_usable & output[raw_column].notna()
        log_values = np.log1p(output.loc[available, raw_column].astype(float))
        cap = float(log_values.quantile(cap_quantile)) if not log_values.empty else float("nan")
        if cap == 0 and not log_values.empty and log_values.eq(0).all():
            cap = 1.0  # all known-disconnected is a genuine zero opportunity
        if not np.isfinite(cap) or cap <= 0:
            raise ValueError(f"Invalid population-travel log1p cap for {minutes} minutes.")
        caps[minutes] = cap
        output[f"POPULATION_TRAVEL_DEMAND_AVAILABLE_{minutes}_MIN"] = available
        output[f"POPULATION_TRAVEL_DEMAND_COMPONENT_{minutes}_MIN"] = (
            (np.log1p(output[raw_column].astype(float)) / cap).clip(0.0, 1.0).where(available)
        )
    primary_column = f"POPULATION_TRAVEL_DEMAND_COMPONENT_{primary_decay_minutes}_MIN"
    output["POPULATION_TRAVEL_OPPORTUNITY_INDEX"] = output[primary_column]
    output["POPULATION_TRAVEL_OPPORTUNITY_AVAILABLE"] = output[
        f"POPULATION_TRAVEL_DEMAND_AVAILABLE_{primary_decay_minutes}_MIN"
    ]
    output["POPULATION_TRAVEL_OPPORTUNITY_STATUS"] = np.where(
        output["POPULATION_TRAVEL_OPPORTUNITY_AVAILABLE"],
        "complete_selected_origin_routing",
        "routing_or_population_coverage_unavailable",
    )
    output.loc[
        output["POPULATION_TRAVEL_OPPORTUNITY_AVAILABLE"] & ~routing_complete,
        "POPULATION_TRAVEL_OPPORTUNITY_STATUS",
    ] = "partial_evaluated_population_lower_bound"
    output["MEASUREMENT_STATUS"] = np.where(
        output["POPULATION_TRAVEL_OPPORTUNITY_AVAILABLE"],
        MeasurementStatus.DERIVED.value,
        MeasurementStatus.UNAVAILABLE.value,
    )
    return output.sort_values("H3_INDEX").reset_index(drop=True), caps


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    allow_partial: bool = False,
    *,
    overwrite: bool = False,
) -> Path:
    cfg = load_population_travel_config(config_path)
    if cfg.source_completeness != "complete" and not allow_partial:
        raise ValueError(
            "Population travel time is partial research evidence; rerun with allow_partial=True."
        )
    raw_manifest = load_manifest(cfg.raw_manifest_path)
    routing_metadata = json.loads(cfg.snapshot_metadata_path.read_text())
    source = pd.read_parquet(cfg.snapshot_path)
    output, caps = build_components(
        source,
        decay_minutes=cfg.decay_minutes,
        primary_decay_minutes=cfg.primary_decay_minutes,
        cap_quantile=cfg.cap_quantile,
        minimum_routed_population_fraction=cfg.minimum_routed_population_fraction,
        allow_partial_evaluated_lower_bound=cfg.allow_partial_evaluated_lower_bound,
    )
    atomic_write_parquet(output, cfg.output_path, overwrite=overwrite)
    artifact = artifact_record(
        cfg.output_path,
        dataset_id="human.accessibility.population_travel_time.h3_r7",
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
            "The index is time-decayed potential population reach, not observed trips or effort.",
            f"The primary {cfg.primary_decay_minutes}-minute decay and q{int(cfg.cap_quantile * 100)} cap are uncalibrated challenger choices.",
            (
                "Routing uses a pinned local road-only graph; unknown snaps remain unavailable."
                if routing_metadata.get("routing_policy") == "road_only_no_ferry_no_shuttle_train"
                else "The legacy routing snapshot cannot be rebuilt from a pinned OSRM graph version."
            ),
            "Ferries, closures, parking, trails, and legal public access require separate evidence.",
            f"Computed log1p caps by decay minutes: {caps}.",
        ],
    )
    payload["routing_policy"] = routing_metadata.get("routing_policy", "legacy_routing_unverified")
    payload["routing_graph_manifest_sha256"] = routing_metadata.get("graph_manifest_sha256")
    payload["partial_evaluated_population_lower_bound"] = cfg.allow_partial_evaluated_lower_bound
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
