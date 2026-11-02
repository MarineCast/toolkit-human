from __future__ import annotations

from pathlib import Path
from datetime import date
from types import SimpleNamespace
import sys

import pytest

from human.core.config.paths import project_root
from human.legacy.activity.ferry import sources
from human.legacy.activity.ferry.sources import (
    DEFAULT_BC_CACHE_DIR,
    DEFAULT_BC_OUTPUT,
    DEFAULT_BC_PREVIEW_DIR,
    DEFAULT_WSF_CACHE_DIR,
    DEFAULT_WSF_OUTPUT,
    FERRY_RAW_DIR,
    FERRY_UTILS_DIR,
)


def test_default_ferry_artifacts_stay_in_ferry_workspace() -> None:
    assert FERRY_UTILS_DIR.name == "ferry"
    assert FERRY_RAW_DIR == project_root() / "data/raw/human/activity_and_effort/ferry"
    assert DEFAULT_WSF_CACHE_DIR == FERRY_RAW_DIR / "wsf_tableau"
    assert DEFAULT_BC_CACHE_DIR == FERRY_RAW_DIR / "bc_ferries_traffic"
    assert DEFAULT_BC_PREVIEW_DIR == DEFAULT_BC_CACHE_DIR / "previews"
    assert DEFAULT_WSF_OUTPUT == FERRY_RAW_DIR / "wsf_ridership.parquet"
    assert DEFAULT_BC_OUTPUT == FERRY_RAW_DIR / "bc_ferries_estimated_ridership.parquet"

    for destination in (
        DEFAULT_WSF_CACHE_DIR,
        DEFAULT_BC_CACHE_DIR,
        DEFAULT_BC_PREVIEW_DIR,
        DEFAULT_WSF_OUTPUT,
        DEFAULT_BC_OUTPUT,
    ):
        assert destination.is_relative_to(FERRY_RAW_DIR)
        assert "tmp" not in destination.relative_to(FERRY_RAW_DIR).parts


@pytest.mark.parametrize("fail_at", [None, "startup", "query"])
def test_hyper_logs_cleaned_after_process_closes(monkeypatch, tmp_path, fail_at) -> None:
    config = sources.Config(
        start_date=date(2020, 1, 1), end_date=date(2020, 1, 2),
        output=tmp_path / "ridership.parquet", cache_dir=tmp_path / "cache",
    )
    config.cache_dir.mkdir()
    hyper_path = config.cache_dir / "source.hyper"
    hyper_path.write_bytes(b"retained source")
    existing_log = tmp_path / "hyperd.log"
    existing_log.write_text("unrelated diagnostics")
    cache_log = config.cache_dir / "hyperd.log"
    cache_log.write_text("earlier diagnostics")
    log_dirs = []
    closed = []

    class Process:
        endpoint = "synthetic"

        def __init__(self, telemetry, *, parameters):
            log_dir = Path(parameters["log_dir"])
            assert log_dir.parent == config.cache_dir
            log_dirs.append(log_dir)
            (log_dir / "hyperd.log").write_text("this run")
            if fail_at == "startup":
                raise RuntimeError("synthetic startup failure")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            # Shutdown can write diagnostics; cleanup must wait until it completes.
            assert closed == ["connection"]
            (log_dirs[0] / "hyperd-shutdown.log").write_text("closed")
            closed.append("process")

    class Connection:
        def __init__(self, endpoint, database):
            assert database == str(hyper_path)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append("connection")

        def execute_list_query(self, query):
            if "information_schema.tables" in query:
                return [("Extract", "Extract")]
            if fail_at == "query":
                raise ValueError("synthetic query failure")
            return [("2020-01-01", "route", "Seattle", "Bainbridge Island",
                     "12:00:00", None, "vessel", 10, 20, 30)]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "tableauhyperapi", SimpleNamespace(
        HyperProcess=Process, Connection=Connection,
        Telemetry=SimpleNamespace(DO_NOT_SEND_USAGE_DATA_TO_TABLEAU=object()),
    ))
    if fail_at:
        with pytest.raises(RuntimeError):
            sources.read_hyper(config, hyper_path)
    else:
        result = sources.read_hyper(config, hyper_path)
        assert result["reported_total_riders"].tolist() == [30]
    assert len(log_dirs) == 1 and not log_dirs[0].exists()
    assert closed == ([] if fail_at == "startup" else ["connection", "process"])
    assert hyper_path.read_bytes() == b"retained source"
    assert existing_log.read_text() == "unrelated diagnostics"
    assert cache_log.read_text() == "earlier diagnostics"
