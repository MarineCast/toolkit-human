"""Meteorology-style settings preserve Human's civil calendar/static distinction."""
import json

import pandas as pd
import pytest

from human.pipelines.delivery.settings import KEYS, preflight
from tests.test_pipeline import setup_pipeline


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in KEYS:monkeypatch.delenv(key,raising=False)


def test_file_environment_arguments_and_path_precedence(tmp_path,monkeypatch):
    folder=tmp_path/'config';folder.mkdir();file=folder/'.env'
    file.write_text('START_DATE=2021-07-01\nEND_DATE=2021-07-02\nDATA_DIR="file data" # relative\nVERBOSITY=True\nSECRET=do-not-load\n')
    a=preflight(env_file=file,workspace=tmp_path)
    assert (a.start_date,a.end_date,a.output_dir,a.verbosity)==('2021-07-01','2021-07-02',folder/'file data',True)
    monkeypatch.setenv('DATA_DIR','process');monkeypatch.setenv('START_DATE','2021-07-02')
    a=preflight(env_file=file,workspace=tmp_path)
    assert a.start_date=='2021-07-02' and a.output_dir==tmp_path/'process'
    a=preflight('2021-07-03','2021-07-04','explicit',False,file,workspace=tmp_path)
    assert a.output_dir==tmp_path/'explicit' and a.verbosity is False
    assert 'SECRET' not in __import__('os').environ
    assert not (folder/'file data').exists()


@pytest.mark.parametrize('line,key', [('START_DATE=bad','START_DATE'),('END_DATE=20210702','END_DATE'),
    ('DATA_DIR=""','DATA_DIR'),('VERBOSITY=maybe','VERBOSITY'),('DATA_DIR="unterminated','line'),
    ('START_DATE=2021-07-03','on or before')])
def test_invalid_settings_are_reported_without_writes(tmp_path,line,key):
    values={'START_DATE':'2021-07-01','END_DATE':'2021-07-02','DATA_DIR':'new','VERBOSITY':'False'}
    changed=line.partition('=')[0];text='\n'.join(f'{k}={v}' for k,v in values.items() if k!=changed)
    (tmp_path/'.env').write_text(text+'\n'+line+'\n')
    with pytest.raises(ValueError,match=key):preflight(workspace=tmp_path)
    assert not (tmp_path/'new').exists()


def test_all_missing_duplicate_and_unreadable_errors_are_safe(tmp_path):
    with pytest.raises(ValueError) as error:preflight(workspace=tmp_path)
    assert all(key in str(error.value) for key in ('START_DATE','END_DATE','DATA_DIR'))
    file=tmp_path/'.env';file.write_text('START_DATE=bad\nSTART_DATE=also-bad\nUNKNOWN_SECRET=never-echo-this\n')
    with pytest.raises(ValueError) as error:preflight(workspace=tmp_path)
    assert 'repeated' in str(error.value) and 'never-echo-this' not in str(error.value)
    file.write_bytes(b'\xff')
    with pytest.raises(ValueError,match='UTF-8'):preflight(workspace=tmp_path)
    with pytest.raises(ValueError,match='regular'):preflight(env_file=tmp_path,workspace=tmp_path)
    with pytest.raises(ValueError,match='does not exist'):preflight(env_file='missing',workspace=tmp_path)


def test_file_requires_three_settings_but_file_free_api_has_recipe_defaults(tmp_path):
    defaults={'START_DATE':'2021-07-01','END_DATE':'2021-07-02','DATA_DIR':'out'}
    assert preflight(workspace=tmp_path,defaults=defaults).output_dir==tmp_path/'out'
    (tmp_path/'.env').write_text('DATA_DIR=out\n')
    with pytest.raises(ValueError,match='START_DATE must be set'):preflight(workspace=tmp_path,defaults=defaults)


def test_preflight_cli_never_starts_pipeline(tmp_path,monkeypatch,capsys):
    from human.cli import main
    monkeypatch.setattr('human.get_human_data',lambda **kw:pytest.fail('pipeline started'))
    assert main(['--workspace',str(tmp_path),'preflight','--start-date','2021-07-01','--end-date','2021-07-02','--output-dir','out'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['START_DATE']=='2021-07-01' and result['DATA_DIR']==str(tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_calendar_override_changes_dates_only_and_records_effective_settings(tmp_path):
    from human import get_human_data
    from human.pipelines.delivery.finalize import read_catalog
    setup_pipeline(tmp_path)
    first=get_human_data(workspace=tmp_path)
    static=pd.read_parquet(first.static);native=pd.read_parquet(first.native)
    (tmp_path/'.env').write_text('DATA_DIR=second\nSTART_DATE=2021-07-03\nEND_DATE=2021-07-05\nVERBOSITY=False\n')
    second=get_human_data(workspace=tmp_path)
    pd.testing.assert_frame_equal(static,pd.read_parquet(second.static))
    pd.testing.assert_frame_equal(native,pd.read_parquet(second.native))
    calendar=pd.read_parquet(second.calendar)
    assert len(calendar)==15 and sorted(calendar.date.unique())==['2021-07-03','2021-07-04','2021-07-05']
    info=read_catalog(second.directory)['pipeline']['resolved_settings']
    assert info['START_DATE']=='2021-07-03' and info['END_DATE']=='2021-07-05'
    explicit=get_human_data(workspace=tmp_path,start_date='2021-07-04',end_date='2021-07-04',output_dir='third')
    assert len(pd.read_parquet(explicit.calendar))==5 and explicit.directory==tmp_path/'third'
    assert not list((tmp_path/'work').iterdir())


def test_bad_dates_fail_before_study_or_sources(tmp_path,monkeypatch):
    from human import get_human_data
    import human.main as pipeline
    recipe,config=setup_pipeline(tmp_path)
    (tmp_path/'.env').write_text('START_DATE=bad\nEND_DATE=bad\nDATA_DIR=out\n')
    monkeypatch.setattr(pipeline,'load_static_study',lambda *a:pytest.fail('study opened'))
    monkeypatch.setattr(pipeline,'collect_native',lambda *a,**kw:pytest.fail('source opened'))
    with pytest.raises(ValueError) as error:get_human_data(workspace=tmp_path)
    assert 'START_DATE' in str(error.value) and 'END_DATE' in str(error.value)
    assert not (tmp_path/'out').exists() and not list(tmp_path.glob('.*human.lock'))
