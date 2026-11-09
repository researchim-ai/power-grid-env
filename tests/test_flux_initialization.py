import copy
import math
from pathlib import Path
import numpy as np
import pytest
from power_grid_env.motor_catalog import catalog_study,catalog_entry
from power_grid_env.flux_network import FluxNetworkSettings,FluxNetwork,run_flux_network
from power_grid_env.flux_initialization import initialize_periodic,assess_periodic,phase_ports
from power_grid_env.warm_flux import run_warm_flux,verify_warm_flux
from power_grid_env.study_artifacts import read_json,encoded,seal,export_zip,extract_zip


def example(kind='aalto-linear-fan'):
    n,s=catalog_study(kind,disturbance=False)
    s=FluxNetworkSettings.model_validate({**s.model_dump(mode='json'),'duration_s':.04,'step_s':.0005,'operating_point':{}})
    return n,s


@pytest.mark.parametrize('kind',['aalto-linear-fan','aalto-saturated-fan'])
def test_multi_bus_periodic_currents_KCL_and_shaft_torque(kind):
    n,s=example(kind);r=run_flux_network(n,s)
    assert r['completed'],r
    init=r['initialization'];assert init['qualified']
    assert init['maximum_normalized_rotating_residual']<1e-9 and init['kcl_relative_residual']<1e-9
    motor=init['motors']['m'];assert .8<motor['speed_pu']<1
    assert motor['torque_nm']==pytest.approx(motor['load_torque_nm'],abs=1e-7)
    # A steady periodic orbit has constant speed/current amplitude, not zero alpha/beta derivative.
    c=r['channels'];speed=np.array(c['m.speed_rad_s']);current=np.hypot(c['m.current_alpha_a'],c['m.current_beta_a'])
    assert np.ptp(speed)<1e-6 and np.ptp(current)<1e-6
    assert np.ptp(c['m.current_alpha_a'])>1
    assert r['maximum_energy_relative_residual']<s.maximum_energy_relative_residual


def test_cold_default_is_preserved_and_does_not_serialize_new_mode():
    n,s=catalog_study('aalto-linear-fan',disturbance=False)
    assert 'operating_point' not in s.model_dump(mode='json')
    c=FluxNetwork(n,s);assert not np.any(c.initial_state())


def test_copied_initial_state_cannot_mutate_cached_equilibrium():
    n,s=example();c=FluxNetwork(n,s);initialize_periodic(c)
    a=c.initial_state();a[:]=0
    assert np.any(c.initial_state()) and assess_periodic(c,c.initial_state(),s.operating_point)['qualified']


@pytest.mark.parametrize('defect',['core','thermal','ambiguous_speed','unknown_method'])
def test_unsupported_equilibrium_profiles_rejected(defect):
    n,s=example();d=s.model_dump(mode='json')
    if defect=='core':d['motors'][0].update(core_loss_s=.001);d['motors'][0]['provenance']['core_loss']='authored'
    elif defect=='thermal':d['motors'][0]=catalog_entry('aalto-saturated-viscous-thermal')['motor'].model_dump(mode='json')
    elif defect=='ambiguous_speed':d['motors'][0]['initial_speed_rad_s']=100
    else:d['operating_point']['method']='warm_import'
    with pytest.raises(ValueError):FluxNetworkSettings.model_validate(d)


def test_work_budget_failure_is_explicit_initialization_failure():
    n,s=example();s.operating_point.maximum_residual_evaluations=1
    r=run_flux_network(n,s)
    assert not r['completed'] and r['status']=='initialization_failed'
    assert 'budget' in r['detail']


def test_excessive_load_is_not_replaced_by_cold_start():
    n,s=example();s.motors[0].fan_nm_per_rad_s2*=100
    r=run_flux_network(n,s)
    assert not r['completed'] and r['reason']=='periodic_initialization'


def test_locked_rotor_requires_external_reaction_instead_of_zero_torque():
    n,s=example();s.motors[0].locked_rotor=True
    r=run_flux_network(n,s);assert r['completed'],r
    m=r['initialization']['motors']['m']
    assert m['speed_rad_s']==pytest.approx(0,abs=1e-10) and abs(m['external_holding_torque_nm'])>1
    assert m['torque_nm']+m['external_holding_torque_nm']==0


def test_export_carries_all_winding_currents_and_speed_with_explicit_terminals():
    n,s=example();c=FluxNetwork(n,s);initialize_periodic(c)
    with pytest.raises(ValueError):phase_ports(s,c.initialization,{})
    p=phase_ports(s,c.initialization,{'m':('a','b','c')})[0]
    from power_grid_env.vf_drive import PhaseMotor
    model=PhaseMotor(p);x=model.initial()
    assert x[:model.at].tolist()==np.array(c.initialization['motors']['m']['currents_ab_a']).ravel().tolist()
    assert x[model.at]==c.initialization['motors']['m']['speed_rad_s']


