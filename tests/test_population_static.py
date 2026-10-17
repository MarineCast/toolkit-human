"""Independent deterministic contracts for the static original-polygon product."""
from __future__ import annotations

import json
import sys
import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import pytest
import shapely
from shapely.geometry import box
from pyproj import Transformer
from shapely.ops import transform

from human import get_population
from human.demography_and_presence.population.static import PopulationSources, PopulationCoverageError, write_sources
from human.demography_and_presence.population.static.geometry import cell_polygon, radius_polygon, GEOD

CELL = h3.latlng_to_cell(48, -123, 7)


def fixture_sources(polygons=None, populations=None, coverage=True):
    if polygons is None:
        polygons = [box(-123.01, 47.99, -122.99, 48.01)]
    if populations is None:
        populations = [100.] * len(polygons)
    n = len(polygons)
    frame = gpd.GeoDataFrame({"source_id": [f"synthetic:{i}" for i in range(n)],
        "country": ["US"] * n, "jurisdiction": ["synthetic"] * n,
        "vintage": [2020] * n, "population": populations}, geometry=polygons, crs=4326)
    support = gpd.GeoDataFrame(geometry=[box(-131, 40, -115, 56)], crs=4326) if coverage else None
    return PopulationSources(frame, support, {"synthetic": True, "rights": "synthetic test fixture"})


def query(tmp_path, sources=None, **kw):
    arguments = dict(target_h3=[CELL], sources=sources or fixture_sources(), workspace=tmp_path)
    arguments.update(kw)
    return get_population(**arguments)


def test_conservation_and_partial_domain_no_renormalization(tmp_path):
    neighbor = sorted(set(h3.grid_disk(CELL, 1)) - {CELL})[0]
    a, b = cell_polygon(CELL), cell_polygon(neighbor)
    source = fixture_sources([a.union(b)], [1000])
    full = query(tmp_path / 'full', source, target_h3=[CELL, neighbor]).table
    assert full.population_count.sum() == pytest.approx(1000, rel=2e-9)
    partial = query(tmp_path / 'partial', source).table.population_count.iloc[0]
    assert 100 < partial < 900
    assert partial == pytest.approx(full.set_index('h3_index').loc[CELL, 'population_count'], rel=2e-5)


def test_monotone_bands_and_geodesic_boundary_crossing(tmp_path):
    lat, lon = h3.cell_to_latlng(CELL)
    # A source straddling the 25 km boundary; its centroid is outside the circle.
    x, y, _ = GEOD.fwd(lon, lat, 90, 26000)
    polygon = box(x - .04, y - .02, x + .04, y + .02)
    result = query(tmp_path, fixture_sources([polygon], [100]), include_bands=True)
    row = result.table.iloc[0]
    assert 0 < row.population_within_25km < 100
    assert row.population_within_50km == pytest.approx(100)
    values = row[['population_within_25km', 'population_within_50km', 'population_within_100km']].astype(float)
    assert (np.diff(values) >= -1e-8).all()
    assert list(result.metadata['radii_km']) == [25., 50., 100.]
    assert not any('250km' in column for column in result.table)
    assert row[['population_0_25km','population_25_50km','population_50_100km']].sum() == pytest.approx(row.population_within_100km)
    assert row.population_count == 0
    assert row.population_count__coverage_status == 'known_zero'
    # Independent intersection fraction in the recorded equal-area system.
    project = Transformer.from_crs(4326, result.metadata['area_crs'], always_xy=True).transform
    expected = 100 * transform(project, polygon).intersection(transform(project, radius_polygon(lat,lon,25,25))).area / transform(project, polygon).area
    assert row.population_within_25km == pytest.approx(expected, abs=1e-9)


