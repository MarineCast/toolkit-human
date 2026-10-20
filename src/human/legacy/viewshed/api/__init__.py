"""Callable Python API for the viewshed workflow."""

from human.legacy.viewshed.api.pipeline import (
    DEFAULT_STAGES,
    ViewshedRunResult,
    run_viewshed,
)
from human.legacy.viewshed.api.service import STAGES, ViewshedRequest, process, validate
from human.legacy.viewshed.api.stages import StageInvocation, run_stage

__all__ = [
    "DEFAULT_STAGES",
    "STAGES",
    "StageInvocation",
    "ViewshedRequest",
    "ViewshedRunResult",
    "process",
    "run_stage",
    "run_viewshed",
    "validate",
]
