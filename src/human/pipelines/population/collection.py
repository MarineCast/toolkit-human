"""Plan and prepare original census inputs for the complete Human study neighborhoods."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import geopandas as gpd

from human.utils.study import load_static_study
from human.pipelines.population.static.collection import collection_footprint
from human.pipelines.population.static.census import prepare_census_sources
from human.pipelines.population.static.sources import checksum


def prepare_study_sources(*, study_config, workspace, specification, mode="offline", plan_only=False):
    study = load_static_study(study_config)
    collection = collection_footprint(study["cells"], study["identity"]["h3_resolution"])
    identity = hashlib.sha256(json.dumps(collection.metadata, sort_keys=True).encode()).hexdigest()
    directory = Path(workspace).resolve()/"collection"/identity
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/"footprint.parquet"
    if path.exists():
        frame = gpd.read_parquet(path)
        if len(frame) != 1 or frame.crs is None or frame.crs.to_epsg() != 4326 or not frame.geometry.iloc[0].equals_exact(collection.geometry, 0):
            raise ValueError("Existing collection footprint differs")
    else:
        temporary = path.with_suffix(".parquet.tmp")
        gpd.GeoDataFrame(geometry=[collection.geometry], crs=4326).to_parquet(temporary, index=False)
        temporary.replace(path)
    report = {**collection.metadata, "study": study["identity"],
        "footprint_artifact": {"path": str(path), "sha256": checksum(path)},
        "mode": mode, "source_manifest": None}
    if not plan_only:
        spec_path = Path(specification)
        spec_sha = checksum(spec_path)
        manifest = prepare_census_sources(workspace=workspace,
            source_extent=collection.metadata["source_extent_wgs84"],
            specification=json.loads(spec_path.read_text()), mode=mode, collection=collection)
        if checksum(spec_path) != spec_sha:
            raise ValueError("Source specification changed during preparation")
        report.update({"source_manifest": str(manifest), "source_manifest_sha256": checksum(manifest),
            "source_specification": {"path": str(spec_path.resolve()), "sha256": spec_sha}})
    if load_static_study(study_config)["identity"] != study["identity"]:
        raise ValueError("Shared study changed during collection preparation")
    target = directory/("collection-plan.only.json" if plan_only else "collection-plan.json")
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    temporary.replace(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--specification", type=Path, required=True)
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    print(prepare_study_sources(**vars(args)))


if __name__ == "__main__":
    main()
