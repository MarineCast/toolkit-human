import runpy
from importlib.resources import files
import yaml
from human.pipelines.population.collected import DISPLAY_LABELS
from human.pipelines.native.features import summary_fields
from human.pipelines.delivery.validation import product_contract


def test_catalog_describes_only_supported_outputs():
    catalog=yaml.safe_load(files('human').joinpath('resources/feature_catalog.yaml').read_text())
    assert {p['artifact'] for p in catalog['collections'].values()}==set(product_contract()['products'])
    assert catalog['default_model_policy']=={}
    assert catalog['pipeline']=='human.get_human_data'
    assert all(not f['model_eligible'] for p in catalog['collections'].values() for f in p['features'])
    static=catalog['collections']['static']['features']
    selected={f['name'] for f in static if f['selectable']}
    assert selected==set(DISPLAY_LABELS)|set(summary_fields([f['name'] for f in static]))
    assert not any('250km' in f['name'] for f in static)


def test_human_feature_catalog_is_generated():
    from human.core.config.paths import project_root
    root=project_root()
    generated=runpy.run_path(str(root/'scripts/update_human_feature_catalog.py'))['build_catalog']()
    assert yaml.safe_load(files('human').joinpath('resources/feature_catalog.yaml').read_text())==generated


def test_ais_adapter_is_not_a_human_registration():
    from human.cli import FAMILIES
    assert not FAMILIES
    assert not files('human').joinpath('activity_and_effort/ais').is_dir()
