"""Native delivery identity, access semantics, source geometry and preservation regressions."""
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon, LineString

from human.pipelines.native.features import compose_native_features, summary_fields, checksum


def specification(tmp_path):
    def spatial(name, records, geometries):
        path=tmp_path/name
        gpd.GeoDataFrame(records,geometry=geometries,crs=4326).to_parquet(path)
        return path
    launch=spatial('launch.parquet',{'ACCESS_SITE_ID':['launch-1'],'SOURCE_RECORD_ID':['1'],'PUBLIC_ACCESS_STATE':['public'],'NAME':['Synthetic launch'],'DATA_VINTAGE':['2004']},[Point(-123,48)])
    shore=spatial('shore.parquet',{'ACCESS_SITE_ID':['shore-1'],'SOURCE_RECORD_ID':['way/1'],'PUBLIC_ACCESS_STATE':['restricted'],'NAME':[None],'FACILITY_TYPE':['beach'],'ACCESS_TAG':['private']},[Point(-123.1,48.1)])
    parks=spatial('parks.parquet',{'FID':[1],'ParkName':['Synthetic park']},[Polygon([(-123,48),(-122.9,48.1),(-123,48.1),(-122.9,48),(-123,48)])])
    lines=spatial('lines.parquet',{'OBJECTID':[1,2],'CLASS':['PUB4','PRIVYACHT'],'CLASS_DESC':['No known access','Private yacht club'],'BEACH_NAME':[None,'Private club']},[LineString([(-123,48),(-123,48.1)]),LineString([(-123,48),(-123.1,48)])])
    places=tmp_path/'places.json';places.write_text(json.dumps([{'id':'wa-state-parks:1','source_ids':['wa-state-parks:1'],'name':'Synthetic park','latitude':48.02,'longitude':-122.98,'category':'Park','destination_type':'park','access':'private'}]))
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'artifacts':[{'path':str(p),'sha256':checksum(p)} for p in (launch,shore,parks,lines,places)]}))
    return {'schema':'human.native_sources.v1','launches':{'manifest':str(manifest),'facilities':str(launch)},'shore_access':{'manifest':str(manifest),'facilities':str(shore),'access_lines':str(lines)},'places':{'manifest':str(manifest),'catalog':str(places),'park_polygons':str(parks)}}


def test_original_record_and_source_geometry_semantics(tmp_path):
    payload,meta=compose_native_features(specification(tmp_path))
    records=[f for f in payload['features'] if f['properties']['role']=='record']
    assert len(records)==5
    assert len(payload['features'])==6
    place=next(f for f in records if f['properties']['group']=='places')
    assert place['geometry']['coordinates']==(-122.98,48.02)
    assert place['properties']['category']=='park'
    assert 'access_state' not in place['properties']
    assert all('access_states' not in group for group in meta['groups'].values())
    assert place['properties']['attributes']['access']=='private'
    assert 'not_entrance' in place['properties']['location_method']
    area=next(f for f in payload['features'] if f['properties']['role']=='source_footprint')
    assert area['properties']['record_id']==place['properties']['record_id']
    assert area['properties']['source_geometry_valid'] is False
    assert area['properties']['source_geometry_wkb_sha256']
    assert 'make_valid' in area['properties']['location_method']
    shore=next(f for f in records if f['properties']['group']=='shore_access')
    assert shore['properties']['name'] is None
    assert 'access_state' not in shore['properties']
    assert 'representative' in shore['properties']['location_method']
    assert meta['groups']['places']['records']==1 and meta['groups']['places']['context_features']==1


def test_changed_source_is_rejected_before_export(tmp_path):
    spec=specification(tmp_path)
    Path(spec['places']['catalog']).write_text('[]')
    with pytest.raises(ValueError,match='checksum-bound'):compose_native_features(spec)


def test_unqualified_legacy_access_lengths_cannot_be_map_metrics():
    fields=summary_fields(['public_shore_access__PUBLIC_ACCESSIBLE_SHORELINE_M','public_shore_access__ACCESSIBLE_WATERFRONT_FRACTION','public_shore_access__PUBLIC_ACCESS_STATE','public_shore_access__TOTAL_MARINE_SHORELINE_M','public_shore_access__RESTRICTED_ACCESS_SITE_COUNT'])
    assert set(fields)=={'public_shore_access__TOTAL_MARINE_SHORELINE_M','public_shore_access__RESTRICTED_ACCESS_SITE_COUNT'}
    assert all('public_shore_access__PUBLIC_ACCESS_STATE' not in f['qualifiers'] for f in fields.values())
