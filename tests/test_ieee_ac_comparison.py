import copy
import numpy as np
import pytest
from benchmarks.ieee_ac_comparison import registered_input,independent_balance,normalize_primary,compare_outputs,worker,LIMITS,CASES
from power_grid_env.balanced_ac import case_from_document,solve_power_flow


@pytest.fixture(scope='module')
def solved():
    d=registered_input('case14');case=case_from_document(d['case'])
    r=solve_power_flow(case,tolerance_pu=1e-10,enforce_q_limits=False)
    return d,normalize_primary(d['case'],r)


def test_preregistered_cases_and_units(solved):
    d,r=solved
    assert CASES==('case14','case30','case118','case300')
    assert len(d['case']['buses'])==14 and independent_balance(d['case'],r)<1e-6
    assert 'pu admittance' in d['shunts']


def test_direct_balance_detects_incorrect_dispatch_and_current_orientation(solved):
    d,r=copy.deepcopy(solved);r['generation_by_bus'][0][1]+=1
    assert independent_balance(d['case'],r)>0.99
    d,r=copy.deepcopy(solved);d['case']['branches'][0]['angle_deg']+=10
    assert independent_balance(d['case'],r)>1


def test_shunt_base_conversion_is_physically_observed(solved):
    d,r=copy.deepcopy(solved)
    bus=next(b for b in d['case']['buses'] if b['bs'])
    bus['bs']*=d['case']['base_mva']
    assert independent_balance(d['case'],r)>1


@pytest.mark.parametrize('defect',['truncated','nonfinite','wrong_angle','wrong_branch','wrong_power_balance'])
def test_comparison_cannot_pass_incomplete_or_corrupted_physics(solved,defect):
    d,r=copy.deepcopy(solved);a={'result':copy.deepcopy(r)};b={'result':copy.deepcopy(r)}
    if defect=='truncated':b['result']['voltage_pu'].pop()
    elif defect=='nonfinite':b['result']['branch_power_mva'][0][0]=float('nan')
    elif defect=='wrong_angle':b['result']['angle_rad'][1]+=.1
    elif defect=='wrong_branch':b['result']['branch_power_mva'][0][0]+=1
    else:b['result']['generation_by_bus'][0][1]+=1
    if defect in {'truncated','nonfinite'}:
        with pytest.raises(ValueError):compare_outputs(d,a,b)
    else:assert not compare_outputs(d,a,b)['passed']


def test_correct_pair_passes_without_speed_superiority_claim(solved):
    d,r=solved;a={'result':r}
    result=compare_outputs(d,a,a)
    assert result['passed'] and result['limits']==LIMITS


@pytest.mark.parametrize('name',['arbitrary','case1354pegase'])
def test_unregistered_cases_cannot_sneak_into_required_corpus(name):
    with pytest.raises(ValueError):registered_input(name)


def test_worker_rejects_unbounded_repeats_before_computation():
    with pytest.raises(ValueError):worker({},'primary',100)


@pytest.fixture(scope='module')
def packet(tmp_path_factory):
    import benchmarks.ieee_ac_comparison as module
    patch=pytest.MonkeyPatch();patch.setattr(module,'CASES',('case14',))
    out=tmp_path_factory.mktemp('ieee')/'packet'
    assert module.run(out,repeats=3)['passed']
    yield out
    patch.undo()


def test_real_small_packet_verification(packet):
    from benchmarks.ieee_ac_comparison import verify_result
    assert verify_result(packet)['passed']


@pytest.mark.parametrize('defect',['median','missing_repeat','policy','summary'])
def test_resealed_measurement_or_scope_edit_is_rejected(packet,tmp_path,defect):
    import shutil
    from benchmarks.ieee_ac_comparison import verify_result
    from power_grid_env.study_artifacts import read_json,encoded,seal
    out=tmp_path/'edited';shutil.copytree(packet,out)
    name='case14/primary.json'
    if defect=='policy':name='protocol.json'
    elif defect=='summary':name='summary.json'
    d=read_json(out/name)
    if defect=='median':d['median_wall_s']*=.1
    elif defect=='missing_repeat':d['samples'].pop()
    elif defect=='policy':d['timing_scope']='pure NR kernel'
    else:d['superiority_proven']=True
    (out/name).write_bytes(encoded(d));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError):verify_result(out)
