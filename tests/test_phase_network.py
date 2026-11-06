import math
from copy import deepcopy

import numpy as np
import pytest
from scipy.linalg import expm

from power_grid_env.phase_network import PhaseCase,PhaseNetwork,run_emt,harmonic_study,_sequence


def rlc(*,saturated=False):
    return PhaseCase.model_validate(dict(id='authored-rlc',nodes=['source','load'],
        sources=[dict(id='grid',node='source',tones=[dict(order=1,rms=230.)])],
        resistors=[dict(id='load-r',frm='load',resistance_ohm=20.)],
        capacitors=[dict(id='load-c',frm='load',capacitance_f=100e-6)],
        inductors=[dict(id='line',frm='source',to='load',resistance_ohm=1.,saturation_flux_vs=.02 if saturated else 0.,saturation_current_a=4.)],
        inductance_h=[[.02]],provenance={'all':'authored SI reference; no measured parameters'}))


def test_full_network_rlc_matches_closed_phasor_and_energy():
    case=rlc();report=run_emt(case,duration_s=.4,sample_s=.0002,max_step_s=.0004)
    omega=2*np.pi*50;expected=230/(1+(1+1j*omega*.02)*(1/20+1j*omega*100e-6))
    actual=complex(*report['final_phasors']['load.voltage_v'])
    assert abs(actual-expected)/abs(expected)<1e-6
    assert report['kcl_max_a']<1e-10
    assert abs(report['energy'][-1]['residual_j'])<1e-5
    harmonic=harmonic_study(case,[1]);found=complex(*harmonic['frames'][0]['voltage']['load'])
    assert abs(found-expected)<1e-10


def test_dc_rl_network_matches_exact_transient_without_capacitors():
    case=rlc().model_dump();case['capacitors']=[];case['sources'][0]['tones']=[dict(order=0,rms=100.)]
    case=PhaseCase.model_validate(case)
    report=run_emt(case,duration_s=.02,sample_s=.0001,max_step_s=.0002)
    times=np.array(report['time_s']);expected=100/21*(1-np.exp(-21/.02*times))
    np.testing.assert_allclose(report['channels']['line.current_a'],expected,atol=2e-7)
    assert abs(report['energy'][-1]['residual_j'])<1e-6


def test_saturated_coupled_network_has_passive_energy_and_tolerance_convergence():
    case=rlc(saturated=True)
    coarse=run_emt(case,duration_s=.1,sample_s=.0005,max_step_s=.0005,rtol=1e-5,atol=1e-7)
    fine=run_emt(case,duration_s=.1,sample_s=.0005,max_step_s=.00025,rtol=1e-9,atol=1e-11)
    reference=run_emt(case,duration_s=.1,sample_s=.0005,max_step_s=.0002,rtol=1e-10,atol=1e-12,method='DOP853')
    a=np.array(coarse['channels']['line.current_a']);b=np.array(fine['channels']['line.current_a']);c=np.array(reference['channels']['line.current_a'])
    assert max(abs(b-c))<2e-7 and max(abs(b-c))<max(abs(a-c))
    assert abs(fine['energy'][-1]['residual_j'])<1e-7
    with pytest.raises(ValueError,match='linear'):harmonic_study(case,[1])


def test_magnetic_differential_and_energy_derivatives_are_consistent():
    model=PhaseNetwork(rlc(saturated=True));h=1e-5
    for value in (-100.,-5.,.1,5.,100.):
        actual=(model.magnetic(np.array([value+h]))-model.magnetic(np.array([value-h])))/(2*h)
        expected=value*(.02+.02/4*(1-np.tanh(value/4)**2))
        assert actual==pytest.approx(expected,rel=1e-7,abs=1e-8)


def test_switching_has_explicit_pre_post_samples_and_no_energy_or_state_jump():
    data=rlc().model_dump();data['events']=[dict(time_s=.04,kind='resistance',equipment_id='load-r',value=10.)]
    report=run_emt(PhaseCase.model_validate(data),duration_s=.1,sample_s=.0005,max_step_s=.0005)
    indices=[i for i,t in enumerate(report['time_s']) if t==.04];assert len(indices)==2
    assert [report['sample_side'][i] for i in indices]==['pre_event','post_event']
    for key in ['load.voltage_v','line.current_a']:assert report['channels'][key][indices[0]]==report['channels'][key][indices[1]]
    assert report['energy'][indices[0]]['stored_j']==report['energy'][indices[1]]['stored_j']
    assert len(report['event_log'])==1 and abs(report['energy'][-1]['residual_j'])<1e-5


