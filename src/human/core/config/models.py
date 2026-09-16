from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RootConfig(StrictConfig):
    data: Path = Path("data")
    artifacts: Path = Path("artifacts")
    outputs: Path = Path("outputs")


class SpatialConfig(StrictConfig):
    h3_resolution: int = Field(default=7, ge=0, le=15)
    crs: str = "EPSG:4326"


class TemporalConfig(StrictConfig):
    timezone: Literal["UTC"] = "UTC"
    frequency: Literal["daily", "weekly"] = "weekly"


class ProjectInfo(StrictConfig):
    name: str = "toolkit-human"
    python: str = "3.11"


class ProjectConfig(StrictConfig):
    schema_version: int = 1
    project: ProjectInfo = ProjectInfo()
    roots: RootConfig = RootConfig()
    spatial: SpatialConfig = SpatialConfig()
    temporal: TemporalConfig = TemporalConfig()
    configs: dict[str, Path] = Field(default_factory=dict)


class SourceConfig(StrictConfig):
    enabled: bool = True
    url: HttpUrl | None = None
    supplied_path: Path | None = None
    credential_env: str | None = None
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def has_location(self) -> "SourceConfig":
        if self.enabled and self.url is None and self.supplied_path is None:
            raise ValueError("Enabled source requires url or supplied_path")
        return self


class CollectionConfig(StrictConfig):
    sources: dict[str, SourceConfig]








class PopulationConfig(StrictConfig):
    country: Literal["us", "canada"]
    census_vintage: int = Field(ge=1900)
    h3_resolution: int = Field(default=7, ge=0, le=15)
    allocation_method: str = "area_weighted"
    parameters: dict[str, Any] = Field(default_factory=dict)


class ViewshedConfig(StrictConfig):
    area: str = "model_area"
    h3_resolution: int = Field(default=7, ge=0, le=15)
    resolution_m: int = Field(default=30, gt=0)
    max_distance_m: float = Field(default=30000, gt=0)
    observer_eye_height_m: float = Field(default=1.7, gt=0)
    target_height_m: float = Field(default=1.0, ge=0)
    parameters: dict[str, Any] = Field(default_factory=dict)


def resolve_project_paths(config: ProjectConfig, project_root: Path) -> ProjectConfig:
    values = config.model_dump()
    roots = values["roots"]
    for key, raw in roots.items():
        path = Path(raw)
        roots[key] = path if path.is_absolute() else (project_root / path).resolve()
    values["configs"] = {
        key: value if Path(value).is_absolute() else (project_root / value).resolve()
        for key, value in values["configs"].items()
    }
    return ProjectConfig.model_validate(values)
