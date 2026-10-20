"""The supported package has real pipeline implementations and no alias tree."""
import ast
import importlib
from human.core.config.paths import project_root


def test_supported_source_layout_and_alias_removal():
    root=project_root()/'src/human'
    assert {p.name for p in root.glob('*.py')}=={'__init__.py','__main__.py','main.py','cli.py'}
    for name in ('population','calendar','native','delivery'):
        assert (root/'pipelines'/name/'__init__.py').is_file()
    for name in ('boat_launches','shore_access','places'):
        assert (root/'pipelines/native'/name/'build.py').is_file()
    assert (root/'resources/feature_catalog.yaml').is_file()
    for p in root.rglob('*.py'):
        assert 'sys.modules[__name__]' not in p.read_text(),p
        if p.is_relative_to(root/'pipelines') or p==root/'main.py':
            for n in ast.walk(ast.parse(p.read_text())):
                if isinstance(n,ast.ImportFrom):assert not (n.module or '').startswith('human.legacy'),p
    assert not any(root.rglob('.gitkeep'))


def test_supported_public_exports_and_deliberate_internal_path_changes():
    import human
    from human.main import get_human_data
    assert human.get_human_data is get_human_data and callable(human.get_population)
    assert set(human.__all__)=={'get_human_data','get_population','HumanData'}
    for name in ('human.pipeline','human.static_matrix','human.shared_study','human.validation'):
        assert importlib.util.find_spec(name) is None,name
    assert callable(importlib.import_module('human.pipelines.native.boat_launches.build').build)
    assert callable(importlib.import_module('human.pipelines.native.shore_access.build').build)
    assert callable(importlib.import_module('human.pipelines.native.places.build').build)