def test_bbox_does_not_clip_cells_or_neighborhood_sources(tmp_path):
    lat, lon = h3.cell_to_latlng(CELL)
    source = fixture_sources([cell_polygon(CELL), box(lon+.03,lat-.001,lon+.032,lat+.001)], [20,80])
    row = get_population(bbox=(lon-.0001,lat-.0001,lon+.0001,lat+.0001), sources=source, workspace=tmp_path).table.iloc[0]
    assert row.population_count == pytest.approx(20)
    assert row.population_within_25km == pytest.approx(100)


def test_cross_border_counts_once_and_vintages(tmp_path):
    source = fixture_sources([box(-123.02,47.99,-123.01,48),box(-122.99,47.99,-122.98,48)],[100,200])
    source.geographies.loc[1,['country','vintage','jurisdiction']] = ['CA',2021,'synthetic_canada']
    result=query(tmp_path,source)
    assert result.table.population_within_25km.iloc[0] == pytest.approx(300)
    assert result.metadata['source_vintages'] == [('CA','2021'),('US','2020')]


def test_missing_source_values_and_unverified_support_are_not_zero(tmp_path):
    source = fixture_sources([cell_polygon(CELL)], [None])
    with pytest.raises(PopulationCoverageError) as error:
        query(tmp_path, source)
    assert error.value.report['incomplete_metrics'] == 4
    result=query(tmp_path,source,allow_partial=True,include_bands=True)
    row=result.table.iloc[0]
    assert pd.isna(row.population_count)
    assert pd.isna(row.population_25_50km)
    assert json.loads(row.population_count__missing_sources) == ['synthetic:0']
    unknown = query(tmp_path/'unverified',fixture_sources(coverage=False),allow_partial=True).table.iloc[0]
    assert pd.isna(unknown.population_within_100km)
    assert unknown.population_within_100km__available_source_subtotal == 100
    zero = query(tmp_path/'zero',fixture_sources(populations=[0])).table.iloc[0]
    assert zero.population_count == 0
    assert zero.population_count__coverage_status == 'known_zero'


def test_complete_bands_require_both_cumulative_supports(tmp_path):
    lat, lon = h3.cell_to_latlng(CELL)
    x,y,_=GEOD.fwd(lon,lat,90,40000)
    source=fixture_sources([box(-123.01,47.99,-122.99,48.01),box(x-.01,y-.01,x+.01,y+.01)],[100,None])
    row=query(tmp_path,source,include_bands=True,allow_partial=True).table.iloc[0]
    assert row.population_0_25km == 100
    assert pd.isna(row.population_25_50km)
    assert pd.isna(row.population_50_100km)


def test_reference_optimization_source_order_batches_workers(tmp_path):
    polygons=[box(-123.8+x*.15,47.4+y*.15,-123.75+x*.15,47.45+y*.15) for x in range(10) for y in range(10)]
    source=fixture_sources(polygons,list(range(100)))
    cells=sorted(h3.grid_disk(CELL,1))
    reference=query(tmp_path/'reference',source,target_h3=cells,reference=True,include_bands=True).table
    for batch,workers in [(1,1),(4,2)]:
        source.geographies=source.geographies.sample(frac=1,random_state=batch)
        optimized=query(tmp_path/f'{batch}-{workers}',source,target_h3=cells,include_bands=True,
                        resource_limits={'batch_size':batch,'workers':workers}).table
        pd.testing.assert_frame_equal(reference,optimized,check_exact=False,rtol=1e-10,atol=1e-8)


def test_resolution_uses_own_center_and_density_full_area(tmp_path):
    source=fixture_sources([box(-123.01,47.99,-122.99,48.01)],[100])
    fine=query(tmp_path/'fine',source,radii_km=[.2,1]).table.iloc[0]
    coarse=query(tmp_path/'coarse',source,h3_res=6,target_h3=[h3.cell_to_parent(CELL,6)],radii_km=[.2,1]).table.iloc[0]
    assert fine.population_within_0_2km != coarse.population_within_0_2km
    assert fine.population_density_per_km2 == pytest.approx(fine.population_count/fine.cell_area_km2)


