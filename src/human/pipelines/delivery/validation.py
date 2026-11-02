"""Semantic validation for the two supported Human delivery products."""
from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from human.pipelines.population.collected import DISPLAY_LABELS, METHOD
from human.pipelines.delivery.finalize import read_catalog
from human.pipelines.delivery.names import STATIC_NAMES, PRESENCE_COLUMNS
from human.pipelines.native.features import summary_fields, delivery_fields


def product_contract(version=7):
    return json.loads(files('human').joinpath(f'resources/contracts/final-products.v{version}.json').read_text())


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
    if info.get('ports'):
        from human.pipelines.native.ports import STATUS, COMPLETE
        _require(frame[STATUS].eq('partial_published_port_inventory').all() and frame[COMPLETE].eq(False).all(), 'Port inventory must remain partial')


def _calendar(frame, info):
    import holidays
    import tzdata
    from human.pipelines.calendar.lookup import build_calendar_lookup, compact_calendar, compact_metadata
    support = info['temporal_support']
    _require(info['rules_version']==holidays.__version__ and info['timezone_rules_version']==tzdata.__version__, 'Calendar requires its pinned holiday/timezone rules')
    expected_info = compact_metadata(info)
    for key in ('jurisdictions', 'holiday_names_policy', 'calendar_fields', 'spatial_support'):
        _require(info.get(key)==expected_info[key], 'Calendar compact policy metadata mismatch: '+key)
    expected = compact_calendar(build_calendar_lookup(support['start'], support['end_exclusive'], as_of=support['as_of']))
    _require(frame.equals(expected), 'Calendar values differ from the pinned country/timezone holiday union or civil-date fields')


def validate_delivery(root, *, expected_rows=None):
    """Verify exact product/column sets, receipt bindings and scientific invariants.

    Returns a compact report. Original source completeness/accuracy is represented
    by provenance; this check never promotes a partial inventory into complete data.
    """
    root = Path(root)
    contract = product_contract()['products']
    catalog = read_catalog(root)
    products = {p['artifact']['path']:p for p in catalog['products']}
    _require(len(products)==len(catalog['products']) and set(products)==set(contract), 'Human requires exactly the two final products')
    report = {}
    for name, spec in contract.items():
        info=products[name];table=pq.read_table(root/name);frame=table.to_pandas()
        _require(info['schema']==spec['schema'], f'Unsupported product schema: {name}')
        _require(info.get('primary_key')==spec['primary_key'], f'Primary key metadata mismatch: {name}')
        _require(info['artifact']['rows']==len(frame), f'Artifact row count mismatch: {name}')
        fields = list(spec['columns'])
        extension = product_contract().get('extensions', {}).get('ports', {})
        if name == 'static.parquet' and any(c['name'] in table.column_names for c in extension.get('columns', [])):
            fields += extension['columns']
        _require(table.column_names==[c['name'] for c in fields], f'Column contract mismatch: {name}')
        _require(not frame.empty and not frame[spec['primary_key']].isna().any().any() and not frame.duplicated(spec['primary_key']).any(), f'Null/duplicate/empty identity: {name}')
        for field in fields:
            actual=str(table.schema.field(field['name']).type)
            _require(actual==field['arrow_type'], f'Type contract mismatch: {name}/{field["name"]}: {actual}')
        if expected_rows and name in expected_rows:
            _require(len(frame)==expected_rows[name], f'Unexpected row count: {name}')
        if name == 'static.parquet':
            from human.pipelines.delivery.static import restore_static
            full, original_info = restore_static(table, info)
            _static(full.to_pandas(), original_info)
            _require(all(frame[name].notna().all() and frame[name].isin([0,1]).all() for name in PRESENCE_COLUMNS), 'Presence indicators require nonnull integer 0/1 values')
            _require(info['infrastructure_fields']==delivery_fields(), 'Compact infrastructure dictionary mismatch')
            _require(set(info['population']['fields'])=={STATIC_NAMES[name] for name in DISPLAY_LABELS}, 'Compact population dictionary mismatch')
            _require({k:v['label'] for k,v in info['population']['fields'].items()}=={STATIC_NAMES[k]:v for k,v in DISPLAY_LABELS.items()}, 'Compact population labels differ')
            for metric, definition in info['population']['fields'].items():
                original_metric=next(raw for raw,public in STATIC_NAMES.items() if public==metric)
                base=original_info['population']['fields'][original_metric]['base_metric']
                _require('coverage_status' not in definition and definition.get('support_location')=='external_static_audit', 'Population support must stay in external audit')
                _require(definition['audit_fields']['coverage_status']==base+'__coverage_status', 'Audit population coverage binding mismatch')
                _require('unverified_geographic_support' not in definition and definition['audit_fields']['unverified_geographic_support']==base+'__unverified_geographic_support', 'Audit population geographic support binding mismatch')
                if 'density' in metric:
                    _require(definition['audit_fields'].get('cell_area_km2')=='cell_area_km2', 'Density denominator must stay in audit')
                _require(definition['method']==METHOD and definition['zero_imputation_rule']=='coverage_status in [partial, unavailable]', 'Compact population method mismatch')
        else:
            _calendar(frame, info)
        report[name]={'rows':len(frame),'columns':table.num_columns,'sha256':info['artifact']['sha256']}
    from human.pipelines.native.ports import COUNT
    from human.pipelines.delivery.static import restore_static
    static = pq.read_table(root/'static.parquet').to_pandas()
    info = products['static.parquet']
    if info.get('ports'):
        from collections import Counter
        _require(static.has_port.notna().all(), 'Invalid port counts')
        _require(bool(info.get('ports', {}).get('sha256')), 'Port source provenance missing')
        assignments=info.get('port_h3_by_source_id', {})
        _require(set(assignments)==set(info['ports']['selected_source_feature_ids']), 'Port source/cell binding missing')
        _require(all(h3.is_valid_cell(c) and h3.get_resolution(c)==7 for c in assignments.values()), 'Port source assignment requires R7 cells')
        counts=Counter(assignments.values())
        _require(static.has_port.tolist()==[int(counts[c]>0) for c in static.h3_index], 'Port counts differ from retained source/cell bindings')
        full,_=restore_static(pq.read_table(root/'static.parquet'), info)
        _require(full[COUNT].to_pylist()==[counts[c] for c in static.h3_index], 'Audited port multiplicities differ from source bindings')
    else:
        _require(static.has_port.eq(0).all(), 'Unprovisioned port presence must use the reviewed zero encoding')
    return report