def test_tiny_capacitance_is_retained_instead_of_being_thresholded_away():
    data=rlc().model_dump();data['nodes'].append('tiny');data['capacitors'].append(dict(id='tiny-c',frm='tiny',to='0',capacitance_f=1e-18));data['resistors'].append(dict(id='tiny-r',frm='tiny',resistance_ohm=1.))
    model=PhaseNetwork(PhaseCase.model_validate(data));assert model.nd==2
    assert min(model.ceig)==pytest.approx(1e-18)


def test_floating_algebraic_cutset_is_diagnosed():
    data=rlc().model_dump();data['capacitors']=[];data['resistors']=[]
    with pytest.raises(ValueError,match='cutset'):PhaseNetwork(PhaseCase.model_validate(data))


def converter_case(kind,*,island=False,limited=False):
    nodes=['a','b','c'];resistors=[]
    if island:resistors=[dict(id='r'+n,frm=n,resistance_ohm=20.) for n in nodes]
    source=[] if island else [dict(id='s'+n,node=n,tones=[dict(order=1,rms=230.,phase_rad=p)]) for n,p in zip(nodes,[0.,-2*np.pi/3,2*np.pi/3])]
    converter=dict(id='der',kind=kind,nodes=nodes,nominal_ln_rms_v=230.,current_limit_rms_a=5. if limited else 100.,current_tau_s=.001,power_tau_s=.01,active_ref_w=6900.,reactive_ref_var=1380.,virtual_r_ohm=2.)
    return PhaseCase.model_validate(dict(id='authored-'+kind,nodes=nodes,sources=source,resistors=resistors,converters=[converter],sequence_monitors={'pcc':nodes},provenance={'all':'authored averaged controls; not an OEM model'}))


def test_gfl_network_tracks_pq_and_exact_first_order_current():
    case=converter_case('gfl');report=run_emt(case,duration_s=.1,sample_s=.0002,max_step_s=.0005)
    expected=math.hypot(6900.,1380.)/(3*230.)*math.sqrt(2.)
    times=np.array(report['time_s']);currents=np.array(report['channels']['der.current_vector_peak_a'])
    np.testing.assert_allclose(currents,expected*(1-np.exp(-times/.001)),atol=2e-6)
    assert report['channels']['der.p_w'][-1]==pytest.approx(6900.,abs=1e-5)
    assert report['channels']['der.q_var'][-1]==pytest.approx(1380.,abs=1e-5)
    assert report['sequences']['pcc']['negative_to_positive']<1e-10


def test_gfm_forms_an_island_and_matches_closed_virtual_rl_transfer():
    case=converter_case('gfm',island=True);report=run_emt(case,duration_s=.3,sample_s=.0002,max_step_s=.0004)
    # tau di/dt=(E-v)/Rv-i; v=Rload*i, so I=E/(Rload+Rv+j*w*tau*Rv).
    expected=230*20/(20+2+1j*2*np.pi*50*.001*2)
    assert abs(complex(*report['final_phasors']['a.voltage_v'])-expected)/abs(expected)<1e-6
    assert report['sequences']['pcc']['positive_rms']>200.
    assert abs(report['energy'][-1]['residual_j'])<1e-5


@pytest.mark.parametrize('kind,island',[('gfl',False),('gfm',True)])
def test_converter_vector_limit_is_enforced_in_actual_network(kind,island):
    case=converter_case(kind,island=island,limited=True);report=run_emt(case,duration_s=.08,sample_s=.0005,max_step_s=.0005)
    assert max(report['channels']['der.current_vector_peak_a'])<=math.sqrt(2)*5+1e-6
    assert any(report['channels']['der.limited'])


def test_phase_harmonics_and_sequence_components_are_not_collapsed_to_positive_sequence():
    data=converter_case('gfl').model_dump();data['converters']=[]
    data['sources'][0]['tones'].append(dict(order=3,rms=23.,phase_rad=0.))
    data['sources'][1]['tones'].append(dict(order=3,rms=23.,phase_rad=0.))
    data['sources'][2]['tones'].append(dict(order=3,rms=23.,phase_rad=0.))
    report=harmonic_study(PhaseCase.model_validate(data),[1,3])
    assert report['voltage_thd']['a']==pytest.approx(.1)
    zero=report['frames'][1]['sequences']['pcc'];assert zero['zero_rms']==pytest.approx(23.) and zero['positive_rms']<1e-10
    reverse=_sequence([230,230*np.exp(2j*np.pi/3),230*np.exp(-2j*np.pi/3)])
    assert reverse['negative_rms']==pytest.approx(230.) and reverse['positive_rms']<1e-10