def test_offline_cache_no_network_and_input_invalidation(tmp_path,monkeypatch):
    import requests
    from human.demography_and_presence.population.static import engine
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *a,**k: pytest.fail('network in offline mode'))
    source=fixture_sources()
    manifest=write_sources(source,tmp_path/'inputs')
    first=get_population(target_h3=[CELL],source_manifest=manifest,workspace=tmp_path)
    monkeypatch.setattr(engine,'AllocationIndex',lambda *a,**k: pytest.fail('rebuilt compatible cache'))
    second=get_population(target_h3=[CELL],source_manifest=manifest,workspace=tmp_path,resource_limits={'batch_size':1,'workers':2})
    assert second.cache_hit
    assert first.path==second.path
    source.geographies.loc[0,'population']=101
    with pytest.raises(BaseException,match='rebuilt'):
        query(tmp_path,source)


@pytest.mark.parametrize('kwargs,match',[
    ({'target_h3':['garbage']},'Invalid target'),
    ({'target_h3':[CELL,CELL]},'Duplicate target'),
    ({'target_h3':[h3.cell_to_parent(CELL,6)]},'match h3_res'),
    ({'h3_res':16},'h3_res'),
    ({'radii_km':[50,25]},'increasing'),
    ({'radii_km':[0]},'positive'),
    ({'radii_km':[float('nan')]},'finite'),
    ({'bbox':(-123,48,-124,49),'target_h3':None},'bbox'),
])
def test_invalid_request(tmp_path,kwargs,match):
    with pytest.raises(ValueError,match=match):query(tmp_path,**kwargs)


@pytest.mark.parametrize('value',[-1,float('inf'),'bad'])
def test_invalid_population(tmp_path,value):
    with pytest.raises((ValueError,TypeError)):
        query(tmp_path,fixture_sources(populations=[value]))


def test_duplicate_sources_and_overlapping_support_fail(tmp_path):
    source=fixture_sources([cell_polygon(CELL),cell_polygon(CELL)],[10,20])
    with pytest.raises(ValueError,match='Overlapping source'):query(tmp_path,source)
    source.geographies.loc[1,'source_id']=source.geographies.loc[0,'source_id']
    with pytest.raises(ValueError,match='Duplicate source_id'):query(tmp_path,source)


def test_geometry_refinement(tmp_path):
    lat,lon=h3.cell_to_latlng(CELL)
    x,y,_=GEOD.fwd(lon,lat,90,25000)
    source=fixture_sources([box(x-.02,y-.02,x+.02,y+.02)],[100])
    values=[]
    for tolerance in [25,6.25,1.5625]:
        result=query(tmp_path/str(tolerance),source,resource_limits={'buffer_tolerance_m':tolerance,'edge_step_m':250})
        values.append(result.table.population_within_25km.iloc[0])
    assert abs(values[1]-values[2]) < abs(values[0]-values[2])
    assert abs(values[1]-values[2]) < .2
    circle=radius_polygon(lat,lon,250,6.25)
    distances=[GEOD.inv(lon,lat,x,y)[2] for x,y in circle.exterior.coords]
    assert max(abs(d-250000) for d in distances)<1e-6


def test_dry_run_cli_export_and_population_imports(tmp_path,monkeypatch,capsys):
    from human.demography_and_presence.population.static.cli import main
    manifest=write_sources(fixture_sources(),tmp_path/'inputs')
    plan=get_population(target_h3=[CELL],workspace=tmp_path/'new',dry_run=True)
    assert not plan['source_available'] and plan['missing_work']
    assert not (tmp_path/'new').exists()
    output=tmp_path/'out.parquet'
    assert main(['--target-h3',CELL,'--source-manifest',str(manifest),'--output',str(output)], workspace=tmp_path)==0
    assert output.exists()
    assert output.with_suffix('.metadata.json').exists()
    with pytest.raises(FileExistsError):
        query(tmp_path).to_parquet(output)
    import subprocess
    subprocess.run([sys.executable, '-c', "import human,sys; from human.demography_and_presence.population.static import get_population; assert not any(m.startswith(('human.viewshed','human.activity_and_effort.ais')) for m in sys.modules)"], check=True)


