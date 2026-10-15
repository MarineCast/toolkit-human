"""Population query CLI, loaded only by `human get population`."""
import argparse
import json
import logging
from pathlib import Path
from .api import get_population


def main(argv=None, workspace=None):
    parser = argparse.ArgumentParser(description=__doc__)
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--bbox", nargs=4, type=float)
    targets.add_argument("--target-h3", nargs="+")
    parser.add_argument("--h3-res", type=int)
    parser.add_argument("--radii-km", nargs="+", type=float)
    parser.add_argument("--include-bands", action="store_true")
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.dry_run and not args.output:
        parser.error("--output is required unless --dry-run is selected")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = get_population(bbox=args.bbox, target_h3=args.target_h3, h3_res=args.h3_res,
        radii_km=args.radii_km, include_bands=args.include_bands, mode=args.mode,
        workspace=workspace, allow_partial=args.allow_partial, source_manifest=args.source_manifest,
        config=args.config, dry_run=args.dry_run,
        resource_limits={"batch_size": args.batch_size, "workers": args.workers})
    print(json.dumps(result, indent=2) if args.dry_run else result.to_parquet(args.output))
    return 0