@pytest.mark.parametrize('change',['duplicate','asymmetric_l','negative_l','unknown_node','bad_event'])
def test_invalid_phase_networks_are_rejected(change):
    data=rlc().model_dump()
    if change=='duplicate':data['nodes'].append('source')
    elif change=='asymmetric_l':data['inductance_h']=[[1,2]]
    elif change=='negative_l':data['inductance_h']=[[-1]]
    elif change=='unknown_node':data['inductors'][0]['to']='missing'
    else:data['events']=[dict(time_s=.1,kind='resistance',equipment_id='missing',value=1.)]
    with pytest.raises(ValueError):PhaseCase.model_validate(data)


def four_wire():
    source=['sa','sb','sc'];load=['a','b','c','n']
    matrix=np.full((4,4),.0002);np.fill_diagonal(matrix,.002)
    return PhaseCase.model_validate(dict(id='authored-mutual-neutral',nodes=source+load,
        sources=[dict(id='s'+n,node=n,tones=[dict(order=1,rms=230.,phase_rad=p),dict(order=3,rms=23.)]) for n,p in zip(source,[0.,-2*np.pi/3,2*np.pi/3])],
        resistors=[dict(id='r'+n,frm=n,to='n',resistance_ohm=r) for n,r in zip(load[:3],[10.,20.,30.])],
        capacitors=[dict(id='c'+n,frm=n,to='n' if n!='n' else '0',capacitance_f=10e-6) for n in load],
        inductors=[dict(id='wire'+str(i),frm=f,to=t,resistance_ohm=r) for i,(f,t,r) in enumerate(zip(source+['0'],load,[.2,.25,.3,.15]))],
        inductance_h=matrix.tolist(),sequence_monitors={'load':load[:3]},sequence_neutrals={'load':'n'},provenance={'all':'authored coupled four-conductor SI circuit'}))


def test_four_wire_mutual_network_matches_separately_written_terminal_system():
    case=four_wire();harmonic=harmonic_study(case,[1,3])
    for frame in harmonic['frames']:
        order=frame['order'];omega=2*np.pi*50*order;admit=np.zeros((4,4),complex)
        for k,r in enumerate([10.,20.,30.]):
            direction=np.zeros(4);direction[k]=1.;direction[3]=-1.
            admit+=(1/r+1j*omega*10e-6)*np.outer(direction,direction)
        admit[3,3]+=1j*omega*10e-6
        z=np.diag([.2,.25,.3,.15])+1j*omega*np.array(case.inductance_h)
        supply=np.r_[[230*np.exp(1j*p) for p in [0.,-2*np.pi/3,2*np.pi/3]],0j] if order==1 else np.array([23.,23.,23.,0.],complex)
        expected=np.linalg.solve(np.eye(4)+z@admit,supply)
        actual=np.array([complex(*frame['voltage'][n]) for n in ['a','b','c','n']])
        np.testing.assert_allclose(actual,expected,atol=2e-10)
        assert frame['kcl_max_a']<1e-9
    report=run_emt(case,duration_s=.25,sample_s=.0002,max_step_s=.00025,rtol=1e-8)
    for n in ['a','b','c','n']:
        assert abs(complex(*report['final_phasors'][n+'.voltage_v'])-complex(*harmonic['frames'][0]['voltage'][n]))<2e-5
    assert report['sequences']['load']['negative_rms']>.01
    assert abs(report['energy'][-1]['residual_j'])<1e-5


def test_gfl_pll_tracks_a_real_frequency_offset_and_initial_phase_error():
    case=converter_case('gfl').model_dump();case['frequency_hz']=51.
    for source in case['sources']:source['tones'][0]['phase_rad']+=.3
    report=run_emt(PhaseCase.model_validate(case),duration_s=.6,sample_s=.0005,max_step_s=.0005)
    assert report['channels']['der.frequency_hz'][-1]==pytest.approx(51.,abs=.001)
    assert report['channels']['der.p_w'][-1]==pytest.approx(6900.,rel=1e-4)


