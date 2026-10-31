"""Electrical-port invariants and true source-terminal sensing."""
import math
import numpy as np
import pytest
from benchmarks.automatic_motor_group import example
from benchmarks.native_terminal_measurements import reconstruct_terminals
from power_grid_env.model import canonical_hash


def fixture(coupled=False,reverse=False,source_zero=False):
    spec=example('generator_staggered' if coupled else 'grid_staggered')
    settings=spec.coupled if coupled else spec.induction
    for m in settings.motors:m.contactor=None;m.thermal=None
    if source_zero:settings.events[0].value=0.
    source,line=spec.network.sources[0],spec.network.lines[0]
    zs=complex(source.r_ohm,source.x_ohm);zl=complex(line.r_ohm[0][0]-line.r_ohm[0][1],line.x_ohm[0][0]-line.x_ohm[0][1])
    base=settings.motors[0].rated_ll_v/math.sqrt(3)
    scales=np.array([1.,1.,0. if source_zero else .5,0. if source_zero else .5,1.,1.])
    es=source.ll_v/math.sqrt(3)*scales*np.exp(1j*source.angle_a_rad)
    current=np.full(6,(-1 if reverse else 1)*(50.-30j))
    voltage=es-(zs+zl)*current;terminal=es-zs*current;net=3*voltage*np.conj(current)
    gen=(2e6+1e5j) if coupled else 0j
    channels={};total=sum(m.rated_va for m in settings.motors)
    for m in settings.motors:
        power=(net+gen)*m.rated_va/total
        channels.update({m.id+'.p_w':power.real.tolist(),m.id+'.q_var':power.imag.tolist(),m.id+'.bus_voltage_pu':(abs(voltage)/base).tolist()})
    if coupled:
        g=spec.network.generators[0];channels[g.id+'.p_w']=[gen.real]*6;channels[g.id+'.q_var']=[gen.imag]*6
    native=dict(completed=True,backend_version='2.0.0',case_sha256=canonical_hash(spec),time_s=[0.,.2,.2,.5,.5,1.2],sample_side=['regular','pre_event','post_event','pre_event','post_event','regular'],channels=channels)
    return spec,native,dict(voltage=abs(terminal)/base,current=abs(current),power=3*terminal*np.conj(current),emf=abs(es)/base)


@pytest.mark.parametrize('coupled,reverse,zero',[(False,False,False),(True,False,False),(True,True,False),(True,True,True)])
def test_actual_terminals_match_known_complex_circuit(coupled,reverse,zero):
    spec,native,known=fixture(coupled,reverse,zero);r=reconstruct_terminals(spec,native)
    assert np.allclose(r['channels']['source.terminal_voltage_pu'],known['voltage'],rtol=0,atol=1e-12)
    assert np.allclose(r['channels']['source.branch_current_a'],known['current'],rtol=0,atol=1e-10)
    assert np.allclose(r['channels']['source.p_w'],known['power'].real,rtol=0,atol=1e-7)
    assert np.allclose(r['channels']['source.q_var'],known['power'].imag,rtol=0,atol=1e-7)
    assert np.max(abs(np.array(r['channels']['source.terminal_voltage_pu'])-known['emf']))>1e-4
    assert r['maximum_source_closure_pu']<1e-12 and not r['production_measurements_used']


def test_pre_and_post_source_voltage_are_distinct():
    spec,native,_=fixture();r=reconstruct_terminals(spec,native)
    assert r['channels']['source.internal_emf_voltage_pu']==pytest.approx([1.,1.,.5,.5,1.,1.])
    assert r['time_s']==native['time_s'] and r['sample_side']==native['sample_side']


@pytest.mark.parametrize('fault',['case','incomplete','power','port_voltage','nonfinite','missing_event_side','missing_channel'])
def test_corrupted_or_inconsistent_measurement_cannot_be_promoted(fault):
    spec,native,_=fixture()
    if fault=='case':native['case_sha256']='wrong'
    elif fault=='incomplete':native['completed']=False
    elif fault=='power':native['channels']['m.q_var'][0]+=1e6
    elif fault=='port_voltage':native['channels']['m2.bus_voltage_pu'][0]+=.1
    elif fault=='nonfinite':native['channels']['m.p_w'][0]=float('nan')
    elif fault=='missing_channel':del native['channels']['m.q_var']
    else:native['sample_side'][1]='regular'
    with pytest.raises(ValueError):reconstruct_terminals(spec,native)


def test_zero_voltage_uses_source_branch_equation_not_zero_current():
    spec,native,_=fixture();base=spec.induction.motors[0].rated_ll_v/math.sqrt(3)
    for m in spec.induction.motors:
        for key in ['p_w','q_var','bus_voltage_pu']:native['channels'][m.id+'.'+key]=[0.]*6
    r=reconstruct_terminals(spec,native);s=spec.network.sources[0];line=spec.network.lines[0]
    z=complex(s.r_ohm+line.r_ohm[0][0]-line.r_ohm[0][1],s.x_ohm+line.x_ohm[0][0]-line.x_ohm[0][1])
    assert r['channels']['source.branch_current_a'][0]==pytest.approx(base/abs(z))
    assert r['channels']['source.branch_current_a'][0]>0


def test_observer_does_not_call_production_solver(monkeypatch):
    spec,native,_=fixture(True)
    def forbidden(*args,**kwargs):raise AssertionError('production solver used for native sensing')
    monkeypatch.setattr('power_grid_env.study_spec.run_study',forbidden)
    monkeypatch.setattr('power_grid_env.motor_contactor.MotorContactorBank',forbidden)
    assert reconstruct_terminals(spec,native)['source_voltage_is_actual_terminal']


def test_refinement_qualifies_finest_step_without_hiding_coarse_error():
    from benchmarks.native_sensing_validation import qualify_case
    rows=[dict(case_id='x',step_s=step,maximum_errors={'p':error},limits={'p':1000.}) for step,error in zip([.00025,.000125,.0000625],[2000.,500.,125.])]
    q=qualify_case(rows)
    assert q['passed'] and q['maximum_normalized_errors_by_step']==[2.,.5,.125]
    rows[-1]['maximum_errors']['p']=1500.
    assert not qualify_case(rows)['passed']


def test_refinement_rejects_missing_step_or_nonconvergent_data():
    from benchmarks.native_sensing_validation import qualify_case
    rows=[dict(case_id='x',step_s=step,maximum_errors={'p':error},limits={'p':1000.}) for step,error in zip([.00025,.000125,.0000625],[100.,200.,300.])]
    assert not qualify_case(rows)['passed']
    with pytest.raises(ValueError):qualify_case(rows[:-1])
