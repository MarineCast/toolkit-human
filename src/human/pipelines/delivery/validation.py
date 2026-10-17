"""Semantic validation for the three supported Human delivery products."""
from __future__ import annotations

from datetime import timezone as utc_timezone
from importlib.resources import files
import json
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import shapely
from shapely.geometry import shape

from human.pipelines.population.collected import DISPLAY_LABELS, METHOD
from human.pipelines.delivery.finalize import read_catalog
from human.pipelines.native.features import summary_fields


def product_contract():
    return json.loads(files('human').joinpath('resources/contracts/final-products.v1.json').read_text())


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _finite_nonnegative(values, label, *, nullable=False):
    values = pd.to_numeric(values, errors='raise')
    if nullable:
        values = values.dropna()
    _require(bool(np.isfinite(values).all() and values.ge(0).all()), f'Invalid {label}')


def _static(frame, info):
    _require(all(isinstance(c, str) and h3.is_valid_cell(c) and h3.get_resolution(c) == 7 for c in frame.h3_index), 'Static identity requires valid R7 H3')
    _require(frame.h3_resolution.eq(7).all(), 'Static row resolution must be 7')
    _require(np.isfinite(frame.cell_area_km2).all() and frame.cell_area_km2.gt(0).all(), 'Full cell area must be positive')
    for base in ('population_count', *(f'population_within_{r}km' for r in (25, 50, 100))):
        status = frame[base+'__coverage_status']
        _require(status.isin(['complete', 'known_zero', 'partial', 'unavailable']).all(), 'Invalid population coverage status')
        missing = frame[base+'__missing_sources'].map(json.loads)
        _require(missing.map(lambda ids: isinstance(ids, list) and all(isinstance(i, str) for i in ids) and len(ids)==len(set(ids))).all(), 'Invalid missing-source identities')
        complete = status.isin(['complete', 'known_zero'])
        _require(not (complete & (missing.map(bool) | frame[base+'__unverified_geographic_support'])).any(), 'Complete population has missing support')
        subtotal = frame[base+'__available_source_subtotal']
        _finite_nonnegative(subtotal, 'source subtotal', nullable=True)
        field = base+'_collected'
        _finite_nonnegative(frame[field], field)
        _require(np.array_equal(frame[field].to_numpy(), subtotal.fillna(0).to_numpy()), 'Collected estimate differs from source subtotal')
        _require(frame[field+'__zero_imputed'].eq(~complete).all(), 'Population zero-imputation flag differs from support')
        _require(frame[field+'__method'].eq(METHOD).all(), 'Unrecognized collected population method')
        _require(not (status.eq('known_zero') & frame[field].ne(0)).any(), 'Supported zero has positive population')
    _require(frame.population_count_collected__coverage_status.equals(frame.population_count__coverage_status), 'Count coverage alias differs')
    density = frame.population_density_per_km2_collected
    _finite_nonnegative(density, 'density')
    _require(np.array_equal(density.to_numpy(), (frame.population_count_collected/frame.cell_area_km2).to_numpy()), 'Density denominator differs from full-cell area')
    for q in ('zero_imputed', 'method'):
        _require(frame['population_density_per_km2_collected__'+q].equals(frame['population_count_collected__'+q]), 'Density provenance differs from count')
    _require((frame.population_within_25km_collected <= frame.population_within_50km_collected+1e-7).all() and (frame.population_within_50km_collected <= frame.population_within_100km_collected+1e-7).all(), 'Non-monotone population neighborhoods')
    _require(info['population']['radii_km']==[25.,50.,100.], 'Population support must be 25/50/100 km')
    definitions = info['population']['fields']
    _require(set(definitions)==set(DISPLAY_LABELS), 'Public population dictionary differs from the five collected quantities')
    _require({k:v['label'] for k,v in definitions.items()}==DISPLAY_LABELS, 'Population display labels differ')
    _require(info.get('infrastructure_fields')==summary_fields(frame.columns), 'Infrastructure field selection differs from supported contract')
    for col in frame:
        if col.endswith('_COUNT') or col.startswith('boat_launch_access__BOAT_LAUNCHES_WITH_'):
            _finite_nonnegative(frame[col], col, nullable=True)


