import json

import geopandas as gpd
import h3
import numpy as np
import pytest
from shapely.geometry import Point, box

from human.pipelines.population.static.collection import collection_footprint, CollectionFootprint
from human.pipelines.population.static.census import prepare_census_sources
from human.pipelines.population.static.geometry import GEOD, cell_polygon
from human.pipelines.population.static.sources import read_sources

CELL = h3.latlng_to_cell(48, -123, 7)


def test_every_geodesic_boundary_and_disconnected_region_is_collected():
    cells = [CELL, h3.latlng_to_cell(50,-127,7), h3.latlng_to_cell(49,-125,7)]
    result = collection_footprint(cells,7)
    for cell in cells:
        lat,lon = h3.cell_to_latlng(cell)
        bearings = np.arange(0,360,.5)
        xs,ys,_ = GEOD.fwd(np.full(len(bearings),lon),np.full(len(bearings),lat),bearings,np.full(len(bearings),100000))
        assert all(result.geometry.covers(Point(x,y)) for x,y in zip(xs,ys))
        assert result.geometry.covers(cell_polygon(cell))
    other = collection_footprint(list(reversed(cells)),7)
    assert result.metadata == other.metadata
    assert result.geometry.equals_exact(other.geometry,0)
    assert result.metadata['maximum_radius_km']==100
    assert result.metadata['target_count']==3


def test_full_coarse_cell_is_retained_even_outside_small_radius():
    cell = h3.latlng_to_cell(48,-123,3)
    result = collection_footprint([cell],3,[1])
    assert result.geometry.covers(cell_polygon(cell))


@pytest.mark.parametrize('radii',[(0,),(-1,),(100,25),(25,25),(float('nan'),),(501,)])
def test_bad_radii_rejected(radii):
    with pytest.raises(ValueError):collection_footprint([CELL],7,radii)


def test_binding_rejects_changed_geometry():
    footprint=collection_footprint([CELL],7)
    with pytest.raises(ValueError,match='binding'):
        CollectionFootprint(box(-124,47,-122,49),footprint.metadata).validate()


def test_full_source_polygons_and_true_footprint_jurisdictions(tmp_path):
    footprint=collection_footprint([CELL],7)
    source=gpd.GeoDataFrame({'GEOID20':['outside-display','zero'],'STATEFP20':['53','53'],'POP20':[100.,0.]},
        geometry=[box(-125,47.99,-123.2,48.01),box(-123.01,47.99,-122.99,48.01)],crs=4326)
    raw=tmp_path/'sources.parquet';source.to_parquet(raw)
    west,south,east,north=footprint.geometry.bounds
    inventory=gpd.GeoDataFrame({'STATEFP':['53','99']},geometry=[box(-126,46,-120,50),box(west,south,west+.01,south+.01)],crs=4326)
    states=tmp_path/'states.parquet';inventory.to_parquet(states)
    spec={'us':[{'jurisdiction':'US-WA','state_fips':'53','geography':str(raw),'population_column':'POP20'}],
        'jurisdiction_inventories':[{'path':str(states),'id_column':'STATEFP','prefix':'US-'}]}
    manifest=prepare_census_sources(workspace=tmp_path,source_extent=footprint.geometry.bounds,
        specification=spec,collection=footprint)
    bundle=read_sources(manifest)
    assert bundle.geographies.geometry.iloc[0].equals(source.geometry.iloc[0])
    assert bundle.geographies.population.tolist()==[100.,0.]
    assert bundle.metadata['required_jurisdictions']==['US-53']
    assert bundle.metadata['missing_jurisdictions']==[]
    assert bundle.metadata['collection_footprint']==footprint.metadata
    assert prepare_census_sources(workspace=tmp_path,source_extent=footprint.geometry.bounds,
        specification=spec,collection=footprint)==manifest
    with pytest.raises(ValueError,match='contain every required neighborhood'):
        prepare_census_sources(workspace=tmp_path,source_extent=(-123.1,47.9,-122.9,48.1),specification=spec,collection=footprint)