def test_prepare_census_retains_missing_zero_and_unclipped_geometry(tmp_path):
    from human.demography_and_presence.population.static.census import prepare_census_sources
    from human.demography_and_presence.population.static.sources import read_sources
    source=gpd.GeoDataFrame({'GEOID20':['a','b'],'STATEFP20':['53','53'],'POP20':[0,None]},geometry=[box(-123,48,-122,49),box(-122,48,-121,49)],crs=4326)
    raw=tmp_path/'raw.parquet';source.to_parquet(raw)
    spec={'us':[{'jurisdiction':'US-WA','state_fips':'53','geography':str(raw),'population_column':'POP20'}]}
    manifest=prepare_census_sources(workspace=tmp_path,source_extent=(-122.1,48.5,-121.9,48.6),specification=spec)
    bundle=read_sources(manifest)
    assert len(bundle.geographies)==2
    assert bundle.geographies.geometry.iloc[0].equals(source.geometry.iloc[0])
    assert bundle.geographies.population.iloc[0]==0
    assert pd.isna(bundle.geographies.population.iloc[1])
    assert prepare_census_sources(workspace=tmp_path,source_extent=(-122.1,48.5,-121.9,48.6),specification=spec)==manifest


def test_explicit_cross_border_quarantine_preserves_denominator_and_unknown(tmp_path):
    from human.demography_and_presence.population.static.sources import quarantine_cross_border_overlaps
    source=fixture_sources([box(-123.02,47.99,-123,48.01),box(-123.01,47.99,-122.99,48.01)],[100,200])
    source.geographies.loc[1,['country','vintage','jurisdiction']]=['CA',2021,'synthetic_CA']
    reconciled=quarantine_cross_border_overlaps(source)
    assert reconciled.metadata['cross_border_overlap_policy']=='quarantine_entire_sources_unknown'
    result=query(tmp_path,reconciled,allow_partial=True)
    row=result.table.iloc[0]
    # Both entire original source polygons become unknown; no disputed allocation
    # is included in a subtotal. Original populations remain provenance only.
    assert pd.isna(row.population_within_25km__available_source_subtotal)
    assert reconciled.geographies.geometry.iloc[0].area > 0
    assert reconciled.metadata['quarantined_known_population'] == 300
    assert pd.isna(row.population_within_25km)
    assert json.loads(row.population_within_25km__missing_sources) == ['synthetic:0','synthetic:1']


def test_configuration_defaults_and_source_geometry_cache_invalidation(tmp_path):
    import yaml
    from human.demography_and_presence.population.static import engine
    config=tmp_path/'population.yaml'
    config.write_text(yaml.safe_dump({'h3_resolution':6,'radii_km':[10,20]}))
    cell=h3.cell_to_parent(CELL,6)
    source=fixture_sources()
    first=get_population(target_h3=[cell],sources=source,workspace=tmp_path,config=config)
    assert list(first.metadata['radii_km'])==[10.,20.]
    assert first.metadata['h3_resolution']==6
    source.geographies.loc[0,'geometry']=box(-123.02,47.98,-122.98,48.02)
    second=get_population(target_h3=[cell],sources=source,workspace=tmp_path,config=config)
    assert not second.cache_hit and first.path!=second.path


def test_center_on_bbox_boundary_is_included(tmp_path):
    lat,lon=h3.cell_to_latlng(CELL)
    result=get_population(bbox=(lon,lat,lon+.0001,lat+.0001),sources=fixture_sources(),workspace=tmp_path)
    assert result.table.h3_index.tolist()==[CELL]


