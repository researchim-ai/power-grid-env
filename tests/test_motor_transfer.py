import copy
import math
from pathlib import Path

import numpy as np
import pytest
from power_grid_env.motor_transfer import transfer_passport,run_transfer,verify_transfer
from power_grid_env.flux_machine import FluxMachinePassport,GammaWinding
from power_grid_env.induction import InductionMotor
from power_grid_env.motor_catalog import catalog_entry
from power_grid_env.study_artifacts import encoded,read_json,seal,export_zip,extract_zip


def reverse():
    p=catalog_entry('aalto-linear-fan')['motor']
    return dict(direction='flux_to_rms',frequency_hz=50.,pole_pairs=2,parameter_provenance='Published catalog circuit with authored mechanism; explicit representation split',
        flux=p.model_dump(mode='json'),rated_va=5000.,rated_ll_v=400.,stator_leakage_fraction=.02)


def forward(m,f=50.,p=2):
    return dict(direction='rms_to_flux',frequency_hz=f,pole_pairs=p,rms=m,parameter_provenance='Derived linear single-cage referral test')


@pytest.mark.parametrize('frequency,poles,rating,voltage',[(50.,2,5000.,400.),(60.,3,2e6,6600.),(25.,1,1000.,230.)])
def test_public_gamma_roundtrip_preserves_si_parameters(frequency,poles,rating,voltage):
    d=reverse();d.update(frequency_hz=frequency,pole_pairs=poles,rated_va=rating,rated_ll_v=voltage);d['flux']['pole_pairs']=poles
    a=transfer_passport(d);b=transfer_passport(forward(a['target'],frequency,poles))
    p=d['flux'];q=b['target']
    for key in ['stator_r_ohm','unsaturated_h','inertia_kg_m2','fan_nm_per_rad_s2','viscous_nm_per_rad_s']:
        assert q[key]==pytest.approx(p[key],rel=2e-13,abs=1e-15)
    assert q['cages'][0]==pytest.approx(p['cages'][0],rel=2e-13)
    assert not b['transient_equivalence_claimed'] and not b['network_transferred']
    assert not b['initial_conditions']['network_consistency_checked']


@pytest.mark.parametrize('slip',[-.2,0.,1e-6,.01,.2,1.,1.5])
def test_independent_T_and_Gamma_complex_impedance_at_every_slip(slip):
    d=reverse();r=transfer_passport(d);m=InductionMotor.model_validate(r['target']);g=d['flux'];wb=2*math.pi*d['frequency_hz'];z=m.rated_ll_v**2/m.rated_va
    zm=1j*m.magnetizing_x_pu*z
    # Explicit equivalent-circuit branches; neither production winding RHS nor Norton model.
    yt=0j if slip==0 else 1/(m.rotor_r_pu*z/slip+1j*m.rotor_x_pu*z)
    T=m.stator_r_pu*z+1j*m.stator_x_pu*z+1/(1/zm+yt)
    yr=0j if slip==0 else 1/(g['cages'][0]['resistance_ohm']/slip+1j*wb*g['cages'][0]['leakage_h'])
    Gamma=g['stator_r_ohm']+1/(1/(1j*wb*g['unsaturated_h'])+yr)
    assert T==pytest.approx(Gamma,rel=2e-13,abs=1e-13)


def test_inertia_and_mechanical_power_are_preserved_at_signed_speeds():
    d=reverse();d['flux']['viscous_nm_per_rad_s']=.07
    r=transfer_passport(d);m=InductionMotor.model_validate(r['target']);p=FluxMachinePassport.model_validate(d['flux']);wm=r['bases']['omega_sync_mechanical_rad_s']
    for speed in [-1.1,-.1,0.,.2,.99,1.1]:
        w=speed*wm
        assert m.inertia_s*m.rated_va*speed**2==pytest.approx(.5*p.inertia_kg_m2*w*w)
        load=p.fan_nm_per_rad_s2*w*abs(w)+p.viscous_nm_per_rad_s*w
        assert m.load_torque(speed)*m.rated_va/wm==pytest.approx(load)


def test_two_admissible_T_splits_have_same_terminal_circuit_but_are_not_identified():
    a=reverse();b=copy.deepcopy(a);b['stator_leakage_fraction']=.04
    x=transfer_passport(a);y=transfer_passport(b)
    assert x['target']['stator_x_pu']!=y['target']['stator_x_pu']
    assert transfer_passport(forward(x['target']))['target']['cages'][0]==pytest.approx(transfer_passport(forward(y['target']))['target']['cages'][0])


@pytest.mark.parametrize('defect',['missing_split','inadmissible_split','wrong_poles','warm','locked','multiple_cages','missing_source','mixed','unknown_field'])
def test_ambiguous_or_incompatible_passports_are_rejected(defect):
    d=reverse()
    if defect=='missing_split':d.pop('stator_leakage_fraction')
    elif defect=='inadmissible_split':d['stator_leakage_fraction']=.9
    elif defect=='wrong_poles':d['pole_pairs']=3
    elif defect=='warm':d['flux']['initial_speed_rad_s']=10
    elif defect=='locked':d['flux']['locked_rotor']=True
    elif defect=='multiple_cages':d['flux']['cages']*=2
    elif defect=='missing_source':d['parameter_provenance']=' '
    elif defect=='mixed':d['rms']=transfer_passport(d)['target']
    else:d['automatic_warm_start']=True
    with pytest.raises(ValueError):transfer_passport(d)


