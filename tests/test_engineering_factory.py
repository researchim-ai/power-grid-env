import copy
import json
import shutil
from pathlib import Path
import pytest
from benchmarks import engineering_agent as legacy
from benchmarks.engineering_scenarios import ScenarioEngine,generate_scenarios,family_pool
from benchmarks.engineering_reference import radial_reference,compare_reference
from benchmarks.engineering_factory import build,public_family,collect_scenario,ProceduralDataset,FACTORY_VARIANTS
from benchmarks.engineering_protocol import RunContract
from power_grid_env.study_artifacts import seal,verify


def test_topology_splits_precede_variants_and_exclude_legacy():
    families=family_pool(420,(4,2,2));groups={s:{f['family_sha256'] for f in families if f['split']==s} for s in ('train','dev','test')}
    assert all(not groups[a]&groups[b] for a,b in [('train','dev'),('train','test'),('dev','test')])
    assert not set.union(*groups.values())&{legacy.family_key(v[1]) for v in legacy.FAMILIES.values()}
    cases=generate_scenarios(420,2,(4,2,2));assert cases==generate_scenarios(420,2,(4,2,2))
    assert len({c['id'] for c in cases})==len(cases)
    base={}
    for case in cases:
        base.setdefault(case['base_project_id'],set()).add(case['split'])
        assert public_family(case['requirements'])==case['family_sha256']
    assert all(len(splits)==1 for splits in base.values())


def test_public_family_invariant_to_renaming_and_edge_order():
    case=generate_scenarios(44,1,(1,1,1))[0];req=copy.deepcopy(case['requirements'])
    nodes={n['id'] for n in case['healthy_project']['nodes']};names={n:'renamed'+str(i) for i,n in enumerate(sorted(nodes))}
    req['source_node']=names[req['source_node']]
    for r in req['routes']:r['from_node']=names[r['from_node']];r['to_node']=names[r['to_node']]
    req['routes'].reverse()
    assert public_family(req)==case['family_sha256']


@pytest.mark.parametrize('index',[1,2,3,4,5,6])
def test_independent_reference_detects_actual_fault_and_wrong_measurement(index):
    case=generate_scenarios(420,1,(1,1,1))[index];engine=ScenarioEngine(case);response=engine.apply('simulate',{})
    assert response['ok']
    compare_reference(engine.state['project'],response['result'])
    assert legacy.physical_errors(response['result'],case['requirements'])
    bad=copy.deepcopy(response['result']);bad['nodes'][0]['phases']['A']['voltage_pu']+=.01
    with pytest.raises(ValueError):compare_reference(engine.state['project'],bad)


@pytest.mark.parametrize('variant',FACTORY_VARIANTS)
@pytest.mark.parametrize('kind',['assemble','diagnose','repair'])
def test_varied_public_plans_and_recovery_are_physically_accepted(variant,kind):
    case=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['split']=='train' and c['kind']==kind)
    episode=collect_scenario(case,variant)
    assert episode['success'] and episode['events'][-1]['action']['name']=='submit'
    assert episode['events'][-1]['response']['result']['reward']==1


def test_judge_rejects_fake_or_stale_results_and_immutable_owner_fields():
    case=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair')
    healthy=copy.deepcopy(case);healthy['project']=healthy['healthy_project'];engine=ScenarioEngine(healthy)
    engine.apply('simulate',{});line=engine.state['project']['lines'][0]
    before=copy.deepcopy(engine.requirements)
    assert not engine.apply('set_line_catalog',dict(equipment_id=line['id'],catalog_id='copper-1',minimum_voltage_pu=0))['ok']
    assert engine.requirements==before
    engine.apply('set_line_catalog',dict(equipment_id=line['id'],catalog_id='copper-1'))
    assert not engine.apply('submit',{})['result']['success']
    diag=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='diagnose')
    engine=ScenarioEngine(diag);engine.apply('simulate',{});engine.state['last_result']['converged']=False
    assert not engine.apply('submit',diag['truth'])['result']['success']


@pytest.fixture(scope='module')
def data(tmp_path_factory):
    root=tmp_path_factory.mktemp('procedural')/'data';summary=build(root,420,1,(2,1,1))
    assert summary['release_passed'] and summary['quarantined_base_projects']==0
    return root


def test_training_loader_never_admits_evaluation_or_wrong_owner_anchor(data):
    with pytest.raises(ValueError):ProceduralDataset(data/'evaluation',verify(data/'evaluation'),RunContract(maximum_actions=24))
    with pytest.raises(ValueError):ProceduralDataset(data/'train','a'*64,RunContract(maximum_actions=24))
    dataset=ProceduralDataset(data/'train',verify(data/'train'),RunContract(maximum_actions=24))
    assert len(dataset.episodes)==2*7*len(FACTORY_VARIANTS)
    assert all(e['split']=='train' for e in dataset.episodes)
    assert all(dataset.episodes[i]['events'][t]['source']=='public_expert' for i,t in dataset.targets)


def test_resealed_family_leak_or_bad_source_cannot_enter_training(data,tmp_path):
    root=tmp_path/'train';shutil.copytree(data/'train',root,ignore=shutil.ignore_patterns('manifest.json'))
    p=root/'scenarios.jsonl';rows=[json.loads(x) for x in p.read_text().splitlines()];rows[0]['split']='dev'
    p.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    with pytest.raises(ValueError):ProceduralDataset(root,seal(root),RunContract(maximum_actions=24))


@pytest.mark.parametrize('mutation',[
 lambda r:r['nodes'][0]['phases']['A'].update(voltage_pu=float('nan')),
 lambda r:r['source'].update(p_kw=float('inf')),
 lambda r:r['nodes'].pop(),
 lambda r:r['branches'].pop(),
 lambda r:r['branches'][0].update(loading_pct=0.),
])
def test_reference_does_not_admit_nan_missing_or_forged_measurements(mutation):
    case=generate_scenarios(420,1,(1,1,1))[1];engine=ScenarioEngine(case);result=engine.apply('simulate',{})['result']
    bad=copy.deepcopy(result);mutation(bad)
    with pytest.raises(ValueError):compare_reference(engine.state['project'],bad)
