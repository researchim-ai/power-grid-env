import copy
import json
from collections import Counter
from types import SimpleNamespace

import pytest

from benchmarks.engineering_catalog_challenges import generate
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_focused_curriculum import derive,FocusedDataset,sources
from benchmarks.engineering_focused_phase import schedule,parent_receipt
from benchmarks.engineering_primitive_curriculum import case_rows,comparison
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_sft_release_v2 import sha
from power_grid_env.study_artifacts import seal


@pytest.fixture(scope='module')
def rows():
    base=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['split']=='train' and c['kind']=='repair')
    return case_rows(generate(base,0)[0])


def test_single_fields_preserve_parent_types_and_no_answers_enter_requests(rows):
    old=copy.deepcopy(rows)
    values=derive(rows)
    assert rows==old and len(values)==188
    parent={r['id']:r for r in rows}
    for r in values:
        public=json.loads(r['messages'][1]['content'])
        assert set(public)<= {'problem','requested_field','task'}
        assert 'answer' not in public and 'source_row_id' not in public
        if r['field'] is not None:
            assert r['answer']=={r['field']:parent[r['source_row_id']]['answer'][r['field']]}
        else:
            assert sha(r['answer'])==sha(comparison(public['problem']))


def test_null_false_and_true_remain_distinct_in_violation_targets(rows):
    values=derive(rows)
    answers={sha(r['answer']) for r in values if r['skill']=='comparison_field' and r['field']=='violation'}
    assert answers=={sha(dict(violation=v)) for v in (True,False,None)}
    assert len({r['input_sha256'] for r in values})==len(values)


def test_nuisance_pairs_preserve_meaning_and_exact_answer(rows):
    pairs={}
    for r in derive(rows):pairs.setdefault((r['pair_group'],r['member']),[]).append(r)
    for pair in pairs.values():
        assert len(pair)==2
        assert sha(pair[0]['answer'])==sha(pair[1]['answer'])
        assert sha(json.loads(pair[0]['messages'][1]['content']))==sha(json.loads(pair[1]['messages'][1]['content']))
        assert pair[0]['messages']!=pair[1]['messages']


def test_coarse_curriculum_preserves_relations_without_unit_conversion(rows):
    parent={r['id']:r for r in rows}
    for r in derive(rows):
        if r['skill']!='coarse_threshold':continue
        before=parent[r['source_row_id']]['answer']
        assert r['answer']==before
        problem=json.loads(r['messages'][1]['content'])['problem']
        if problem['observed'] is not None:
            assert problem['observed']['unit']==problem['limit']['unit']


def test_heldout_projections_are_rejected_instead_of_relabelled(rows):
    altered=copy.deepcopy(rows);altered[0]['split']='dev'
    with pytest.raises(ValueError,match='must not consume heldout'):
        derive(altered)


def test_parent_threshold_label_tampering_is_rejected(rows):
    altered=copy.deepcopy(rows)
    row=next(r for r in altered if r['skill']=='threshold')
    row['answer']['violation']=not row['answer']['violation']
    with pytest.raises(ValueError,match='parent comparison label differs'):
        derive(altered)


def test_focused_schedule_balances_retention_and_has_exact_ceiling():
    data=SimpleNamespace(rows=[dict(id='focus-'+str(i)) for i in range(10)],
        parent=SimpleNamespace(rows=[dict(id='old-'+str(i)) for i in range(10)]),
        selection=lambda:(list(range(10)),list(range(10,20)),list(range(10)),list(range(10,20))))
    first=schedule(data)
    assert first==schedule(data) and len(first)==60
    assert Counter(x['source'] for x in first)=={'retention':30,'focused':30}
    assert set(Counter((x['source'],x['index']) for x in first).values())=={3}


