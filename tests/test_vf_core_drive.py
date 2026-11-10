import copy
import numpy as np
import pytest
from types import SimpleNamespace
from power_grid_env.phase_network import PhaseCase,run_emt
from power_grid_env.vf_drive import VfDrive
from benchmarks.vf_core_cases import document


@pytest.mark.parametrize('thermal',[False,True])
def test_local_whole_drive_energy_and_dc_port_identities(thermal):
    c=PhaseCase.model_validate(document('thermal_multicage' if thermal else 'core_runup')['case']);m=VfDrive(c.drives[0]);x=m.initial();x[m.motor_at:m.speed_at]=np.tile([.2,.1],m.n);x[m.speed_at]=30.
    if thermal:x[m.speed_at+1:]=[35.,40.]
    x[2]=-5.;dx,q=m.rates(.06,300.+20j,x,True);h=1e-7
    derivative=(m.rates(.06,300.+20j,x+h*dx,True)[1]['stored_j']-m.rates(.06,300.+20j,x-h*dx,True)[1]['stored_j'])/(2*h)
    assert derivative==pytest.approx(q['grid_power_w']-q['loss_w']-q['mechanical_power_w'],rel=3e-7,abs=1e-4)
    assert dx[4]==pytest.approx(q['rectifier_dc_power_w']-q['inverter_dc_power_w']-q['bleed_loss_w'],abs=1e-9)
    assert q['motor_core_loss_w']>=0 and q['rectifier_loss_w']>=0 and q['inverter_loss_w']>=0


def test_core_zero_limit_preserves_drive_trajectory():
    d=document('core_runup');d['emt'].update(duration_s=.02,sample_s=.0005);cases=[]
    for gc in [0.,1e-10]:
        data=copy.deepcopy(d['case']);data['drives'][0]['motor']['core_loss_s']=gc;cases.append(run_emt(PhaseCase.model_validate(data),**d['emt']))
    for key in ['drive.dc_voltage_v','drive.motor_current_peak_a','drive.motor_speed_rad_s']:
        np.testing.assert_allclose(cases[0]['channels'][key],cases[1]['channels'][key],atol=2e-6,rtol=1e-7)


def test_malformed_drive_flux_inventory_is_rejected():
    d=document('core_runup')['case'];d['drives'][0]['motor_initial_fluxes_ab_vs']=[[.1,0.]]*3
    with pytest.raises(ValueError,match='inventory'):PhaseCase.model_validate(d)


def test_dc_negative_energy_is_not_silently_clipped():
    c=PhaseCase.model_validate(document('core_runup')['case']);m=VfDrive(c.drives[0]);x=m.initial();x[4]=-.01
    with pytest.raises(ValueError,match='nonnegative'):m.rates(0.,300.+0j,x,True)


@pytest.mark.parametrize('grid_sign',[-1,1])
@pytest.mark.parametrize('motor_angle',[0.,np.pi])
def test_efficiency_losses_stay_passive_in_both_power_directions(grid_sign,motor_angle):
    d=document('core_runup');m=VfDrive(PhaseCase.model_validate(d['case']).drives[0]);x=m.initial();x[m.motor_at:m.speed_at]=[.3,.2,.25,.1];x[2]=grid_sign*5.;x[6]=motor_angle
    _,q=m.rates(.1,300.+20j,x,True)
    assert q['grid_power_w']*grid_sign<0
    assert (q['motor_power_w']>0)==(motor_angle==0.)
    assert q['rectifier_loss_w']>=0 and q['inverter_loss_w']>=0
    assert q['grid_power_w']-q['rectifier_dc_power_w']==pytest.approx(q['rectifier_loss_w'])
    assert q['inverter_dc_power_w']-q['motor_power_w']==pytest.approx(q['inverter_loss_w'])


def test_blocking_rectifier_preserves_dc_energy_and_motor_flux_at_event():
    d=document('core_runup');m=VfDrive(PhaseCase.model_validate(d['case']).drives[0]);x=m.initial();x[m.motor_at:m.speed_at]=[.3,.2,.25,.1];x[2]=-5.;before=x.copy()
    a,qa=m.rates(.06,300.+20j,x,True);b,qb=m.rates(.06,300.+20j,x,False)
    np.testing.assert_array_equal(x,before);assert qa['dc_energy_j']==qb['dc_energy_j']
    assert qa['motor_stator_flux_alpha_vs']==qb['motor_stator_flux_alpha_vs']
    assert qb['grid_power_w']==0 and qa['grid_power_w']>0


