"""Explicit collected-source estimates; raw unknown counts and strict totals stay intact."""
from __future__ import annotations

import copy
import numpy as np
import pandas as pd

FIELD = 'population_within_100km_collected'
BASE = 'population_within_100km'
METHOD = 'area_weighted_collected_sources_missing_as_zero.v1'
LABEL = 'Population Within 100km'
BASES = {'population_count': ('Population Count', None),
         **{f'population_within_{r}km': (f'Population Within {r}km', r) for r in (25, 50, 100)}}
DISPLAY_LABELS = {base + '_collected': label for base, (label, _) in BASES.items()}
DISPLAY_LABELS['population_density_per_km2_collected'] = 'Population Density (people/km²)'
COLLECTED_LIMITATION = ('Unavailable source/geographic contributions are assumed zero for this estimate. '
                        'Source provenance and zero-imputation flags remain available; raw unknown census counts are not overwritten.')


def add_collected_population(table, metadata):
    """Apply one documented missing-contribution policy to each supported metric.

    The finite available-source subtotal is reused without rounding or changing
    allocation. Only these separately named estimates assume missing contributions
    are zero; source values, strict totals and their qualifiers are preserved.
    """
    frame, meta = table.copy(deep=True), copy.deepcopy(metadata)
    summaries = {}
    for base, (label, radius) in BASES.items():
        if base + '__available_source_subtotal' not in frame:
            continue
        field = base + '_collected'
        subtotal = pd.to_numeric(frame[base + '__available_source_subtotal'], errors='raise')
        if (subtotal.dropna() < 0).any() or not np.isfinite(subtotal.dropna()).all():
            raise ValueError('Collected population subtotal must be finite and nonnegative')
        status = frame[base + '__coverage_status']
        if not status.isin(['complete', 'known_zero', 'partial', 'unavailable']).all():
            raise ValueError('Collected population requires explicit source coverage status')
        frame[field] = subtotal.fillna(0.).astype(float)
        if radius is None:
            frame[field + '__coverage_status'] = status
        frame[field + '__zero_imputed'] = ~status.isin(['complete', 'known_zero'])
        frame[field + '__method'] = METHOD
        meta['fields'][field] = {'units': 'persons', 'measurement_type': 'derived_collected_source_sum',
            'coverage_status': field + '__coverage_status', 'zero_imputed': field + '__zero_imputed',
            'method_column': field + '__method', 'source_subtotal': base + '__available_source_subtotal',
            'base_metric': base, 'radius_km': radius, 'label': label, 'method': METHOD,
            'limitation': COLLECTED_LIMITATION}
        if radius is not None:
            meta['fields'][field].pop('coverage_status', None)
        if base in meta['fields']:
            strict_label = 'Population per cell' if radius is None else f'Population within {radius} km'
            meta['fields'][base]['label'] = strict_label + ' (complete coverage only)'
        summaries[field] = {'field': field, 'non_null_rows': len(frame),
            'zero_imputed_rows': int(frame[field + '__zero_imputed'].sum()),
            'zero_imputed_zero_rows': int((frame[field + '__zero_imputed'] & frame[field].eq(0)).sum()),
            'supported_zero_rows': int((~frame[field + '__zero_imputed'] & frame[field].eq(0)).sum())}
    if 'population_count_collected' in frame and 'cell_area_km2' in frame:
        area = pd.to_numeric(frame.cell_area_km2, errors='raise')
        if not np.isfinite(area).all() or not area.gt(0).all():
            raise ValueError('Collected density requires finite positive full-cell areas')
        field = 'population_density_per_km2_collected'
        frame[field] = frame.population_count_collected / area
        for qualifier in ('zero_imputed', 'method'):
            frame[field + '__' + qualifier] = frame['population_count_collected__' + qualifier]
        meta['fields'][field] = {**meta['fields']['population_count_collected'],
            'units': 'persons/km2', 'label': DISPLAY_LABELS['population_density_per_km2_collected'],
            'zero_imputed': field + '__zero_imputed',
            'method_column': field + '__method', 'denominator': 'full_target_h3_polygon_area_km2'}
        meta['fields'][field].pop('coverage_status', None)
        if 'population_density_per_km2' in meta['fields']:
            meta['fields']['population_density_per_km2']['label'] = 'Population density per km² (complete coverage only)'
    meta['collected_population_summaries'] = summaries
    if FIELD in summaries:
        meta['collected_population_summary'] = summaries[FIELD]
    return frame, meta


