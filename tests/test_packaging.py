"""Package boundary and installed-resource contracts."""
from __future__ import annotations

import ast
import pytest
from importlib.resources import files
from pathlib import Path

from human.cli import initialize_workspace
from human.core.config.paths import project_root
from human.legacy.scope import LEGACY_MODELING_MODULES


def test_runtime_has_no_application_imports():
    root = Path(__file__).resolve().parents[1] / 'src' / 'human'
    forbidden = []
    for path in root.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            modules = []
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                modules = [node.module or '']
            if any(m == 'orcacast' or m.startswith('orcacast.') for m in modules):
                forbidden.append(str(path))
    assert not forbidden


def test_workspace_is_explicit_and_independent_of_installation(tmp_path, monkeypatch):
    monkeypatch.delenv('HUMAN_WORKSPACE', raising=False)
    monkeypatch.chdir(tmp_path)
    assert project_root() == tmp_path.resolve()
    chosen = tmp_path / 'chosen'
    monkeypatch.setenv('HUMAN_WORKSPACE', str(chosen))
    assert project_root() == chosen


def test_initialization_preserves_edits_and_includes_routing_profile(tmp_path):
    created = initialize_workspace(tmp_path)
    assert created
    config = tmp_path / 'config/human.yaml'
    assert config.is_file()
    config.write_text('custom configuration\n')
    assert initialize_workspace(tmp_path) == []
    assert config.read_text() == 'custom configuration\n'
    assert files('human').joinpath('legacy/accessibility/land_transport_access/road_only.lua').is_file()


def test_initialization_excludes_modeling_configs_and_preserves_legacy_edits(tmp_path):
    legacy = tmp_path / 'config/data/human/activity_and_effort/land_reporting_opportunity.yaml'
    legacy.parent.mkdir(parents=True)
    legacy.write_text('existing research configuration\n')
    created = initialize_workspace(tmp_path)
    assert legacy.read_text() == 'existing research configuration\n'
    for path in created:
        if path.parent == legacy.parent:
            assert not any(path.stem == name or path.stem.startswith(name + '_')
                           for name in LEGACY_MODELING_MODULES)
    assert not (legacy.parent / 'ferry.yaml').exists()
    assert [p.relative_to(tmp_path).as_posix() for p in created] == ['config/human.yaml', 'config/census-sources.v1.json', '.env.example']
    assert not (legacy.parent / 'ais.yaml').exists()


def test_packaged_configuration_matches_checkout():
    root = Path(__file__).resolve().parents[1]
    source = root / 'config'
    packaged = root / 'src/human/resources/config'
    assert {p.relative_to(source) for p in source.rglob('*') if p.is_file()} == {
        p.relative_to(packaged) for p in packaged.rglob('*') if p.is_file()
    }
    for path in source.rglob('*'):
        if path.is_file():
            assert path.read_bytes() == (packaged / path.relative_to(source)).read_bytes()


@pytest.mark.legacy
def test_viewshed_artifact_api_is_packaged():
    from human.core.artifacts import ArtifactRef, RunManifest
    from human.legacy.viewshed.finalize import final_artifacts

    assert ArtifactRef
    assert RunManifest
    assert final_artifacts


@pytest.mark.parametrize('family', ['land-reporting-opportunity', 'water-observation-opportunity', 'observer-effort'])
@pytest.mark.parametrize('action', ['build', 'download', 'inspect'])
def test_modeling_proxies_are_not_human_cli_products(action, family):
    from human.cli import main
    with pytest.raises(SystemExit) as error:
        main([action, family])
    assert error.value.code == 2
