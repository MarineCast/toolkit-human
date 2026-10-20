import csv
import zipfile
import pandas as pd
import pytest
from human.pipelines.population.gaf import COLUMNS,read_gaf_da_counts


def write_gaf(path,rows):
    with path.open('w',newline='',encoding='latin-1') as f:
        writer=csv.writer(f);writer.writerow(COLUMNS)
        for block,count,flag in rows:
            da=block[:8];writer.writerow(['59',da,'2021S0512'+da,block,'2021S0513'+block,count,flag])


def test_complete_blocks_sum_but_incomplete_zero_and_missing_stay_unknown(tmp_path):
    path=tmp_path/'gaf.csv';write_gaf(path,[('59000001001',5,'F'),('59000001002',7,'F'),
        ('59000002001',0,'T'),('59000003001','','F')])
    result=read_gaf_da_counts(path,province_code='59').set_index('DGUID')
    assert result.loc['2021S051259000001','Population, 2021']==12
    assert pd.isna(result.loc['2021S051259000002','Population, 2021'])
    assert pd.isna(result.loc['2021S051259000003','Population, 2021'])
    assert result.attrs['gaf_audit']['national_blocks']==4


@pytest.mark.parametrize('rows,match',[
    ([('59000001001',1,'F'),('59000001001',1,'F')],'Duplicate'),
    ([('59000001001','bad','F')],'Invalid GAF population'),
    ([('59000001001',1,'?')],'Invalid 2021 GAF geography'),
])
def test_invalid_or_duplicate_block_fails(tmp_path,rows,match):
    path=tmp_path/'gaf.csv';write_gaf(path,rows)
    with pytest.raises(ValueError,match=match):read_gaf_da_counts(path)


def test_zip_and_csv_semantics_match(tmp_path):
    path=tmp_path/'gaf.csv';write_gaf(path,[('59000001001',3,'F')]);archive=tmp_path/'gaf.zip'
    with zipfile.ZipFile(archive,'w') as z:z.write(path,'2021_92-151_X.csv')
    pd.testing.assert_frame_equal(read_gaf_da_counts(path),read_gaf_da_counts(archive))


def test_census_preparation_accepts_explicit_gaf_and_retains_unknown(tmp_path):
    import geopandas as gpd
    from shapely.geometry import box
    from human.pipelines.population.static.census import prepare_census_sources
    from human.pipelines.population.static.sources import read_sources
    table=tmp_path/'gaf.csv';write_gaf(table,[('59000001001',5,'F'),('59000001002',7,'F'),('59000002001',0,'T')])
    geography=tmp_path/'da.parquet'
    gpd.GeoDataFrame({'DGUID':['2021S051259000001','2021S051259000002'],'PRUID':['59','59']},
        geometry=[box(-123.1,48,-123,48.1),box(-122.9,48,-122.8,48.1)],crs=4326).to_parquet(geography)
    manifest=prepare_census_sources(workspace=tmp_path,source_extent=(-124,47,-122,49),
        specification={'canada':[{'jurisdiction':'CA-BC','province_code':'59','geography':str(geography),
            'population':str(table),'population_format':'statcan_gaf_2021','population_id':'DGUID',
            'population_column':'Population, 2021'}]},mode='offline')
    sources=read_sources(manifest).geographies.set_index('source_id')
    assert sources.loc['CA2021:2021S051259000001','population']==12
    assert pd.isna(sources.loc['CA2021:2021S051259000002','population'])
