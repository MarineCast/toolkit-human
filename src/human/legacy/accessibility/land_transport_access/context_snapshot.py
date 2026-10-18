"""Pin adopted population/city context independently of notebook output paths."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from human.utils.artifacts import atomic_write_json, sha256_file


def snapshot(root: Path, output: Path, *, od_metadata: Path) -> Path:
    expected = json.loads(od_metadata.read_text())
    sources = {
        "population_origins.parquet": (
            root
            / "outputs/effort/land_source_context/population_travel_time/population_travel_origins_h3_r4_prototype.parquet",
            "origin_sha256",
        ),
        "city_origins.metadata.json": (
            root
            / "outputs/effort/land_source_context/transport_access/land_source_transport_access_h3_r7_prototype.metadata.json",
            "city_origins_sha256",
        ),
    }
    for source, key in sources.values():
        if sha256_file(source) != expected[key]:
            raise ValueError(f"Origin context changed since OD evaluation: {source}")
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for name, (source, _) in sources.items():
        target = output / name
        shutil.copy2(source, target)
        records.append(
            {
                "path": str(target.resolve()),
                "sha256": sha256_file(target),
                "adopted_from": str(source),
            }
        )
    return atomic_write_json(
        output / "manifest.json",
        {
            "status": "versioned_adopted_contemporary_origin_context",
            "od_metadata": str(od_metadata.resolve()),
            "od_metadata_sha256": sha256_file(od_metadata),
            "artifacts": records,
            "limitation": "Origin selection/centroids are a pinned adopted population snapshot, not newly reconstructed population geography.",
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--od-metadata", type=Path, required=True)
    args = parser.parse_args()
    print(snapshot(args.root, args.output, od_metadata=args.od_metadata))


if __name__ == "__main__":
    main()