def test_collection_plan_binds_real_study_and_companion(tmp_path):
    from tests.test_static_delivery import fixture_study
    from human.pipelines.population.collection import prepare_study_sources
    study,cell=fixture_study(tmp_path)
    plan=prepare_study_sources(study_config=study,workspace=tmp_path/'work',specification=tmp_path/'not-needed.json',plan_only=True)
    record=json.loads(plan.read_text())
    assert record['target_count']==1 and record['source_manifest'] is None
    assert record['maximum_radius_km']==100
    artifact=record['footprint_artifact']
    from human.pipelines.population.static.sources import checksum
    assert checksum(artifact['path'])==artifact['sha256']
    assert prepare_study_sources(study_config=study,workspace=tmp_path/'work',specification=tmp_path/'not-needed.json',plan_only=True)==plan


def test_delivery_rejects_preparation_for_other_targets(tmp_path):
    from tests.test_static_delivery import fixture_study
    from human.pipelines.population.delivery import export_static_study
    from human.pipelines.population.static.sources import write_sources, PopulationSources
    study,cell=fixture_study(tmp_path)
    wrong=collection_footprint([h3.latlng_to_cell(49,-123,7)],7)
    sources=PopulationSources(gpd.GeoDataFrame({'source_id':['x'],'country':['US'],'vintage':[2020],
        'jurisdiction':['US-WA'],'population':[1.]},geometry=[box(-123.1,47.9,-122.9,48.1)],crs=4326),
        None,{'collection_footprint':wrong.metadata})
    manifest=write_sources(sources,tmp_path/'sources')
    with pytest.raises(ValueError,match='footprint does not match'):
        export_static_study(study_config=study,workspace=tmp_path/'work',source_manifest=manifest,output_dir=tmp_path/'final')


def test_standalone_live_api_uses_same_complete_collection_plan(tmp_path,monkeypatch):
    from human import get_population
    from human.pipelines.population.static.sources import write_sources, PopulationSources
    from human.pipelines.population.static import census
    sources=PopulationSources(gpd.GeoDataFrame({'source_id':['x'],'country':['US'],'vintage':[2020],
        'jurisdiction':['US-WA'],'population':[100.]},geometry=[box(-123.01,47.99,-122.99,48.01)],crs=4326),
        gpd.GeoDataFrame(geometry=[box(-130,40,-115,55)],crs=4326),{'synthetic':True})
    manifest=write_sources(sources,tmp_path/'sources')
    calls=[]
    def prepare(**kwargs):
        calls.append(kwargs)
        assert kwargs['mode']=='live'
        kwargs['collection'].validate()
        assert kwargs['source_extent']==kwargs['collection'].metadata['source_extent_wgs84']
        return manifest
    monkeypatch.setattr(census,'prepare_census_sources',prepare)
    result=get_population(target_h3=[CELL],h3_res=7,radii_km=[25,50,100],workspace=tmp_path/'work',mode='live')
    assert len(calls)==1 and result.table.population_within_100km.iloc[0]==pytest.approx(100.)
    assert calls[0]['collection'].metadata['maximum_radius_km']==100


def test_planning_again_preserves_completed_source_preparation(tmp_path):
    from tests.test_static_delivery import fixture_study
    from human.pipelines.population.collection import prepare_study_sources
    study,cell=fixture_study(tmp_path)
    raw=tmp_path/'raw.parquet'
    gpd.GeoDataFrame({'GEOID20':['x'],'STATEFP20':['53'],'POP20':[100.]},
        geometry=[box(-123.01,47.99,-122.99,48.01)],crs=4326).to_parquet(raw)
    spec=tmp_path/'spec.json';spec.write_text(json.dumps({'us':[{'jurisdiction':'US-WA','state_fips':'53',
        'geography':str(raw),'population_column':'POP20'}]}))
    completed=prepare_study_sources(study_config=study,workspace=tmp_path/'work',specification=spec)
    before=completed.read_bytes()
    planned=prepare_study_sources(study_config=study,workspace=tmp_path/'work',specification=spec,plan_only=True)
    assert completed != planned and completed.read_bytes()==before
    assert json.loads(completed.read_text())['source_manifest']
    assert json.loads(planned.read_text())['source_manifest'] is None
