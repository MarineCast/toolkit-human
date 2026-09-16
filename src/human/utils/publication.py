"""Checksum-verified atomic publication helpers for human artifacts."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .artifacts import sha256_file


def publish_file(
    source: str | Path,
    destination: str | Path,
    *,
    overwrite: bool = False,
    expected_sha256: str | None = None,
) -> Path:
    """Atomically publish one immutable artifact after verifying its checksum."""
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Human publication source does not exist: {source_path}")
    source_sha256 = sha256_file(source_path)
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise ValueError(f"Human publication source checksum mismatch: {source_path}")

    output = Path(destination)
    if output.exists() and not overwrite:
        raise FileExistsError(f"Published artifact already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.part")
    try:
        shutil.copy2(source_path, temporary)
        if sha256_file(temporary) != source_sha256:
            raise ValueError(f"Human publication copy checksum mismatch: {temporary}")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