def test_gfm_frequency_droop_closes_through_the_electrical_load():
    from scipy.optimize import brentq
    data=converter_case('gfm',island=True).model_dump();data['converters'][0].update(droop_rad_s_per_w=.001,active_ref_w=0.,reactive_ref_var=0.)
    report=run_emt(PhaseCase.model_validate(data),duration_s=.3,sample_s=.0005,max_step_s=.0005)
    def power(w):return 3*230**2*20/(22**2+(w*.001*2)**2)
    omega=brentq(lambda w:w-2*np.pi*50+.001*power(w),2*np.pi*45,2*np.pi*50)
    assert report['channels']['der.frequency_hz'][-1]==pytest.approx(omega/(2*np.pi),abs=1e-5)
    assert report['channels']['der.p_w'][-1]==pytest.approx(power(omega),rel=1e-5)


def drive_case(locked=False):
    import json
    from pathlib import Path
    motor=json.loads(Path('cases/studies/flux-saturated_dol.json').read_text())['flux_network']['motors'][0]
    motor.update(locked_rotor=locked,saturation_beta_per_vs=0.,fan_nm_per_rad_s2=0. if locked else motor['fan_nm_per_rad_s2'])
    data=converter_case('gfl').model_dump();data['converters']=[]
    data['drives']=[dict(id='vfd',nodes=['a','b','c'],motor=motor,grid_ln_rms_v=230.,grid_current_limit_rms_a=30.,dc_capacitance_f=.02,dc_reference_v=700.,initial_dc_v=700.,motor_ln_rms_v=230.,ramp_s=.05)]
    return PhaseCase.model_validate(data)


def test_full_rectifier_dc_link_and_vf_motor_drive_closes_energy_and_accelerates():
    case=drive_case();result=run_emt(case,duration_s=.25,sample_s=.0005,max_step_s=.0005,rtol=1e-8)
    assert result['channels']['vfd.motor_speed_rad_s'][-1]>10.
    assert min(result['channels']['vfd.dc_voltage_v'])>600.
    assert max(result['channels']['vfd.current_vector_peak_a'])<=math.sqrt(2)*30+1e-5
    assert abs(result['energy'][-1]['residual_j'])<1e-4
    assert result['kcl_max_a']<1e-8


def test_vf_locked_rotor_matches_separate_steady_gamma_equivalent():
    case=drive_case(locked=True);result=run_emt(case,duration_s=.8,sample_s=.001,max_step_s=.0005,rtol=1e-8)
    w=2*np.pi*50;lm=.34;rr=2.5;leak=.023;rs=3.7
    impedance=rs+(1j*w*lm)*(rr+1j*w*leak)/(rr+1j*w*(lm+leak))
    expected=230/abs(impedance)*math.sqrt(2)
    assert result['channels']['vfd.motor_current_peak_a'][-1]==pytest.approx(expected,rel=1e-3)
    assert abs(result['energy'][-1]['residual_j'])<1e-4
    assert result['channels']['vfd.motor_speed_rad_s'][-1]==0.


def test_rectifier_vf_motor_transient_matches_independent_flux_coordinate_ode():
    from scipy.integrate import solve_ivp
    case=drive_case(locked=True);result=run_emt(case,duration_s=.2,sample_s=.0005,max_step_s=.0005,rtol=1e-9)
    ramp=.05;w=2*np.pi*50
    def rate(t,flux):
        psi_s,psi_r=flux;ir=(psi_r-psi_s)/.023;is_=psi_s/.34-ir
        angle=w*(t*t/(2*ramp) if t<ramp else t-ramp/2)
        voltage=np.sqrt(2)*230*min(t/ramp,1)*np.exp(1j*angle)
        return [voltage-3.7*is_,-2.5*ir]
    reference=solve_ivp(rate,(0.,.2),np.zeros(2,dtype=complex),method='DOP853',rtol=1e-11,atol=1e-12,max_step=.0001,t_eval=result['time_s'])
    ir=(reference.y[1]-reference.y[0])/.023;is_=reference.y[0]/.34-ir
    assert not any(result['channels']['vfd.modulation_limited'])
    np.testing.assert_allclose(result['channels']['vfd.motor_current_peak_a'],abs(is_),atol=2e-5)


