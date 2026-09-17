from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from human.core.config.paths import project_root

REPO_ROOT = project_root()
DATA_DIR = REPO_ROOT / "data"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
OUTPUTS_DIR = REPO_ROOT / "outputs"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
FEATURES_DIR = ARTIFACTS_DIR / "features"
FEATURE_STORE_DIR = ARTIFACTS_DIR / "features" / "stores"
MODELING_DIR = ARTIFACTS_DIR / "modeling"
FORECASTS_DIR = ARTIFACTS_DIR / "forecasts"
EXPORTS_DIR = OUTPUTS_DIR
CATALOG_DIR = DATA_DIR / "catalog"
QA_DIR = DATA_DIR / "qa"

RAW_POPULATION_DIR = RAW_DIR / "population"
RAW_ORCA_POPULATION_DIR = RAW_DIR / "orca_population"
PROCESSED_POPULATION_DIR = PROCESSED_DIR / "population"

FEATURES_STATIC_DIR = FEATURES_DIR / "static"
FEATURES_DYNAMIC_DIR = FEATURES_DIR / "dynamic"
FEATURES_LABELS_DIR = FEATURES_DIR / "labels"

FEATURE_STORE_DAILY_DIR = FEATURE_STORE_DIR / "daily"
FEATURE_STORE_WEEKLY_DIR = FEATURE_STORE_DIR / "weekly"
FEATURE_STORE_SNAPSHOTS_DIR = FEATURE_STORE_DIR / "snapshots"

MODELING_TRAINING_DATA_DIR = MODELING_DIR / "training_data"
MODEL_INPUTS_DIR = MODELING_DIR / "model_inputs"
MODEL_OUTPUTS_DIR = MODELING_DIR / "model_outputs"
VALIDATION_SPLITS_DIR = MODELING_DIR / "validation_splits"

FORECASTS_DAILY_DIR = FORECASTS_DIR / "daily"
FORECASTS_WEEKLY_DIR = FORECASTS_DIR / "weekly"
FORECASTS_ARCHIVED_DIR = FORECASTS_DIR / "archived"
STATIC_MAPS_DIR = EXPORTS_DIR / "static_maps"
MAP_EXPORTS_DIR = EXPORTS_DIR / "maps"
EXPORTS_STATIC_MAPS_DIR = STATIC_MAPS_DIR
EXPORTS_MAPS_DIR = MAP_EXPORTS_DIR
CATALOG_TREE_SNAPSHOTS_DIR = CATALOG_DIR / "tree_snapshots"


def ensure_dir(path: str | Path) -> Path:
    """Create a directory and return it as a Path."""
    resolved = Path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def ensure_parent(path: str | Path) -> Path:
    """Create the parent directory for a file path and return it as a Path."""
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def resolve_existing_path(*candidate_paths: str | Path) -> Path:
    """Return the first existing path from transition candidates."""
    paths = [Path(path) for path in candidate_paths]
    for path in paths:
        if path.exists():
            return path
    raise FileNotFoundError(
        "None of the candidate paths exist: " + ", ".join(str(path) for path in paths)
    )


