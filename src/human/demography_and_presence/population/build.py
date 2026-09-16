"""Build country-specific and harmonized cross-border H3 population products."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import pandas as pd

from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_parquet,
    load_manifest,
    manifest_payload,
    write_manifest,
)

from .canada.config import load_canada_config
from .canada.pipeline import run_canada_population_pipeline_result
from .config import DEFAULT_CONFIG_PATH, load_population_family_config
from .context import build_population_context
from .us.config import load_us_config
from .us.pipeline import run_us_population_pipeline


def _harmonize_country(frame: pd.DataFrame, *, country: str) -> pd.DataFrame:
    year = 2020 if country == "US" else 2021
    population = f"population_{year}"
    density = f"population_density_{year}_per_km2"
    subdivision = "state_abbr" if country == "US" else "province_abbr"
    output = frame.rename(
        columns={
            "h3": "H3_INDEX",
            "h3_resolution": "H3_RESOLUTION",
            subdivision: "SUBDIVISION_CODE",
            population: "POPULATION",
            f"{population}_round": "POPULATION_ROUNDED",
            density: "POPULATION_DENSITY_PER_KM2",
        }
    ).copy()
    output.insert(2, "COUNTRY_CODE", country)
    output.insert(4, "CENSUS_YEAR", year)
    common = [
        "H3_INDEX",
        "H3_RESOLUTION",
        "COUNTRY_CODE",
        "SUBDIVISION_CODE",
        "CENSUS_YEAR",
        "POPULATION",
        "POPULATION_ROUNDED",
        "POPULATION_DENSITY_PER_KM2",
    ]
    water = [column for column in output if column.startswith(("distance_", "within_", "water_"))]
    provenance = [
        column
        for column in ("source_dataset", "source_geography_level", "allocation_method", "crs_area")
        if column in output
    ]
    return output[[*common, *water, *provenance]]


def harmonize_population_frames(us: pd.DataFrame, canada: pd.DataFrame) -> pd.DataFrame:
    """Return the stable cross-border schema with a country-qualified key."""
    output = pd.concat(
        [_harmonize_country(us, country="US"), _harmonize_country(canada, country="CA")],
        ignore_index=True,
        sort=False,
    )
    if output.duplicated(["COUNTRY_CODE", "H3_INDEX"]).any():
        raise ValueError("Cross-border population contains duplicate country/H3 keys.")
    return output.sort_values(["COUNTRY_CODE", "H3_INDEX"]).reset_index(drop=True)


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    allow_partial: bool = False,
    *,
    overwrite: bool = False,
) -> Path:
    if allow_partial:
        raise ValueError("Population does not publish a partial cross-border product.")
    family = load_population_family_config(config_path)
    raw_manifest = load_manifest(family.raw_manifest_path)
    us_config = load_us_config(config_path)
    canada_config = load_canada_config(config_path)
    if overwrite:
        us_config = replace(
            us_config,
            runtime=replace(us_config.runtime, overwrite_output=True),
        )
        canada_config = replace(
            canada_config,
            runtime=replace(canada_config.runtime, overwrite_output=True),
        )
    us_result = run_us_population_pipeline(us_config)
    canada_result = run_canada_population_pipeline_result(canada_config)
    us = pd.read_parquet(us_result.output_path)
    canada = pd.read_parquet(canada_result.output_path)
    cross_border = harmonize_population_frames(us, canada)
    context = build_population_context(cross_border)
    atomic_write_parquet(cross_border, family.cross_border_path, overwrite=overwrite)
    atomic_write_parquet(context, family.context_path, overwrite=overwrite)
    artifacts = [
        artifact_record(
            family.us_path, dataset_id="human.population.us_h3_r7", frame=us, h3_resolution=7
        ),
        artifact_record(
            family.canada_path,
            dataset_id="human.population.canada_h3_r7",
            frame=canada,
            h3_resolution=7,
        ),
        artifact_record(
            family.cross_border_path,
            dataset_id="human.population.cross_border_h3_r7",
            frame=cross_border,
            h3_resolution=7,
        ),
        artifact_record(
            family.context_path,
            dataset_id="human.population.context_h3_r7",
            frame=context,
            h3_resolution=7,
        ),
    ]
    measurement_statuses = {
        MeasurementStatus(status) for status in raw_manifest["measurement_statuses"]
    }
    measurement_statuses.add(MeasurementStatus.DERIVED)
    unavailable_rows = sum(
        int(record.get("rows") or 0)
        for record in raw_manifest["artifacts"]
        if record["dataset_id"].endswith("_unavailable")
    )
    limitations = [
        "Population is a land-based context source; no observer access, travel, viewshed, or marine-cell transfer is implied.",
        "The H3-unique context preserves U.S. 2020 and Canada 2021 components; its total is not a contemporaneous census estimate.",
    ]
    if unavailable_rows:
        limitations.append(
            f"Statistics Canada reports {unavailable_rows} British Columbia DA population "
            "values as unavailable; affected candidate geographies are excluded from "
            "allocation and are never treated as zero."
        )
    payload = manifest_payload(
        config=family.human,
        stage="build",
        artifacts=artifacts,
        sources=raw_manifest["sources"],
        inputs=raw_manifest["artifacts"],
        source_completeness="complete",
        measurement_statuses=sorted(measurement_statuses, key=str),
        attribution=raw_manifest["attribution"],
        licenses=raw_manifest["licenses"],
        h3_resolution=7,
        spatial_bounds={"countries": ["US", "CA"], "subdivisions": ["WA", "OR", "BC"]},
        temporal={"census_vintages": [2020, 2021]},
        limitations=limitations,
    )
    write_manifest(family.manifest_path, payload)
    return family.cross_border_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite processed outputs while reusing the downloaded raw snapshots.",
    )
    args = parser.parse_args(argv)
    print(build(args.config, allow_partial=args.allow_partial, overwrite=args.overwrite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
