"""Family-level configuration for cross-border population products."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from human.utils.config import HumanConfig

DEFAULT_CONFIG_PATH = "config/data/human/demography_and_presence/population.yaml"


@dataclass(frozen=True)
class PopulationFamilyConfig:
    human: HumanConfig
    raw_manifest_path: Path
    us_path: Path
    canada_path: Path
    cross_border_path: Path
    context_path: Path
    manifest_path: Path
    report_path: Path
    h3_resolution: int


def load_population_family_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> PopulationFamilyConfig:
    human = HumanConfig.load(
        path,
        product="population",
        category="demography_and_presence",
        extra_keys={"h3_resolution", "output_crs", "paths", "runtime"},
    )
    resolution = int(human.raw.get("h3_resolution", -1))
    if resolution != 7:
        raise ValueError("The harmonized population contract requires H3 resolution 7.")
    return PopulationFamilyConfig(
        human=human,
        raw_manifest_path=human.path_value("raw", "manifest_path"),
        us_path=human.path_value("output", "us_path"),
        canada_path=human.path_value("output", "canada_path"),
        cross_border_path=human.path_value("output", "cross_border_path"),
        context_path=human.path_value("output", "context_path"),
        manifest_path=human.path_value("output", "manifest_path"),
        report_path=human.path_value("inspection", "report_path"),
        h3_resolution=resolution,
    )
