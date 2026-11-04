"""Independent sparse MNA, bounded long profiles and fail-closed resource receipts."""
import copy,json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from scipy.sparse import issparse
from benchmarks.ex03_cases import LONG,SCALES,long_case,scale_case,native_long
from benchmarks.ex03_reference import reference
from benchmarks.ex03_validation import compare,contract,fresh,NAMES,case,Work
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.coupled_rms import validate_coupled

@pytest.fixture(scope='module')
def small():
    s=long_case('gen_long_restart');s.coupled.duration_s=.25;s.coupled.step_s=.05;s.coupled.events=[e for e in s.coupled.events if e.time_s<.25]
    a=run_study(s);assert a['completed'],a
    c=reference(s);b=c.run(a);b.update(completed=True,status='completed')
    return s,a,b

@pytest.mark.parametrize('name',NAMES)
def test_declared_cases_are_exactly_replayable(name):
    s=case(name);assert StudySpec.model_validate_json(s.model_dump_json())==s
    from power_grid_env.model import canonical_hash
    expected=json.loads(Path('requirements/ex03-case-manifest.json').read_text())['cases'][name]
    assert canonical_hash(s)==expected['sha256']
    if name in (*LONG,'scale_64_4_1'):
        p=Path('cases/studies')/('ex03_'+name+'.json');assert StudySpec.model_validate_json(p.read_text())==s
    if s.coupled:assert validate_coupled(s.network,s.coupled)['ok']

@pytest.mark.parametrize('name',LONG)
def test_long_profiles_have_actual_start_and_cooling_horizons(name):
    s=long_case(name);p=s.coupled or s.induction
    assert p.duration_s==45. and all(not m.connected for m in p.motors)
    assert all(m.inertia_s>=1.5 for m in p.motors)
    assert len(s.network.lines)+len(s.network.transformers)>=len(s.network.connectivity_nodes)
    if name=='gen_thermal_cooling':assert p.duration_s>=8*p.motors[0].thermal.time_constant_s

def test_sparse_si_full_states_complex_nodes_and_independent_balance(small):
    s,a,b=small;r=compare(s,a,b);assert r['passed'],r
    assert r['all_node_count']==len(s.network.connectivity_nodes)
    assert b['sparse_MNA'] and max(r['residuals'].values())<1e-8
    assert b['integration']['method']=='DOP853'

@pytest.mark.parametrize('damage',['node','cage','thermal','nonfinite','axis','completed'])
def test_incomplete_or_corrupt_reference_cannot_qualify(small,damage):
    s,a,b=small;x=copy.deepcopy(b)
    if damage=='node':x['node_voltage'].pop(next(iter(x['node_voltage'])))
    if damage=='cage':x['channels'].pop('m.cage1_e_real_pu')
    if damage=='thermal':x['balance_audit']=[]
    if damage=='nonfinite':x['channels']['m.speed_pu'][0]=float('nan')
    if damage=='axis':x['time_s'][-1]-=.01
    if damage=='completed':x['completed']=False
    with pytest.raises(ValueError):compare(s,a,x)

def test_oracle_does_not_use_production_equations_or_stamps(small,monkeypatch):
    s,a,_=small
    def forbidden(*args,**kwargs):raise AssertionError('production physics imported into SI oracle')
    with monkeypatch.context() as m:
        m.setattr('power_grid_env.coupled_rms.CoupledNetwork.rhs',forbidden)
        m.setattr('power_grid_env.induction_network.InductionNetwork.rhs',forbidden)
        m.setattr('power_grid_env.induction.InductionMotor.constants',forbidden)
        m.setattr('power_grid_env.topology.compile_topology',forbidden)
        c=reference(s);b=c.run(a)
    assert b['production_stamps_used'] is False


def test_sparse_reference_never_allocates_global_dense_square(monkeypatch):
    s=scale_case(256,8,2);original=np.zeros
    def bounded(shape,*a,**k):
        if isinstance(shape,tuple) and len(shape)==2 and shape[0]==shape[1] and shape[0]>=256:raise AssertionError('dense global MNA allocation')
        return original(shape,*a,**k)
    with monkeypatch.context() as m:
        m.setattr(np,'zeros',bounded);c=reference(s)
    assert issparse(c.Y) and c.matrix_nnz<20*256


