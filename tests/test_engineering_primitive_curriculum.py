import copy
import json

import pytest

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_family_registry import build as registry_build, load as registry_load, scenarios
from benchmarks.engineering_primitive_curriculum import build, load, comparison, decimal_value, sha, validate_rows
from benchmarks.engineering_scenarios import generate_scenarios
from power_grid_env.study_artifacts import seal, verify


@pytest.fixture(scope='module')
def registry(tmp_path_factory):
    root = tmp_path_factory.mktemp('primitive')
    history = root/'history'
    rows = generate_scenarios(420,1,(1,1,1))
    write_json(history/'summary.json',dict(schema='power-grid-env.procedural-data/v1', admission_passed=True, scenarios=len(rows)))
    (history/'scenarios.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    spec = [dict(path=str(history),manifest_sha256=seal(history))]
    path = root/'registry'
    registry_build(spec,path,counts=(1,1,1),pool_size=24)
    return path, verify(path)


@pytest.fixture(scope='module')
def release(registry, tmp_path_factory):
    output=tmp_path_factory.mktemp('release')/'corpus'
    build(*registry,output,variants=2)
    return output,verify(output)


def test_registry_excludes_history_and_freezes_topology_before_parameters(registry):
    data=registry_load(*registry)
    keys={r['family_sha256'] for r in data['assignments']}
    assert not keys & set(data['excluded_family_sha256'])
    generated=scenarios(*registry,variants=2)
    assert len(generated)==6
    expected={r['family_sha256']:r['split'] for r in data['assignments']}
    assert all(r['split']==expected[r['family_sha256']] for r in generated)


def test_registry_is_deterministic_and_rejects_bad_anchor(registry,tmp_path):
    original=registry_load(*registry)
    registry_build(original['historical_sources'],tmp_path/'copy',counts=(1,1,1),pool_size=24)
    assert verify(tmp_path/'copy')==registry[1]
    with pytest.raises(ValueError,match='manifest differs'):
        registry_load(registry[0],'0'*64)


@pytest.mark.parametrize('observed,limit,unit,other,expected',[
    ('0.001','1','kA','A','equal'),('0.000999','1','kA','A','below'),
    ('0.001001','1','kA','A','above'),('95','.95','%voltage','pu','equal'),
    ('0.4','400','kV','V','equal')])
def test_decimal_comparison_converts_units_exactly(observed,limit,unit,other,expected):
    result=comparison(dict(observed=dict(value=observed,unit=unit),limit=dict(value=limit,unit=other),direction='maximum'))
    assert result['relation']==expected
    assert result['violation']==(expected=='above')


def test_equality_is_not_undervoltage_and_missing_is_not_zero():
    problem=dict(observed=dict(value='95',unit='%voltage'),limit=dict(value='.95',unit='pu'),direction='minimum')
    assert comparison(problem)['violation'] is False
    problem['observed']=None
    assert comparison(problem)==dict(computable=False,relation='unknown',violation=None)


@pytest.mark.parametrize('value',[True,1.,'NaN','Infinity','1e100','1e-30','x'])
def test_nonfinite_or_ambiguous_numeric_inputs_rejected(value):
    with pytest.raises(ValueError): decimal_value(value)


def test_incompatible_dimensions_are_not_silently_compared():
    with pytest.raises(ValueError,match='incompatible'):
        comparison(dict(observed=dict(value='100',unit='%loading'),limit=dict(value='1',unit='pu'),direction='maximum'))


def test_release_separates_heldout_gold_and_replays_api_effects(release):
    train,owner,summary=load(*release)
    assert train and owner
    assert {r['split'] for r in train}=={'train'}
    assert {r['family_sha256'] for r in train}.isdisjoint({r['family_sha256'] for r in owner})
    for split in ('dev','test'):
        prompts=[json.loads(line) for line in (release[0]/(split+'-prompts.jsonl')).read_text().splitlines()]
        assert prompts and all(set(r)=={'id','split','skill','messages'} for r in prompts)
    rejected=next(r for r in train if r['skill']=='tool_effect' and r['member']=='rejected_mutation')
    assert rejected['answer']['accepted'] is False and rejected['answer']['fresh_after'] is True
    mutation=next(r for r in train if r['skill']=='tool_effect' and r['member']=='fresh_mutation')
    assert mutation['answer']['revision_delta']==1 and mutation['answer']['fresh_after'] is False
    assert 'installed_line_ids' in json.loads(mutation['messages'][1]['content'])


def test_heldout_cannot_be_relabelled_as_train(release,registry):
    train,owner,_=load(*release)
    altered=copy.deepcopy(train+owner)
    row=next(r for r in altered if r['split']=='test')
    row['split']='train';row['id']=sha({k:v for k,v in row.items() if k!='id'})
    with pytest.raises(ValueError,match='split differs'):
        validate_rows(altered,registry_load(*registry))


def test_nuisance_pair_and_semantic_group_must_be_complete(release,registry):
    train,owner,_=load(*release)
    with pytest.raises(ValueError,match='nuisance pair'):
        validate_rows((train+owner)[1:],registry_load(*registry))


def test_wrong_boolean_label_is_rejected_canonically(release,registry):
    train,owner,_=load(*release)
    values=copy.deepcopy(train+owner)
    row=next(r for r in values if r['skill']=='threshold')
    row['answer']['computable']=int(row['answer']['computable'])
    row['id']=sha({k:v for k,v in row.items() if k!='id'})
    with pytest.raises(ValueError,match='factual label'):
        validate_rows(values,registry_load(*registry))


def test_primitive_dataset_rejects_failed_native_admission_before_loading_rows(tmp_path):
    from benchmarks.engineering_primitive_admission import PrimitiveDataset
    p=tmp_path/'failed'
    write_json(p/'summary.json',dict(schema='power-grid-env.primitive-native-admission/v1',passed=False))
    anchor=seal(p)
    with pytest.raises(ValueError,match='passing matching'):
        PrimitiveDataset(p,anchor,'a'*64)


def test_heldout_example_is_rejected_even_if_injected_into_train_view(release):
    from benchmarks.engineering_primitive_admission import PrimitiveDataset
    _,owner,_=load(*release)
    data=object.__new__(PrimitiveDataset)
    data.rows=[owner[0]]
    with pytest.raises(ValueError,match='heldout row'):
        data.example(0)
    for bad in (-1,True,1):
        with pytest.raises(ValueError,match='bounded train index'):
            data.example(bad)


def test_pilot_schedule_has_fixed_ceiling_and_no_implicit_extra_epochs():
    from benchmarks.engineering_primitive_pilot import order
    values=list(range(10))
    first=order(values)
    assert first==order(values) and first!=order(values,20261120)
    assert len(first)==30
    assert all(first.count(i)==3 for i in values)
    with pytest.raises(ValueError,match='ten distinct'):
        order([0]*10)


def test_resealed_wrong_api_effect_label_does_not_survive_engine_replay(release,tmp_path):
    import shutil
    p=tmp_path/'altered'
    shutil.copytree(release[0],p)
    rows=[json.loads(line) for line in (p/'train.jsonl').read_text().splitlines()]
    affected=next(r for r in rows if r['skill']=='tool_effect')
    # Change BOTH nuisance partners consistently so pair consistency cannot
    # substitute for replay of the actual tool engine.
    for row in rows:
        if row['pair_group']==affected['pair_group'] and row['member']==affected['member']:
            row['answer']['accepted']=not row['answer']['accepted']
            row['id']=sha({k:v for k,v in row.items() if k!='id'})
    (p/'train.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    (p/'manifest.json').unlink()
    anchor=seal(p)
    with pytest.raises(ValueError,match='engine replay'):
        load(p,anchor)


def test_generated_grading_distinguishes_protocol_schema_and_value_errors():
    from benchmarks.engineering_primitive_pilot_audit import grade
    expected=dict(computable=True,relation='equal',violation=False)
    assert grade(json.dumps(expected),expected)=='correct'
    assert grade('```json\n'+json.dumps(expected)+'\n```',expected)=='protocol_error'
    assert grade('{"relation":"equal"}',expected)=='field_schema_error'
    assert grade('{"computable":1,"relation":"equal","violation":false}',expected)=='wrong_values_or_types'
