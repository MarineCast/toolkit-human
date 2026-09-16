"""Acquire Census/TIGER and Statistics Canada population snapshots."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    manifest_payload,
    source_record,
    write_manifest,
)

from .canada.config import load_canada_config
from .canada.geography import load_bc_source_geography
from .canada.statcan import STATCAN_UNAVAILABLE_FILENAME, join_candidate_population
from .cli import find_project_root, load_dotenv
from .config import DEFAULT_CONFIG_PATH, load_population_family_config
from .us.census import fetch_block_population
from .us.config import load_us_config
from .us.geography import load_state_blocks, load_state_boundaries


def _files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def download(config_path: str | Path = DEFAULT_CONFIG_PATH, overwrite: bool = False) -> Path:
    family = load_population_family_config(config_path)
    load_dotenv(find_project_root() / "config" / ".env")
    us = load_us_config(config_path)
    states = load_state_boundaries(us, overwrite_downloads=overwrite)
    blocks = load_state_blocks(us, overwrite_downloads=overwrite)
    fetch_block_population(us, blocks, overwrite=overwrite)

    canada = load_canada_config(config_path)
    geographies, join_column = load_bc_source_geography(canada, overwrite_downloads=overwrite)
    join_candidate_population(canada, geographies, join_column, overwrite=overwrite)

    files = [*_files(us.paths.raw_dir), *_files(canada.paths.raw_dir)]
    if not files:
        raise RuntimeError("Population download produced no raw snapshots.")
    sources = []
    artifacts = []
    statuses: set[MeasurementStatus] = {MeasurementStatus.OBSERVED}
    for path in files:
        is_canada = path.is_relative_to(canada.paths.raw_dir)
        provider = "Statistics Canada" if is_canada else "United States Census Bureau"
        license_name = (
            "Statistics Canada Open Licence" if is_canada else "United States public domain"
        )
        status = (
            MeasurementStatus.UNAVAILABLE
            if path.name == STATCAN_UNAVAILABLE_FILENAME
            else MeasurementStatus.OBSERVED
        )
        statuses.add(status)
        sources.append(
            source_record(
                path,
                name=path.name,
                provider=provider,
                license_name=license_name,
                attribution=provider,
                measurement_status=status,
            )
        )
        artifacts.append(artifact_record(path, dataset_id=f"human.population.raw.{path.stem}"))
    payload = manifest_payload(
        config=family.human,
        stage="download",
        artifacts=artifacts,
        sources=sources,
        source_completeness="complete",
        measurement_statuses=sorted(statuses, key=str),
        attribution=["United States Census Bureau", "Statistics Canada"],
        licenses=["United States public domain", "Statistics Canada Open Licence"],
        h3_resolution=family.h3_resolution,
        spatial_bounds={"countries": ["US", "CA"], "subdivisions": ["WA", "OR", "BC"]},
        temporal={"census_vintages": [2020, 2021]},
    )
    write_manifest(family.raw_manifest_path, payload)
    del states
    return family.raw_manifest_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    print(download(args.config, overwrite=args.overwrite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
