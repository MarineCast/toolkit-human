"""Shared, calendar-keyed land features for retrospective and shadow models.

Only raw dynamic values enter models. Transformations belong to the estimator's
training pipeline, never to this artifact adapter. Contemporary static inputs
make these features retrospective context, not operational historical replay.
"""

from __future__ import annotations

from pathlib import Path

import h3
import numpy as np
import pandas as pd

from human.utils.artifacts import load_manifest, sha256_file
from human.utils.config import HumanConfig

from .dynamic import STREAM_COMPONENTS

FEATURE_CONTRACT_VERSION = 3
RAW_COLUMNS = {f"{stream}_RAW": f"land_{stream.lower()}_raw" for stream, _ in STREAM_COMPONENTS}
COVERAGE_COLUMNS = {
    "LAND_EFFORT_PROXY_DYNAMIC_CONTEXT_COVERAGE": "land_dynamic_coverage",
    "LAND_EFFORT_PROXY_STATIC_CONTEXT_FRACTION": "land_mapped_access_support",
    "VERIFIED_LAND_EFFORT_PROXY_STATIC_CONTEXT_FRACTION": "land_verified_access_support",
    "LAND_REACHABILITY_OPPORTUNITY_STATIC_CONTEXT_FRACTION": "land_routing_support",
    "LAND_EFFORT_PROXY_AVAILABLE_DAY_COUNT": "land_available_days",
    "POPULATION_ORIGIN_EVALUATED_COVERAGE": "land_population_evaluated_fraction",
}
FORMULATIONS = ("baseline", "coverage", "composite", "components", "combined")
LAG_PREFIXES = ("previous_week_", "rolling_4_prior_", "rolling_13_prior_")
COMPOSITES = {RAW_COLUMNS["LAND_EFFORT_PROXY_RAW"], RAW_COLUMNS["VERIFIED_LAND_EFFORT_PROXY_RAW"]}


def resolve_weekly(path: Path) -> tuple[Path, dict]:
    """Resolve and validate one generation, including upstream input identities."""
    manifest_path = path.parent / "manifest.json"
    manifest = load_manifest(manifest_path)
    if manifest.get("generation_id"):
        parents = {Path(a["path"]).parent for a in manifest["artifacts"]}
        if len(parents) != 1 or next(iter(parents)).name != manifest["generation_id"]:
            raise ValueError("Mixed land artifact generations")
    matches = [a for a in manifest["artifacts"] if a["dataset_id"].endswith(".weekly_h3_r6")]
    if len(matches) != 1:
        raise ValueError("Land generation must contain exactly one weekly artifact")
    for item in manifest["inputs"]:
        source = Path(item["path"])
        if sha256_file(source) != item["sha256"]:
            raise ValueError(f"Stale land input checksum: {source}")
    return Path(matches[0]["path"]), manifest


