import copy
from collections import Counter
from types import SimpleNamespace
import pytest
from benchmarks.engineering_skill_audit import inventory,schedule


def row(i, **kw):
    return dict(id=str(i),skill='perception',mode='image',question='Read L1',image_sha256='a'*64,
                answer={'value':'10.00'},kind='line_rating',view='full_scene',variant='factual',
                family_sha256='fam',base_project_id='base',**kw)


def test_visible_duplicates_ignore_source_ids_and_keep_family_origins():
    a=row(0);b=row(1);b.update(family_sha256='other',base_project_id='other')
    r,_=inventory(SimpleNamespace(rows=[a,b]))
    assert r['source_rows']==2 and r['unique_visible_inputs']==1
    assert r['unique_visible_targets']==1 and r['repeated_visible_target_rows']==1
    assert not r['deterministic_conflicts']


def test_conflicting_visible_readout_is_not_treated_as_valid_alternative():
    a=row(0);b=row(1);b['answer']={'value':'20.00'}
    r,_=inventory(SimpleNamespace(rows=[a,b]))
    assert len(r['deterministic_conflicts'])==1 and not r['choice_alternative_targets']


def test_missing_image_identity_does_not_include_invisible_pixels():
    a=row(0);b=row(1)
    for r in (a,b):r.update(mode='none',answer={'value':None})
    b['image_sha256']='b'*64
    r,_=inventory(SimpleNamespace(rows=[a,b]));assert r['unique_visible_targets']==1


def test_duplicate_occurrences_do_not_change_sampling_probabilities():
    a=row(0);b=row(1);b.update(question='Read L2',answer={'value':'20.00'})
    plain=SimpleNamespace(rows=[a,b]);duplicate=SimpleNamespace(rows=[a,b]+[copy.deepcopy(a) for _ in range(100)])
    x=schedule(plain,128,420);y=schedule(duplicate,128,420)
    assert [r['row_id'] for r in x]==[r['row_id'] for r in y]
    assert {r['row_id'] for r in x}=={'0','1'}
    with pytest.raises(ValueError):schedule(plain,0,420)
