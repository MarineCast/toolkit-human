"""Vegetation obstruction and attenuation weights."""

from human.legacy.viewshed.weights.vegetation.cli import main, run_vegetation_path_weights
from human.legacy.viewshed.weights.vegetation.source_h3 import run_vegetation_weights

__all__ = ["main", "run_vegetation_path_weights", "run_vegetation_weights"]
