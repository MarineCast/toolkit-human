"""Small deterministic HTML reports shared by human inspectors."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


def write_html_report(
    path: str | Path,
    *,
    title: str,
    summary: Mapping[str, Any],
    frame: pd.DataFrame | None = None,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_text = html.escape(json.dumps(dict(summary), indent=2, default=str))
    sample = ""
    if frame is not None:
        sample = frame.head(100).to_html(index=False, escape=True, border=0)
    output.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui;margin:2rem;max-width:1200px}"
        "table{border-collapse:collapse;font-size:.85rem}td,th{padding:.3rem;border:1px solid #ddd}"
        "pre{background:#f5f5f5;padding:1rem;overflow:auto}</style></head><body>"
        f"<h1>{html.escape(title)}</h1><h2>Summary</h2><pre>{summary_text}</pre>"
        f"<h2>Sample</h2>{sample}</body></html>\n",
        encoding="utf-8",
    )
    return output
