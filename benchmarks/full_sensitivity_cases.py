"""Frozen physical-parameter sensitivity profiles and independent exact checks."""
from power_grid_env.study_sensitivity import SensitivityRequest
from power_grid_env.study_spec import run_study
from benchmarks.network_automation_reference import example as automation

NAMES=('pv_initialization','ats_delay','contactor_delay','exciter_bound','uel_bound')


def case(name):
    if name not in NAMES:raise ValueError('registered full sensitivity case required')
    if name=='pv_initialization':
        s=automation('matched_island');s.coupled.events=[];s.coupled.duration_s=.02;s.network.generators[0].p_w=200000.
        path='/network/sources/0/r_ohm';unit='ohm';step=.001
        outputs=['/initial/g.delta','/operating_point/generator_initial/g/internal_voltage_pu'];expected=None
    elif name=='ats_delay':
        s=automation('standby_transfer');path='/coupled/network_automation/breakers/2/closing_delay_s';unit='s';step=.001
        b=run_study(s);index=next(i for i,e in enumerate(b['event_log']) if e['kind']=='breaker_close' and e['equipment_id']=='reserve-cb' and e['outcome']=='applied')
        outputs=['/event_times/'+str(index),'/final/g.delta'];expected=1.
    elif name=='contactor_delay':
        from benchmarks.automatic_motor_group import example
        s=example('grid_feedback');p=s.induction
        for i,m in enumerate(p.motors):
            if m.contactor:m.contactor.dropout_delay_s=.03+.02*i
        b=run_study(s)
        if not b.get('completed'):raise ValueError('contactor sensitivity baseline failed')
        index=next(i for i,e in enumerate(b['event_log']) if e['kind']=='motor_open' and e.get('cause')=='undervoltage_timer')
        id_=b['event_log'][index]['equipment_id'];i=next(i for i,m in enumerate(p.motors) if m.id==id_)
        path='/induction/motors/'+str(i)+'/contactor/dropout_delay_s';unit='s';step=.0005
        outputs=['/event_times/'+str(index),'/final/'+id_+'.speed_pu'];expected=1.
    elif name=='exciter_bound':
        from benchmarks.genrou_jacobian_validation import case as original
        from power_grid_env.coupled_rms import CoupledEvent
        s=original('genrou_limits_recovery');p=s.coupled;p.duration_s=.6
        gid=p.machines[0].generator_id;p.events=[CoupledEvent(time_s=.1,kind='voltage_reference_step',equipment_id=gid,value=.2)]
        path='/coupled/machines/0/exciter/EMAX';unit='pu';step=.001
        outputs=['/final/'+gid+'.SEXS.field_pu'];expected=1.
    else:
        from benchmarks.generator_underexcitation_cases import example
        s=example('limited_authority');s.coupled.duration_s=.8;s.coupled.events=[e for e in s.coupled.events if e.time_s<.8]
        for e in s.coupled.events:
            if e.kind=='voltage_reference_step':e.value=-.12
        path='/coupled/underexcitation_limiters/0/MEL_MAX';unit='pu';step=.00001
        outputs=['/final/g2.UEL.output_pu'];expected=1.
    p=s.coupled or s.induction;p.rtol=1e-10;p.atol=1e-12;p.event_policy='coincident_roots_v1'
    protocol=SensitivityRequest(parameters=[dict(path=path,step=step,unit=unit)],outputs=outputs,isolate=False,derivative_atol=2e-6)
    return s,protocol,expected


def pv_oracle():
    import math,numpy as np
    from scipy.optimize import brentq
    vb=10000/math.sqrt(3);z=2.2+9j;zg=25j;yl=(300000-100000j)/10000**2
    def q(theta):
        v=vb*np.exp(1j*theta);return v,yl*v+(v-vb)/z
    theta=brentq(lambda a:3*(q(a)[0]*np.conj(q(a)[1])).real-200000,-.2,.2,xtol=1e-14)
    v,i=q(theta);it=1j*v*(yl+1/z);ir=-(v-vb)/z**2
    dt=-(v*np.conj(ir)).real/(1j*v*np.conj(i)+v*np.conj(it)).real
    e=v+zg*i;de=1j*v*dt+zg*(it*dt+ir)
    return [(de/e).imag,(np.conj(e)*de).real/abs(e)/vb]
