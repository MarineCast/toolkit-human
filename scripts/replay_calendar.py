"""Replay every delivered calendar row from the installed Human package."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from human.pipelines.calendar.lookup import build_calendar_lookup, compact_calendar

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('delivery', type=Path)
p.add_argument('output', type=Path)
a = p.parse_args()
source = a.delivery / 'dynamic-calendar.parquet'
table = pq.read_table(source)
info = json.loads(table.schema.metadata[b'human_study'])
assert importlib.metadata.version('holidays') == info['rules_version']
assert importlib.metadata.version('tzdata') == info['timezone_rules_version']
support = info['temporal_support']
replay = compact_calendar(build_calendar_lookup(support['start'], support['end_exclusive'], as_of=support['as_of']))
key = ['date', 'country', 'timezone']
pd.testing.assert_frame_equal(table.to_pandas().sort_values(key).reset_index(drop=True), replay.sort_values(key).reset_index(drop=True), check_exact=True)
assert not replay.duplicated(key).any()
a.output.mkdir(parents=True, exist_ok=True)
result = pa.Table.from_pandas(replay, preserve_index=False)
assert table.schema.remove_metadata() == result.schema.remove_metadata()
pq.write_table(result, a.output / 'calendar-replay.parquet')
evidence = {'rows':len(replay), 'columns':len(replay.columns), 'all_values_types_keys_nulls_equal':True, 'semantic_parity':True, 'byte_identity_claimed':False, 'full_delivery_validation':'blocked by missing external static receipt/audit', 'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(), 'versions':{n:importlib.metadata.version(n) for n in ['toolkit-human','holidays','tzdata','pandas','pyarrow']}, 'holiday_rows':replay.groupby('country').is_holiday.sum().to_dict(), 'nulls':replay.isna().sum().to_dict()}
(a.output / 'calendar-evidence.json').write_text(json.dumps(evidence,indent=2))
print(json.dumps(evidence,indent=2))
