from __future__ import annotations

from pathlib import Path

from human.core.config.paths import project_root
from human.activity_and_effort.ferry.sources import (
    DEFAULT_BC_CACHE_DIR,
    DEFAULT_BC_OUTPUT,
    DEFAULT_BC_PREVIEW_DIR,
    DEFAULT_WSF_CACHE_DIR,
    DEFAULT_WSF_OUTPUT,
    FERRY_RAW_DIR,
    FERRY_UTILS_DIR,
)


def test_default_ferry_artifacts_stay_in_ferry_workspace() -> None:
    assert FERRY_UTILS_DIR.name == "ferry"
    assert FERRY_RAW_DIR == project_root() / "data/raw/human/activity_and_effort/ferry"
    assert DEFAULT_WSF_CACHE_DIR == FERRY_RAW_DIR / "wsf_tableau"
    assert DEFAULT_BC_CACHE_DIR == FERRY_RAW_DIR / "bc_ferries_traffic"
    assert DEFAULT_BC_PREVIEW_DIR == DEFAULT_BC_CACHE_DIR / "previews"
    assert DEFAULT_WSF_OUTPUT == FERRY_RAW_DIR / "wsf_ridership.parquet"
    assert DEFAULT_BC_OUTPUT == FERRY_RAW_DIR / "bc_ferries_estimated_ridership.parquet"

    for destination in (
        DEFAULT_WSF_CACHE_DIR,
        DEFAULT_BC_CACHE_DIR,
        DEFAULT_BC_PREVIEW_DIR,
        DEFAULT_WSF_OUTPUT,
        DEFAULT_BC_OUTPUT,
    ):
        assert destination.is_relative_to(FERRY_RAW_DIR)
        assert "tmp" not in destination.relative_to(FERRY_RAW_DIR).parts
