"""Aggregate CLI for the calendar download, build, and inspect stages."""

from __future__ import annotations

import argparse
from typing import Sequence

from .build import build
from .config import DEFAULT_CONFIG_PATH
from .download import download
from .inspect import inspect


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("download", "build", "inspect"))
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)
    if args.stage == "download":
        print(download(args.config, overwrite=args.overwrite))
    elif args.stage == "build":
        print(build(args.config, allow_partial=args.allow_partial, overwrite=args.overwrite))
    else:
        for path in inspect(args.config):
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