@pytest.mark.parametrize('currents',[[[1,0]],[[1,0],[0,0],[0,0]],[[float('nan'),0],[0,0]]])
def test_phase_initial_current_inventory_and_finiteness(currents):
    from power_grid_env.phase_network import MotorPort
    with pytest.raises(ValueError):MotorPort(id='m',nodes=('a','b','c'),passport=catalog_entry('aalto-linear-fan')['motor'],initial_currents_ab_a=currents)


@pytest.fixture(scope='module')
def packet(tmp_path_factory):
    n,s=example();out=tmp_path_factory.mktemp('warm')/'packet'
    assert run_warm_flux(dict(network=n,settings=s,phase_terminals={'m':('a','b','c')}),out)['numerically_qualified']
    return out


def test_sealed_equilibrium_and_zip_roundtrip(packet,tmp_path):
    a=verify_warm_flux(packet);assert a['verified'] and a['numerically_qualified']
    export_zip(packet,tmp_path/'study.zip');extract_zip(tmp_path/'study.zip',tmp_path/'extracted')
    assert verify_warm_flux(tmp_path/'extracted')==a


@pytest.mark.parametrize('defect',['current','speed','branch','phase_port','initial_trace','summary','metric'])
def test_resealed_forgery_cannot_replace_initial_physics(packet,tmp_path,defect):
    import shutil
    out=tmp_path/'forged';shutil.copytree(packet,out)
    name='result.json';r=read_json(out/name)
    if defect=='current':r['initialization']['motors']['m']['currents_ab_a'][0][0]+=1
    elif defect=='speed':r['initialization']['motors']['m']['speed_rad_s']-=10
    elif defect=='branch':r['initialization']['branch_currents_ab_a']['feeder'][0]+=1
    elif defect=='initial_trace':r['channels']['m.current_alpha_a'][0]+=1
    elif defect=='metric':r['maximum_energy_relative_residual']=0
    elif defect=='summary':name='summary.json';r=read_json(out/name);r['stability_proven']=True
    else:name='phase-ports.json';r=read_json(out/name);r[0]['initial_currents_ab_a'][0][0]+=1
    (out/name).write_bytes(encoded(r));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError):verify_warm_flux(out)


def test_cli_schema_and_identity(packet,tmp_path,capsys):
    from power_grid_env.cli import main
    assert main(['verify-warm-flux-study',str(packet)])==0
    assert main(['schemas','--out',str(tmp_path/'schemas')])==0
    assert read_json(tmp_path/'schemas/warm-flux-study.schema.json')['title']=='WarmFluxStudy'


def test_rotating_equilibrium_follows_global_source_phase_without_changing_speed():
    n,s=example();a=FluxNetwork(n,s);initialize_periodic(a)
    shifted=n.model_copy(deep=True)
    for source in shifted.sources:source.angle_a_rad+=.4
    b=FluxNetwork(shifted,s);initialize_periodic(b)
    x=a.initialization['motors']['m'];y=b.initialization['motors']['m']
    assert x['speed_rad_s']==pytest.approx(y['speed_rad_s'],abs=1e-9)
    for u,v in zip(x['currents_ab_a'],y['currents_ab_a']):
        assert complex(*u)*np.exp(.4j)==pytest.approx(complex(*v),abs=1e-8)


def test_motor_order_does_not_change_shared_network_equilibrium():
    from benchmarks.warm_flux_acceptance import example as network_example
    n,s=network_example('meshed_pair');a=FluxNetwork(n,s);initialize_periodic(a)
    other=s.model_copy(deep=True);other.motors.reverse();b=FluxNetwork(n,other);initialize_periodic(b)
    for name in a.initialization['motors']:
        assert a.initialization['motors'][name]['speed_rad_s']==pytest.approx(b.initialization['motors'][name]['speed_rad_s'],abs=1e-8)
        assert np.array(a.initialization['motors'][name]['currents_ab_a'])==pytest.approx(np.array(b.initialization['motors'][name]['currents_ab_a']),abs=1e-8)


def test_huge_inertia_cannot_hide_shaft_torque_mismatch():
    n,s=example();a=FluxNetwork(n,s);initialize_periodic(a)
    huge=s.model_copy(deep=True);huge.motors[0].inertia_kg_m2=1e20
    b=FluxNetwork(n,huge);initialize_periodic(b)
    m=b.initialization['motors']['m']
    assert m['torque_nm']==pytest.approx(m['load_torque_nm'],abs=1e-7)
    assert m['speed_rad_s']==pytest.approx(a.initialization['motors']['m']['speed_rad_s'],abs=1e-8)
    bad=b.initial_state();bad[b.addresses[0][1]]*=.99
    assert not assess_periodic(b,bad,huge.operating_point)['qualified']
