"""Shared pipeline result, timing, and reporting utilities."""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, TextIO

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    """Result returned by country-specific pipeline implementations."""

    output_path: Path
    summary: dict[str, Any]
    debug_output_path: Path | None = None

    def __fspath__(self) -> str:
        return os.fspath(self.output_path)

    def __str__(self) -> str:
        return str(self.output_path)


@contextmanager
def timed_stage(name: str, *, logger: logging.Logger | None = None) -> Iterator[None]:
    """Log elapsed time around one pipeline stage."""
    active_logger = logger or LOGGER
    start = time.perf_counter()
    active_logger.info("%s started", name)
    try:
        yield
    except Exception:
        active_logger.exception(
            "%s failed after %.1fs",
            name,
            time.perf_counter() - start,
        )
        raise
    else:
        active_logger.info("%s completed in %.1fs", name, time.perf_counter() - start)


def print_summary(summary: dict[str, Any], *, stream: TextIO | None = None) -> None:
    """Emit a stable JSON summary at the CLI boundary."""
    text = json.dumps(summary, indent=2, sort_keys=True)
    if stream is None:
        print(text)
    else:
        stream.write(text + "\n")
