import copy
from collections import Counter
import pytest
from benchmarks import engineering_skill_pilot as pilot
from power_grid_env.study_artifacts import read_json,seal,verify
from benchmarks.engineering_factory import write_json


class FakeSkills:
    def __init__(self, sources):
        self.sources=sources;self.rows=[dict(id=str(i),skill=s) for i,s in enumerate(['binding']*2+['readiness']*2+['choice']*2+['perception']*2)]
        self.duplicate_rows=3
    def pilot_indices(self):return list(range(8))


def test_preregistered_schedule_covers_each_exemplar_once_per_menu_presentation(tmp_path,monkeypatch):
    monkeypatch.setattr(pilot,'SkillDataset',FakeSkills)
    root=tmp_path/'plan';r=pilot.create_plan({},'a'*64,root)
    assert r['steps']==32 and len(r['schedule'])==32
    assert Counter(x['index'] for x in r['schedule'])=={i:4 for i in range(8)}
    assert all({x['presentation_seed'] for x in r['schedule'] if x['index']==i}=={420,421,422,423} for i in range(8))
    assert r['evaluation_presentation_seeds']==[420,777]
    assert 777 not in r['train_presentation_seeds']
    assert r['optimizer_run'] is False and not r['full_workflow_or_generalization_claim']
    data,loaded=pilot.check_plan(root,verify(root));assert loaded==r


@pytest.mark.parametrize('mutation',['schedule','exemplars','source','context','eval'])
def test_owner_pilot_checker_rejects_changed_recipe_even_with_new_manifest(tmp_path,monkeypatch,mutation):
    monkeypatch.setattr(pilot,'SkillDataset',FakeSkills)
    a=tmp_path/'original';pilot.create_plan({},'a'*64,a);r=read_json(a/'summary.json')
    if mutation=='schedule':r['schedule'][0]['index']=99
    if mutation=='exemplars':r['exemplars'][0]['row_id']='wrong'
    if mutation=='source':r['source_sha256']['engineering_skill_tasks.py']='0'*64
    if mutation=='context':r['context_tokens']=128
    if mutation=='eval':r['evaluation_presentation_seeds']=[420]
    b=tmp_path/'changed';write_json(b/'summary.json',r);seal(b)
    with pytest.raises(ValueError):pilot.check_plan(b,verify(b))
