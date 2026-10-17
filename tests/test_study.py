"""Study export preserves native time support, spatial grain and nulls."""
import hashlib
import json
from importlib.resources import files

import geopandas as gpd
import h3
import pandas as pd
import pytest
import pyarrow.parquet as pq
from shapely.geometry import box
import yaml

from human import get_population
from human.demography_and_presence.population.static import PopulationSources
from human.study import export_study


def test_study_export(tmp_path):
    cell=h3.latlng_to_cell(48,-123,7)
    source=PopulationSources(gpd.GeoDataFrame({'source_id':['synthetic'],'country':['US'],
        'vintage':[2020],'jurisdiction':['synthetic'],'population':[100.]},
        geometry=[box(-123.01,47.99,-122.99,48.01)],crs=4326),
        gpd.GeoDataFrame(geometry=[box(-131,40,-115,56)],crs=4326),{'synthetic':True})
    population=get_population(bbox=(-123.02,47.98,-122.98,48.02),sources=source,workspace=tmp_path/'work')
    native=tmp_path/'ferry.parquet'
    pd.DataFrame({'service_date':pd.to_datetime(['2026-03-08']), 'source_h3':[cell],
        'h3_resolution':[7],'value':[None],
        'native_interval_start_utc':pd.to_datetime(['2026-03-08T08:00Z']),
        'native_interval_end_utc':pd.to_datetime(['2026-03-09T07:00Z'])}).to_parquet(native)
    support=tmp_path/'support.json'
    support.write_text(json.dumps({'native_h3_resolution':7,'native_calendar_timezone':'America/Los_Angeles'}))
    manifest=tmp_path/'release.json'
    manifest.write_text(json.dumps({'artifacts':[{'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in [native,support]]}))
    config=yaml.safe_load(files('human').joinpath('resources/config/data/human/temporal_context/calendar.yaml').read_text())
    config['time']['start_date']='2026-03-08';config['time']['end_date']='2026-03-09'
    cfg=tmp_path/'calendar.yaml';cfg.write_text(yaml.safe_dump(config))
    output=tmp_path/'export'
    catalog=export_study(population=population,output_dir=output,calendar_config=cfg,dynamic_inputs=[{
        'manifest':manifest,'artifact':native.name,'support_manifest':support.name,'family':'ferry',
        'date_column':'service_date','cell_column':'source_h3','resolution_column':'h3_resolution'}],metadata_dir=tmp_path/'receipts')
    metadata=json.loads(catalog.read_text())
    assert len(metadata['products'])==3
    assert {p.name for p in output.iterdir()}=={'static.parquet','dynamic-calendar.parquet','dynamic-ferry-r7.parquet'}
    calendar=pd.read_parquet(output/'dynamic-calendar.parquet')
    assert len(calendar)==2 and not any('h3' in c for c in calendar)
    dynamic=pd.read_parquet(output/'dynamic-ferry-r7.parquet')
    assert dynamic.value.isna().all()
    assert (dynamic.native_interval_end_utc-dynamic.native_interval_start_utc).dt.total_seconds().iloc[0]==23*3600
    embedded=json.loads(pq.read_schema(output/'dynamic-ferry-r7.parquet').metadata[b'human_study'])
    assert embedded['native_support']['native_calendar_timezone']=='America/Los_Angeles'


@pytest.mark.parametrize('family', ['land-opportunity', 'land-reporting-opportunity',
                                   'water-observation-opportunity', 'observer-effort'])
def test_study_rejects_modeling_composites_before_writing(tmp_path, family):
    output = tmp_path / 'human-study'
    with pytest.raises(ValueError, match='belong to modeling'):
        export_study(population=None, output_dir=output,
                     calendar_config=tmp_path / 'unused-calendar.yaml',
                     dynamic_inputs=[{'family': family}])
    assert not output.exists()



def test_study_rejects_ais_owned_by_dedicated_toolkit(tmp_path):
    with pytest.raises(ValueError, match='AIS belongs to toolkit-ais'):
        export_study(population=None, output_dir=tmp_path/'study',
                     calendar_config=tmp_path/'unused.yaml', dynamic_inputs=[{'family':'ais'}])
    assert not (tmp_path/'study').exists()
