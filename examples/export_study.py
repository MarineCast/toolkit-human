"""Compute population and export a bounded local study with explicit retained inputs."""
from pathlib import Path
import argparse
import logging

import yaml

from human import get_population
from human.legacy.study import export_study


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--metadata-dir',type=Path,help='Toolkit-managed receipts outside final data')
    parser.add_argument('--source-manifest',type=Path,required=True)
    parser.add_argument('--common-config',type=Path,default=Path('config/common.yaml'))
    parser.add_argument('--calendar-config',type=Path,default=Path('config/data/human/temporal_context/calendar.yaml'))
    parser.add_argument('--bbox',type=float,nargs=4)
    parser.add_argument('--static-release',type=Path)
    parser.add_argument('--dynamic-release',type=Path)
    parser.add_argument('--allow-partial',action='store_true')
    parser.add_argument('--workers',type=int,default=1)
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    bounds=args.bbox
    if bounds is None:
        area=yaml.safe_load(args.common_config.read_text())['areas']['model_area']['bbox_wgs84']
        bounds=[area[k] for k in ['min_lon','min_lat','max_lon','max_lat']]
    population=get_population(bbox=bounds,h3_res=7,include_bands=True,workspace=args.workspace,
        source_manifest=args.source_manifest,mode='offline',allow_partial=args.allow_partial,
        resource_limits={'workers':args.workers})
    static_input={'manifest':args.static_release/'release.json','artifact':'static-native.parquet'} if args.static_release else None
    dynamic=[]
    if args.dynamic_release:
        for directory,family,date,cell,resolution in [
            ('ferry','ferry','service_date','source_h3','h3_resolution')]:
            dynamic.append({'manifest':args.dynamic_release/'release.json',
                'artifact':f'{directory}/regional-native-local-day.parquet',
                'support_manifest':f'{directory}/test-manifest.json','family':family,
                'date_column':date,'cell_column':cell,'resolution_column':resolution})
    print(export_study(population=population,output_dir=args.output_dir,
        calendar_config=args.calendar_config,static_input=static_input,dynamic_inputs=dynamic,metadata_dir=args.metadata_dir))


if __name__=='__main__':
    main()