def display_contract():
    return {'field': FIELD, 'label': LABEL, 'units': 'persons', 'method': METHOD,
        'zero_imputation_flag': FIELD + '__zero_imputed',
        'method_field': FIELD + '__method',
        'description': 'Sum of collected census polygon allocations within a WGS84 100 km radius of the H3 center; unavailable contributions count as zero. Source provenance and the zero-imputation flag remain available.'}



DISTANCE_BASES = tuple(f'population_within_{radius}km' for radius in (25, 50, 100))
REMOVED_DISTANCE_COVERAGE_FIELDS = frozenset(base + '_collected__coverage_status' for base in DISTANCE_BASES)
REMOVED_POPULATION_COVERAGE_FIELDS = REMOVED_DISTANCE_COVERAGE_FIELDS | {'population_density_per_km2_collected__coverage_status'}


PUBLIC_BASES = (*DISTANCE_BASES, 'population_count', 'population_density_per_km2')


def public_population_delivery(table, metadata):
    """Publish one collected quantity per support without changing any values.

    Strict calculations remain in the standalone engine/validation stages. Public
    delivery retains source-subtotals and underlying source/missingness provenance.
    Collected distance and density coverage aliases are intentionally excluded.
    Labels and family describe the quantities; metadata/help carries assumptions.
    """
    meta = copy.deepcopy(metadata)
    fields = meta['fields']
    for base in PUBLIC_BASES:
        if base in table or base in fields:
            if base + '_collected' not in table or base + '_collected' not in fields:
                raise ValueError(f'Cannot remove {base} without its collected estimate')
        fields.pop(base, None)
    frame = table.drop(columns=[name for name in (*PUBLIC_BASES, *REMOVED_POPULATION_COVERAGE_FIELDS) if name in table])
    for name in REMOVED_POPULATION_COVERAGE_FIELDS:
        fields.pop(name, None)
    for definition in fields.values():
        if definition.get('coverage_status') in REMOVED_POPULATION_COVERAGE_FIELDS:
            definition.pop('coverage_status')
    for field, label in DISPLAY_LABELS.items():
        if field in fields:
            fields[field].update(label=label, family='Population', limitation=COLLECTED_LIMITATION)
    meta['distance_delivery'] = {
        'schema': 'human.population.distance.collected_only.v1',
        'fields': [base + '_collected' for base in DISTANCE_BASES if base + '_collected' in frame],
        'qualifiers': 'Original source-subtotal, missing-source, geographic-support and zero-imputation qualifiers remain evidence. Collected distance coverage aliases are excluded.',
        'excluded_fields': sorted(REMOVED_DISTANCE_COVERAGE_FIELDS),
        'strict_totals': 'Internal validation only; excluded from the shared-study public field dictionary and table.',
    }
    meta['population_delivery'] = {
        'schema': 'human.population.collected_only.v1', 'family': 'Population',
        'excluded_coverage_fields': sorted(REMOVED_POPULATION_COVERAGE_FIELDS),
        'fields': [field for field in DISPLAY_LABELS if field in frame],
        'limitation': COLLECTED_LIMITATION,
        'strict_totals': 'Internal validation only; excluded from the shared-study public field dictionary and table.',
    }
    return frame, meta


def public_distance_delivery(table, metadata):
    """Compatibility entry point for the unified collected-only public projection."""
    return public_population_delivery(table, metadata)
