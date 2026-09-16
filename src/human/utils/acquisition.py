"""Acquisition helpers for immutable human source snapshots."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import requests


def materialize_source(
    source: str | Path,
    destination: str | Path,
    *,
    overwrite: bool = False,
    timeout_seconds: int = 180,
) -> Path:
    """Copy or download one source into an immutable configured destination."""
    output = Path(destination)
    if output.exists() and not overwrite:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.part")
    value = str(source)
    if value.startswith(("https://", "http://")):
        with requests.get(value, stream=True, timeout=timeout_seconds) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
    else:
        local = Path(source).expanduser().resolve()
        if not local.is_file():
            raise FileNotFoundError(f"Configured human source does not exist: {local}")
        shutil.copy2(local, temporary)
    os.replace(temporary, output)
    return output
