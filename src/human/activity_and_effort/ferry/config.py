"""Validated configuration for ferry effort products."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from human.utils.config import HumanConfig, require_mapping

DEFAULT_CONFIG_PATH = "config/data/human/activity_and_effort/ferry.yaml"
OUTPUT_FIELDS = {
    "route_daily_r7": "route_daily_path",
    "daily_r7": "daily_path",
    "weekly_r6": "weekly_path",
}


@dataclass(frozen=True)
class FerrySource:
    name: str
    supplied_path: Path
    provider: str
    license_name: str
    attribution: str
    measurement_status: str


@dataclass(frozen=True)
class FerryConfig:
    human: HumanConfig
    sources: dict[str, FerrySource]
    source_completeness: str
    native_h3_resolution: int
    model_h3_resolution: int
    snapshot_dir: Path
    raw_manifest_path: Path
    output_dir: Path
    route_daily_path: Path
    daily_path: Path
    weekly_path: Path
    manifest_path: Path
    report_path: Path


def load_ferry_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    resolve_generation: bool = True,
) -> FerryConfig:
    human = HumanConfig.load(path, product="ferry", category="activity_and_effort")
    source_section = human.section("source")
    completeness = str(source_section.get("source_completeness", "unknown"))
    if completeness not in {"complete", "partial"}:
        raise ValueError("ferry source_completeness must be complete or partial.")
    sources: dict[str, FerrySource] = {}
    for name in (
        "wsf_ridership",
        "bc_ridership",
        "route_segments",
        "route_mapping",
        "wsf_vessel_history",
    ):
        values = require_mapping(source_section.get(name), f"source.{name}")
        sources[name] = FerrySource(
            name=name,
            supplied_path=human.resolve(str(values["supplied_path"])),
            provider=str(values["provider"]),
            license_name=str(values["license"]),
            attribution=str(values["attribution"]),
            measurement_status=str(values["measurement_status"]),
        )
    parameters = human.section("parameters")
    native = int(parameters.get("native_h3_resolution", -1))
    model = int(parameters.get("model_h3_resolution", -1))
    if (native, model) != (7, 6):
        raise ValueError("ferry currently requires native H3 R7 and model H3 R6.")
    cfg = FerryConfig(
        human=human,
        sources=sources,
        source_completeness=completeness,
        native_h3_resolution=native,
        model_h3_resolution=model,
        snapshot_dir=human.path_value("raw", "snapshot_dir"),
        raw_manifest_path=human.path_value("raw", "manifest_path"),
        output_dir=human.path_value("output", "directory"),
        route_daily_path=human.path_value("output", "route_daily_path"),
        daily_path=human.path_value("output", "daily_path"),
        weekly_path=human.path_value("output", "weekly_path"),
        manifest_path=human.path_value("output", "manifest_path"),
        report_path=human.path_value("inspection", "report_path"),
    )
    if resolve_generation:
        return resolve_outputs(cfg)
    return cfg


def resolve_outputs(cfg: FerryConfig) -> FerryConfig:
    """Resolve one immutable generation without importing publication code."""

    if not cfg.manifest_path.exists():
        return cfg
    payload = json.loads(cfg.manifest_path.read_text(encoding="utf-8"))
    if not payload.get("generation_id"):
        return cfg
    outputs = {
        item["dataset_id"].split(".")[-1]: Path(item["path"])
        for item in payload.get("artifacts", [])
    }
    if not set(OUTPUT_FIELDS).issubset(outputs):
        raise ValueError("Incomplete ferry generation.")
    parents = {outputs[key].parent for key in OUTPUT_FIELDS}
    if len(parents) != 1:
        raise ValueError("Mixed ferry generation.")
    directory = parents.pop()
    return replace(
        cfg,
        output_dir=directory,
        **{field: outputs[key] for key, field in OUTPUT_FIELDS.items()},
    )
