from __future__ import annotations

from pathlib import Path

import yaml

from human.core.config import ConfigDocument
from human.viewshed.config import load_app_config
from human.viewshed.config.paths import DEFAULT_CONFIG
from human.viewshed.finalize.final_artifacts import (
    static_scientific_config_hash,
)

CANONICAL_CONFIG = Path("config/modeling/effort/viewshed.yaml")


def test_canonical_config_is_the_only_viewshed_yaml_under_config() -> None:
    matches = sorted(
        path
        for path in Path("config").rglob("*.yaml")
        if "viewshed" in path.parts or path.name == "viewshed.yaml"
    )

    assert matches == [CANONICAL_CONFIG]
    legacy_human_config = Path("config/data/human.yaml")
    if legacy_human_config.exists():
        assert "viewshed" not in yaml.safe_load(legacy_human_config.read_text())
    assert DEFAULT_CONFIG == CANONICAL_CONFIG.resolve()


def test_canonical_config_uses_model_area_bbox() -> None:
    app = load_app_config(CANONICAL_CONFIG)

    assert app.region.name == "model_area"
    assert app.region.bbox_wgs84 == {
        "min_lon": -125.8,
        "min_lat": 46.85,
        "max_lon": -121.6,
        "max_lat": 50.0,
    }
    assert app.h3.source_resolution == 7
    assert app.h3.target_resolution == 7
    static_maps = app.raw_config["static_maps"]
    assert static_maps["enabled"] is True
    assert static_maps["selected_location"] == {
        "latitude": 48.135238,
        "longitude": -122.767230,
    }
    assert app.run.write_maps is False
    assert app.paths.map_dir == Path("outputs/effort/viewshed").resolve()
    assert app.paths.output_dir == Path("data/tmp/viewshed/h3r7").resolve()
    assert app.paths.final_output_dir == Path("data/processed/domain/human/viewshed/RES7").resolve()


