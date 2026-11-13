"""Independent continuous plant: scope, passive ports and energy identities."""
import itertools
import os
from pathlib import Path
import numpy as np
import pytest
from benchmarks.continuous_drive_acceptance import document
from benchmarks.continuous_drive_reference import Circuit,authenticate,read_document,run,PHASE,CLARKE


@pytest.fixture(scope='module')
def native():
    wheel=os.environ.get('PGE_MOTULATOR_WHEEL')
    if not wheel:pytest.skip('set PGE_MOTULATOR_WHEEL to the pinned 0.23.1 wheel')
    return authenticate(wheel)


@pytest.mark.parametrize('case',['no_core','multicage','locked','switch_loss','device_rt','observer'])
def test_unsupported_profiles_reject_before_native_import(case):
    p=document('saturated_core_start')
    if case=='no_core':p['motor']['core_loss_s']=0.
    if case=='multicage':p['motor']['cages']*=2
    if case=='locked':p['motor']['locked_rotor']=True
    if case=='switch_loss':p['switching_on_j']=.001
    if case=='device_rt':p['thermal']['resistance_alpha_per_k']=.004
    if case=='observer':p['observer_motor']['core_loss_s']=.001
    with pytest.raises(ValueError):Circuit(p)


@pytest.mark.parametrize('payload',['{"x":NaN}','{"x":Infinity}','{"x":1e999}','{"x":1,"x":2}'])
def test_data_only_input_rejects_nonfinite_and_duplicate_keys(tmp_path,payload):
    path=tmp_path/'study.json';path.write_text(payload)
    with pytest.raises(ValueError):read_document(path)


@pytest.mark.parametrize('settings',[dict(method='RK45'),dict(rtol=float('nan')),dict(atol=1.),dict(max_step=0.)])
def test_invalid_reference_controls_reject_before_model(settings):
    with pytest.raises(ValueError):run({},**settings)


@pytest.mark.parametrize('gates',list(itertools.product([0,1,2],repeat=3)))
@pytest.mark.parametrize('flux',[[0.,0.,0.,0.],[.3,.1,.25,.02],[-.5,.4,.1,-.2]])
def test_every_safe_gate_state_satisfies_kcl_and_passive_power(native,gates,flux):
    c=Circuit(document('saturated_core_start'));x=c.initial();x[5:9]=flux;c.gates=np.array([[g==1,g==2] for g in gates])
    current,v,top,bottom,upper,lower,heat=c.port(x)
    residual=PHASE@np.array([current.real,current.imag])-top+bottom+upper-lower
    assert max(abs(residual))<2e-8 and heat>=-1e-9
    voltage=complex(*(CLARKE@v));dc_power=x[3]*(sum(top)-sum(upper))+x[4]*(-sum(bottom)+sum(lower))
    assert dc_power==pytest.approx(1.5*(voltage*current.conjugate()).real+heat,rel=1e-10,abs=1e-7)


def test_isolated_motor_common_mode_is_a_gauge(native):
    c=Circuit(document('saturated_core_start'));x=c.initial();x[5:9]=[1e-4,2e-4,2e-4,1e-4]
    first=c.port(x);x[3:5]+=80.;second=c.port(x)
    np.testing.assert_allclose(CLARKE@first[1],CLARKE@second[1],atol=1e-10,rtol=0)
    assert first[0]==pytest.approx(second[0],abs=1e-10)
    np.testing.assert_allclose(second[1]-first[1],80.,atol=1e-10,rtol=0)


@pytest.mark.parametrize('name',['linear_core_start','saturated_core_start','warm_core_start'])
def test_full_continuous_energy_derivative_including_thermal_storage(native,name):
    c=Circuit(document(name));x=c.initial();x[:3]=[1.,-.5,0.];x[5:9]=[.03,.01,.01,-.02];x[9]=15.;c.mode[:]=[1,-1,0];c.bypassed=True;c.gates=np.array([[1,0],[0,1],[0,0]],bool)
    dx=c.rhs(.01,x);epsilon=1e-8
    derivative=(c.stored(x+epsilon*dx)-c.stored(x-epsilon*dx))/(2*epsilon)
    power=dx[c.base]-dx[c.base+1]-dx[c.base+2]
    assert derivative==pytest.approx(power,rel=1e-8,abs=1e-3)


