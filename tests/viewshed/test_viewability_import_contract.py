"""The toolkit owns geometry finalization; application publishing stays external."""
from __future__ import annotations

import importlib
from types import ModuleType


def test_static_viewability_dotted_import_resolves_to_module() -> None:
    module = importlib.import_module("human.viewshed.finalize.final_artifacts")

    assert isinstance(module, ModuleType)
    assert callable(module.build_static_viewability_lazy)
    assert callable(module.materialize_static_viewability_outputs)