def validate_weekly(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "WEEK_START",
        "H3_INDEX",
        "H3_RESOLUTION",
        "PERIOD_DAY_COUNT",
        *RAW_COLUMNS,
        *COVERAGE_COLUMNS,
    }
    # Legacy snapshots did not preserve OD evaluation coverage; never infer it.
    frame = frame.copy()
    if "POPULATION_ORIGIN_EVALUATED_COVERAGE" not in frame:
        frame["POPULATION_ORIGIN_EVALUATED_COVERAGE"] = np.nan
    missing = required - set(frame)
    if missing:
        raise ValueError(f"Weekly land artifact missing columns: {sorted(missing)}")
    result = frame.copy()
    result["WEEK_START"] = pd.to_datetime(result["WEEK_START"], errors="raise")
    if (
        result[["WEEK_START", "H3_INDEX"]].isna().any().any()
        or result.duplicated(["WEEK_START", "H3_INDEX"]).any()
    ):
        raise ValueError("Weekly land keys must be non-null and unique")
    if not result["WEEK_START"].dt.weekday.eq(0).all():
        raise ValueError("Weekly land dates must be Mondays")
    if not result["H3_RESOLUTION"].eq(6).all() or any(
        not h3.is_valid_cell(cell) or h3.get_resolution(cell) != 6
        for cell in result["H3_INDEX"].unique()
    ):
        raise ValueError("Land modeling requires valid R6 cells")
    if not result["PERIOD_DAY_COUNT"].between(1, 7).all():
        raise ValueError("Invalid land period day count")
    for column in COVERAGE_COLUMNS:
        values = pd.to_numeric(result[column], errors="raise")
        upper = 7 if column.endswith("_DAY_COUNT") else 1
        invalid = values.notna() & (~np.isfinite(values) | ~values.between(0, upper))
        if column.endswith("_DAY_COUNT"):
            invalid |= values.notna() & values.mod(1).ne(0)
        if invalid.any():
            raise ValueError(f"Invalid land coverage: {column}")
    for column in RAW_COLUMNS:
        values = pd.to_numeric(result[column], errors="raise")
        if ((values < 0) | np.isinf(values)).any():
            raise ValueError(f"Invalid land raw opportunity: {column}")
        # Partial sums are not comparable to a complete weekly opportunity.
        days = f"{column.removesuffix('_RAW')}_AVAILABLE_DAY_COUNT"
        if days in result:
            result.loc[result[days].ne(7), column] = np.nan
        result.loc[result["PERIOD_DAY_COUNT"].ne(7), column] = np.nan
    return result


def load_weekly(
    path: Path,
    *,
    expected_config_hash: str | None = None,
    allow_access_conditioned: bool = False,
    research_adapter: str | None = None,
) -> tuple[pd.DataFrame, dict]:
    resolved, manifest = resolve_weekly(path)
    from .schema import validate_land_schema

    adapter = research_adapter or ("land_access_v4" if allow_access_conditioned else None)
    validate_land_schema(manifest, research_adapter=adapter)
    if expected_config_hash is None:
        expected_config_hash = HumanConfig.load(manifest["config_path"]).config_hash
    if expected_config_hash is not None and manifest["config_hash"] != expected_config_hash:
        raise ValueError("Land feature configuration does not match the requested generation")
    frame = validate_weekly(pd.read_parquet(resolved))
    frame.attrs["knowledge_time_contract"] = "retrospective_contemporary_static_context"
    frame.attrs["artifact_available_at_utc"] = manifest["build_time_utc"]
    return frame, manifest


def calendar_lags(frame: pd.DataFrame, *, group: str | None = None) -> pd.DataFrame:
    """Lag on a full Monday calendar, including the next forecast week.

    Rolling windows are calendar windows; counts expose incomplete histories.
    No same-week value or future-period scaling is used.
    """
    if frame.empty:
        return frame.copy()
    keys = ["week_start"] + ([group] if group else [])
    if frame.duplicated(keys).any():
        raise ValueError("Duplicate feature calendar keys")
    start, end = pd.to_datetime(frame.week_start).min(), pd.to_datetime(frame.week_start).max()
    weeks = pd.date_range(start, end + pd.Timedelta(weeks=1), freq="W-MON")
    parts = []
    groups = frame.groupby(group, sort=True) if group else [(None, frame)]
    for name, piece in groups:
        piece = piece.set_index("week_start").drop(columns=[group] if group else []).reindex(weeks)
        output = {"week_start": weeks}
        if group:
            output[group] = name
        for column in piece:
            prior = pd.to_numeric(piece[column], errors="raise").shift(1)
            output[f"previous_week_{column}"] = prior.to_numpy()
            for window in (4, 13):
                rolling = prior.rolling(window, min_periods=1)
                output[f"rolling_{window}_prior_{column}"] = rolling.mean().to_numpy()
                output[f"rolling_{window}_prior_{column}_weeks_available"] = (
                    rolling.count().to_numpy()
                )
        parts.append(pd.DataFrame(output))
    return pd.concat(parts, ignore_index=True)