def test_sparse_factorization_reused_but_source_context_is_fresh(small):
    c=reference(small[0]);x=c.state.copy();old=c.electrical(x)[0].copy();count=c.factorizations
    cfg=copy.deepcopy(c.cfg);cfg['scale']=.95;c.set_context(cfg);new=c.electrical(x)[0]
    assert c.factorizations==count and max(abs(new-old))>1.
    cfg['connected'][0]=True;c.set_context(cfg);assert c.factorizations==count+1


def test_sparse_and_dense_winding_systems_agree(small):
    from benchmarks.multibus_multi_generator import MultiCircuit
    s=small[0];a=reference(s);b=MultiCircuit(s,maximum_duration_s=60.)
    for left,right in zip(a.electrical(a.state),b.electrical(b.state)):
        assert np.max(abs(left-right))<1e-8

@pytest.mark.parametrize('resource,value,reason',[('maximum_rhs_evaluations',1,'rhs_evaluations'),('maximum_retained_scalars',1,'retained_scalars')])
def test_exhausted_runtime_budget_has_no_partial_success_trace(small,resource,value,reason):
    s=small[0].model_copy(deep=True);setattr(s.coupled.runtime_limits,resource,value);r=run_study(s)
    assert not r['completed'] and r['status']=='runtime_limit_exceeded' and r['reason']==reason and 'channels' not in r


def test_failed_numerical_solve_cannot_become_a_trace(small,monkeypatch):
    monkeypatch.setattr('power_grid_env.induction_network.solve_ivp',lambda *a,**k:SimpleNamespace(nfev=1,njev=0,nlu=0,success=False,y=np.zeros((1,1)),message='injected solve failure'))
    r=run_study(small[0]);assert not r['completed'] and r['status']=='numerically_not_converged' and 'channels' not in r


def test_fresh_worker_deadline_is_an_explicit_failure(tmp_path,small):
    r=fresh('gen_long_restart','primary',tmp_path/'deadline',spec=small[0],timeout_s=.001)
    assert not r['completed'] and r['status']=='worker_timeout'
    assert not (tmp_path/'deadline/trace.json').exists()


def test_network_work_instrumentation_restores_solvers_on_failure():
    import power_grid_env.coupled_rms as module
    original=module.splu
    with pytest.raises(RuntimeError):
        with Work():
            assert module.splu is not original
            raise RuntimeError('stop')
    assert module.splu is original


def test_native_long_horizon_requires_explicit_opt_in():
    from benchmarks.esst1a_network import scope
    s=native_long()
    with pytest.raises(ValueError):scope(s)
    baseline=scope(s,maximum_duration_s=30.,allow_cold_prestart=True)
    assert baseline.coupled.duration_s==30. and not baseline.coupled.esst1a_exciters
    s.coupled.motors[0].connected=True
    with pytest.raises(ValueError):scope(s,maximum_duration_s=30.,allow_cold_prestart=True)


def test_short_or_incomplete_runup_does_not_pass_the_long_contract(small):
    s=long_case('gen_long_restart');r=small[1]
    assert not contract('gen_long_restart',s,r)['passed']

@pytest.mark.parametrize('text,expected',[('Name:\ttest\nVmHWM:\t123456 kB\nVmRSS:\t4000 kB\n',123456),('VmHWM: 999 kB\n',999)])
def test_memory_reads_current_executable_high_water_mark(tmp_path,text,expected):
    from benchmarks.ex03_validation import executable_peak_rss_kib
    p=tmp_path/'status';p.write_text(text);assert executable_peak_rss_kib(p)==expected

@pytest.mark.parametrize('text',['VmHWM: 12 MB','VmHWM: -1 kB','VmRSS: 100 kB','VmHWM: nan kB','VmHWM: 0 kB'])
def test_missing_or_ambiguous_memory_is_not_a_measurement(tmp_path,text):
    from benchmarks.ex03_validation import executable_peak_rss_kib
    p=tmp_path/'status';p.write_text(text)
    with pytest.raises(ValueError):executable_peak_rss_kib(p)


def test_case_export_is_pinned_and_does_not_overwrite_previous_evidence(tmp_path):
    from examples.export_ex03_cases import export
    from power_grid_env.model import canonical_hash
    out=export(tmp_path/'export',['scale_64_4_1'])
    s=StudySpec.model_validate_json((out/'ex03_scale_64_4_1.json').read_text())
    assert canonical_hash(s)==json.loads((out/'manifest.json').read_text())['scale_64_4_1']['sha256']
    with pytest.raises(ValueError):export(out,['scale_64_4_1'])
