import hashlib
import json
import geopandas as gpd
import h3
import pandas as pd
from shapely.geometry import box
import shapely
import pytest
from human import get_population
from human.demography_and_presence.population.static import PopulationSources
from human.demography_and_presence.population.static.geometry import cell_polygon
from human.static_water_support import load_qualified_water, qualify_water_support
CELL=h3.latlng_to_cell(48,-123,7)


def population(tmp_path,value):
    sources=PopulationSources(gpd.GeoDataFrame({'source_id':['known-or-null'],'country':['US'],
        'jurisdiction':['US-WA'],'vintage':[2020],'population':[value]},
        geometry=[box(-123.001,47.999,-122.999,48.001)],crs=4326),None,{'synthetic':True})
    result=get_population(target_h3=[CELL],radii_km=[25],sources=sources,workspace=tmp_path,allow_partial=True)
    return result.table,result.metadata


def qualify(table,meta,water):
    return qualify_water_support(table,meta,census_coverage=shapely.GeometryCollection(),
        water=gpd.GeoDataFrame(geometry=[water],crs=4326),provenance={'synthetic':True})


def test_water_completes_cell_but_never_extrapolates_to_radius(tmp_path):
    table,meta=population(tmp_path,100.)
    subtotal=table.population_count__available_source_subtotal.iloc[0]
    revised,new_meta=qualify(table,meta,cell_polygon(CELL))
    assert revised.population_count.iloc[0]==pytest.approx(subtotal)
    assert revised.population_count__coverage_status.iloc[0]=='complete'
    assert revised.population_density_per_km2.iloc[0]==pytest.approx(subtotal/revised.cell_area_km2.iloc[0])
    assert pd.isna(revised.population_within_25km.iloc[0])
    assert revised.population_within_25km__unverified_geographic_support.iloc[0]
    assert pd.isna(table.population_count.iloc[0])
    assert 'verified_water_support' not in meta


def test_water_preserves_missing_counts_and_land_gaps(tmp_path):
    table,meta=population(tmp_path/'missing',None)
    revised,_=qualify(table,meta,cell_polygon(CELL))
    assert pd.isna(revised.population_count.iloc[0])
    assert not revised.population_count__unverified_geographic_support.iloc[0]
    assert json.loads(revised.population_count__missing_sources.iloc[0])==['known-or-null']
    table,meta=population(tmp_path/'land-gap',100.)
    revised,_=qualify(table,meta,box(-125,47,-124,48))
    assert pd.isna(revised.population_count.iloc[0])
    assert revised.population_count__unverified_geographic_support.iloc[0]


def test_water_loader_excludes_pinned_land_and_rejects_tampering(tmp_path):
    config=tmp_path/'study.json';config.write_text('{}')
    water=tmp_path/'water.parquet';land=tmp_path/'land.parquet'
    gpd.GeoDataFrame({'SOURCE_SCALE_QUALIFICATION':['static_cartographic_reference']},geometry=[box(0,0,2,2)],crs=4326).to_parquet(water)
    gpd.GeoDataFrame(geometry=[box(1,0,2,2)],crs=4326).to_parquet(land)
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'physical_context':{name+'_polygon_path':{'relative_path':p.name,'sha256':digest(p)} for name,p in [('water',water),('land',land)]}}))
    study={'identity':{'config_path':str(config)},'data_root':tmp_path,'config':{'grid_registry':{'manifest_relative_path':manifest.name,'manifest_sha256':digest(manifest)}}}
    qualified,provenance=load_qualified_water(study)
    assert qualified.geometry.iloc[0].equals(box(0,0,1,2))
    assert provenance['water']['sha256']==digest(water)
    water.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='checksum'):
        load_qualified_water(study)


def test_census_free_qualified_water_is_known_zero(tmp_path):
    sources=PopulationSources(gpd.GeoDataFrame({'source_id':['distant-land'],'country':['US'],
        'jurisdiction':['US-WA'],'vintage':[2020],'population':[100.]},
        geometry=[box(-124,47,-123.99,47.01)],crs=4326),None,{'synthetic':True})
    result=get_population(target_h3=[CELL],radii_km=[25],sources=sources,workspace=tmp_path,allow_partial=True)
    assert result.table.population_count__coverage_status.iloc[0]=='unavailable'
    revised,_=qualify(result.table,result.metadata,cell_polygon(CELL))
    assert revised.population_count.iloc[0]==0
    assert revised.population_count__available_source_subtotal.iloc[0]==0
    assert revised.population_count__coverage_status.iloc[0]=='known_zero'
    assert pd.isna(revised.population_within_25km.iloc[0])
