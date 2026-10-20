import pandas as pd
import pytest
from human.pipelines.population.collected import add_collected_population,FIELD,BASE,METHOD,display_contract


def test_collected_sum_keeps_raw_unknowns_and_distinguishes_imputed_zero():
    original=pd.DataFrame({BASE:[0.,None,None,None],
        BASE+'__available_source_subtotal':[0.,12.,None,0.],
        BASE+'__coverage_status':['known_zero','partial','unavailable','partial'],
        BASE+'__missing_sources':['[]','["missing"]','[]','[]'],
        BASE+'__unverified_geographic_support':[False,True,True,True],
        'population_count':[2.,None,None,0.]})
    meta={'fields':{BASE:{'units':'persons'}}}
    revised,updated=add_collected_population(original,meta)
    pd.testing.assert_frame_equal(revised[original.columns],original)
    assert revised[FIELD].tolist()==[0.,12.,0.,0.]
    assert revised[FIELD+'__zero_imputed'].tolist()==[False,True,True,True]
    assert FIELD+'__coverage_status' not in revised
    assert 'coverage_status' not in updated['fields'][FIELD]
    assert revised[FIELD+'__method'].eq(METHOD).all()
    assert FIELD not in original and FIELD not in meta['fields']
    assert updated['collected_population_summary']['zero_imputed_zero_rows']==2
    assert display_contract()['field']==FIELD


def test_corrupt_subtotal_is_not_silently_zeroed():
    for value in [-1,float('inf'),'corrupt']:
        frame=pd.DataFrame({BASE+'__available_source_subtotal':[value],BASE+'__coverage_status':['partial']})
        with pytest.raises((ValueError,TypeError)):add_collected_population(frame,{'fields':{}})


def test_collected_metrics_share_policy_and_preserve_raw_unknowns():
    from human.pipelines.population.collected import BASES
    table=pd.DataFrame({'cell_area_km2':[2.,4.]})
    for i,base in enumerate(BASES,1):
        table[base]=[float(i),None]
        table[base+'__available_source_subtotal']=[float(i),float(i*10)]
        table[base+'__coverage_status']=['complete','partial']
        table[base+'__missing_sources']=['[]','["unknown"]']
    old=table.copy(deep=True)
    result,meta=add_collected_population(table,{'fields':{b:{} for b in BASES}})
    pd.testing.assert_frame_equal(result[old.columns],old)
    for i,base in enumerate(BASES,1):
        assert result[base+'_collected'].tolist()==[float(i),float(i*10)]
        assert result[base+'_collected__zero_imputed'].tolist()==[False,True]
        assert meta['fields'][base+'_collected']['base_metric']==base
    assert result.population_density_per_km2_collected.tolist()==[.5,2.5]
    assert result.population_density_per_km2_collected__zero_imputed.tolist()==[False,True]
    assert 'population_density_per_km2_collected__coverage_status' not in result
    assert 'coverage_status' not in meta['fields']['population_density_per_km2_collected']
    assert 'population_count_collected__coverage_status' in result
    assert 'complete coverage only' in meta['fields']['population_within_25km']['label']



def test_public_projection_removes_only_duplicate_scalars_and_is_idempotent():
    from human.pipelines.population.collected import BASES, PUBLIC_BASES, DISPLAY_LABELS, public_population_delivery, public_distance_delivery
    table = pd.DataFrame({'h3_index': ['a', 'b'], 'cell_area_km2': [2., 4.]})
    for base in BASES:
        table[base] = [1., None]
        table[base + '__available_source_subtotal'] = [1., 12.5]
        table[base + '__coverage_status'] = ['complete', 'partial']
        table[base + '__missing_sources'] = ['[]', '["unknown"]']
        table[base + '__unverified_geographic_support'] = [False, True]
    table['population_density_per_km2'] = [.5, None]
    table['places__CATALOG_RECORD_COUNT'] = [3., None]
    collected, metadata = add_collected_population(table, {'fields': {b: {} for b in [*BASES, 'population_density_per_km2']}})
    from human.pipelines.population.collected import REMOVED_POPULATION_COVERAGE_FIELDS
    for name in REMOVED_POPULATION_COVERAGE_FIELDS:
        collected[name] = ['complete', 'partial']
        metadata['fields'][name.removesuffix('__coverage_status')]['coverage_status'] = name
    original = collected.copy(deep=True)
    projected, public = public_population_delivery(collected, metadata)
    assert set(original) - set(projected) == set(PUBLIC_BASES) | REMOVED_POPULATION_COVERAGE_FIELDS
    pd.testing.assert_frame_equal(projected, original.drop(columns=[*PUBLIC_BASES, *REMOVED_POPULATION_COVERAGE_FIELDS]))
    assert set(metadata['fields']) - set(public['fields']) == set(PUBLIC_BASES)
    assert set(PUBLIC_BASES) <= set(collected)
    assert set(PUBLIC_BASES) <= set(metadata['fields'])
    twice, twice_metadata = public_distance_delivery(projected, public)
    pd.testing.assert_frame_equal(twice, projected)
    assert twice_metadata == public
    assert {k: v["label"] for k, v in public["fields"].items()} == DISPLAY_LABELS
    assert {v["family"] for v in public["fields"].values()} == {"Population"}


@pytest.mark.parametrize('base', ['population_count', 'population_density_per_km2', *[f'population_within_{r}km' for r in (25, 50, 100)]])
def test_public_projection_refuses_to_discard_an_unreplaced_value(base):
    from human.pipelines.population.collected import public_population_delivery
    with pytest.raises(ValueError, match='without its collected estimate'):
        public_population_delivery(pd.DataFrame({base: [12.]}), {'fields': {base: {}}})


def test_default_contract_has_no_removed_distance_coverage_reference():
    from human.pipelines.population.collected import REMOVED_DISTANCE_COVERAGE_FIELDS
    import json
    assert all(name not in json.dumps(display_contract()) for name in REMOVED_DISTANCE_COVERAGE_FIELDS)
    assert 'coverage_status_field' not in display_contract()
