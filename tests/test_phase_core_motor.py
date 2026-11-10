import copy
import numpy as np
import pytest
from power_grid_env.phase_network import PhaseCase,PhaseNetwork,MotorPort,run_emt,_space,_abc
from power_grid_env.phase_core_motor import PhaseCoreMotor
from tests.test_phase_network import unbalanced_motor_case


def core_case(thermal=False):
    d=unbalanced_motor_case().model_dump(mode='json');p=d['motors'][0]['passport'];p['core_loss_s']=.015;p['provenance']['core_loss']='authored conductance, Aalto Gamma equation (5); not a measured motor'
    p['locked_rotor']=False;p['initial_speed_rad_s']=20.;p['saturation_beta_per_vs']=.8
    p['cages'].append(dict(resistance_ohm=5.,leakage_h=.01))
    if thermal:
        p['thermal']=dict(stator_capacity_j_per_k=2000.,rotor_capacity_j_per_k=1000.,stator_ambient_w_per_k=10.,rotor_ambient_w_per_k=8.,cross_w_per_k=2.)
        p['provenance']['thermal']='authored lumped capacities and cooling'
    return PhaseCase.model_validate(d)


@pytest.mark.parametrize('thermal',[False,True])
def test_local_energy_identity_core_loss_and_torque_exclude_eddy_current(thermal):
    case=core_case(thermal);m=PhaseCoreMotor(case.motors[0]);x=m.initial();x[:m.at]=[.3,.2,.25,.16,.27,.17]
    if thermal:x[m.at+1:]=[35.,40.]
    v=150.+40j;dx,info=m.rates(0.,v,x);h=1e-7
    plus=m.rates(0.,v,x+h*dx)[1]['stored_j'];minus=m.rates(0.,v,x-h*dx)[1]['stored_j']
    assert (plus-minus)/(2*h)==pytest.approx(info['motor_power_w']-info['loss_w']-info['mechanical_power_w'],rel=2e-7,abs=1e-5)
    assert info['core_loss_w']>0
    _,other=m.rates(0.,-300.+100j,x);assert other['torque_nm']==info['torque_nm']


def test_algebraic_feedthrough_clarke_projection_matches_direct_kcl():
    d=core_case().model_dump(mode='json');d['nodes']+=['la','lb','lc'];d['motors'][0]['nodes']=['la','lb','lc'];d['resistors']=[dict(id='feed'+str(i),frm=s,to=t,resistance_ohm=r) for i,(s,t,r) in enumerate(zip(['a','b','c'],['la','lb','lc'],[1.,2.,3.]))]+[dict(id='ground'+str(i),frm=n,resistance_ohm=1e6) for i,n in enumerate(['la','lb','lc'])]
    c=PhaseCase.model_validate(d);m=PhaseNetwork(c);x=m.initial();v,_,j,_=m.fields(0.,x)
    g=.015/(1+.015*c.motors[0].passport.stator_r_ohm);P=np.eye(3)-np.ones((3,3))/3;feed=np.diag([1.,.5,1/3]);known=m.boundary(0.)
    expected=np.linalg.solve(feed+np.eye(3)/1e6+g*P,feed@known)
    np.testing.assert_allclose(v[m.free],expected,atol=1e-10);np.testing.assert_allclose((m.g@v-j)[m.free],0.,atol=1e-10)
    assert sum(j[m.free])==pytest.approx(0.,abs=1e-10)


def test_thermal_core_machine_in_unbalanced_network_preserves_total_energy():
    c=core_case(True);r=run_emt(c,duration_s=.02,sample_s=.0005,max_step_s=.0002,rtol=1e-9,atol=1e-11)
    assert r['completed'] and r['channels']['motor.stator_temperature_c'][-1]>20.
    assert min(r['channels']['motor.core_loss_w'])>=0
    assert abs(r['energy'][-1]['residual_j'])<1e-5 and r['kcl_max_a']<1e-8


