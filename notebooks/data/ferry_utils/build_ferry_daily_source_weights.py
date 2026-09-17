#!/usr/bin/env python3
"""CLI wrapper for the daily ferry source-cell effort pipeline."""

from human.activity_and_effort.ferry.pipeline import main


if __name__ == "__main__":
    raise SystemExit(main())
