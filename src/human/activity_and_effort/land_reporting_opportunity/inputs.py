"""Read exact inventoried bytes; never discover extra partitions while consuming."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pandas as pd

from human.utils.artifacts import sha256_file


def _inventory(cfg):
    content = cfg.input_inventory_path.read_bytes()
    raw = json.loads(cfg.raw_manifest_path.read_text())
    matching = [
        a
        for a in raw["artifacts"]
        if Path(a["path"]).resolve() == cfg.input_inventory_path.resolve()
    ]
    identity = hashlib.sha256(content).hexdigest()
    if len(matching) != 1 or matching[0]["sha256"] != identity:
        raise ValueError("Land input inventory is not the manifest-pinned snapshot")
    return json.loads(content)["inputs"], identity


def _bytes(item):
    content = Path(item["path"]).read_bytes()
    if hashlib.sha256(content).hexdigest() != item["sha256"]:
        raise ValueError(f"Pinned input changed while reading: {item['path']}")
    return content


def read_pinned_bytes(cfg, name):
    inputs, _ = _inventory(cfg)
    return _bytes(inputs[name])


def read_pinned_json(cfg, name):
    inputs, _ = _inventory(cfg)
    return json.loads(_bytes(inputs[name]))


def read_pinned_parquet(cfg, prefix, columns=None, *, exact=False):
    inputs, _ = _inventory(cfg)
    selected = [
        item
        for name, item in sorted(inputs.items())
        if (name == prefix if exact else name.startswith(prefix))
    ]
    if not selected:
        raise ValueError(f"No pinned Parquet input for {prefix}; rerun input inventory")
    # Hash exactly the bytes subsequently decoded. A path rewrite after this
    # point cannot alter the consumed table, and unlisted files are excluded.
    return pd.concat(
        [
            pd.read_parquet(
                io.BytesIO(_bytes(item)), columns=None if columns is None else list(columns)
            )
            for item in selected
        ],
        ignore_index=True,
    )


def validate_pinned_inputs(cfg, *, expected_identity=None):
    inputs, identity = _inventory(cfg)
    if expected_identity is not None and expected_identity != identity:
        raise ValueError("Land input inventory changed during the build")
    for item in inputs.values():
        if sha256_file(item["path"]) != item["sha256"]:
            raise ValueError(f"Land input inventory checksum is stale: {item['path']}")
    for prefix, directory in (
        ("weather_partition_", cfg.surface_weather_path),
        ("daylight_partition_", cfg.daylight_path),
    ):
        expected = {
            Path(item["path"]).resolve() for name, item in inputs.items() if name.startswith(prefix)
        }
        current = {path.resolve() for path in directory.rglob("*.parquet")}
        if not expected or expected != current:
            raise ValueError(f"Dynamic partition set changed: {directory}; rerun input inventory")
    return identity
