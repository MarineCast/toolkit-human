"""Explicit local study export retaining native resolution, time support and provenance."""
from __future__ import annotations

import json
from pathlib import Path

import h3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from human.pipelines.population.static.sources import checksum
from human.legacy.scope import HUMAN_ACTIVITY_FAMILIES


def _verified_artifact(manifest_path, relative_path):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    matches = [item for item in manifest['artifacts'] if item['path'] == relative_path]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one manifest artifact: {relative_path}')
    path = manifest_path.parent / relative_path
    if checksum(path) != matches[0]['sha256']:
        raise ValueError(f'Artifact checksum mismatch: {path}')
    return path, manifest


from human.utils.artifacts import write_study_parquet as _write


def _export_study(*, population, output_dir, calendar_config, static_input=None, dynamic_inputs=()):
    """Export population/static H3, non-spatial calendar, and native spatial activity.

    `population` is a PopulationResult. Optional retained artifacts are checksum-verified
    against explicit release manifests. No acquisition or legacy activity rebuild occurs.
    Dynamic families are ferry source products; AIS is owned by toolkit-ais. Cross-domain observation-
    opportunity proxies belong to modeling and are rejected before any writes.
    Each dynamic descriptor supplies manifest, artifact, family, date_column,
    cell_column, resolution_column, and support_manifest. Different native resolutions
    produce separate files; local days retain their original UTC interval boundaries.
    """
    from human.legacy.calendar.download import download
    from human.legacy.calendar.build import build
    dynamic_inputs = tuple(dynamic_inputs)
    for entry in dynamic_inputs:
        if entry['family'] not in HUMAN_ACTIVITY_FAMILIES:
            raise ValueError(
                f"{entry['family']!r} is outside Human study activity scope. "
                "Supported family is ferry; AIS belongs to toolkit-ais and observation-opportunity "
                "composites belong to modeling."
            )
    output_dir = Path(output_dir)
    cfg = yaml.safe_load(Path(calendar_config).read_text())
    start, end = pd.Timestamp(cfg['time']['start_date']), pd.Timestamp(cfg['time']['end_date'])
    resolution = population.metadata['h3_resolution']
    frame = population.table
    cells = set(frame.h3_index)
    manifest = {'schema': 'human.study.v1', 'bbox': population.metadata.get('bbox'),
        'requested_calendar_start': str(start.date()), 'requested_calendar_end': str(end.date()),
        'products': [], 'model_eligible': False,
        'product_scope': 'human_source_inputs; modeling_composites_excluded', 'limitations': [
            'Static population is computed once; no census interpolation or daily population expansion.',
            'Dynamic files retain native cadence, coverage and timezone; no implied full-period availability.']}
    static_meta = {'schema': 'human.study.static.v1', 'population': population.metadata,
                   'primary_key': ['h3_index'], 'h3_resolution': resolution}
    if static_input:
        path, provenance = _verified_artifact(static_input['manifest'], static_input['artifact'])
        native = pd.read_parquet(path)
        if native.H3_INDEX.duplicated().any() or not native.H3_RESOLUTION.eq(resolution).all():
            raise ValueError('Retained static matrix has duplicate cells/incompatible resolution')
        native = native.rename(columns={'H3_INDEX': 'h3_index', 'H3_RESOLUTION': 'h3_resolution'})
        frame = frame.merge(native, on=['h3_index','h3_resolution'], how='left', validate='one_to_one')
        static_meta['retained_static_provenance'] = {'path': str(path), 'sha256': checksum(path),
            'manifest': str(static_input['manifest']), 'manifest_sha256': checksum(static_input['manifest']),
            'qualification': provenance.get('qualification'),
            'native_schema_metadata': {k.decode(): v.decode() for k,v in (pq.read_schema(path).metadata or {}).items()}}
    manifest['products'].append(_write(frame, output_dir / f'human-r{resolution}-static.parquet', static_meta))
    # Reuse the independently validated calendar producer, with isolated absolute outputs.
    work = output_dir / '.workspace/calendar'
    work.mkdir(parents=True, exist_ok=True)
    calendar_cfg = {**cfg, 'raw': {'holiday_snapshot': str((work/'holidays.parquet').resolve()),
        'manifest_path': str((work/'raw-manifest.json').resolve())},
        'output': {'daily_path': str((work/'calendar.parquet').resolve()),
            'manifest_path': str((work/'build-manifest.json').resolve())},
        'inspection': {'report_path': str((work/'calendar.html').resolve())}}
    config_path = work/'calendar.yaml'
    config_path.write_text(yaml.safe_dump(calendar_cfg, sort_keys=False))
    download(config_path, overwrite=True)  # Local python-holidays; no provider requests.
    calendar_path = build(config_path, overwrite=True)
    calendar = pd.read_parquet(calendar_path)
    if calendar.date.duplicated().any():
        raise ValueError('Calendar date is not unique')
    calendar_meta = {'schema': 'human.study.temporal.v1', 'primary_key': ['date'],
        'spatial_support': 'non_spatial', 'temporal_support': cfg['time'],
        'native_manifest': json.loads((work/'build-manifest.json').read_text()),
        'limitations': ['Calendar context and legacy calendar proxy weights are not observed human presence.']}
    manifest['products'].append(_write(calendar, output_dir/'calendar-temporal.parquet', calendar_meta))
    for entry in dynamic_inputs:
        path, source_manifest = _verified_artifact(entry['manifest'], entry['artifact'])
        support_path, _ = _verified_artifact(entry['manifest'], entry['support_manifest'])
        support = json.loads(support_path.read_text())
        native_res = int(support['native_h3_resolution'])
        cell_column, date_column = entry['cell_column'], entry['date_column']
        # Keep only the bounded study region at this product's own native centers.
        bbox = population.metadata.get('bbox')
        if native_res == resolution:
            selected = cells
        elif bbox is not None:
            from human.pipelines.population.static.geometry import target_cells
            selected = set(target_cells(bbox, None, native_res, 100000))
        else:
            raise ValueError('A bbox is required to select a different native dynamic resolution')
        native = pd.read_parquet(path, filters=[(cell_column, 'in', sorted(selected)),
            (date_column, '>=', start), (date_column, '<=', end)])
        if native.duplicated([date_column,cell_column]).any() or native[[date_column,cell_column]].isna().any().any():
            raise ValueError('Dynamic source has null/duplicate date-cell identity')
        if not native[entry['resolution_column']].eq(native_res).all():
            raise ValueError('Dynamic source has mixed resolutions')
        if any(not h3.is_valid_cell(c) or h3.get_resolution(c)!=native_res for c in native[cell_column]):
            raise ValueError('Invalid native dynamic H3 identity')
        if not {'native_interval_start_utc','native_interval_end_utc'}.issubset(native):
            raise ValueError('Native temporal support requires explicit UTC interval boundaries')
        native = native.rename(columns={cell_column:'h3_index', date_column:'date',entry['resolution_column']:'h3_resolution'})
        native = native.sort_values(['date','h3_index']).reset_index(drop=True)
        metadata = {'schema':'human.study.dynamic.v1', 'primary_key':['date','h3_index'],
            'h3_resolution':native_res, 'family':entry['family'], 'native_support':support,
            'source_artifact_sha256':checksum(path), 'source_manifest_sha256':checksum(entry['manifest']),
            'source_manifest':str(entry['manifest']), 'source_qualification':source_manifest.get('qualification'),
            'actual_start':str(native.date.min()), 'actual_end':str(native.date.max()),
            'limitations':['Retained native research artifact; current checksum/schema validation does not validate empirical accuracy.',
                'America/Los_Angeles local days retain UTC boundaries, including 23/25-hour DST days.',
                'Unavailable dates/cells are not zero; no future activity or missing historical coverage is fabricated.']}
        manifest['products'].append(_write(native,output_dir/f"{entry['family']}-r{native_res}-dynamic.parquet",metadata))
    catalog = output_dir/'catalog.json'
    if catalog.exists():
        raise FileExistsError(catalog)
    catalog.write_text(json.dumps(manifest,indent=2,sort_keys=True))
    return catalog