@pytest.fixture
def release(rows,tmp_path,monkeypatch):
    import benchmarks.engineering_focused_curriculum as module
    parent=SimpleNamespace(rows=rows)
    monkeypatch.setattr(module,'PrimitiveDataset',lambda *args:parent)
    root=tmp_path/'focused'
    values=derive(rows)
    write_json(root/'summary.json',dict(schema='power-grid-env.focused-native-release/v1',passed=True,failures=[],
        target_sha256='a'*64,source_sha256=sources(),parent_admission='unused',parent_manifest_sha256='b'*64,
        records=len(values),checked_targets=len(values)))
    (root/'records.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in values))
    write_json(root/'checks.json',[dict(index=i,row_id=r['id']) for i,r in enumerate(values)])
    return root,seal(root)


def test_loader_reconstructs_targets_from_parent_not_stored_gold(release):
    dataset=FocusedDataset(*release,'a'*64)
    assert len(dataset.rows)==188
    sample=dataset.example(0);sample['answer']['accepted']='tampered'
    assert dataset.example(0)!=sample
    for index in (-1,True,len(dataset.rows)):
        with pytest.raises(ValueError,match='bounded focused'):
            dataset.example(index)


def test_resealed_false_vs_null_tampering_is_detected(release):
    path,_=release
    rows=[json.loads(line) for line in (path/'records.jsonl').read_text().splitlines()]
    row=next(r for r in rows if r['answer']==dict(violation=None))
    row['answer']=dict(violation=False);row['id']=sha({k:v for k,v in row.items() if k!='id'})
    (path/'records.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    (path/'manifest.json').unlink()
    anchor=seal(path)
    with pytest.raises(ValueError,match='labels do not reconstruct'):
        FocusedDataset(path,anchor,'a'*64)


def test_partial_native_inventory_cannot_reach_optimizer(release):
    path,_=release
    checks=json.loads((path/'checks.json').read_text());checks.pop()
    write_json(path/'checks.json',checks)
    (path/'manifest.json').unlink();anchor=seal(path)
    with pytest.raises(ValueError,match='complete native inventory'):
        FocusedDataset(path,anchor,'a'*64)


def test_failed_or_partial_parent_checkpoint_is_not_a_warm_start(tmp_path):
    write_json(tmp_path/'summary.json',dict(schema='power-grid-env.primitive-native-pilot/v1',complete=False))
    anchor=seal(tmp_path)
    with pytest.raises(ValueError,match='complete research parent'):
        parent_receipt(tmp_path,anchor,None,'a'*64)


def test_equal_total_accuracy_does_not_hide_individual_retention_regressions():
    from benchmarks.engineering_focused_audit import regressions
    before=[dict(source='retention',row_id='a',correct=True),dict(source='retention',row_id='b',correct=False)]
    after=[dict(source='retention',row_id='a',correct=False),dict(source='retention',row_id='b',correct=True)]
    changes=regressions(before,after)
    assert changes==dict(regressed=[dict(source='retention',row_id='a')],gained=[dict(source='retention',row_id='b')])
    with pytest.raises(ValueError,match='matching unique'):
        regressions(before,after[:1])


@pytest.fixture
def selection_data(rows):
    from benchmarks.engineering_primitive_admission import PrimitiveDataset
    parent=SimpleNamespace(rows=rows)
    parent.pilot_indices=lambda:PrimitiveDataset.pilot_indices(parent)
    data=object.__new__(FocusedDataset)
    data.rows=derive(rows);data.parent=parent
    return data


def test_selected_error_mining_can_omit_classes_present_in_admitted_corpus(selection_data):
    from benchmarks.engineering_focused_balance import coverage,require_coverage
    original=selection_data.selection()[0]
    result=coverage(selection_data,original)
    assert not result['passed'] and result['deficient_heads']==4
    with pytest.raises(ValueError,match='lack typed answer contrasts'):
        require_coverage(selection_data,original)


def test_balanced_selection_adds_negative_and_unknown_controls(selection_data):
    from benchmarks.engineering_focused_balance import balanced_selection,require_coverage
    selected,result=balanced_selection(selection_data)
    assert len(selected)==16 and result['passed']
    assert require_coverage(selection_data,selected)['passed']
    assert set(selection_data.selection()[0])<=set(selected)
    for head in result['heads']:
        if head['head'][:2]==['api_field','fresh_after']:
            assert {sha(x['answer']) for x in head['selected']}=={sha(dict(fresh_after=v)) for v in (True,False)}
        if head['head'][:2]==['comparison_field','violation']:
            assert {sha(x['answer']) for x in head['selected']}=={sha(dict(violation=v)) for v in (True,False,None)}


def test_balanced_recipe_keeps60_updates_and_exposes_every_added_contrast(selection_data):
    from benchmarks.engineering_focused_balance import balanced_selection,make_schedule
    selected,_=balanced_selection(selection_data)
    first=make_schedule(selection_data,selected)
    assert first==make_schedule(selection_data,selected) and len(first)==60
    assert Counter(r['source'] for r in first)=={'focused':30,'retention':30}
    assert set(selected)<={r['index'] for r in first if r['source']=='focused'}