def _calendar(frame, info):
    from human.pipelines.calendar.lookup import DEFAULT_JURISDICTIONS
    from importlib.resources import files as resource_files
    from zoneinfo import ZoneInfo
    support = info['temporal_support']
    dates = pd.date_range(support['start'], support['end_exclusive'], inclusive='left')
    jurisdictions = {c+('-'+s if s else ''):(c,s,z) for c,s,z in DEFAULT_JURISDICTIONS}
    _require(set(frame.jurisdiction)==set(jurisdictions), 'Calendar requires all five reference jurisdictions')
    _require(frame.as_of.eq(support['as_of']).all(), 'Calendar as-of mismatch')
    for jurisdiction, group in frame.groupby('jurisdiction'):
        country, subdivision, timezone = jurisdictions[jurisdiction]
        _require(group.date.tolist()==dates.strftime('%Y-%m-%d').tolist(), 'Calendar has missing, unordered or extra civil dates')
        _require(group.country.eq(country).all() and group.subdivision.fillna('').eq(subdivision or '').all() and group.timezone.eq(timezone).all(), 'Calendar jurisdiction/reference-zone mismatch')
        with resource_files('tzdata.zoneinfo').joinpath(*timezone.split('/')).open('rb') as handle:
            zone = ZoneInfo.from_file(handle, key=timezone)
        # Scalar localization honors the supplied ZoneInfo file even when pandas'
        # vectorized timezone cache was initialized against an older OS database.
        starts = pd.DatetimeIndex([ts.to_pydatetime().replace(tzinfo=zone).astimezone(utc_timezone.utc) for ts in dates])
        ends = pd.DatetimeIndex([(ts+pd.Timedelta(days=1)).to_pydatetime().replace(tzinfo=zone).astimezone(utc_timezone.utc) for ts in dates])
        _require(np.array_equal(group.local_day_start_utc.to_numpy(), starts.to_numpy()) and np.array_equal(group.local_day_end_utc.to_numpy(), ends.to_numpy()), 'Calendar local-day UTC boundaries differ from packaged rules')
        _require(np.array_equal(group.day_of_week, dates.dayofweek) and np.array_equal(group.is_weekend, dates.dayofweek>=5), 'Calendar weekday mismatch')
    _require(frame.rules_version.eq(info['rules_version']).all() and frame.timezone_rules_version.eq(info['timezone_rules_version']).all(), 'Calendar rule version mismatch')
    _require(frame.is_actual_holiday.eq(frame.actual_holiday_names.ne('')).all() and frame.is_observed_holiday.eq(frame.observed_holiday_names.ne('')).all(), 'Holiday names and flags differ')
    _require(frame.is_holiday.eq(frame.is_actual_holiday | frame.is_observed_holiday).all(), 'Holiday union mismatch')
    basis = np.where(frame.date.gt(support['as_of']), 'rule_based_future', 'rule_based_calendar')
    _require(np.array_equal(frame.calendar_basis, basis), 'Calendar basis/as-of mismatch')


def _native(frame, info):
    props = frame.properties_json.map(json.loads)
    geometries = shapely.from_wkb(frame.geometry.to_numpy())
    _require(not shapely.is_empty(geometries).any() and shapely.is_valid(geometries).all(), 'Invalid native display geometry')
    bounds=shapely.bounds(geometries)
    _require(np.isfinite(bounds).all() and (bounds[:,0]>=-180).all() and (bounds[:,2]<=180).all() and (bounds[:,1]>=-90).all() and (bounds[:,3]<=90).all(), 'Native geometry exceeds WGS84 coordinate bounds')
    _require(all(shapely.equals_exact(g, shape(json.loads(raw)), 0) for g,raw in zip(geometries,frame.geometry_json)), 'Native JSON/WKB geometry mismatch')
    _require(set(frame.group)=={'launches','shore_access','places','access_lines'}, 'Native delivery requires all four source groups')
    _require(frame.role.isin(['record','source_footprint']).all(), 'Invalid native feature role')
    _require(all(h3.is_valid_cell(c) and h3.get_resolution(c)==7 for c in frame.h3_index), 'Native labels require R7 identity')
    records = set(frame.loc[frame.role.eq('record'),'record_id'])
    _require(frame.loc[frame.role.eq('source_footprint'),'record_id'].isin(records).all(), 'Orphan native context footprint')
    for col in ('record_id','group','role','name','category','location_method','h3_index','label_point'):
        for value, prop in zip(frame[col], props):
            if hasattr(value, 'tolist'): value=value.tolist()
            if value is pd.NA or (not isinstance(value,(list,dict)) and pd.isna(value)): value=None
            _require(value==prop.get(col), f'Native property projection differs: {col}')
    for group, desc in info['groups'].items():
        _require(int((frame.group.eq(group)&frame.role.eq('record')).sum())==desc['records'], 'Native group count mismatch')
    _require(len(frame)==info['storage_counts']['features'], 'Native storage count mismatch')


def validate_delivery(root, *, expected_rows=None):
    """Verify exact product/column sets, receipt bindings and scientific invariants.

    Returns a compact report. Original source completeness/accuracy is represented
    by provenance; this check never promotes a partial inventory into complete data.
    """
    root = Path(root)
    contract = product_contract()['products']
    catalog = read_catalog(root)
    products = {p['artifact']['path']:p for p in catalog['products']}
    _require(len(products)==len(catalog['products']) and set(products)==set(contract), 'Human requires exactly the three final products')
    report = {}
    for name, spec in contract.items():
        info=products[name];table=pq.read_table(root/name);frame=table.to_pandas()
        _require(info['schema']==spec['schema'], f'Unsupported product schema: {name}')
        _require(info.get('primary_key')==spec['primary_key'], f'Primary key metadata mismatch: {name}')
        _require(info['artifact']['rows']==len(frame), f'Artifact row count mismatch: {name}')
        _require(table.column_names==[c['name'] for c in spec['columns']], f'Column contract mismatch: {name}')
        _require(not frame.empty and not frame[spec['primary_key']].isna().any().any() and not frame.duplicated(spec['primary_key']).any(), f'Null/duplicate/empty identity: {name}')
        for field in spec['columns']:
            actual=str(table.schema.field(field['name']).type)
            _require(actual==field['arrow_type'], f'Type contract mismatch: {name}/{field["name"]}: {actual}')
        if expected_rows and name in expected_rows:
            _require(len(frame)==expected_rows[name], f'Unexpected row count: {name}')
        {'static.parquet':_static,'dynamic-calendar.parquet':_calendar,'static-native.parquet':_native}[name](frame,info)
        report[name]={'rows':len(frame),'columns':table.num_columns,'sha256':info['artifact']['sha256']}
    return report
