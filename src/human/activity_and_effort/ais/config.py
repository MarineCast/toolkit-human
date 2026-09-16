"""Validated configuration for the AIS activity pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from human.utils.config import HumanConfig

DEFAULT_CONFIG_PATH = "config/data/human/activity_and_effort/ais.yaml"


@dataclass(frozen=True)
class AisConfig:
    human: HumanConfig
    supplied_paths: tuple[Path, ...]
    provider: str
    license_name: str
    attribution: str
    source_completeness: str
    materialization_mode: str
    h3_resolution: int
    sog_not_available_min_knots: float
    observer_capable_classes: tuple[str, ...]
    snapshot_dir: Path
    raw_manifest_path: Path
    daily_path: Path
    weekly_path: Path
    qc_path: Path
    manifest_path: Path
    report_path: Path


def load_ais_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AisConfig:
    human = HumanConfig.load(path, product="ais", category="activity_and_effort")
    source = human.section("source")
    supplied = source.get("supplied_paths")
    if not isinstance(supplied, list) or not supplied:
        raise ValueError("AIS source.supplied_paths must be a non-empty list.")
    completeness = str(source.get("source_completeness", "unknown"))
    if completeness not in {"complete", "partial"}:
        raise ValueError("AIS source_completeness must be complete or partial.")
    materialization_mode = str(source.get("materialization_mode", "copy_snapshot"))
    if materialization_mode not in {"copy_snapshot", "reference_existing"}:
        raise ValueError(
            "AIS source.materialization_mode must be copy_snapshot or reference_existing."
        )
    parameters = human.section("parameters")
    resolution = int(parameters.get("h3_resolution", -1))
    if resolution != 6:
        raise ValueError("The current AIS source and model contract requires H3 resolution 6.")
    sog_not_available = float(parameters.get("sog_not_available_min_knots", 102.0))
    if not 0 < sog_not_available <= 102.3:
        raise ValueError("AIS sog_not_available_min_knots must be in (0, 102.3].")
    observer_classes = tuple(
        str(value).strip().lower()
        for value in parameters.get("observer_capable_classes", ["recreational", "passenger"])
    )
    allowed_classes = {
        "fishing",
        "towing",
        "recreational",
        "passenger",
        "cargo",
        "tanker",
        "unknown",
    }
    if not observer_classes or set(observer_classes).difference(allowed_classes):
        raise ValueError("AIS observer_capable_classes contains an unsupported vessel class.")
    manifest_path = human.path_value("output", "manifest_path")
    qc_path = human.resolve(
        human.section("output").get("qc_path", str(manifest_path.with_name("ais_activity_qc.json")))
    )
    return AisConfig(
        human=human,
        supplied_paths=tuple(human.resolve(value) for value in supplied),
        provider=str(source.get("provider", "unresolved")),
        license_name=str(source.get("license", "unresolved")),
        attribution=str(source.get("attribution", "unresolved")),
        source_completeness=completeness,
        materialization_mode=materialization_mode,
        h3_resolution=resolution,
        sog_not_available_min_knots=sog_not_available,
        observer_capable_classes=observer_classes,
        snapshot_dir=human.path_value("raw", "snapshot_dir"),
        raw_manifest_path=human.path_value("raw", "manifest_path"),
        daily_path=human.path_value("output", "daily_path"),
        weekly_path=human.path_value("output", "weekly_path"),
        qc_path=qc_path,
        manifest_path=manifest_path,
        report_path=human.path_value("inspection", "report_path"),
    )
