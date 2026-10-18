"""DEM acquisition and terrain/canopy raster preparation."""

from human.legacy.viewshed.prepare.elevation.acquire import DemDownloadResult, download_dem_for_config
from human.legacy.viewshed.prepare.elevation.canopy import (
    CanopySurfaceResult,
    build_canonical_canopy_base_surface,
    build_canopy_obstacle_surface,
    build_observer_grounded_canopy_surface_from_base,
)

__all__ = [
    "CanopySurfaceResult",
    "DemDownloadResult",
    "build_canopy_obstacle_surface",
    "build_canonical_canopy_base_surface",
    "build_observer_grounded_canopy_surface_from_base",
    "download_dem_for_config",
]