def prepare_features(frame: pd.DataFrame, cells: list[str], *, regional: bool) -> pd.DataFrame:
    """Use a fixed support denominator; never treat missing cells as known zero."""
    frame = validate_weekly(frame)
    cells = sorted(set(cells))
    if not cells:
        raise ValueError("Empty land model support")
    weeks = pd.date_range(
        max(pd.Timestamp("2020-01-06"), frame.WEEK_START.min()),
        frame.WEEK_START.max(),
        freq="W-MON",
    )
    keys = pd.MultiIndex.from_product([weeks, cells], names=["week_start", "h3"])
    selected = frame.rename(
        columns={"WEEK_START": "week_start", "H3_INDEX": "h3", **RAW_COLUMNS, **COVERAGE_COLUMNS}
    )
    values = list(RAW_COLUMNS.values()) + list(COVERAGE_COLUMNS.values())
    dense = selected.set_index(["week_start", "h3"])[values].reindex(keys).reset_index()
    dense["land_supported_fraction"] = (
        dense[RAW_COLUMNS["LAND_EFFORT_PROXY_RAW"]].notna().astype(float)
    )
    if regional:
        # Fixed-denominator observed lower bound. all-missing remains null.
        by_week = dense.groupby("week_start")
        aggregated = by_week[list(RAW_COLUMNS.values())].sum(min_count=1) / len(cells)
        coverage = by_week[[*COVERAGE_COLUMNS.values(), "land_supported_fraction"]].mean()
        dense = aggregated.join(coverage).reset_index()
    lagged = calendar_lags(dense, group=None if regional else "h3")
    lagged["prior_land_effort_available"] = (
        lagged[f"previous_week_{RAW_COLUMNS['LAND_EFFORT_PROXY_RAW']}"].notna().astype(int)
    )
    return lagged


def feature_columns(frame: pd.DataFrame, formulation: str) -> list[str]:
    if formulation not in FORMULATIONS:
        raise ValueError(f"Unknown land formulation: {formulation}")
    if formulation == "baseline":
        return []
    measures = set(COVERAGE_COLUMNS.values()) | {"land_supported_fraction"}
    if formulation in {"composite", "combined"}:
        measures |= COMPOSITES
    if formulation in {"components", "combined"}:
        measures |= set(RAW_COLUMNS.values()) - COMPOSITES
    allowed = {prefix + measure for prefix in LAG_PREFIXES for measure in measures}
    allowed |= {
        f"{prefix}{measure}_weeks_available" for prefix in LAG_PREFIXES[1:] for measure in measures
    }
    allowed.add("prior_land_effort_available")
    return [column for column in frame if column in allowed]


def shadow_predictions(
    features: pd.DataFrame, *, formulation: str, incumbent: np.ndarray, candidate: np.ndarray
) -> pd.DataFrame:
    """Explicit candidate/served values; no promotion or stale-input rescue here."""
    if len(features) != len(incumbent) or len(features) != len(candidate):
        raise ValueError("Shadow predictions must align with feature rows")
    columns = [f"previous_week_{value}" for value in RAW_COLUMNS.values()]
    if formulation == "composite":
        columns = [f"previous_week_{RAW_COLUMNS['LAND_EFFORT_PROXY_RAW']}"]
    elif formulation == "components":
        columns = [
            column for column in columns if column.removeprefix("previous_week_") not in COMPOSITES
        ]
    available = features.reindex(columns=columns).notna().any(axis=1).to_numpy()
    available = available & np.isfinite(candidate)
    return pd.DataFrame(
        {
            "incumbent_prediction": incumbent,
            "shadow_prediction": candidate,
            "candidate_with_fallback": np.where(available, candidate, incumbent),
            "served_prediction": incumbent,
            "effort_available": available,
            "routing_status": np.where(available, "shadow_only", "incumbent_fallback"),
        },
        index=features.index,
    )
