"""Read-only freshness and support checks for the active land generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import DEFAULT_CONFIG_PATH, load_land_reporting_config
from .modeling import load_weekly


def check(
    config: str | Path = DEFAULT_CONFIG_PATH,
    *,
    as_of: str | None = None,
    max_age_days: int = 14,
    previous_support_fraction: float | None = None,
) -> dict:
    cfg = load_land_reporting_config(config)
    frame, manifest = load_weekly(
        cfg.manifest_path.parent / cfg.weekly_output_path.name,
        expected_config_hash=cfg.human.config_hash,
    )
    complete = frame.loc[frame.PERIOD_DAY_COUNT.eq(7)]
    latest = complete.WEEK_START.max()
    reference = (
        pd.Timestamp(as_of) if as_of else pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    )
    age = int((reference - (latest + pd.Timedelta(days=6))).days)
    current = complete.loc[complete.WEEK_START.eq(latest)]
    fraction = float(current.LAND_EFFORT_PROXY_RAW.notna().mean())
    change = fraction - previous_support_fraction if previous_support_fraction is not None else None
    return {
        "generation_id": manifest.get("generation_id", "legacy_flat"),
        "valid_contract": True,
        "routing_policy": manifest.get("routing_policy", "legacy_routing_unverified"),
        "latest_complete_week": str(latest.date()),
        "age_days": age,
        "fresh": 0 <= age <= max_age_days,
        "mapped_proxy_supported_fraction": fraction,
        "support_fraction_change": change,
        "coverage_alert": change is not None and abs(change) > 0.05,
        "source_completeness": manifest["source_completeness"],
        "production_promoted": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--as-of")
    parser.add_argument("--max-age-days", type=int, default=14)
    parser.add_argument("--previous-support-fraction", type=float)
    args = parser.parse_args()
    print(
        json.dumps(
            check(
                args.config,
                as_of=args.as_of,
                max_age_days=args.max_age_days,
                previous_support_fraction=args.previous_support_fraction,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