def export_study(*, population, output_dir, calendar_config, static_input=None,
                 dynamic_inputs=(), metadata_dir=None):
    """Export native grains as final-only Parquets with external build receipts."""
    import hashlib
    import tempfile
    from human.pipelines.delivery.finalize import finalize_delivery
    # Validate scope before creating any target or staging directory.
    dynamic_inputs=tuple(dynamic_inputs)
    for entry in dynamic_inputs:
        if entry['family'] not in HUMAN_ACTIVITY_FAMILIES:
            raise ValueError(f"{entry['family']!r} is outside Human study activity scope. Supported family is ferry; AIS belongs to toolkit-ais and observation-opportunity composites belong to modeling.")
    output=Path(output_dir)
    if output.exists() and any(output.iterdir()): raise FileExistsError('Use an empty final delivery directory')
    identity=hashlib.sha256(str(output.resolve()).encode()).hexdigest()[:16]
    receipts=Path(metadata_dir) if metadata_dir else Path.cwd()/'data/delivery-metadata'/identity
    receipts.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='human-study-',dir=receipts.parent) as temporary:
        staging=Path(temporary)/'products'
        _export_study(population=population,output_dir=staging,calendar_config=calendar_config,
            static_input=static_input,dynamic_inputs=dynamic_inputs)
        return finalize_delivery(staging,output,receipts)
