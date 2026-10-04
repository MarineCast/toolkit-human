"""Explicit workspace initialization and family pipeline commands."""
from __future__ import annotations
import argparse
import importlib
from importlib.resources import files
import os
from pathlib import Path

FAMILIES = {'calendar': 'temporal_context.calendar', 'public-shore-access': 'accessibility.public_shore_access', 'land-transport-access': 'accessibility.land_transport_access', 'boat-launch-access': 'accessibility.boat_launch_access', 'population-travel-time': 'accessibility.population_travel_time', 'places': 'demography_and_presence.places', 'population': 'demography_and_presence.population', 'land-reporting-opportunity': 'activity_and_effort.land_reporting_opportunity', 'ais': 'activity_and_effort.ais', 'observer-effort': 'activity_and_effort.observer_effort', 'water-observation-opportunity': 'activity_and_effort.water_observation_opportunity', 'ferry': 'activity_and_effort.ferry'}

def initialize_workspace(root: Path) -> list[Path]:
    """Copy packaged defaults without replacing existing user configuration."""
    created = []
    def visit(source, relative: Path):
        for item in sorted(source.iterdir(), key=lambda item: item.name):
            target = root / relative / item.name
            if item.is_dir():
                visit(item, relative / item.name)
            elif not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    handle.write(item.read_bytes())
                created.append(target)
    visit(files("human").joinpath("resources/config"), Path("config"))
    return created



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, help="Config/data root; defaults to HUMAN_WORKSPACE or cwd.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Copy packaged defaults without overwriting files.")
    commands.add_parser("families", help="List implemented product families.")
    for action in ("build", "download", "inspect"):
        sub = commands.add_parser(action)
        sub.add_argument("family", choices=[family for family in FAMILIES
            if action != "download" or family != "observer-effort"])
        sub.add_argument("arguments", nargs=argparse.REMAINDER)
    matrix = commands.add_parser("export-static-matrix", help="Export native static H3 R7 components.")
    matrix.add_argument("--manifest", type=Path, action="append", required=True)
    matrix.add_argument("--output", type=Path, required=True)
    matrix.add_argument("--input-manifest", type=Path, help="Checksum-verified supplemental inputs.")
    for action in ("audit-manifests", "publish-static-snapshot"):
        sub = commands.add_parser(action)
        sub.add_argument("--manifest", type=Path, action="append", required=True)
        sub.add_argument("--output", type=Path, required=True)
        if action == "publish-static-snapshot":
            sub.add_argument("--code-sha", required=True)
            sub.add_argument("--input-manifest", type=Path)
            sub.add_argument("--record-count-profile", action="store_true", help="Include the narrow retained-record counts application profile.")
    verify = commands.add_parser("verify-static-snapshot")
    verify.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    if args.command in ("audit-manifests", "publish-static-snapshot", "verify-static-snapshot"):
        from human.release import audit, publish_static, verify_static
        import json
        if args.command == "audit-manifests":
            report = audit(args.manifest)
            with args.output.open("x") as handle:
                handle.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
            return 0 if report["integrity_passed"] else 1
        if args.command == "publish-static-snapshot":
            print(publish_static(args.manifest, args.output, code_sha=args.code_sha,
                                 input_manifest=args.input_manifest, record_count_profile=args.record_count_profile))
        else:
            print(verify_static(args.path)["data_release_id"])
        return 0
    if args.command == "export-static-matrix":
        from human.static_matrix import export
        print(export(args.manifest, args.output, args.input_manifest))
        return 0
    if args.workspace:
        os.environ["HUMAN_WORKSPACE"] = str(args.workspace.expanduser().resolve())
    from human.core.config.paths import project_root
    if args.command == "init":
        print(f"Created {len(initialize_workspace(project_root()))} configuration files in {project_root()}")
        return 0
    if args.command == "families":
        print("\n".join(sorted(FAMILIES)))
        return 0
    module = importlib.import_module(f"human.{FAMILIES[args.family]}.{args.command}")
    return module.main(args.arguments)
