import copy
import json
from collections import Counter

import pytest

from benchmarks.engineering_assisted_workflow import control
from benchmarks.engineering_compact_workflow import CompactEpisode,decode,run
from benchmarks.engineering_gpu_workflow import schedule,frozen_hash
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_catalog_challenges import generate
from benchmarks.engineering_workflow_campaign import tradeoff_case,heuristic
from benchmarks.engineering_workflow_sft import leaf_spans


@pytest.fixture(scope='module')
def case():
    base=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair' and c['split']=='train' and c['truth']['cause']=='ampacity')
    return tradeoff_case(generate(base,0)[0])[0]


def test_real_physics_rejects_both_shortcut_catalogue_rankings(case):
    assert run(case,control)['success']
    for ranking in ('max_rating','min_resistance'):
        assert not run(case,lambda q:heuristic(q,ranking))['success']


def test_preview_preserves_actual_state_and_does_not_certify_submission(case):
    owner=CompactEpisode(case)
    owner.apply(dict(name='simulate',arguments={}))
    before=owner.public();state=copy.deepcopy(owner.episode.engine.state)
    args=dict(equipment_id=before['scene']['lines'][0]['id'],catalog_id=before['scene']['catalog'][0]['id'])
    reply=owner.apply(dict(name='preview_line_catalog',arguments=args))
    assert reply['ok']
    for key in ('project','revision','simulations','last_result_revision','last_result'):
        assert owner.episode.engine.state[key]==state[key]
    assert owner.public()['current_result']==before['current_result']
    assert owner.preview_count==1 and owner.physics_checks
    # A correct hypothetical result cannot turn the actual damaged network into a pass.
    owner.apply(dict(name='submit',arguments={}))
    assert owner.episode.engine.state['terminal']['success'] is False


def test_stale_preview_is_hidden_after_a_real_mutation(case):
    owner=CompactEpisode(case);p=owner.public()
    args=dict(equipment_id=p['scene']['lines'][0]['id'],catalog_id=p['scene']['catalog'][0]['id'])
    owner.apply(dict(name='preview_line_catalog',arguments=args))
    assert owner.public()['previews']
    assert owner.apply(dict(name='set_line_catalog',arguments=args))['ok']
    assert owner.public()['previews']==[] and not owner.public()['has_fresh_measurements']


def test_optional_arguments_never_invent_required_ids(case):
    p=CompactEpisode(case).public()
    assert decode('{"name":"simulate"}',p)==(dict(name='simulate',arguments={}),True)
    for raw in ('{"name":"set_line_catalog"}','{"tool":"simulate"}',
                '{"name":"set_line_catalog","arguments":{"equipment_id":"L1","catalog_id":"copper-2"}}'):
        with pytest.raises(ValueError):decode(raw,p)


def test_invalid_calls_consume_budget_and_repetition_stops(case):
    result=run(case,lambda q:'{"tool":"simulate"}')
    assert not result['success'] and result['calls']==3
    assert all(e['decode_error'] for e in result['events'])


def test_leaf_spans_supervise_nested_values_not_json_keys():
    answer=dict(name='set_line_catalog',arguments=dict(equipment_id='line_"x',catalog_id='part_42'),nested=[False,None,0,''])
    raw,spans=leaf_spans(answer)
    values={tuple(s['path']):raw[s['start']:s['end']] for s in spans}
    assert values[('arguments','catalog_id')]=='part_42'
    assert values[('nested','0')]=='false' and values[('nested','1')]=='null'
    assert values[('nested','3')]=='""'
    assert all('arguments'!=v for v in values.values())
    assert json.loads(raw)==answer