def test_source_mode_entry_zero_release_and_no_repeated_zero_event(native):
    c=Circuit(document('linear_core_start'));x=c.initial();c.synchronize(0.,x)
    assert c.mode[0]==1 and c.rhs(0.,x)[0]>0
    count=len(c.source_events);c.synchronize(0.,x);assert len(c.source_events)==count
    c.synchronize(.005,x);assert c.mode[0]==0
    count=len(c.source_events);c.synchronize(.005,x);assert len(c.source_events)==count
    c.synchronize(.01,x);assert c.mode[0]==-1 and c.rhs(.01,x)[0]<0


def test_controller_and_observer_have_complete_partial_period_output(native):
    p=document('linear_core_start');p['duration_s']=.00107
    result=run(p,method='DOP853',rtol=1e-9,atol=1e-11,max_step=5e-5)
    assert result['completed'] and result['time_s'][-1]==.00107
    assert all(len(v)==len(result['time_s']) for v in result['channels'].values())
    assert result['metrics']['maximum_energy_relative_error']<1e-8


@pytest.fixture(scope='module')
def reporting_sources():
    import zipfile
    import power_grid_env.switching_drive as runtime
    wheel=os.environ.get('PGE_PREVIOUS_RUNTIME_WHEEL')
    if not wheel:pytest.skip('set PGE_PREVIOUS_RUNTIME_WHEEL for reporting-only migration tests')
    with zipfile.ZipFile(wheel) as z:old=z.read('power_grid_env/switching_drive.py').decode()
    return old,Path(runtime.__file__).read_text()


def test_reporting_source_compatibility_accepts_actual_clock_correction(reporting_sources):
    from benchmarks.continuous_drive_collection import reporting_only_ast
    assert reporting_only_ast(*reporting_sources)


@pytest.mark.parametrize('mutation',['inductance','newton','flux','control','clock_scale','raw_clock'])
def test_reporting_source_compatibility_rejects_other_changes(reporting_sources,mutation):
    from benchmarks.continuous_drive_collection import reporting_only_ast
    old,current=reporting_sources
    substitutions={
        'inductance':('2*p.source_inductance_h/h','3*p.source_inductance_h/h'),
        'newton':('range(45)','range(44)'),
        'flux':('motor_current_alpha_a=float(currents[0])','motor_current_alpha_a=float(2*currents[0])'),
        'control':('m.controller.update(start,current,m.speed,','m.controller.update(start,current,m.speed+1e-3,'),
        'clock_scale':('round(t/p.sample_s)*p.sample_s','round(t/p.sample_s)*p.sample_s+1e-6'),
        'raw_clock':('times.append(float(reported))','times.append(float(t))')}
    before,after=substitutions[mutation];assert before in current
    assert not reporting_only_ast(old,current.replace(before,after,1))


def test_native_authentication_rejects_unregistered_wheel_before_import(tmp_path):
    wheel=tmp_path/'fake.whl';wheel.write_bytes(b'not the pinned native distribution')
    with pytest.raises(ValueError,match='SHA256'):authenticate(wheel)


def test_reference_worker_rejects_preloaded_production_modules():
    import subprocess,sys
    script="""
import power_grid_env,runpy,sys
sys.argv=['reference','--study','missing-study.json','--wheel','missing-wheel.whl','--out','not-created.json']
runpy.run_module('benchmarks.continuous_drive_reference',run_name='__main__')
"""
    completed=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=30)
    assert completed.returncode!=0 and 'production modules were loaded before isolation' in completed.stderr
    assert 'FileNotFoundError' not in completed.stderr
