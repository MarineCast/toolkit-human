"""Bounded SYNTHETIC benchmark of grouped versus direct source intersections."""
import argparse
import json
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from shapely.geometry import box

from human import get_population
from human.pipelines.population.static import PopulationSources


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True,help='Use a fresh directory for measured computation.')
    args=parser.parse_args()
    polygons=[box(-124+x*.07,47.3+y*.07,-123.98+x*.07,47.32+y*.07) for x in range(30) for y in range(20)]
    sources=PopulationSources(gpd.GeoDataFrame({'source_id':[f'synthetic:{i}' for i in range(len(polygons))],
        'country':['SYNTHETIC']*len(polygons),'jurisdiction':['synthetic']*len(polygons),
        'vintage':[2020]*len(polygons),'population':np.arange(len(polygons),dtype=float)},geometry=polygons,crs=4326),
        gpd.GeoDataFrame(geometry=[box(-131,40,-115,56)],crs=4326),{'synthetic':True})
    cells=sorted(h3.grid_disk(h3.latlng_to_cell(48,-123,7),3))
    results={}
    for reference in (True,False):
        key='reference' if reference else 'optimized'
        result=get_population(target_h3=cells,sources=sources,include_bands=True,
            workspace=args.workspace/key,reference=reference)
        results[key]=result
    columns=['population_count',*[f'population_within_{r}km' for r in (25,50,100)]]
    delta=(results['reference'].table[columns]-results['optimized'].table[columns]).abs().max().max()
    report={'synthetic':True,'max_absolute_difference_persons':float(delta),
        'runs':{key:{**value.metadata['benchmark'],'cache_hit':value.cache_hit} for key,value in results.items()}}
    print(json.dumps(report,indent=2,sort_keys=True))


if __name__=='__main__':
    main()
