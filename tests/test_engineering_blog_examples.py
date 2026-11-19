import json
from pathlib import Path
import pytest
from benchmarks.grid_editor import solve
from benchmarks.engineering_blog_examples import build

ROOT=Path(__file__).resolve().parents[1]

def test_public_examples_are_train_only_and_each_stage_has_source_identity():
    data=json.loads((ROOT/'web/assets/learning-examples.json').read_text())
    assert len(data['examples'])==2
    assert all(e['split']=='train' and not e['base']['success'] and e['sft']['success'] for e in data['examples'])
    assert all(e['base']['trace'] and all(t['decode_error'] for t in e['base']['trace']) for e in data['examples'])
    assert 'not a new test' in data['scope']
    assert all(len(v['manifest_sha256'])==64 for v in data['source'].values())
    assert {e['kind'] for e in data['examples']}=={'verify_healthy','compare_replacements'}
    assert [(r['total'],r['base_successes'],r['sft_successes']) for r in data['task_types']]==[(15,0,15),(15,0,13)]

@pytest.mark.parametrize('index',[0,1])
def test_published_network_files_reproduce_both_diagram_measurements(index):
    data=json.loads((ROOT/'web/assets/learning-examples.json').read_text());e=data['examples'][index]
    for filename,state in [(e['project_file'],e['initial']),(e['modified_project_file'],e['final'])]:
        network=json.loads((ROOT/'web/assets'/filename).read_text());result=solve(network)
        minimum=min(p['voltage_pu'] for n in result['nodes'] if n['energized'] for p in n['phases'].values())
        maximum=max(b['loading_pct'] for b in result['branches'] if b['loading_pct'] is not None)
        assert minimum==pytest.approx(state['assessment']['minimum_voltage_pu'],abs=2e-6)
        assert maximum==pytest.approx(state['assessment']['maximum_branch_loading_pct'],abs=2e-4)
    if e['kind']=='verify_healthy':
        assert e['changed_line'] is None and e['selected_passport'] is None
        assert not e['previews'] and e['initial']==e['final']
        assert [t['action']['name'] for t in e['sft']['trace']]==['simulate','submit']
        assert (ROOT/'web/assets'/e['project_file']).read_bytes()==(ROOT/'web/assets'/e['modified_project_file']).read_bytes()
    else:
        assert e['initial']['assessment']['maximum_branch_loading_pct']>100
        assert e['changed_line']=='L4' and len(e['previews'])==2
        bad,good=e['previews']
        assert not bad['assessment']['measurement_feasible'] and good['assessment']['measurement_feasible']
        assert bad['assessment']['maximum_branch_loading_pct']>400
        assert bad['passport']['r_ohm_km']<good['passport']['r_ohm_km']
        assert bad['actual_assessment']==good['actual_assessment']==e['initial']['assessment']
        assert bad['actual_revision']==good['actual_revision']==0
        assert good['catalog_id']==e['selected_passport']['id']
    assert e['final']['assessment']['maximum_branch_loading_pct']<=100
    assert e['final']['assessment']['minimum_voltage_pu']>=.95


def test_published_examples_rebuild_from_sealed_recorded_model_actions(tmp_path):
    rebuilt=build(tmp_path)
    assert rebuilt==json.loads((ROOT/'web/assets/learning-examples.json').read_text())
    for file in tmp_path.iterdir():
        assert file.read_bytes()==(ROOT/'web/assets'/file.name).read_bytes()
