"""Dynamic output-column contracts for water-distance population features."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from decimal import Decimal

DISTANCE_COLUMNS = [
    "distance_to_water_m",
    "distance_to_water_miles",
    "distance_to_water_capped_miles",
]


def format_distance_label(distance_miles: float) -> str:
    """Return a stable, column-safe label for a positive distance value."""
    value = float(distance_miles)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"Distance thresholds must be finite and positive, got {distance_miles!r}."
        )
    text = format(Decimal(str(value)).normalize(), "f")
    return text.replace(".", "_")


def threshold_feature_columns(thresholds_miles: Iterable[float]) -> list[str]:
    """Return threshold feature columns in the established contract order."""
    labels = [format_distance_label(value) for value in thresholds_miles]
    return (
        [f"within_{label}mi_water" for label in labels]
        + [f"water_proximity_weight_{label}mi" for label in labels]
        + [f"water_weighted_population_{label}mi" for label in labels]
    )


def water_feature_columns(thresholds_miles: Iterable[float]) -> list[str]:
    """Return all water-distance columns in output order."""
    return [*DISTANCE_COLUMNS, *threshold_feature_columns(thresholds_miles), "water_distance_basis"]


def build_output_columns(
    leading_columns: Sequence[str],
    thresholds_miles: Iterable[float],
    trailing_columns: Sequence[str],
) -> list[str]:
    """Build a complete output contract from identity, water, and provenance columns."""
    columns = [*leading_columns, *water_feature_columns(thresholds_miles), *trailing_columns]
    duplicates = sorted({column for column in columns if columns.count(column) > 1})
    if duplicates:
        raise ValueError(f"Output contract contains duplicate columns: {duplicates}")
    return columns