def test_nonlinear_projection_requires_exact_explicit_omissions():
    d=reverse();d['flux']['saturation_beta_per_vs']=.84;d['flux']['core_loss_s']=.01;d['flux']['provenance']['core_loss']='Authored Gc'
    with pytest.raises(ValueError,match='omission'):transfer_passport(d)
    d['acknowledged_omissions']=['saturation','core_loss']
    r=transfer_passport(d);assert r['omitted_properties']==['core_loss','saturation']
    d['acknowledged_omissions'].append('contactor')
    with pytest.raises(ValueError,match='omission'):transfer_passport(d)


def test_RMS_controls_and_thermal_replica_are_not_winding_temperature():
    d=forward(transfer_passport(reverse())['target']);m=d['rms']
    m['thermal']=dict(time_constant_s=10,trip_level=10,initial_level=0)
    m['connected']=False
    with pytest.raises(ValueError,match='omission'):transfer_passport(d)
    d['acknowledged_omissions']=['thermal_replica','disconnected_state']
    r=transfer_passport(d);assert r['target']['thermal'] is None
    assert r['initial_conditions']['mode']=='cold_zero'


def test_coulomb_friction_is_rejected_instead_of_disappearing():
    d=forward(transfer_passport(reverse())['target']);d['rms']['mechanical_load'].update(friction_torque_pu=.1,smoothing_speed_pu=.01)
    with pytest.raises(ValueError,match='friction'):transfer_passport(d)


def test_artifact_recompute_detects_resealed_physics_edit_and_zip(tmp_path):
    out=tmp_path/'transfer';run_transfer(reverse(),out);before=verify_transfer(out)
    export_zip(out,tmp_path/'transfer.zip');extract_zip(tmp_path/'transfer.zip',tmp_path/'extracted')
    assert verify_transfer(tmp_path/'extracted')==before
    r=read_json(out/'result.json');r['target']['rotor_r_pu']*=2
    (out/'result.json').write_bytes(encoded(r));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError,match='recomputed'):verify_transfer(out)


def test_cli_and_schema(tmp_path,capsys):
    from power_grid_env.cli import main
    path=tmp_path/'input.json';path.write_bytes(encoded(reverse()));out=tmp_path/'result'
    assert main(['motor-transfer','--input',str(path),'--out',str(out)])==0
    assert main(['verify-motor-transfer',str(out)])==0
    assert main(['schemas','--out',str(tmp_path/'schemas')])==0
    assert read_json(tmp_path/'schemas/motor-transfer.schema.json')['title']=='MotorTransferPlan'


def test_phase_port_preserves_saturation_and_thermal_without_replica_substitution():
    p=catalog_entry('aalto-saturated-viscous-thermal')['motor']
    d=dict(direction='flux_to_phase',flux=p.model_dump(mode='json'),frequency_hz=50,pole_pairs=2,
        phase_nodes=['a','b','c'],parameter_provenance='Catalog phase-port transfer')
    r=transfer_passport(d)
    assert r['target_kind']=='phase_port' and r['target']['passport']==p.model_dump(mode='json')
    assert r['target']['nodes']==['a','b','c'] or r['target']['nodes']==('a','b','c')
    assert r['omitted_properties']==[] and not r['network_transferred']


@pytest.mark.parametrize('nodes',[['a','a','c'],['0','b','c'],['a','b']])
def test_phase_port_rejects_ambiguous_terminals(nodes):
    d=reverse();d.update(direction='flux_to_phase',phase_nodes=nodes)
    for key in ['stator_leakage_fraction','rated_va','rated_ll_v']:d.pop(key)
    with pytest.raises(ValueError):transfer_passport(d)


def test_phase_port_does_not_silently_enable_unsupported_core_loss():
    d=reverse();d.update(direction='flux_to_phase',phase_nodes=['a','b','c']);d['flux']['core_loss_s']=.001;d['flux']['provenance']['core_loss']='Authored Gc'
    for key in ['stator_leakage_fraction','rated_va','rated_ll_v']:d.pop(key)
    with pytest.raises(ValueError,match='omission'):transfer_passport(d)
    d['acknowledged_omissions']=['core_loss']
    r=transfer_passport(d);assert r['omitted_properties']==['core_loss']
    assert r['target']['passport'].get('core_loss_s',0)==0


def test_equivalent_T_splits_preserve_reduced_RMS_electromagnetic_constants():
    d=reverse();a=InductionMotor.model_validate(transfer_passport(d)['target'])
    d['stator_leakage_fraction']=.04;b=InductionMotor.model_validate(transfer_passport(d)['target'])
    assert a.constants(50.)==pytest.approx(b.constants(50.),rel=2e-13)


def test_double_cage_RMS_is_rejected_without_silent_aggregation():
    d=forward(transfer_passport(reverse())['target'])
    d['rms'].update(model='double_cage',rotor2_r_pu=.03,rotor2_x_pu=.1)
    with pytest.raises(ValueError,match='single-cage'):transfer_passport(d)


def test_multicage_gamma_can_be_attached_to_phase_without_aggregation():
    p=catalog_entry('aalto-linear-fan')['motor'].model_dump(mode='json');p['cages']*=4
    d=dict(direction='flux_to_phase',flux=p,frequency_hz=50,pole_pairs=2,phase_nodes=['a','b','c'],parameter_provenance='Authored four-branch cage spectrum')
    result=transfer_passport(d)
    assert len(result['target']['passport']['cages'])==4 and result['omitted_properties']==[]
