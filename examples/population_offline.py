"""Runnable synthetic population example. No provider access or real census data."""
from pathlib import Path
import argparse
import geopandas as gpd
import h3
from shapely.geometry import box
from human import get_population
from human.pipelines.population.static import PopulationSources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path('synthetic-population-workspace'))
    args = parser.parse_args()
    sources = PopulationSources(
        gpd.GeoDataFrame({'source_id': ['synthetic:one'], 'country': ['SYNTHETIC'],
            'jurisdiction': ['synthetic'], 'vintage': [2020], 'population': [1000.]},
            geometry=[box(-123.01, 47.99, -122.99, 48.01)], crs=4326),
        gpd.GeoDataFrame(geometry=[box(-131, 40, -115, 56)], crs=4326),
        {'synthetic': True, 'license': 'CC0 synthetic example',
         'coverage_assertion': 'Synthetic world is empty outside the one source polygon.'})
    result = get_population(target_h3=[h3.latlng_to_cell(48, -123, 7)],
        workspace=args.workspace, sources=sources, include_bands=True, mode='offline')
    print(result.table.to_string(index=False))
    print(result.to_parquet(args.workspace / 'synthetic-population-r7-static.parquet'))


if __name__ == '__main__':
    main()
