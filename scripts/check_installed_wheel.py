"""Check the supported Human API from outside a checkout in a regular wheel."""
from __future__ import annotations
import contextlib
import importlib.abc
from importlib.resources import files
import io
from pathlib import Path
import sys
import tempfile

class BlockApplicationImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname=='orcacast' or fullname.startswith('orcacast.'):
            raise AssertionError(f'Application dependency: {fullname}')
        return None

sys.meta_path.insert(0,BlockApplicationImports())
import human
from human.cli import main,initialize_workspace,FAMILIES
from human.pipelines.delivery.validation import product_contract
root=Path(str(files('human'))).resolve()
assert root.is_relative_to(Path(sys.prefix).resolve()) and 'site-packages' in root.parts
assert callable(human.get_human_data) and not FAMILIES
assert {p.name for p in root.glob('*.py')}=={'__init__.py','__main__.py','main.py','cli.py'}
from human.main import get_human_data
assert human.get_human_data is get_human_data
for removed in ('human.pipeline','human.static_matrix','human.shared_study','human.validation'):
    assert __import__('importlib').util.find_spec(removed) is None
assert not any(name.startswith('human.legacy') for name in sys.modules)
assert root.joinpath('resources/feature_catalog.yaml').is_file()
assert set(product_contract()['products'])=={'static.parquet','dynamic-calendar.parquet','static-native.parquet'}
with tempfile.TemporaryDirectory() as tmp:
    workspace=Path(tmp);created=initialize_workspace(workspace)
    assert [p.relative_to(workspace).as_posix() for p in created]==['config/human.yaml', 'config/census-sources.v1.json', '.env.example']
    created[0].write_text('user edit\n');assert initialize_workspace(workspace)==[]
    assert created[0].read_text()=='user edit\n'
for argv in (['--help'],['get-human-data','--help'],['validate','--help']):
    with contextlib.redirect_stdout(io.StringIO()):
        try:main(argv)
        except SystemExit as e:assert e.code==0
        else:raise AssertionError('Expected help exit')
assert not root.joinpath('activity_and_effort/ais').exists()
print('Installed Human pipeline API, contract, CLI and initialization passed:',human.__file__)
