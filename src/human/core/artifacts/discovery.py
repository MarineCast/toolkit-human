"""Read-only discovery for run-scoped artifact trees."""

from __future__ import annotations

from pathlib import Path


def list_run_artifacts(run_id: str, root_dir: Path) -> dict[str, dict[str, Path]]:
    run_dir = Path(root_dir) / run_id
    artifacts: dict[str, dict[str, Path]] = {"baselines": {}, "composites": {}}
    for kind in artifacts:
        kind_dir = run_dir / kind
        if not kind_dir.exists():
            continue
        for model_dir in kind_dir.iterdir():
            if not model_dir.is_dir():
                continue
            forecast_path = model_dir / "forecast.parquet"
            if forecast_path.exists():
                artifacts[kind][model_dir.name] = forecast_path
    return artifacts
