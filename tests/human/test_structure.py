from __future__ import annotations

from pathlib import Path

from human.core.config.paths import project_root

ACTIVE = (
    "demography_and_presence/population",
    "demography_and_presence/places",
    "temporal_context/calendar",
    "activity_and_effort/ais",
    "activity_and_effort/ferry",
    "activity_and_effort/land_reporting_opportunity",
    "activity_and_effort/water_observation_opportunity",
    "accessibility/public_shore_access",
    "accessibility/boat_launch_access",
)
PLANNED = (
    "demography_and_presence/seasonal_population_pressure",
    "activity_and_effort/observer_effort",
    "activity_and_effort/fishing_activity",
    "accessibility/land_transport_access",
    "accessibility/population_travel_time",
    "tourism_and_recreation/park_visitation",
    "tourism_and_recreation/whale_watch_activity",
    "tourism_and_recreation/recreational_boating",
    "tourism_and_recreation/tourism_pressure",
    "exposure/vessel_acoustic_exposure",
)


def test_human_family_structure_and_authoritative_todo() -> None:
    root = project_root() / "src/human"
    todo = (root / "TODO.txt").read_text(encoding="utf-8")
    required = {"download.py", "build.py", "inspect.py", "config.py", "DATA_SOURCES.md"}
    assert len(ACTIVE) + len(PLANNED) == 19
    assert len(PLANNED) == 10
    for relative in ACTIVE:
        family = root / relative
        assert family.is_dir()
        assert required.issubset({path.name for path in family.iterdir()})
    for relative in PLANNED:
        family = root / relative
        assert family.is_dir()
        implemented_files = [
            path for path in family.iterdir() if path.is_file() and path.name != ".gitkeep"
        ]
        if implemented_files:
            assert not (family / ".gitkeep").exists()
        else:
            assert (family / ".gitkeep").is_file()
        assert f"Path: `{relative}/`" in todo
    assert list(root.rglob("TODO.txt")) == [root / "TODO.txt"]
    for legacy in ("population", "places", "calendar", "ais", "tourism"):
        assert not (root / legacy).exists()


def test_active_configs_mirror_package_hierarchy() -> None:
    root = project_root()
    for relative in ACTIVE:
        config = root / "config/data/human" / f"{relative}.yaml"
        assert config.is_file()
