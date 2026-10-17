"""Human-only static delivery with verified shared selection and civil calendar."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

from human import get_population
from human.pipelines.population.static.sources import checksum, read_sources, bundle_inventory
from human.pipelines.population.collected import add_collected_population, display_contract, public_population_delivery
from human.pipelines.population.water_support import load_qualified_water, qualify_water_support
import shapely
from human.utils.study import load_static_study
from human.utils.artifacts import write_study_parquet as _write
from human.pipelines.calendar.lookup import build_calendar_lookup

ACCESS_PREFIXES = ('boat_launch_access__', 'public_shore_access__', 'places__')


def _export_static_study(*, study_config, workspace, source_manifest, output_dir,
                        retained_static=None, allow_partial=False, workers=1, as_of=None, native_source_specification=None,
                        population_cache_workspace=None, infrastructure_rebuilt=False, calendar_start=None, calendar_end=None):
    """Build one static table and a separate jurisdiction/date calendar lookup.

    Retained access measurements remain at their native snapshot/support. Missing
    reporting cells stay null. Legacy population-transfer and effort/viewability
    columns are intentionally not carried into this static delivery.
    """
    study = load_static_study(study_config)
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Use an empty task-owned staging directory')
    resolution = study['identity']['h3_resolution']
    inventory = bundle_inventory(source_manifest)
    collection = inventory["metadata"].get("collection_footprint")
    if collection is not None:
        import hashlib
        member_sha = hashlib.sha256(("\n".join(study["cells"])+"\n").encode()).hexdigest()
        if (collection.get("target_membership_sha256") != member_sha
                or collection.get("h3_resolution") != resolution
                or collection.get("radii_km") != [25., 50., 100.]):
            raise ValueError("Prepared collection footprint does not match the study/radii")
    qualified = load_qualified_water(study)
    population = get_population(target_h3=study['cells'], h3_res=resolution,
        radii_km=(25, 50, 100), include_bands=False, workspace=population_cache_workspace or workspace,
        source_manifest=source_manifest, mode='offline', allow_partial=True,
        resource_limits={'workers': workers, 'buffer_tolerance_m': 0.25},
        cache_only=population_cache_workspace is not None)
    frame = population.table
    population_metadata = population.metadata
    if qualified is not None:
        water, provenance = qualified
        coverage = read_sources(source_manifest).coverage
        geometry = shapely.union_all(coverage.to_crs(population_metadata['area_crs']).geometry.values) if coverage is not None else shapely.GeometryCollection()
        frame, population_metadata = qualify_water_support(frame, population_metadata,
            census_coverage=geometry, water=water, provenance=provenance)
    frame, population_metadata = add_collected_population(frame, population_metadata)
    frame, population_metadata = public_population_delivery(frame, population_metadata)
    metadata = {'schema': 'human.study.static.v1', 'population': population_metadata,
        'primary_key': ['h3_index'], 'h3_resolution': resolution, 'study': study['identity']}
    if retained_static is not None:
        source = Path(retained_static)
        original_sha = checksum(source)
        native = pd.read_parquet(source)
        if native.h3_index.duplicated().any() or not native.h3_resolution.eq(resolution).all():
            raise ValueError('Retained static identity is invalid')
        columns = [c for c in native if c.startswith(ACCESS_PREFIXES)]
        frame = frame.merge(native[['h3_index', *columns]], on='h3_index', how='left', validate='one_to_one')
        if checksum(source) != original_sha:
            raise ValueError('Retained static source changed during export')
        metadata['retained_static_provenance'] = {'path': str(source.resolve()), 'sha256': original_sha,
            'columns': columns, 'qualification': ('source_build_summaries_rebuilt_from_verified_originals'
                if infrastructure_rebuilt else 'retained_research_snapshot_not_reacquired'),
            'missing_rows': len(set(frame.h3_index) - set(native.h3_index)),
            'limitations': ['Retained mapped infrastructure is not a complete inventory or evidence of use.',
                'No access values are filled for newly selected cells or unavailable measurements.']}
        # Final-only inputs embed their methods; do not discard ancestry when
        # the receipt deliberately lives outside the public delivery directory.
        import pyarrow.parquet as pq
        embedded_source = (pq.read_schema(source).metadata or {}).get(b'human_study')
        if embedded_source:
            metadata['retained_static_provenance']['source_metadata'] = json.loads(embedded_source)
        else:
            matrix_source = (pq.read_schema(source).metadata or {}).get(b'human_static_matrix')
            if matrix_source:
                metadata['retained_static_provenance']['source_metadata'] = json.loads(matrix_source)
        companion = source.with_suffix('.metadata.json')
        if companion.exists():
            metadata['retained_static_provenance']['companion'] = {
                'path': str(companion.resolve()), 'sha256': checksum(companion),
                'role': 'retained native source methods, support, units and provenance'}
    native = None
    if native_source_specification is not None:
        from human.pipelines.native.features import export_native_features, summary_fields
        native = export_native_features(native_source_specification, output)
        metadata['native_features'] = native['artifact']
        metadata['infrastructure_fields'] = summary_fields(frame.columns)
    static = _write(frame, output / f'human-r{resolution}-static.parquet', metadata)
    time_cfg = study['config']['time']
    today = str(as_of or date.today())
    first=calendar_start or time_cfg['start']
    end_exclusive=(date.fromisoformat(calendar_end)+timedelta(days=1)).isoformat() if calendar_end else time_cfg['end_exclusive']
    calendar = build_calendar_lookup(first, end_exclusive, as_of=today)
    temporal = _write(calendar, output / 'calendar-temporal.parquet', {
        'schema': 'human.study.calendar_lookup.v1', 'primary_key': ['date', 'jurisdiction'],
        'spatial_support': 'jurisdiction_reference; no H3 expansion', 'study': study['identity'],
        'temporal_support': {'start': first, 'end_exclusive': end_exclusive,
            'date_semantics': 'local civil date; not UTC daily labels',
            'timezones': sorted(calendar.timezone.unique()), 'as_of': today},
        'rules_version': str(calendar.rules_version.iloc[0]),
        'timezone_rules_version': str(calendar.timezone_rules_version.iloc[0]),
        'limitations': ['Rule-based holiday context, not observed human presence or work attendance.',
            'National rows are federal reference calendars; regional rows follow applicable state/provincial rules.',
            'is_actual_holiday means nominal rule date; is_observed_holiday means additional shifted observance.',
            'Convert a timestamp to its own local date before joining; reference timezones are not jurisdiction-wide timezone maps.',
            'Future dates are rule-based and may change with legislation.'],
    })
    # Fail if shared selection changed while a potentially long computation ran.
    if load_static_study(study_config)['identity'] != study['identity']:
        raise ValueError('Study configuration changed during computation')
    catalog = {'schema': 'human.study.v1', 'products': [static, temporal, *([native] if native else [])],
        'requested_calendar_start': first,
        'requested_calendar_end': (date.fromisoformat(end_exclusive)-timedelta(days=1)).isoformat(),
        'study': study['identity'], 'model_eligible': False,
        'product_scope': 'static_human_population_infrastructure_and_separate_calendar',
        'default_population': display_contract(),
        'limitations': ['US 2020 / Canada 2021 mixed-vintage resident baseline, not annual historical population.',
            'Population estimates use collected source allocations and explicitly assume missing contributions are zero; coverage and imputation flags are retained.',
            'Cumulative WGS84 geodesic radii are centered on H3 centers; overlapping neighborhoods must not be summed.',
            'No AIS, viewshed, opportunity, effort score or dynamic activity is part of this delivery.']}
    path = output / 'catalog.json'
    path.write_text(json.dumps(catalog, indent=2, sort_keys=True))
    return path


def export_static_study(*, study_config, workspace, source_manifest, output_dir,
                        retained_static=None, allow_partial=False, workers=1, as_of=None,
                        native_source_specification=None, metadata_dir=None):
    """Publish final-only Parquets; stage processing and receipts in the workspace."""
    import hashlib
    import tempfile
    from human.pipelines.delivery.finalize import finalize_delivery
    output, work = Path(output_dir), Path(workspace)
    if output.exists() and any(output.iterdir()): raise FileExistsError('Use an empty final delivery directory')
    if work.resolve().is_relative_to(output.resolve()):
        raise ValueError('The processing workspace must be outside the final data directory')
    work.mkdir(parents=True,exist_ok=True)
    identity=hashlib.sha256(str(output.resolve()).encode()).hexdigest()[:16]
    receipts=Path(metadata_dir) if metadata_dir else work/'delivery-metadata'/identity
    with tempfile.TemporaryDirectory(prefix='human-delivery-',dir=work) as temporary:
        staging=Path(temporary)/'products'
        _export_static_study(study_config=study_config,workspace=workspace,source_manifest=source_manifest,
            output_dir=staging,retained_static=retained_static,allow_partial=allow_partial,
            workers=workers,as_of=as_of,native_source_specification=native_source_specification)
        return finalize_delivery(staging,output,receipts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('study-config', 'workspace', 'source-manifest', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--metadata-dir', type=Path, help='Toolkit-managed receipts outside the final output directory')
    parser.add_argument('--retained-static', type=Path)
    parser.add_argument('--native-source-specification', type=Path, help='Checksum-bound retained launch/shore/place sources')
    parser.add_argument('--allow-partial', action='store_true')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--as-of')
    args = parser.parse_args()
    print(export_static_study(**vars(args)))


if __name__ == '__main__':
    main()