@dataclass(frozen=True)
class PathManager:
    """Central artifact layout for data, trained models, and forecast outputs.

    Layout conventions:
    - trained baseline models:   models/baselines/<run_id>[/<response_type>]/<model_name>
    - trained composite models:  models/composites/<run_id>/<composite_name>
    - weekly forecast outputs:   artifacts/forecasts/weekly/<run_id>/<group>/<model_name>
    - weekly feature stores:     data/feature_store/weekly/<run_id>/...
    - replay feature stores:     data/feature_store/weekly/<run_id>/replay/...

    Composite model artifacts previously lived under the baseline model tree at
    models/baselines/<run_id>/composites/<name>. Reads may still need to resolve that
    legacy location during migration, but new writes should use the explicit composite
    root above.
    """

    root: Path

    @classmethod
    def from_project_root(cls) -> "PathManager":
        return cls(root=project_root())

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "PathManager":
        root = config.get("base_directory")
        if root:
            return cls(root=Path(root).expanduser().resolve())
        return cls.from_project_root()

    @classmethod
    def from_repo_root(cls) -> "PathManager":
        warnings.warn(
            "PathManager.from_repo_root() is deprecated; use "
            "PathManager.from_project_root() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.from_project_root()

    def models_root(self) -> Path:
        return self.artifacts_root() / "models"

    def artifacts_root(self) -> Path:
        return (
            Path(os.environ.get("ORCACAST_ARTIFACT_ROOT", self.root / "artifacts"))
            .expanduser()
            .resolve()
        )

    def data_dir(self) -> Path:
        return Path(os.environ.get("ORCACAST_DATA_ROOT", self.root / "data")).expanduser().resolve()

    def raw_dir(self) -> Path:
        return self.data_dir() / "raw"

    def processed_dir(self) -> Path:
        return self.data_dir() / "processed"

    def features_root(self) -> Path:
        return self.artifacts_root() / "features"

    def feature_store_root(self) -> Path:
        return self.features_root() / "stores"

    def modeling_root(self) -> Path:
        return self.artifacts_root() / "modeling"

    def forecasts_root(self) -> Path:
        return self.artifacts_root() / "forecasts"

    def exports_root(self) -> Path:
        return (
            Path(os.environ.get("ORCACAST_OUTPUT_ROOT", self.root / "outputs"))
            .expanduser()
            .resolve()
        )

    def catalog_root(self) -> Path:
        return self.data_dir() / "catalog"

    def qa_root(self) -> Path:
        return self.data_dir() / "qa"

    def raw_population_dir(self) -> Path:
        return self.raw_dir() / "population"

    def raw_orca_population_dir(self) -> Path:
        return self.raw_dir() / "orca_population"

    def processed_population_dir(self) -> Path:
        return self.processed_dir() / "population"

    def features_static_dir(self) -> Path:
        return self.features_root() / "static"

    def features_dynamic_dir(self) -> Path:
        return self.features_root() / "dynamic"

    def features_labels_dir(self) -> Path:
        return self.features_root() / "labels"

    def feature_store_daily_dir(self) -> Path:
        return self.feature_store_root() / "daily"

    def feature_store_weekly_dir(self) -> Path:
        return self.feature_store_root() / "weekly"

    def feature_store_snapshots_dir(self) -> Path:
        return self.feature_store_root() / "snapshots"

    def modeling_training_data_dir(self) -> Path:
        return self.modeling_root() / "training_data"

    def model_inputs_dir(self) -> Path:
        return self.modeling_root() / "model_inputs"

    def model_outputs_dir(self) -> Path:
        return self.modeling_root() / "model_outputs"

    def validation_splits_dir(self) -> Path:
        return self.modeling_root() / "validation_splits"

    def forecasts_daily_dir(self) -> Path:
        return self.forecasts_root() / "daily"

    def forecasts_weekly_dir(self) -> Path:
        return self.forecasts_root() / "weekly"

    def forecasts_archived_dir(self) -> Path:
        return self.forecasts_root() / "archived"

    def static_maps_dir(self) -> Path:
        return self.exports_root() / "static_maps"

    def map_exports_dir(self) -> Path:
        return self.exports_root() / "maps"

    def catalog_source_registry_path(self) -> Path:
        return self.catalog_root() / "source_registry.yaml"

    def catalog_links_path(self) -> Path:
        return self.catalog_root() / "links.md"

    def catalog_tree_snapshots_dir(self) -> Path:
        return self.catalog_root() / "tree_snapshots"

    def srkw_population_workbook_path(self) -> Path:
        return self.raw_orca_population_dir() / "Number of Southern Resident killer whales.xlsx"

    def replay_store_dir(self, run_id: str) -> Path:
        return self.feature_store_weekly_dir() / run_id / "replay"

    def evaluation_root(self) -> Path:
        return self.artifacts_root() / "evaluation"

    def evaluation_run_dir(self, run_id: str) -> Path:
        return self.evaluation_root() / run_id

    # -------------------------
    # BASELINE MODELS
    # -------------------------
    def baseline_models_fit_dir(self, fit_run_id: str, response_type: str | None = None) -> Path:
        return self.baselines_run_dir(fit_run_id, response_type=response_type)

    def baselines_run_dir(self, run_id: str, response_type: str | None = None) -> Path:
        if response_type:
            return self.models_root() / "baselines" / run_id / response_type
        return self.models_root() / "baselines" / run_id

    def baseline_model_dir(
        self, run_id: str, model_name: str, response_type: str | None = None
    ) -> Path:
        return self.baselines_run_dir(run_id, response_type=response_type) / model_name

    def baseline_model_fit_dir(
        self, fit_run_id: str, model_name: str, response_type: str | None = None
    ) -> Path:
        return self.baseline_model_dir(
            run_id=fit_run_id, model_name=model_name, response_type=response_type
        )

    # -------------------------
    # COMPOSITE MODELS
    # -------------------------
    def composites_run_dir(self, run_id: str) -> Path:
        return self.models_root() / "composites" / run_id

    def composite_model_dir(self, run_id: str, composite_name: str) -> Path:
        return self.composites_run_dir(run_id) / composite_name

    def composite_model_fit_dir(self, fit_run_id: str, composite_name: str) -> Path:
        return self.composite_model_dir(run_id=fit_run_id, composite_name=composite_name)

    def legacy_composite_model_dir(self, run_id: str, composite_name: str) -> Path:
        return self.baselines_run_dir(run_id) / "composites" / composite_name

    def resolve_composite_model_dir(self, run_id: str, composite_name: str) -> Path:
        canonical = self.composite_model_dir(run_id=run_id, composite_name=composite_name)
        if canonical.exists():
            return canonical
        legacy = self.legacy_composite_model_dir(run_id=run_id, composite_name=composite_name)
        if legacy.exists():
            return legacy
        return canonical

    # -------------------------
    # FORECAST OUTPUTS
    # -------------------------
    def forecast_output_dir(self, forecast_run_id: str) -> Path:
        return self.forecast_run_dir(forecast_run_id)

    def baseline_forecasts_dir(self, run_id: str, response_type: str | None = None) -> Path:
        if response_type:
            return self.forecasts_root() / "weekly" / "baselines" / run_id / response_type
        return self.forecasts_weekly_dir() / "baselines" / run_id

    def composite_forecasts_dir(self, run_id: str) -> Path:
        return self.forecasts_weekly_dir() / "composites" / run_id

    def forecast_run_dir(self, run_id: str) -> Path:
        return self.forecasts_weekly_dir() / run_id

    def forecast_model_dir(self, run_id: str, model_type: str, model_name: str) -> Path:
        return self.forecast_run_dir(run_id) / model_type / model_name

    def forecast_parquet_path(self, run_id: str, model_type: str, model_name: str) -> Path:
        return self.forecast_model_dir(run_id, model_type, model_name) / "forecast.parquet"

    def forecast_manifest_path(self, run_id: str, model_type: str, model_name: str) -> Path:
        return self.forecast_model_dir(run_id, model_type, model_name) / "manifest.json"

    def forecast_bundle_dir(self, run_id: str, mode: str) -> Path:
        return self.forecast_run_dir(run_id) / "bundles" / mode

    def forecast_bundle_paths(self, run_id: str, mode: str) -> dict[str, Path]:
        bundle_dir = self.forecast_bundle_dir(run_id, mode)
        return {
            "dir": bundle_dir,
            "all_predictions": bundle_dir / "all_predictions.parquet",
            "registry": bundle_dir / "registry.json",
        }

    def legacy_forecasts_root(self) -> Path:
        return self.forecasts_archived_dir() / "_legacy"

    def legacy_baseline_forecasts_dir(self, run_id: str, response_type: str | None = None) -> Path:
        if response_type:
            return self.legacy_forecasts_root() / "baselines" / run_id / response_type
        return self.legacy_forecasts_root() / "baselines" / run_id

    def legacy_composite_forecasts_dir(self, run_id: str) -> Path:
        return self.legacy_forecasts_root() / "composites" / run_id

    def baseline_forecast_file(
        self, run_id: str, horizon_steps: int, response_type: str | None = None
    ) -> Path:
        return (
            self.baseline_forecasts_dir(run_id, response_type=response_type)
            / f"forecast_next_{horizon_steps}.parquet"
        )

    def baseline_per_row_file(self, run_id: str, response_type: str | None = None) -> Path:
        return (
            self.baseline_forecasts_dir(run_id, response_type=response_type)
            / "per_row_predictions.parquet"
        )