def test_nearby_custom_radii_share_boundary_azimuths(tmp_path):
    lat,lon=h3.cell_to_latlng(CELL)
    x,y,_=GEOD.fwd(lon,lat,35,24995)
    source=fixture_sources([box(x-.001,y-.001,x+.001,y+.001)],[100])
    row=query(tmp_path,source,radii_km=[25,25.001,25.002],include_bands=True).table.iloc[0]
    assert row.population_within_25km <= row.population_within_25_001km <= row.population_within_25_002km
    assert row.population_25_25_001km >= 0


def test_prepared_coverage_is_owned_by_querying_thread():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from human.demography_and_presence.population.static.engine import CoverageIndex
    coverage=gpd.GeoDataFrame(geometry=[box(0,0,10,10)],crs=3857)
    index=CoverageIndex(coverage,box(-1,-1,11,11),1)
    barrier=threading.Barrier(4)
    def check(_):
        barrier.wait()
        assert index.complete(box(1,1,2,2))
        return index._thread.coverage
    with ThreadPoolExecutor(max_workers=4) as workers:
        geometries=list(workers.map(check,range(4)))
    assert len({id(g) for g in geometries})==4


def test_coverage_cache_reuses_union_and_verifies_checksum(tmp_path,monkeypatch):
    from human.demography_and_presence.population.static.engine import CoverageIndex
    frame=gpd.GeoDataFrame(geometry=[box(0,0,10,10)],crs=3857)
    path=tmp_path/'coverage.wkb'
    first=CoverageIndex(frame,box(-1,-1,11,11),1,cache_path=path)
    assert first.complete(box(1,1,2,2)) and not first.cache_hit
    monkeypatch.setattr(shapely,'union_all',lambda *a,**k:pytest.fail('rebuilt support union'))
    second=CoverageIndex(frame,box(-1,-1,11,11),1,cache_path=path)
    assert second.complete(box(1,1,2,2)) and second.cache_hit
    path.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):
        CoverageIndex(frame,box(-1,-1,11,11),1,cache_path=path)


def test_oversized_source_keeps_local_groups_and_reference_totals(tmp_path):
    large=box(-124,47,-122,49).difference(box(-123.1,47.9,-122.9,48.1))
    source=fixture_sources([large,box(-123.02,47.99,-123.01,48),box(-122.99,47.99,-122.98,48)],[10000,100,200])
    cells=sorted(h3.grid_disk(CELL,1))
    optimized=query(tmp_path/'optimized',source,target_h3=cells,include_bands=True).table
    reference=query(tmp_path/'reference',source,target_h3=cells,include_bands=True,reference=True).table
    pd.testing.assert_frame_equal(optimized,reference,check_exact=False,rtol=1e-10,atol=1e-7)


def test_target_h3_case_variants_have_one_canonical_identity(tmp_path):
    assert query(tmp_path,target_h3=[CELL.upper()]).table.h3_index.tolist()==[CELL]
    with pytest.raises(ValueError,match='Duplicate target H3 identities'):
        query(tmp_path,target_h3=[CELL,CELL.upper()])


def test_distinct_national_counts_survive_cartographic_overlap(tmp_path):
    from human.demography_and_presence.population.static.sources import retain_distinct_national_counts
    source=fixture_sources([box(-123.02,47.99,-123,48.01),box(-123.01,47.99,-122.99,48.01)],[100,200])
    source.geographies.loc[1,['country','vintage','jurisdiction']]=['CA',2021,'synthetic_CA']
    revised=retain_distinct_national_counts(source)
    assert revised.geographies.population.tolist()==[100,200]
    assert revised.metadata['cross_border_overlap_source_count']==2
    row=query(tmp_path,revised).table.iloc[0]
    assert row.population_within_25km==pytest.approx(300)
    assert row.population_within_25km__missing_sources=='[]'
    # Neither full original denominator nor national counts are clipped to a seam.
    original_area=source.geographies.to_crs(revised.geographies.crs).area.to_numpy()
    np.testing.assert_allclose(revised.geographies.area,original_area)
    source.geographies.loc[1,'country']='US'
    with pytest.raises(ValueError,match='Same-country overlapping'):
        retain_distinct_national_counts(source)
