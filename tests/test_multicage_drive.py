"""Independent PHY-20 equations, exact limits, full-drive evidence and negatives."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from benchmarks.multicage_drive_reference import MulticageCircuit,AllFluxMachine,run
from benchmarks.multicage_drive_acceptance import document,verify_result


def set_machine(machine,flux,u,speed):
    machine.state.psi_s_ab=flux[0];machine.state.psi_r_ab=np.array(flux[1:],complex)
    machine.inp.u_s_ab=u;machine.inp.w_M=speed;machine.set_outputs(0.)
    return machine.rhs(0.)


@pytest.mark.parametrize('count',[1,2,3,4])
@pytest.mark.parametrize('beta',[0.,2.])
def test_full_storage_power_derivative_for_all_cages_and_saturation(count,beta):
    p=document('four_saturated_warm');p['motor']['cages']=p['motor']['cages'][:count];p['motor']['saturation_beta_per_vs']=beta
    p['motor_initial_fluxes_ab_vs']=p['motor_initial_fluxes_ab_vs'][:count+1]
    c=MulticageCircuit(p);x=c.initial();x[:3]=[1.,-.5,0.];x[c.speed_index]=15.;c.mode[:]=[1,-1,0];c.bypassed=True
    c.gates=np.array([[1,0],[0,1],[0,0]],bool);dx=c.rhs(.001,x);epsilon=1e-8
    derivative=(c.stored(x+epsilon*dx)-c.stored(x-epsilon*dx))/(2*epsilon)
    assert derivative==pytest.approx(dx[c.base]-dx[c.base+1]-dx[c.base+2],rel=1e-7,abs=1e-3)
    assert sum(dx[c.base+10:])==pytest.approx(dx[c.base+4],abs=1e-12)


@pytest.mark.parametrize('count',[2,3,4])
@pytest.mark.parametrize('beta',[0.,2.])
def test_identical_parallel_cages_collapse_exactly_to_one_cage(count,beta):
    p=document('four_saturated_warm')['motor'];p['saturation_beta_per_vs']=beta
    cage=copy.deepcopy(p['cages'][0]);p['cages']=[copy.deepcopy(cage) for _ in range(count)]
    q=copy.deepcopy(p);q['cages']=[dict(resistance_ohm=cage['resistance_ohm']/count,leakage_h=cage['leakage_h']/count)]
    a=AllFluxMachine(p);b=AllFluxMachine(q);flux=[.35+.12j]+[.31+.08j]*count
    ds,dr=set_machine(a,flux,170+42j,23.);ss,sr=set_machine(b,[flux[0],flux[1]],170+42j,23.)
    assert ds==pytest.approx(ss,abs=1e-12);np.testing.assert_allclose(dr,sr[0],atol=1e-12,rtol=0)
    assert a.out.i_s_ab==pytest.approx(b.out.i_s_ab,abs=1e-12)
    assert a.out.tau_M==pytest.approx(b.out.tau_M,abs=1e-12)
    assert sum(a.par.R_r*abs(a.out.i_r_ab)**2)==pytest.approx(b.par.R_r[0]*abs(b.out.i_r_ab[0])**2,abs=1e-12)


@pytest.mark.parametrize('count',[2,4])
def test_opposed_equal_rotor_difference_mode_has_exact_exponential_decay(count):
    p=document('four_saturated_warm')['motor'];cage=copy.deepcopy(p['cages'][0]);p['cages']=[copy.deepcopy(cage) for _ in range(count)]
    model=AllFluxMachine(p);initial=np.zeros(count+1,complex);initial[1:3]=[.02+.01j,-.02-.01j]
    def rhs(t,flux):
        ds,dr=set_machine(model,flux,0j,0.);return np.r_[ds,dr]
    result=solve_ivp(rhs,(0.,.005),initial,rtol=1e-11,atol=1e-13)
    assert result.success
    np.testing.assert_allclose(result.y[:,-1],initial*np.exp(-cage['resistance_ohm']/cage['leakage_h']*.005),atol=1e-12,rtol=1e-10)


def test_cage_permutation_preserves_network_shaft_thermal_and_energy():
    p=document('four_saturated_warm');p['motor_initial_fluxes_ab_vs']=[[.6,.01],[.58,.02],[.57,-.01],[.59,.03],[.56,-.02]]
    permutation=[2,0,3,1];q=copy.deepcopy(p)
    q['motor']['cages']=[p['motor']['cages'][k] for k in permutation]
    q['motor_initial_fluxes_ab_vs']=[p['motor_initial_fluxes_ab_vs'][0]]+[p['motor_initial_fluxes_ab_vs'][k+1] for k in permutation]
    a=MulticageCircuit(p);b=MulticageCircuit(q);x=a.initial();y=b.initial()
    a.gates=b.gates=np.array([[1,0],[0,1],[0,0]],bool);a.mode[:]=b.mode[:]=[1,-1,0]
    dx=a.rhs(.001,x);dy=b.rhs(.001,y)
    np.testing.assert_allclose(dx[:7],dy[:7],atol=1e-9,rtol=1e-12)
    np.testing.assert_allclose(dx[a.speed_index:a.base+10],dy[b.speed_index:b.base+10],atol=1e-8,rtol=1e-12)
    np.testing.assert_allclose(dx[7:a.speed_index].reshape(-1,2)[permutation],dy[7:b.speed_index].reshape(-1,2),atol=1e-12)
    np.testing.assert_allclose(dx[a.base+10:][permutation],dy[b.base+10:],atol=1e-12)
    assert a.stored(x)==pytest.approx(b.stored(y),abs=1e-10)


@pytest.fixture(scope='module')
def native():
    from benchmarks.continuous_drive_reference import authenticate
    wheel=os.environ.get('PGE_MOTULATOR_WHEEL')
    if not wheel:pytest.skip('set pinned native wheel for single-cage reduction tests')
    return authenticate(wheel)


@pytest.mark.parametrize('temperature',[[25.,25.],[90.,70.],[120.,100.]])
@pytest.mark.parametrize('flux',[[[0.,0.],[0.,0.]],[[.3,.1],[.25,.02]],[[-.5,.4],[.1,-.2]]])
def test_single_cage_point_reduces_to_original_motulator_rhs(native,temperature,flux):
    from benchmarks.electrothermal_drive_acceptance import document as single
    from benchmarks.electrothermal_drive_reference import ElectrothermalCircuit
    p=single('warm_device_losses');a=MulticageCircuit(p);b=ElectrothermalCircuit(p);x=a.initial();y=b.initial()
    x[a.flux_states]=y[5:9]=np.asarray(flux).ravel();x[a.winding_states]=y[13:15]=temperature
    x[a.speed_index]=y[9]=23.
    a.prepare(x);b.prepare(y)
    a.machine.inp.u_s_ab=b.machine.inp.u_s_ab=125+35j
    a.machine.set_outputs(0.);b.machine.set_outputs(0.)
    ds,dr=a.machine.rhs(0.);ss,sr=b.machine.rhs(0.)
    assert ds==pytest.approx(ss,abs=1e-11)
    assert dr[0]==pytest.approx(sr,abs=1e-11)
    assert a.machine.out.i_s_ab==pytest.approx(b.machine.out.i_s_ab,abs=1e-11)
    assert a.machine.out.i_r_ab[0]==pytest.approx(b.machine.out.i_r_ab,abs=1e-11)
    assert a.machine.out.tau_M==pytest.approx(b.machine.out.tau_M,abs=1e-10)


def test_full_single_cage_hybrid_reduction_matches_native_reference(native):
    from benchmarks.electrothermal_drive_acceptance import document as single
    from benchmarks.electrothermal_drive_reference import run as native_run
    p=single('warm_device_losses');p['duration_s']=.00083
    a=run(p,rtol=1e-11,atol=1e-13);b=native_run(p,rtol=1e-11,atol=1e-13)
    assert a['time_s']==b['time_s']
    for key in b['channels']:np.testing.assert_allclose(a['channels'][key],b['channels'][key],atol=1e-8,rtol=1e-10)
    assert a['event_log']==b['event_log']
    assert a['metrics']['maximum_energy_relative_error']<1e-8
    assert sum(a['metrics']['rotor_cage_heat_j'])==pytest.approx(a['metrics']['motor_rotor_heat_j'],abs=1e-10)


@pytest.mark.parametrize('defect',['zero_core','five_cages','locked','observer','bad_leakage','flux_inventory'])
def test_unsupported_or_invalid_profiles_reject(defect):
    p=document('four_saturated_warm')
    if defect=='zero_core':p['motor']['core_loss_s']=0.
    elif defect=='five_cages':p['motor']['cages'].append(p['motor']['cages'][0])
    elif defect=='locked':p['motor']['locked_rotor']=True
    elif defect=='observer':p['observer_mode']='multicage_current'
    elif defect=='bad_leakage':p['motor']['cages'][2]['leakage_h']=-1
    else:p['motor_initial_fluxes_ab_vs'].pop()
    with pytest.raises(ValueError):MulticageCircuit(p).initial()


@pytest.mark.parametrize('count',[2,3,4])
def test_switch_energy_jump_preserves_all_cage_fluxes_and_total_storage(count):
    p=document('four_saturated_warm');p['motor']['cages']=p['motor']['cages'][:count];p['motor_initial_fluxes_ab_vs']=p['motor_initial_fluxes_ab_vs'][:count+1]
    c=MulticageCircuit(p);x=c.initial();before=x[c.flux_states].copy();energy=c.stored(x)
    c.switch(0.,x,np.array([[1,0],[0,1],[0,0]],bool))
    np.testing.assert_array_equal(x[c.flux_states],before)
    assert c.switching_heat>0 and c.stored(x)==pytest.approx(energy,abs=1e-9)


def test_multicage_reference_rejects_preloaded_production_before_reading_input():
    script="import power_grid_env,runpy,sys;sys.argv=['ref','--study','absent','--out','absent'];runpy.run_module('benchmarks.multicage_drive_reference',run_name='__main__')"
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=30)
    assert result.returncode and 'loaded before isolation' in result.stderr


@pytest.mark.parametrize('count',[2,3,4])
@pytest.mark.parametrize('gate_code',range(27))
@pytest.mark.parametrize('flux_kind',['zero','energized','unequal'])
def test_runtime_terminal_current_matches_independent_all_flux_port(count,gate_code,flux_kind):
    from power_grid_env.switching_magnetics import SwitchingMagnetics
    from power_grid_env.switching_drive import SwitchingDriveStudy
    p=document('four_saturated_warm');p['motor']['cages']=p['motor']['cages'][:count]
    flux=[[0.,0.] for _ in range(count+1)] if flux_kind=='zero' else [[.6,0.]]+[[.58,0.] for _ in range(count)] if flux_kind=='energized' else [[.3,.1]]+[[.27+.005*k,.09-.01*k] for k in range(count)]
    p['motor_initial_fluxes_ab_vs']=flux;c=MulticageCircuit(p);x=c.initial();value=gate_code;gates=[]
    for _ in range(3):
        code=value%3;value//=3;gates.append([code==1,code==2])
    c.gates=np.array(gates,bool);factor=c.factors(x)[1]
    a=SwitchingMagnetics(SwitchingDriveStudy.model_validate(p).motor).terminal_current(x[c.flux_states],x[3:5],c.gates,p['mosfet_resistance_ohm']*factor,p['body_diode_resistance_ohm']*factor,p['body_diode_drop_v'],x[c.winding_states])
    b=c.port(x)[0]
    np.testing.assert_allclose(a[:2],[b.real,b.imag],atol=1e-9,rtol=1e-10)
    rotor=(np.asarray(flux[1:])-flux[0])/np.array([k['leakage_h'] for k in p['motor']['cages']])[:,None]
    np.testing.assert_allclose(a[2:],rotor.ravel(),atol=1e-12)


@pytest.fixture(scope='module')
def corpus():
    path=os.environ.get('PGE_MULTICAGE_CORPUS')
    if not path:pytest.skip('set PGE_MULTICAGE_CORPUS for sealed corpus verification tests')
    return Path(path)


def test_recompute_full_inventory(corpus):
    r=verify_result(corpus)
    assert r['verified'] and r['passed'] and r['cases']==6 and r['full_declared_inventory_executed']


@pytest.mark.parametrize('defect',['limits','sources','solver','external_claim','producer','fourth_flux','cage_heat','summary','study','inventory'])
def test_resealed_evidence_cannot_change_physics_or_claim_full_inventory(corpus,tmp_path,defect):
    import shutil
    from power_grid_env.study_artifacts import read_json,encoded,seal
    root=tmp_path/'corpus';shutil.copytree(corpus,root);folder=root/'four_saturated_warm'
    if defect=='limits':
        path=root/'protocol.json';r=read_json(path);r['limits']['flux_vs']*=10
    elif defect=='sources':
        path=folder/'reference-0.json';r=read_json(path);r['reference_source_sha256']={}
    elif defect=='solver':
        path=folder/'reference-0.json';r=read_json(path);r['reference']['rtol']=.0001
    elif defect=='external_claim':
        path=folder/'reference-0.json';r=read_json(path);r['reference']['external_machine']=True
    elif defect=='producer':
        path=folder/'primary-0.json';r=read_json(path);r['code_sha256']='0'*64
    elif defect=='fourth_flux':
        path=folder/'primary-0.json';r=read_json(path);r['channels']['rotor_cage_4_flux_alpha_vs'][2]+=.01
    elif defect=='cage_heat':
        path=folder/'reference-0.json';r=read_json(path);r['metrics']['rotor_cage_heat_j'][3]*=-1
    elif defect=='summary':
        path=root/'summary.json';r=read_json(path);r['rows']=[]
    elif defect=='study':
        path=folder/'study.json';r=read_json(path);r['motor']['cages'][3]['resistance_ohm']*=2
    else:
        path=root/'summary.json';r=read_json(path);r['full_declared_inventory_executed']=False
    path.write_bytes(encoded(r));(root/'manifest.json').unlink();seal(root)
    with pytest.raises(ValueError):verify_result(root)


@pytest.mark.parametrize('count',[1,2,3,4])
@pytest.mark.parametrize('frequency_hz',[5.,50.,200.])
@pytest.mark.parametrize('slip',[0.,.02,1.,-.1])
def test_linear_sinusoidal_limit_matches_independent_equivalent_impedance_and_power(count,frequency_hz,slip):
    # Independently derive branch admittance in the sinusoidal steady regime.
    # This includes blocked rotor, synchronous speed and generating operation.
    p=document('four_saturated_warm')['motor'];p['cages']=p['cages'][:count];p['saturation_beta_per_vs']=0.
    model=AllFluxMachine(p);omega=2*np.pi*frequency_hz;s=1j*omega
    resistance=np.array([c['resistance_ohm'] for c in p['cages']]);leakage=np.array([c['leakage_h'] for c in p['cages']])
    admittance=p['core_loss_s']+1/(s*p['unsaturated_h'])+sum(slip/(resistance+s*slip*leakage))
    impedance=p['stator_r_ohm']+1/admittance;voltage=180+30j;current=voltage/impedance
    stator=(voltage-p['stator_r_ohm']*current)/s
    rotor=resistance/(resistance+s*slip*leakage)*stator
    speed=(1-slip)*omega/p['pole_pairs'];ds,dr=set_machine(model,np.r_[stator,rotor],voltage,speed)
    assert model.out.i_s_ab==pytest.approx(current,rel=1e-11,abs=1e-11)
    assert ds==pytest.approx(s*stator,rel=1e-11,abs=1e-10)
    np.testing.assert_allclose(dr,s*rotor,rtol=1e-11,atol=1e-10)
    power=1.5*(voltage*current.conjugate()).real
    heat=1.5*(p['stator_r_ohm']*abs(current)**2+sum(resistance*abs(model.out.i_r_ab)**2)+p['core_loss_s']*abs(ds)**2)
    assert power==pytest.approx(heat+model.out.tau_M*speed,rel=1e-10,abs=1e-8)
    if slip>=0:assert impedance.real>0
