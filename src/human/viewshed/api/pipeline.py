"""High-level, argparse-free viewshed workflow orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..config import AppConfig
from .stages import StageInvocation, run_stage

DEFAULT_STAGES = (
    "download-data",
    "build-land-cells",
    "prepare-source-target-lookup",
    "build-distance-weights",
    "build-dual-surface-canopy-weights",
    "terrain-weight",
    "build-vegetation-path-weights",
    "finalize-viewshed-lookups",
    "export-static-maps",
)


@dataclass(frozen=True)
class ViewshedRunResult:
    """Results returned by a complete API-driven viewshed run."""

    config: AppConfig
    stage_results: Mapping[str, tuple[object, ...]]


def run_viewshed(config: AppConfig) -> ViewshedRunResult:
    """Run the canonical viewshed workflow using typed Python calls only."""

    results: dict[str, tuple[object, ...]] = {}
    for stage in DEFAULT_STAGES:
        source_types: tuple[str | None, ...]
        if stage == "build-distance-weights":
            source_types = ("land", "water")
        elif stage in {"terrain-weight", "build-vegetation-path-weights"}:
            source_types = ("water",)
        else:
            source_types = (None,)
        results[stage] = tuple(
            run_stage(
                config,
                StageInvocation(
                    stage,
                    source_type=source_type,
                    overwrite=stage
                    in {
                        "finalize-viewshed-lookups",
                        "export-static-maps",
                    },
                ),
            )
            for source_type in source_types
        )
    from ..finalize.final_artifacts import cleanup_viewshed_dir_to_static_outputs

    cleanup_viewshed_dir_to_static_outputs(config.config_path)
    return ViewshedRunResult(config=config, stage_results=results)