def test_reference_does_not_call_native_drive_or_machine(monkeypatch):
    from benchmarks.vf_core_reference import run_reference
    from power_grid_env.phase_core_motor import PhaseCoreMotor
    from power_grid_env.phase_network import PhaseNetwork
    d=document('core_runup')
    def forbidden(*a,**kw):raise AssertionError('native drive or machine called by independent reference')
    monkeypatch.setattr(VfDrive,'rates',forbidden);monkeypatch.setattr(PhaseCoreMotor,'rates',forbidden);monkeypatch.setattr(PhaseNetwork,'rhs',forbidden)
    r=run_reference(d['case'],[0.,.001,.002],['initial','regular','regular']);assert len(r['records'])==3 and not r['primary_rhs_used']


def test_coherently_forged_dc_diagnostics_fail_independent_replay(tmp_path,monkeypatch):
    import benchmarks.vf_core_acceptance as bench
    from power_grid_env.study_artifacts import read_json,encoded,seal
    original=bench.document
    def short(name):
        d=original(name);d['emt']['duration_s']=.015;return d
    monkeypatch.setattr(bench,'NAMES',('dc_undervoltage',));monkeypatch.setattr(bench,'document',short);monkeypatch.setattr(bench,'sources',lambda:{'test':'fixed'})
    out=tmp_path/'assessment';assert bench.qualify(out)['passed'];assert bench.verify_qualification(out)['verified']
    folder=out/'dc_undervoltage';ref=read_json(folder/'reference.json');key='drive.dc_energy_j';index=10;ref['records'][index]['channels'][key]+=.01;(folder/'reference.json').write_bytes(encoded(ref));results=[]
    for level in ['fine','coarse']:
        p=folder/level;r=read_json(p/'result.json');r['channels'][key][index]+=.01;(p/'result.json').write_bytes(encoded(r));(p/'manifest.json').unlink();seal(p);results.append(r)
    s=read_json(out/'summary.json');f=bench.comparison(short('dc_undervoltage'),results[0],ref);c=bench.comparison(short('dc_undervoltage'),results[1],ref);s['rows'][0].update(fine=f,coarse=c,improved=f['maximum_scaled_error']<=c['maximum_scaled_error'],passed=f['passed'] and f['maximum_scaled_error']<=c['maximum_scaled_error']);s['passed']=s['rows'][0]['passed'];assert s['passed']
    (out/'summary.json').write_bytes(encoded(s));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError,match='independent drive replay differs'):bench.verify_qualification(out)


def test_core_losses_change_dc_power_and_rectifier_demand_not_only_diagnostics():
    d=document('core_runup')['case'];d['drives'][0]['motor_initial_fluxes_ab_vs']=[[.3,.2],[.25,.1]];results=[]
    for gc in [0.,.006]:
        data=copy.deepcopy(d);data['drives'][0]['motor']['core_loss_s']=gc;m=VfDrive(PhaseCase.model_validate(data).drives[0]);results.append(m.rates(.1,300.+20j,m.initial(),True))
    a,qa=results[0];b,qb=results[1]
    assert qa['motor_core_loss_w']==0 and qb['motor_core_loss_w']>0
    assert qb['motor_power_w']>qa['motor_power_w']
    assert b[4]<a[4] and b[2]<a[2]


def test_averaged_braking_resistor_duty_uses_mean_square_voltage_power():
    d=document('braking_resistor');m=VfDrive(PhaseCase.model_validate(d['case']).drives[0]);x=m.initial();x[4]=.5*m.p.dc_capacitance_f*740.**2;dx,q=m.rates(.06,300.+20j,x,False)
    assert q['braking_duty']==pytest.approx(.5)
    assert q['braking_power_w']==pytest.approx(.5*740.**2/40.)
    assert dx[4]==pytest.approx(-q['inverter_dc_power_w']-q['bleed_loss_w']-q['braking_power_w'])
    h=1e-8;derivative=(m.rates(.06,300.+20j,x+h*dx,False)[1]['stored_j']-m.rates(.06,300.+20j,x-h*dx,False)[1]['stored_j'])/(2*h)
    assert derivative==pytest.approx(-q['loss_w']-q['mechanical_power_w'],rel=1e-6,abs=1e-4)


@pytest.mark.parametrize('change',[dict(braking_resistance_ohm=0.),dict(braking_start_v=650.),dict(braking_full_v=700.),dict(braking_provenance=''),dict(braking_full_v=None)])
def test_ambiguous_or_unsourced_braking_parameters_are_rejected(change):
    d=document('braking_resistor')['case'];d['drives'][0].update(change)
    with pytest.raises(ValueError):PhaseCase.model_validate(d)