def test_initial_flux_conversion_and_small_Gc_limit_match_lossless_phase_port():
    d=core_case().model_dump(mode='json');d['motors'][0]['initial_fluxes_ab_vs']=[[.1,.02],[.09,.01],[.095,.015]]
    a=copy.deepcopy(d);a['motors'][0]['passport']['core_loss_s']=0.
    b=copy.deepcopy(d);b['motors'][0]['passport']['core_loss_s']=1e-10
    reports=[run_emt(PhaseCase.model_validate(x),duration_s=.01,sample_s=.0005,max_step_s=.0001,rtol=1e-9,atol=1e-11) for x in [a,b]]
    for key in ['motor.current_alpha_a','motor.current_beta_a','motor.motor_speed_rad_s']:
        np.testing.assert_allclose(reports[0]['channels'][key],reports[1]['channels'][key],atol=1e-6,rtol=1e-8)


@pytest.mark.parametrize('kind',['current','both','inventory','provenance'])
def test_ambiguous_or_unsourced_initialization_rejected(kind):
    d=core_case().model_dump(mode='json');p=d['motors'][0]
    if kind=='current':p['initial_currents_ab_a']=[[0.,0.]]*3
    elif kind=='both':p['initial_currents_ab_a']=[[0.,0.]]*3;p['initial_fluxes_ab_vs']=[[0.,0.]]*3
    elif kind=='inventory':p['initial_fluxes_ab_vs']=[[0.,0.]]*2
    else:del p['passport']['provenance']['core_loss']
    with pytest.raises(ValueError):PhaseCase.model_validate(d)


def test_dc_steady_magnetic_flux_has_no_eddy_current_loss():
    c=core_case();m=PhaseCoreMotor(c.motors[0]);x=m.initial();x[m.at]=0.;x[:m.at]=np.tile([.2,.1],m.n)
    psi,_,_,magnetic,rs,_,_=m.internal(x);q=m.quantities(x,rs*magnetic)
    assert q['core_loss_w']==pytest.approx(0.,abs=1e-25)
    assert abs(q['stator_flux_derivative'])<1e-12


def test_isolated_star_core_branch_does_not_create_zero_sequence_path():
    d=core_case().model_dump(mode='json')
    for s in d['sources']:s['tones']=[dict(order=0,rms=120.)]
    r=run_emt(PhaseCase.model_validate(d),duration_s=.002,sample_s=.0005,max_step_s=.0001)
    assert max(abs(v) for v in r['channels']['motor.current_alpha_a'])<1e-10
    assert max(abs(v) for v in r['channels']['motor.current_beta_a'])<1e-10


def test_physical_core_conduction_can_anchor_two_algebraic_phase_rows():
    d=core_case().model_dump(mode='json');d['sources']=d['sources'][:1];d['resistors']=[];d['capacitors']=[];d['inductors']=[dict(id='L'+n,frm=n,resistance_ohm=.5) for n in ['b','c']];d['inductance_h']=[[.01,0.],[0.,.01]]
    m=PhaseNetwork(PhaseCase.model_validate(d));v,i,j,_=m.fields(0.,m.initial());assert not np.any(m.g)
    np.testing.assert_allclose((m.a@i-j)[m.free],0.,atol=1e-10)
    d['sources']=[]
    with pytest.raises(ValueError,match='floating'):PhaseNetwork(PhaseCase.model_validate(d))


def test_reference_uses_no_primary_motor_or_network_rhs(monkeypatch):
    from benchmarks.phase_core_acceptance import document
    from benchmarks.phase_core_reference import run_reference
    d=document('algebraic_mixed')
    def forbidden(*a,**kw):raise AssertionError('primary RHS used by independent reference')
    monkeypatch.setattr(PhaseCoreMotor,'rates',forbidden);monkeypatch.setattr(PhaseNetwork,'rhs',forbidden)
    r=run_reference(d['case'],[0.,.001,.002]);assert len(r['records'])==3 and not r['primary_rhs_used']


