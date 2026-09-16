from __future__ import annotations

import runpy

import yaml

from human.core.config.paths import project_root


def test_human_feature_catalog_is_generated_and_model_safe() -> None:
    root = project_root()
    namespace = runpy.run_path(str(root / "scripts/update_human_feature_catalog.py"))
    catalog_path = root / "src/human/feature_catalog.yaml"
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    assert catalog == namespace["build_catalog"]()
    assert set(catalog["measurement_statuses"]) == {
        "observed",
        "derived",
        "estimated",
        "fallback",
        "unavailable",
    }
    used_roles = set()
    for collection in catalog["collections"].values():
        for feature in collection["features"]:
            used_roles.add(feature["role"])
            if feature["model_eligible"]:
                assert feature["role"] == "predictor"
            if feature["role"] in {"coverage", "provenance", "support", "qc"}:
                assert not feature["model_eligible"]
    assert used_roles == set(catalog["roles"])
    for collection_name, feature_names in catalog["default_model_policy"].items():
        collection = catalog["collections"][collection_name]
        features = {feature["name"]: feature for feature in collection["features"]}
        assert collection["model_policy"] not in {
            "app_only",
            "evidence_only",
            "partial_source_research_only",
        }
        for name in feature_names:
            assert features[name]["role"] == "predictor"
            assert features[name]["model_eligible"]
    assert catalog["collections"]["places_catalog"]["model_policy"] == "app_only"
    population_context = catalog["collections"]["population_context_r7"]
    assert population_context["model_policy"] == "evidence_only"
    assert not any(feature["model_eligible"] for feature in population_context["features"])
