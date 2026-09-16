"""Command-line entrypoint for country population-to-H3 pipelines."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .common.pipeline import print_summary


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Build Census population H3 water-distance features."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the canonical human population YAML.",
    )
    parser.add_argument(
        "--country",
        choices=["us", "canada"],
        default="us",
        help="Country pipeline to run.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite downloads, caches, and outputs.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write optional debug GeoParquet and verbose logs.",
    )
    parser.add_argument(
        "--skip-if-missing-inputs",
        action="store_true",
        help=(
            "Return success when optional country inputs are unavailable. "
            "Intended for aggregate Make targets."
        ),
    )
    return parser


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries from a local .env file if present."""
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def find_project_root() -> Path:
    """Find the repository root for loading config-level environment files."""
    from human.core.config.paths import project_root  # type: ignore[import-not-found]

    return project_root()


def main() -> None:
    """Run the selected population pipeline and print its QA summary."""
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    from human.core.config.paths import resolve_config_path  # type: ignore[import-not-found]

    config_path = resolve_config_path(args.config)
    repository_root = find_project_root()
    load_dotenv(repository_root / "config" / ".env")
    logging.info("Loading config: %s", config_path)

    if args.country == "canada":
        from .canada.config import load_canada_config
        from .canada.pipeline import run_canada_population_pipeline_result
        from .canada.statcan import MissingCanadaInputError

        cfg = load_canada_config(config_path)
        try:
            result = run_canada_population_pipeline_result(
                cfg,
                overwrite=args.overwrite,
                debug=args.debug,
            )
        except MissingCanadaInputError as exc:
            if not args.skip_if_missing_inputs:
                raise
            logging.warning("Skipping Canada population pipeline: %s", exc)
            return
    else:
        from .us.config import load_us_config
        from .us.pipeline import run_us_population_pipeline

        cfg = load_us_config(config_path)
        result = run_us_population_pipeline(
            cfg,
            overwrite=args.overwrite,
            debug=args.debug,
        )

    print_summary(result.summary)
    logging.info("Population output written: %s", result.output_path)


if __name__ == "__main__":
    main()
