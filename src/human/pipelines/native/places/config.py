"""Validated configuration for the places pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from human.utils.config import HumanConfig

DEFAULT_CONFIG_PATH = "config/data/human/demography_and_presence/places.yaml"


@dataclass(frozen=True)
class PlacesConfig:
    human: HumanConfig
    bbox: tuple[float, float, float, float]
    parameters: dict[str, Any]
    sources: dict[str, Any]
    cache_dir: Path
    inventory_path: Path
    raw_manifest_path: Path
    catalog_path: Path
    manifest_path: Path
    report_path: Path


def load_places_config(path: str | Path = DEFAULT_CONFIG_PATH) -> PlacesConfig:
    human = HumanConfig.load(path, product="places", category="demography_and_presence")
    bbox = human.section("bbox")
    values = tuple(float(bbox[key]) for key in ("west", "south", "east", "north"))
    if values[0] >= values[2] or values[1] >= values[3]:
        raise ValueError("places bbox must have west < east and south < north.")
    sources = human.section("sources")
    if not sources:
        raise ValueError("places sources must not be empty.")
    return PlacesConfig(
        human=human,
        bbox=values,
        parameters=human.section("parameters"),
        sources=sources,
        cache_dir=human.path_value("raw", "cache_dir"),
        inventory_path=human.path_value("raw", "inventory_path"),
        raw_manifest_path=human.path_value("raw", "manifest_path"),
        catalog_path=human.path_value("output", "catalog_path"),
        manifest_path=human.path_value("output", "manifest_path"),
        report_path=human.path_value("inspection", "report_path"),
    )
