"""Run from outside the checkout in a fresh environment containing a regular wheel."""
from __future__ import annotations

import contextlib
import io
import importlib.abc
from importlib.resources import files
from pathlib import Path
import sys
import tempfile


class BlockApplicationImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "orcacast" or fullname.startswith("orcacast."):
            raise AssertionError(f"Application dependency: {fullname}")
        return None


sys.meta_path.insert(0, BlockApplicationImports())
import human
import human.core.artifacts
from human.cli import FAMILIES, initialize_workspace, main
from human.viewshed.finalize.final_artifacts import build_static_viewability_lazy

root = Path(str(files("human"))).resolve()
assert root.is_relative_to(Path(sys.prefix).resolve()), root
assert "site-packages" in root.parts, root
assert callable(build_static_viewability_lazy)
required = (
    "core/artifacts/__init__.py",
    "core/artifacts/checksums.py",
    "core/artifacts/contracts.py",
    "resources/config/common.yaml",
    "resources/config/data/human/activity_and_effort/land_reporting_opportunity.yaml",
    "resources/config/modeling/effort/viewshed.yaml",
    "accessibility/land_transport_access/road_only.lua",
)
assert all(root.joinpath(path).is_file() for path in required)
with tempfile.TemporaryDirectory() as directory:
    workspace = Path(directory)
    assert initialize_workspace(workspace)
    config = workspace / "config/common.yaml"
    config.write_text("user edit\n")
    assert initialize_workspace(workspace) == []
    assert config.read_text() == "user edit\n"
print(f"Standalone wheel/resource/init checks passed: {human.__file__}")

count = 0
for family in FAMILIES:
    for action in ("build", "download", "inspect"):
        if family == "observer-effort" and action == "download":
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                main([action, family, "--help"])
            except SystemExit as error:
                assert error.code == 0, (action, family, error.code)
            else:
                raise AssertionError(f"Help did not exit: {action} {family}")
        count += 1
print(f"Passed {count} family CLI help routes with application imports blocked")
