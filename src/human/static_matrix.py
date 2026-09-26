"""Explicit static H3 matrix export; no marine transfer or temporal aggregation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import h3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

KEYS = ['H3_INDEX', 'H3_RESOLUTION']


def _units(component: str, column: str) -> str:
    if column in ('POPULATION', 'POPULATION_US_2020', 'POPULATION_CA_2021'):
        return 'allocated persons'
    if column == 'POPULATION_LOG1P':
        return 'ln(1 + allocated persons)'
    if column.endswith(('_QC', '_AVAILABLE', '_APPLIED', '_COMPLETE')):
        return 'boolean flag'
    if column == 'SOURCE_DATASET_COUNT':
        return 'source datasets'
    if column.endswith('_M'):
        return 'metres'
    if column.endswith('_COUNT') or column.startswith('BOAT_LAUNCHES_WITH_'):
        return 'source records' if column != 'COUNTRY_COUNT' else 'countries'
    if 'FRACTION' in column or 'RATIO' in column:
        return 'dimensionless ratio'
    if column.startswith('CENSUS_YEAR_'):
        return 'census year'
    return 'label or QC flag'


def combine_frames(frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    """Outer join independently supported static components, preserving nulls."""
    if not frames:
        raise ValueError('At least one static component is required.')
    output = None
    fields = {}
    for component, source in frames.items():
        if not component.replace('_', '').isalnum():
            raise ValueError(f'Invalid component name: {component}')
        frame = source.copy()
        if not set(KEYS).issubset(frame):
            raise ValueError(f'{component}: missing H3 identity columns')
        if frame.empty or frame[KEYS].isna().any().any() or frame.duplicated(KEYS).any():
            raise ValueError(f'{component}: empty, null or duplicate H3 identity')
        if frame.H3_INDEX.duplicated().any():
            raise ValueError(f'{component}: duplicate cell identity')
        for cell, resolution in frame[KEYS].itertuples(index=False, name=None):
            if not h3.is_valid_cell(cell) or resolution != 7 or h3.get_resolution(cell) != resolution:
                raise ValueError(f'{component}: expected valid native H3 resolution 7')
        if 'geometry' in frame:
            frame = frame.drop(columns='geometry')
        rename = {}
        for column in frame.columns.difference(KEYS, sort=False):
            name = f'{component}__{column}'
            rename[column] = name
            fields[name] = {'component': component, 'variable': column, 'units': _units(component, column)}
        frame = frame.rename(columns=rename)
        output = frame if output is None else output.merge(frame, on=KEYS, how='outer', validate='one_to_one')
    return output.sort_values(KEYS).reset_index(drop=True), fields


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def export(manifests: list[Path], output: Path, input_manifest: Path | None = None) -> Path:
    """Consume checksum-verified native build artifacts, retaining provenance."""
    if output.exists():
        raise FileExistsError(output)
    supplemental = None
    if input_manifest is not None:
        supplemental = json.loads(input_manifest.read_text())
        for record in supplemental["records"]:
            if _checksum(Path(record["path"])) != record["sha256"]:
                raise ValueError(f"Input inventory checksum mismatch: {record['path']}")
    frames, provenance = {}, []
    selectors = {
        'population_context_h3_r7.parquet': 'population',
        'boat_launch_access_h3_r7.parquet': 'boat_launch_access',
        'public_shore_access_h3_r7.parquet': 'public_shore_access',
        'places_of_interest.json': 'places',
    }
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('stage') != 'build':
            raise ValueError('Static matrix requires native build manifests.')
        selected = False
        for artifact in manifest['artifacts']:
            path = Path(artifact['path'])
            component = selectors.get(path.name)
            if component is None:
                continue
            if component in frames:
                raise ValueError(f'Duplicate component: {component}')
            if _checksum(path) != artifact['sha256']:
                raise ValueError(f'Artifact checksum mismatch: {path}')
            if component == 'places':
                items = json.loads(path.read_text())
                points = pd.DataFrame(items)
                # Catalog ranking scores are intentionally excluded from the matrix.
                lat = 'lat' if 'lat' in points else 'latitude'
                lon = 'lon' if 'lon' in points else 'longitude'
                points['H3_INDEX'] = [h3.latlng_to_cell(float(a), float(b), 7) for a, b in zip(points[lat], points[lon])]
                frame = points.groupby('H3_INDEX').size().rename('CATALOG_RECORD_COUNT').reset_index()
                frame['H3_RESOLUTION'] = 7
                frame['MEASUREMENT_STATUS'] = 'derived'
                frame['SOURCE_COVERAGE_COMPLETE'] = False
            else:
                frame = pd.read_parquet(path)
            frames[component] = frame
            selected = True
        if not selected:
            raise ValueError(f'No supported static component: {manifest_path}')
        provenance.append({'manifest_path': str(manifest_path.resolve()), 'manifest_sha256': _checksum(manifest_path), 'native_manifest': manifest})
    frame, fields = combine_frames(frames)
    metadata = {
        'schema_version': 1, 'fields': fields, 'components': sorted(frames),
        'sources': provenance, 'supplemental_input_manifest': supplemental, 'model_eligible': False,
        'limitations': [
            'Static native H3 R7 union; no marine transfer, viewability, observer counts or detection probability.',
            'Population retains US 2020 and Canada 2021 vintages; totals are not contemporaneous.',
            'Absent component cells remain null; partial access coverage does not establish absence.',
            'Places count catalog records, not deduplicated physical sites or measured visits.',
            'Temporal activity, routing and legacy viewshed are excluded.',
        ],
    }
    table = pa.Table.from_pandas(frame, preserve_index=False)
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), b'human_static_matrix': json.dumps(metadata, sort_keys=True).encode()})
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix('.parquet.tmp')
    pq.write_table(table, temp, compression='zstd')
    temp.replace(output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--input-manifest', type=Path, help='Optional checksum-verified source and external geometry inventory.')
    args = parser.parse_args(argv)
    print(export(args.manifest, args.output, args.input_manifest))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
