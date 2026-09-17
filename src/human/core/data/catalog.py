from __future__ import annotations

import pyarrow as pa

from .contracts import DatasetFormat, DatasetId, DatasetLayer, DatasetSpec, ProcessingMode
from .registry import DATASETS

def _register(
    dataset_id: str,
    layer: DatasetLayer,
    format: DatasetFormat,
    path: str,
    producer: str,
    *,
    dependencies: tuple[str, ...] = (),
    schema: pa.Schema | None = None,
    primary_key: tuple[str, ...] = (),
    partition_keys: tuple[str, ...] = (),
    modes: tuple[ProcessingMode, ...] | None = None,
    schema_version: str = "1",
) -> None:
    DATASETS.register(
        DatasetSpec(
            dataset_id=DatasetId(dataset_id),
            layer=layer,
            format=format,
            path_template=path,
            producer=producer,
            dependencies=tuple(DatasetId(item) for item in dependencies),
            schema=schema,
            primary_key=primary_key,
            partition_keys=partition_keys,
            schema_version=schema_version,
            allowed_modes=modes or (ProcessingMode.RETROSPECTIVE, ProcessingMode.AS_OF),
        )
    )


def register_builtin_datasets() -> None:
    for dataset_id, path, producer, primary_key in (
        (
            "human.population.us_h3_r7",
            "{data_root}/processed/domain/human/demography_and_presence/population/us_population_h3_r7.parquet",
            "human.demography_and_presence.population.build",
            ("h3",),
        ),
        (
            "human.population.canada_h3_r7",
            "{data_root}/processed/domain/human/demography_and_presence/population/canada_population_h3_r7.parquet",
            "human.demography_and_presence.population.build",
            ("h3",),
        ),
        (
            "human.population.cross_border_h3_r7",
            "{data_root}/processed/domain/human/demography_and_presence/population/cross_border_population_h3_r7.parquet",
            "human.demography_and_presence.population.build",
            ("COUNTRY_CODE", "H3_INDEX"),
        ),
        (
            "human.population.context_h3_r7",
            "{data_root}/processed/domain/human/demography_and_presence/population/population_context_h3_r7.parquet",
            "human.demography_and_presence.population.build",
            ("H3_INDEX",),
        ),
        (
            "human.calendar.daily",
            "{data_root}/processed/domain/human/temporal_context/calendar/calendar_daily.parquet",
            "human.temporal_context.calendar.build",
            ("date",),
        ),
        (
            "human.ais.daily_r6",
            "{data_root}/processed/domain/human/activity_and_effort/ais/ais_activity_daily_r6.parquet",
            "human.activity_and_effort.ais.build",
            ("DATE", "H3_INDEX"),
        ),
        (
            "human.ais.weekly_r6",
            "{data_root}/processed/domain/human/activity_and_effort/ais/ais_activity_weekly_r6.parquet",
            "human.activity_and_effort.ais.build",
            ("WEEK_START", "H3_INDEX"),
        ),
        (
            "human.ferry.route_daily_r7",
            "{data_root}/processed/domain/human/activity_and_effort/ferry/ferry_effort_daily_by_route_r7.parquet",
            "human.activity_and_effort.ferry.build",
            ("service_date", "route_key", "source_h3"),
        ),
        (
            "human.ferry.daily_r7",
            "{data_root}/processed/domain/human/activity_and_effort/ferry/ferry_effort_daily_r7.parquet",
            "human.activity_and_effort.ferry.build",
            ("service_date", "source_h3"),
        ),
        (
            "human.ferry.weekly_r6",
            "{data_root}/processed/domain/human/activity_and_effort/ferry/ferry_effort_weekly_r6.parquet",
            "human.activity_and_effort.ferry.build",
            ("week_start", "source_h3"),
        ),
        (
            "human.accessibility.boat_launch_access.facilities_r7",
            "{data_root}/processed/domain/human/accessibility/boat_launch_access/boat_launch_facilities_r7.parquet",
            "human.accessibility.boat_launch_access.build",
            ("ACCESS_SITE_ID",),
        ),
        (
            "human.accessibility.boat_launch_access.h3_r7",
            "{data_root}/processed/domain/human/accessibility/boat_launch_access/boat_launch_access_h3_r7.parquet",
            "human.accessibility.boat_launch_access.build",
            ("H3_INDEX",),
        ),
        (
            "human.accessibility.public_shore_access.facilities_r7",
            "{data_root}/processed/domain/human/accessibility/public_shore_access/public_shore_facilities_r7.parquet",
            "human.accessibility.public_shore_access.build",
            ("ACCESS_SITE_ID",),
        ),
        (
            "human.accessibility.public_shore_access.h3_r7",
            "{data_root}/processed/domain/human/accessibility/public_shore_access/public_shore_access_h3_r7.parquet",
            "human.accessibility.public_shore_access.build",
            ("H3_INDEX",),
        ),
    ):
        _register(
            dataset_id,
            DatasetLayer.DOMAIN,
            DatasetFormat.PARQUET,
            path,
            producer,
            primary_key=primary_key,
            schema_version="1",
        )
    _register(
        "human.places.catalog",
        DatasetLayer.DOMAIN,
        DatasetFormat.JSON,
        "{data_root}/processed/domain/human/demography_and_presence/places/places_of_interest.json",
        "human.demography_and_presence.places.build",
        schema_version="1",
    )


    _register(
        "human.viewshed.static",
        DatasetLayer.DOMAIN,
        DatasetFormat.DIRECTORY,
        "{data_root}/processed/domain/human/viewshed/RES7",
        "human.viewshed",
        schema_version="2",
    )
    for source_type in ("land", "water"):
        _register(
            f"human.viewshed.static_{source_type}_r7",
            DatasetLayer.DOMAIN,
            DatasetFormat.PARQUET,
            "{data_root}/processed/domain/human/viewshed/RES7/"
            f"{source_type.upper()}_STATIC_WEIGHTS_R7.parquet",
            "human.viewshed",
            dependencies=("human.viewshed.static",),
            primary_key=("source_h3", "target_h3"),
            schema_version="2",
        )



register_builtin_datasets()