def test_positive_core_loss_motor_preserves_flux_and_energy_when_contacts_open():
    from benchmarks.protected_motor_cases import case
    from power_grid_env.protected_motor import run_protected_motor
    from power_grid_env.phase_network import PhaseEvent
    s=case('aalto-saturated-fan__restart');s.duration_s=.02;s.maximum_runup_s=.015
    for source in s.case.sources:source.envelope=[(0.,1.)]
    p=s.case.motors[0].passport;p.core_loss_s=.006;p.provenance['core_loss']='authored eddy conductance'
    s.case.events=[PhaseEvent(time_s=.01,kind='resistance',equipment_id=id_,value=s.off_resistance_ohm) for id_ in s.contact_ids]
    r=run_protected_motor(s);assert r['completed'],r
    assert r['maximum_energy_relative_residual']<2e-5 and min(r['channels']['motor.core_loss_w'])>=0
    indices=[i for i,t in enumerate(r['time_s']) if t==.01]
    assert len(indices)==2
    for key in ['motor.stator_flux_alpha_vs','motor.stator_flux_beta_vs']:
        assert r['channels'][key][indices[0]]==r['channels'][key][indices[1]]


def test_coherent_native_and_reference_tampering_is_caught_by_reference_replay(tmp_path,monkeypatch):
    import benchmarks.phase_core_acceptance as bench
    from power_grid_env.study_artifacts import read_json,encoded,seal
    monkeypatch.setattr(bench,'NAMES',('algebraic_mixed',));monkeypatch.setattr(bench,'sources',lambda:{'test':'fixed'})
    out=tmp_path/'assessment';assert bench.qualify(out)['passed'];assert bench.verify_qualification(out)['verified']
    folder=out/'algebraic_mixed';ref=read_json(folder/'reference.json');key='core.current_alpha_a';index=20
    ref['records'][index]['channels'][key]+=.01;(folder/'reference.json').write_bytes(encoded(ref));checks=[]
    for level in ['fine','coarse']:
        p=folder/level;r=read_json(p/'result.json');r['channels'][key][index]+=.01;(p/'result.json').write_bytes(encoded(r));(p/'manifest.json').unlink();seal(p);checks.append(bench.comparison(bench.document('algebraic_mixed'),r,ref))
    s=read_json(out/'summary.json');f,c=checks;s['rows'][0].update(fine=f,coarse=c,improved=f['maximum_scaled_error']<=c['maximum_scaled_error'],passed=f['passed'] and f['maximum_scaled_error']<=c['maximum_scaled_error']);s['passed']=s['rows'][0]['passed'];assert s['passed']
    (out/'summary.json').write_bytes(encoded(s));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError,match='reference replay differs'):bench.verify_qualification(out)


@pytest.mark.parametrize('cages',[1,4])
def test_locked_rotor_linear_core_model_matches_exact_multibranch_admittance(cages):
    d=core_case().model_dump(mode='json');p=d['motors'][0]['passport'];p['locked_rotor']=True;p['initial_speed_rad_s']=0.;p['saturation_beta_per_vs']=0.;p['cages']=[dict(resistance_ohm=2.5+i,leakage_h=.023+.004*i) for i in range(cages)]
    for s in d['sources']:s['tones'][0]['rms']=230.
    omega=2*np.pi*50.;y=.015+1/(1j*omega*.34)+sum(1/(c['resistance_ohm']+1j*omega*c['leakage_h']) for c in p['cages']);current_rms=230*y/(1+3.7*y);emf=230-3.7*current_rms;psi=np.sqrt(2)*emf/(1j*omega);rotors=[psi-c['leakage_h']*np.sqrt(2)*emf/(c['resistance_ohm']+1j*omega*c['leakage_h']) for c in p['cages']]
    d['motors'][0]['initial_fluxes_ab_vs']=[[float(z.real),float(z.imag)] for z in [psi,*rotors]]
    r=run_emt(PhaseCase.model_validate(d),duration_s=.04,sample_s=.0005,max_step_s=.0001,rtol=1e-10,atol=1e-12)
    expected=np.sqrt(2)*current_rms*np.exp(1j*omega*np.array(r['time_s']));actual=np.array(r['channels']['motor.current_alpha_a'])+1j*np.array(r['channels']['motor.current_beta_a'])
    np.testing.assert_allclose(actual,expected,atol=2e-7,rtol=1e-9)
