from __future__ import annotations

import importlib
from types import ModuleType


def test_viewability_builder_dotted_import_resolves_to_module() -> None:
    module = importlib.import_module(
        "orcacast.publishing.prep.viewability.build_viewability_artifacts"
    )

    assert isinstance(module, ModuleType)
    assert callable(module.build_viewability_artifacts)