def test_vf_drive_rides_through_grid_port_disconnect_with_continuous_dc_energy():
    data=drive_case().model_dump();data['events']=[dict(time_s=.08,kind='converter_connection',equipment_id='vfd',value=0.),dict(time_s=.11,kind='converter_connection',equipment_id='vfd',value=1.)]
    report=run_emt(PhaseCase.model_validate(data),duration_s=.2,sample_s=.0005,max_step_s=.0005,rtol=1e-8)
    assert min(report['channels']['vfd.dc_voltage_v'])>500.
    assert all(report['channels']['vfd.grid_power_w'][i]==0 for i,t in enumerate(report['time_s']) if .08<t<.11)
    for moment in (.08,.11):
        idx=[i for i,t in enumerate(report['time_s']) if t==moment]
        assert report['energy'][idx[0]]['stored_j']==pytest.approx(report['energy'][idx[1]]['stored_j'],abs=1e-10)
    assert abs(report['energy'][-1]['residual_j'])<1e-4


def test_duplicate_current_spectrum_and_nonsymmetric_mutual_matrix_are_rejected():
    data=rlc().model_dump();data['injections']=[dict(id='polluted',node='load',tones=[dict(order=1,rms=1.),dict(order=1,rms=2.)])]
    with pytest.raises(ValueError,match='unique'):PhaseCase.model_validate(data)
    data=four_wire().model_dump();data['inductance_h'][0][1]+=1e-16
    with pytest.raises(ValueError,match='symmetric'):PhaseCase.model_validate(data)


def unbalanced_motor_case():
    data=converter_case('gfl').model_dump();data['converters']=[]
    data['sources'][1]['tones'][0]['rms']=190.
    passport=drive_case(True).drives[0].motor.model_dump()
    data['motors']=[dict(id='motor',nodes=['a','b','c'],passport=passport)]
    return PhaseCase.model_validate(data)


def test_negative_sequence_reaches_rotor_dynamics_and_matches_independent_flux_equations():
    from scipy.integrate import solve_ivp
    case=unbalanced_motor_case();report=run_emt(case,duration_s=.2,sample_s=.0005,max_step_s=.0005,rtol=1e-9)
    a=np.exp(2j*np.pi/3)
    def rate(t,state):
        ps,pr=state;ir=(pr-ps)/.023;current=ps/.34-ir
        va=np.sqrt(2)*230*np.cos(2*np.pi*50*t);vb=np.sqrt(2)*190*np.cos(2*np.pi*50*t-2*np.pi/3);vc=np.sqrt(2)*230*np.cos(2*np.pi*50*t+2*np.pi/3)
        u=2/3*(va+a*vb+a*a*vc)
        return [u-3.7*current,-2.5*ir]
    reference=solve_ivp(rate,(0.,.2),np.zeros(2,dtype=complex),method='DOP853',rtol=1e-11,atol=1e-12,max_step=.0001,t_eval=report['time_s'])
    current=reference.y[0]/.34-(reference.y[1]-reference.y[0])/.023
    actual=np.array(report['channels']['motor.current_alpha_a'])+1j*np.array(report['channels']['motor.current_beta_a'])
    np.testing.assert_allclose(actual,current,atol=2e-5)
    assert report['sequences']['pcc']['negative_to_positive']>.01
    assert abs(report['energy'][-1]['residual_j'])<1e-4
    assert max(report['channels']['motor.rotor_copper_w'])>0.


def test_frequency_to_emt_bridge_reconstructs_all_phase_tones_without_startup_transient():
    from power_grid_env.phase_network import periodic_initialization
    case=four_wire();phasors=harmonic_study(case,[1,3]);prepared=periodic_initialization(case)
    report=run_emt(prepared,duration_s=.02,sample_s=.0002,max_step_s=.0004,rtol=1e-9)
    t=np.array(report['time_s'])
    for node in ['a','b','c','n']:
        expected=sum(np.sqrt(2)*np.real(complex(*frame['voltage'][node])*np.exp(1j*2*np.pi*50*frame['order']*t)) for frame in phasors['frames'])
        np.testing.assert_allclose(report['channels'][node+'.voltage_v'],expected,atol=2e-5)
    assert abs(report['energy'][-1]['residual_j'])<1e-6


def test_dc_undervoltage_derating_avoids_a_discontinuous_on_off_guard():
    data=drive_case(True).model_dump();data['drives'][0]['dc_capacitance_f']=.001
    for source in data['sources']:source['envelope']=[(0.,1.),(.06,1.),(.061,0.),(.2,0.),(.201,1.)]
    report=run_emt(PhaseCase.model_validate(data),duration_s=.25,sample_s=.0005,max_step_s=.0004,rtol=1e-8)
    assert min(report['channels']['vfd.dc_voltage_v'])>0.
    assert any(report['channels']['vfd.undervoltage_derated'])
    assert abs(report['energy'][-1]['residual_j'])<1e-4
