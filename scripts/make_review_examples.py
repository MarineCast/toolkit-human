"""Render local documentation previews; never treats a missing audit as validation."""
import argparse
import hashlib
import json
from pathlib import Path
import h3
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.colors import Normalize, ListedColormap, BoundaryNorm, SymLogNorm
from matplotlib.ticker import FuncFormatter, MaxNLocator
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from pyproj import Transformer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('delivery', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--allow-unverified', action='store_true', help='Explicit local preview of delivered values when external receipt/audit is absent')
    args = parser.parse_args()
    from human.pipelines.delivery.validation import validate_delivery
    status = 'Validated delivery'
    try:
        validate_delivery(args.delivery)
    except FileNotFoundError:
        if not args.allow_unverified:
            raise
        status = 'UNVERIFIED DELIVERY: external receipt/audit unavailable'
    args.output.mkdir(parents=True, exist_ok=True)
    frame = pq.read_table(args.delivery/'static.parquet').to_pandas()
    assert frame.h3_index.is_unique and frame.h3_resolution.eq(7).all()
    transform = Transformer.from_crs(4326, 32610, always_xy=True)
    polygons = []
    for cell in frame.h3_index:
        assert h3.get_resolution(cell) == 7
        boundary = h3.cell_to_boundary(cell)
        x, y = transform.transform([v[1] for v in boundary], [v[0] for v in boundary])
        polygons.append(np.column_stack([np.array(x)/1000, np.array(y)/1000]))
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11, 'axes.spines.top':False,'axes.spines.right':False})
    specs = [
        ('population_density_per_km2','Population density','people / km²'),
        ('population_within_100km','Population within 100 km','people (cumulative 100 km)'),
        ('boat_launch_count','Boat launch records','records / R7 cell'),
        ('shore_access_site_count','Shore access site records','records / R7 cell'),
        ('place_record_count','Named destination records','records / R7 cell'),
        ('has_port','Recorded port presence','0 / 1')]
    for field, title, units in specs:
        fig, ax = plt.subplots(figsize=(7,8))
        fig.subplots_adjust(left=.13,right=.84,bottom=.24,top=.85)
        values = frame[field].to_numpy(dtype=float)
        if field == 'has_port':
            cmap = ListedColormap(['#e3e6e8','#175781'])
            norm = BoundaryNorm([-.5,.5,1.5],2)
        else:
            cmap = plt.get_cmap('cividis').copy()
            norm = (SymLogNorm(linthresh=1, vmin=0, vmax=max(float(np.nanmax(values)),1))
                    if field == 'population_density_per_km2' else Normalize(vmin=0,vmax=max(float(np.nanmax(values)),1)))
        cmap.set_bad('#ed90be')
        collection = PolyCollection(polygons,array=np.ma.masked_invalid(values),cmap=cmap,norm=norm,edgecolors='none')
        ax.add_collection(collection);ax.autoscale_view();ax.set_aspect('equal')
        ax.set_xlabel('UTM zone 10N easting (km)');ax.set_ylabel('Northing (km)')
        ax.set_title('R7 reporting grid • EPSG:32610',fontsize=11,loc='left')
        bar = fig.colorbar(collection, ax=ax, fraction=.035,pad=.025)
        bar.set_label(units + (' (symlog; linear below 1)' if field == 'population_density_per_km2' else ''))
        if field == 'has_port':bar.set_ticks([0,1])
        else:
            bar.ax.yaxis.set_major_formatter(FuncFormatter(lambda v,p:f'{v:,.0f}'))
            if field.endswith('_count'):
                bar.locator = MaxNLocator(integer=True, nbins=6)
                bar.update_ticks()
        fig.suptitle(title,fontsize=19,x=.13,ha='left',y=.96)
        fig.text(.13,.91,status,fontsize=10,color='#8b4818')
        note = 'Source: delivered Human static.parquet • reviewed 2026-10-29\n'
        if field.startswith('population'):
            note += 'US Census 2020 / StatCan 2021 area allocation; missing contributions\ncount as zero. Coverage flags cannot be recovered without the audit.\n'
        else:
            note += 'Retained inventory snapshot; public zeros include unmeasured support.\nMapped records do not establish current access, use or physical absence.\n'
        note += 'White: outside delivered grid. Pink: null value (none in this delivery).\nLocal documentation preview; not a source-reproduction certificate.'
        fig.text(.13,.04,note,fontsize=9,linespacing=1.5)
        fig.savefig(args.output/f'{field}.png',dpi=135,facecolor='white');plt.close(fig)
    calendar = pq.read_table(args.delivery/'dynamic-calendar.parquet').to_pandas()
    info = json.loads(pq.read_schema(args.delivery/'dynamic-calendar.parquet').metadata[b'human_study'])
    year = 2025
    selected = calendar[calendar.year.eq(year)].copy()
    selected['month'] = pd.to_datetime(selected.date).dt.month
    grouped = selected.groupby(['month','country']).is_holiday.sum().unstack()
    fig,ax=plt.subplots(figsize=(10,5.5));fig.subplots_adjust(bottom=.27,top=.79)
    months=np.arange(1,13)
    for country,offset,color in [('US',-.18,'#175781'),('CA',.18,'#cc8b24')]:
        counts=grouped[country].to_numpy()
        bars=ax.bar(months+offset,counts,width=.36,label=country,color=color)
        ax.bar_label(bars,padding=3,fontsize=9)
    ax.set_xticks(months,['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'])
    ax.set_ylabel('Civil dates with a holiday');ax.set_ylim(0,float(grouped.max().max())+1)
    ax.legend(frameon=False,ncols=2,loc='upper right');ax.yaxis.get_major_locator().set_params(integer=True)
    fig.suptitle('Regional holiday calendar • 2025',x=.125,ha='left',fontsize=19)
    fig.text(.125,.85,'Country/reference timezone union; one count per date, even with multiple holiday names',fontsize=10)
    fig.text(.125,.04,'Source: delivered dynamic-calendar.parquet • reviewed 2026-10-29\nUS: federal + WA + OR / America/Los_Angeles; CA: federal + BC / America/Vancouver.\nRules: holidays '+info['rules_version']+', tzdata '+info['timezone_rules_version']+'; as of '+info['temporal_support']['as_of']+'.\nRule-based context, not nationwide observance or measured human activity.',fontsize=9,linespacing=1.5)
    fig.savefig(args.output/'calendar-2025.png',dpi=135,facecolor='white');plt.close(fig)
    evidence={'delivery_status':status,'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in args.delivery.glob('*.parquet')},'static_rows':len(frame),'statistics':{},'calendar_monthly_holiday_dates_2025':grouped.to_dict()}
    for field,_,_ in specs:
        evidence['statistics'][field]={'nulls':int(frame[field].isna().sum()),'min':float(frame[field].min()),'max':float(frame[field].max()),'mean':float(frame[field].mean())}
    (args.output/'examples-evidence.json').write_text(json.dumps(evidence,indent=2))

if __name__ == '__main__':
    main()
