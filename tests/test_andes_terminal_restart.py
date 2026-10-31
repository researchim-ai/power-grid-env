"""Native rotor laws + explicit open stator, exact coast and restart trajectories."""
import copy
import numpy as np
import pytest
from benchmarks.andes_terminal_restart import example,compare,coast_check,closed_equivalence,NAMES
from benchmarks.andes_joint_motor_reference import native_reference,scope


@pytest.mark.parametrize('name',NAMES)
def test_joint_restart_with_adapted_native_terminal_and_step_refinement(name,tmp_path):
    r=compare(example(name),steps=(.00025,.0000625),out=tmp_path/name)
    assert r['passed'] and r['convergence_passed'],r
    assert r['adapted_terminal_reference'] and not r['stock_black_box_coast_qualified']
    assert not r['field_validated'] and not r['industrial_profile_qualified']
    for row in r['steps']:
        assert row['initial_nonmotor_rhs']<1e-8
        if name!='cold_restart' and name!='frequency_base_restart':assert row['initial_motor_rhs']<1e-8
    if name=='loaded_hold':
        import json
        n=json.loads((tmp_path/name/'andes-adapted-6.25e-05.json').read_text())
        for values in n['native_motor_states'].values():assert max(abs(np.array(values)-values[0]))<1e-7
    else:
        assert r['steps'][-1]['coast']['intervals']
        assert all(i['speed_change_pu']<0 for i in r['steps'][-1]['coast']['intervals'])


def test_closed_adapter_matches_stock_and_does_not_change_rotor_laws_or_cache(tmp_path):
    r=closed_equivalence(tmp_path/'equivalence');assert r['passed'],r
    import json
    n=json.loads((tmp_path/'equivalence/adapted.json').read_text())
    assert n['stock_generated_cache_unchanged']
    a=n['terminal_adaptation'];assert a['differential_equations_unchanged']
    assert set(a['native_differential_equations'])=={'slip','e1d','e1q','e2d','e2q'}
    assert set(a['adapted_terminal_equations'])=={'Id','Iq'}
    assert a['generated_Motor5_sha256']==a['generated_source_sha256']['Motor5.py']


@pytest.fixture(scope='module')
def coast_trace():
    s=example('loaded_restart');n=native_reference(s,.00025,terminal_adapter=True)
    assert n.get('completed'),n
    assert coast_check(s,n)['passed']
    return s,n


@pytest.mark.parametrize('defect',['frozen_speed','frozen_flux','nonzero_current','no_open_samples'])
def test_exact_boundary_audit_rejects_incorrect_native_coasting(coast_trace,defect):
    s,original=coast_trace;n=copy.deepcopy(original)
    off=np.flatnonzero(~np.array(n['terminal_connected']))
    if defect=='frozen_speed':
        for i in off:n['native_motor_states']['slip'][i]=n['native_motor_states']['slip'][off[0]]
    elif defect=='frozen_flux':
        for key in ['e1d','e1q','e2d','e2q']:
            for i in off:n['native_motor_states'][key][i]=n['native_motor_states'][key][off[0]]
    elif defect=='nonzero_current':n['channels']['m.current_pu'][int(off[len(off)//2])]=.1
    else:n['terminal_connected']=[True]*len(n['time_s'])
    assert not coast_check(s,n)['passed']


def test_stock_adapter_still_rejects_loaded_and_open_motor_cases():
    with pytest.raises(ValueError):scope(example('loaded_restart'))
    with pytest.raises(ValueError):scope(example('cold_restart'))
    scope(example('loaded_restart'),terminal_adapter=True)


def test_adapted_worker_failures_cannot_pass(monkeypatch):
    monkeypatch.setattr('benchmarks.andes_terminal_restart.native_reference',lambda *a,**k:dict(completed=False,status='native_worker_failed'))
    s=example('loaded_hold');s.coupled.duration_s=.02
    r=compare(s,steps=(.00025,.0000625));assert not r['passed'] and not r['convergence_passed']


def test_failed_qualification_overwrites_earlier_success(tmp_path,monkeypatch):
    import json
    import benchmarks.andes_terminal_restart as module
    (tmp_path/'summary.json').write_text('{"passed":true}')
    def failed(*args,**kwargs):raise RuntimeError('native reference failed')
    monkeypatch.setattr(module,'closed_equivalence',failed)
    monkeypatch.setattr('sys.argv',['benchmark','--out',str(tmp_path)])
    with pytest.raises(RuntimeError,match='native reference'):module.main()
    r=json.loads((tmp_path/'summary.json').read_text());assert r['passed'] is False and r['status']=='failed'


@pytest.mark.parametrize('name',NAMES)
def test_same_restart_cases_match_independent_physical_si_winding_flux(name):
    from benchmarks.andes_terminal_si_reference import compare as si_compare
    r=si_compare(example(name));assert r['passed'],r