def test_gradient_accumulation_has_equal_action_mass_and_full_class_coverage():
    rows=[dict(split='train',answer=dict(name=name)) for name,count in
          [('simulate',24),('preview_line_catalog',16),('set_line_catalog',8),('submit',16)] for _ in range(count)]
    order=schedule(rows,96,42)
    assert order==schedule(rows,96,42)
    assert all(len(set(rows[i]['answer']['name'] for i in batch))==4 for batch in order)
    assert set(i for batch in order for i in batch)==set(range(64))
    counts=Counter(rows[i]['answer']['name'] for batch in order for i in batch)
    assert set(counts.values())=={96}
    rows[0]['split']='dev'
    with pytest.raises(ValueError,match='heldout'):schedule(rows,96,42)


def test_frozen_hash_handles_bf16_and_excludes_trainable_adapters():
    torch=pytest.importorskip('torch')
    model=torch.nn.Linear(3,2).to(dtype=torch.bfloat16)
    model.weight.requires_grad_(False)
    initial=frozen_hash(model)
    with torch.no_grad():model.bias.add_(1.)
    assert frozen_hash(model)==initial
    with torch.no_grad():model.weight.add_(1.)
    assert frozen_hash(model)!=initial


def test_decision_coverage_teaches_only_physically_supported_transitions(case,tmp_path):
    from benchmarks.engineering_branch_sft import corpus
    from benchmarks.engineering_factory import write_json
    healthy=copy.deepcopy(case)
    healthy.update(id='healthy-'+case['id'],project=copy.deepcopy(case['healthy_project']))
    write_json(tmp_path/'train-episodes.json',[dict(case=case),dict(case=healthy)])
    rows,proofs=corpus(dict(parent_plan=str(tmp_path)))
    assert len(proofs)==20 and all(p['success'] for p in proofs)
    assert all(r['split']=='train' for r in rows)
    observed_subsets=set()
    for row in rows:
        public=json.loads(row['messages'][1]['content']);action=row['answer'];name=action['name']
        if row['task_id']==case['id'] and public['has_fresh_measurements'] and public['catalog_edits_used']==0:
            observed_subsets.add(frozenset(p['catalog_id'] for p in public['previews']))
        if name=='set_line_catalog':
            matching=[p for p in public['previews'] if p['catalog_id']==action['arguments']['catalog_id']]
            assert matching and matching[0]['assessment']['measurement_feasible'] is True
        elif name=='simulate':assert not public['has_fresh_measurements']
        elif name=='submit':
            assert public['has_fresh_measurements'] and public['current_result']['measurement_feasible']
        elif name=='preview_line_catalog':
            assert not any(p['assessment']['measurement_feasible'] for p in public['previews'])
    assert len(observed_subsets)==16
    case=copy.deepcopy(case);case['split']='test'
    write_json(tmp_path/'train-episodes.json',[dict(case=case)])
    with pytest.raises(ValueError,match='heldout'):corpus(dict(parent_plan=str(tmp_path)))


def test_model_trace_replay_rejects_a_forged_terminal_score(case):
    from benchmarks.engineering_workflow_replay import replay,metrics
    trace=run(case,control)
    assert replay(case,trace)==trace
    counts=metrics([trace])
    assert counts['train']['repair']==dict(successes=1,total=1)
    assert counts['train']['healthy']==dict(successes=0,total=0)
    forged=copy.deepcopy(trace);forged['terminal']['success']=False
    with pytest.raises(ValueError,match='physically replay'):replay(case,forged)
    forged=copy.deepcopy(trace);forged['events'][0]['query']['messages'][0]['content']='different observation'
    with pytest.raises(ValueError,match='observe this physical state'):replay(case,forged)


def test_more_total_successes_do_not_hide_a_lost_correct_workflow():
    from benchmarks.engineering_workflow_campaign_audit import changes
    before=[dict(task_id=str(i),split='train',base_project_id=str(i),success=i==0) for i in range(3)]
    after=copy.deepcopy(before)
    for row in after:row['success']=not row['success']
    delta=changes(before,after)
    assert delta['train']==dict(lost=['0'],gained=['1','2'])
    with pytest.raises(ValueError,match='complete identical'):changes(before,after[:-1])
