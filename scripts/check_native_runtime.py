"""Fail early when a requested native integration runtime is incomplete."""
from __future__ import annotations

import argparse
import shutil
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", choices=("gdal", "osrm"))
    args = parser.parse_args()
    if args.runtime == "gdal":
        from osgeo import gdal, gdal_array
        import rasterio
        import pyarrow.fs

        # Exercise Arrow after loading GDAL to catch incompatible native libraries.
        pyarrow.fs.LocalFileSystem()
        assert callable(gdal.ViewshedGenerate)
        assert gdal_array is not None
        assert shutil.which("gdal_viewshed"), "gdal_viewshed CLI is required"
        print(f"GDAL bindings: {gdal.VersionInfo('--version')}")
        print(f"Rasterio: {rasterio.__version__}; GDAL: {rasterio.__gdal_version__}")
        subprocess.run(["gdal_viewshed", "--utility_version"], check=True)
    else:
        from human.accessibility.land_transport_access.routing import OSRM_ROOT

        for name in ("extract", "partition", "customize", "routed"):
            version = subprocess.check_output(
                [str(OSRM_ROOT / f"bin/osrm-{name}"), "--version"], text=True
            ).strip()
            assert "26.8.0" in version, f"Unexpected OSRM runtime: {version}"
            print(f"osrm-{name}: {version}")
        assert (OSRM_ROOT / "share/osrm/profiles/car.lua").is_file()


if __name__ == "__main__":
    main()
