"""Independent SI motor/circuit and six-state GENROU reference with transfer blocks.

One source, one transposed line, one round-rotor generator and one single/two-cage motor. Solves
physical stator/rotor/generator currents together; never calls production
network stamps, initialization, model constants or state derivatives.
"""
from pathlib import Path
import argparse
import cmath
import math
import numpy as np
from scipy.integrate import solve_ivp
from scipy import signal
from scipy.optimize import root
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from benchmarks.induction_reference import mechanical_torque_reference
from benchmarks.genrou_reference import saturation_curve


def reference(spec,report,*,hybrid_runner=None):
    n,specification=spec.network,spec.coupled
    assert len(n.generators)==len(n.lines)==len(n.sources)==len(specification.motors)==len(specification.machines)==1
    assert not(n.loads or n.transformers or n.inverters or n.shunts)
    m=specification.motors[0];g=n.generators[0];machine=specification.machines[0];source=n.sources[0];line=n.lines[0]
    gov,avr=machine.governor,machine.exciter
    assert gov is not None and avr is not None and machine.stabilizer is None
    if hybrid_runner is not None and m.contactor is None:raise ValueError('independent hybrid runner requires a contactor passport')
    if m.contactor is not None and hybrid_runner is None:raise ValueError('automatic contactor requires independent hybrid runner')
    supported={'source_voltage','load_torque_scale','governor_step','voltage_reference_step','motor_open','motor_close'}
    if any(e.kind not in supported for e in specification.events):raise ValueError('unsupported event in coupled SI reference')
    if hybrid_runner is not None:
        for e in specification.events:
            target=source.id if e.kind=='source_voltage' else g.id if e.kind in {'governor_step','voltage_reference_step'} else m.id
            if e.equipment_id!=target:raise ValueError('independent hybrid event target mismatch')
    if hybrid_runner is not None:
        if n.switches or n.storages or n.neutral_grounds or n.study_faults or n.harmonics or specification.faults:raise ValueError('unsupported independent hybrid network device')
        if g.ll_v!=m.rated_ll_v:raise ValueError('independent hybrid requires common voltage base')
        for matrix in [line.r_ohm,line.x_ohm]:
            values=np.array(matrix);balanced=np.full((3,3),values[0,1]);np.fill_diagonal(balanced,values[0,0])
            if not np.allclose(values,balanced,rtol=0,atol=1e-12):raise ValueError('independent hybrid requires balanced feeder')
    terminals={t.id:t.connectivity_node_id for t in n.terminals}
    assert terminals[g.terminal_id]==m.node_id and terminals[source.terminal_id]!=m.node_id
    assert not np.any(line.b_siemens)
    wb=2*math.pi*n.frequency_hz;vb=m.rated_ll_v/math.sqrt(3);ib=m.rated_va/(3*vb)
    gb=g.ll_v/math.sqrt(3);gib=machine.rated_va/(3*gb)
    zb=m.rated_ll_v**2/m.rated_va
    rs=m.stator_r_pu*zb;rr=m.rotor_r_pu*zb
    lm=m.magnetizing_x_pu*zb/wb;ls=(m.stator_x_pu+m.magnetizing_x_pu)*zb/wb;lr=(m.rotor_x_pu+m.magnetizing_x_pu)*zb/wb
    rotor_r=np.array([rr]+([m.rotor2_r_pu*zb] if m.model=='double_cage' else []))
    cages=len(rotor_r)
    rotor_l=np.full((cages,cages),lm+(m.rotor_mutual_x_pu*zb/wb if cages==2 else 0.))
    rotor_l+=np.diag(np.array([m.rotor_x_pu]+([m.rotor2_x_pu] if cages==2 else []))*zb/wb)
    def winding_flux(x):return np.array([complex(x[0],x[1])]+([complex(x[14],x[15])] if cages==2 else []))
    zline=complex(line.r_ohm[0][0]-line.r_ohm[0][1],line.x_ohm[0][0]-line.x_ohm[0][1])
    zs=complex(source.r_ohm,source.x_ohm);feed=zs+zline
    zg=complex(machine.armature_r_pu,machine.x_d_subtransient_pu)*g.ll_v**2/machine.rated_va
    assert machine.model=='GENROU'
    es=source.ll_v/math.sqrt(3)*cmath.exp(1j*source.angle_a_rad)
    steady=specification.operating_point is not None
    def initial_current(voltage,speed):
        if not m.connected:return 0j,np.zeros(cages,dtype=complex)
        matrix=np.zeros((1+cages,1+cages),dtype=complex)
        matrix[0,0]=rs+1j*wb*ls;matrix[0,1:]=1j*wb*lm
        slip=1-speed if steady else 1.
        if steady:
            matrix[1:,0]=1j*wb*slip*lm
            matrix[1:,1:]=np.diag(rotor_r)+1j*wb*slip*rotor_l
        else:
            matrix[1:,0]=lm;matrix[1:,1:]=rotor_l
        currents=np.linalg.solve(matrix,np.r_[voltage,np.zeros(cages)])
        return currents[0],currents[1:]
    def initial_error(values):
        voltage=g.v_set_pu*gb*cmath.exp(1j*values[0]);speed=values[1] if steady and m.connected else 0.
        istator,irotor=initial_current(voltage,speed)
        ig=istator-(es-voltage)/feed
        error=[((3*voltage*np.conj(ig)).real-g.p_w)/machine.rated_va]
        if steady and m.connected:
            torque=-3*wb*lm*(sum(irotor)*np.conj(istator)).imag/m.rated_va
            error.append(torque-mechanical_torque_reference(m,speed))
        return error
    guess=[source.angle_a_rad,.99] if steady and m.connected else [source.angle_a_rad]
    solved=root(initial_error,guess,tol=1e-11)
    assert solved.success and max(abs(np.array(initial_error(solved.x))))<1e-9,solved.message
    voltage=g.v_set_pu*gb*cmath.exp(1j*solved.x[0]);speed=solved.x[1] if steady and m.connected else 0.
    if steady and m.connected:assert specification.operating_point.minimum_speed_pu<=speed<=1.
    istator,irotor=initial_current(voltage,speed)
    ig=istator-(es-voltage)/feed;eg=voltage+zg*ig
    flux=lm*istator+rotor_l@irotor
    theta=m.thermal.initial_level if m.thermal else 0.
    if steady and specification.operating_point.thermal_state=='equilibrium' and m.thermal:theta=(abs(istator)/ib)**2
    # Independent state-space realizations of the two lead-lag blocks.
    Ag,Bg,Cg,Dg=[float(x.item()) for x in signal.tf2ss([gov.T2,1.],[gov.T3,1.])]
    Aa,Ba,Ca,Da=[float(x.item()) for x in signal.tf2ss([avr.TATB*avr.TB,1.],[avr.TB,1.])]
    # Independent dq initialization from the physical terminal/current phasors.
    xd,xd1,xd2=machine.x_d_pu,machine.x_d_prime_pu,machine.x_d_subtransient_pu
    xq,xq1,xq2=machine.x_q_pu,machine.x_q_prime_pu,machine.x_q_subtransient_pu;xl=machine.leakage_pu
    gd1=(xd2-xl)/(xd1-xl);gd2=(xd1-xd2)/(xd1-xl)**2
    gq1=(xq2-xl)/(xq1-xl);gq2=(xq1-xq2)/(xq1-xl)**2;gqd=(xq-xl)/(xd-xl)
    def initial_dq(delta):
        emf=eg/gb*cmath.exp(-1j*delta);curr=ig/gib*cmath.exp(-1j*delta)
        return -curr.imag,curr.real,emf.real,-emf.imag
    def q_error(value):
        id_,iq,pd,pq=initial_dq(value[0])
        return [(1+saturation_curve(machine,abs(eg)/gb)*gqd)*pq-(xq-xq2)*iq]
    guess=cmath.phase(voltage/gb+complex(machine.armature_r_pu,xq)*ig/gib)
    rotor=root(q_error,[guess],tol=1e-11)
    assert max(abs(np.array(q_error(rotor.x))))<1e-9
    delta=float(rotor.x[0]);id_,iq,pd,pq=initial_dq(delta)
    initial_field=(1+saturation_curve(machine,abs(eg)/gb))*pd+(xd-xd2)*id_
    initial_power=g.p_w/machine.rated_va+machine.armature_r_pu*abs(ig/gib)**2
    vref=abs(voltage)/gb+initial_field/avr.K
    state=np.array([flux[0].real,flux[0].imag,speed,delta,1.,theta,
                    initial_power,-Bg/Ag*initial_power,-Ba/Aa*(initial_field/avr.K),initial_field,
                    pd+(xd1-xd2)*id_,pq-(xq1-xq2)*iq,pd+(xl-xd2)*id_,pq+(xq2-xl)*iq])
    if cages==2:state=np.r_[state,flux[1].real,flux[1].imag]
    connected=m.connected;latched=False;scale=1.;load_scale=1.;pm=initial_power
    def algebraic(x):
        pd=gd1*x[10]+gd2*(xd1-xl)*x[12];pq=gq1*x[11]+(1-gq1)*x[13]
        eg=complex(pd,-pq)*gb*cmath.exp(1j*x[3]);flux=winding_flux(x)
        matrix=np.zeros((cages+2,cages+2),dtype=complex)
        matrix[0,0]=rs+1j*wb*ls+feed;matrix[0,1:1+cages]=1j*wb*lm;matrix[0,-1]=-feed
        matrix[1:1+cages,0]=lm;matrix[1:1+cages,1:1+cages]=rotor_l
        matrix[-1,0]=feed;matrix[-1,-1]=-feed-zg
        rhs=np.r_[es*scale,flux,es*scale-eg]
        if not connected:
            matrix[0]=0.;matrix[0,0]=1.;rhs[0]=0.
        solution=np.linalg.solve(matrix,rhs);im=solution[0];ir=solution[1:1+cages];ig=solution[-1]
        voltage=es*scale-feed*(im-ig)
        return im,ir,ig,voltage,eg
    def derivative(time,x):
        im,ir,ig,voltage,eg=algebraic(x)
        df=-rotor_r*ir-1j*wb*(1-x[2])*winding_flux(x)
        te=-3*wb*lm*(sum(ir)*np.conj(im)).imag/m.rated_va
        pe=3*(eg*np.conj(ig)).real/machine.rated_va
        dtheta=((abs(im)/ib)**2-x[5])/m.thermal.time_constant_s if m.thermal else 0.
        valve=np.clip(x[6],gov.VMIN,gov.VMAX)
        pout=Cg*x[7]+Dg*valve-gov.Dt*(x[4]-1)
        valve_rate=(pm-(x[4]-1)/gov.R-x[6])/gov.T1
        if (x[6]>=gov.VMAX and valve_rate>0) or (x[6]<=gov.VMIN and valve_rate<0):valve_rate=0.
        error=vref-abs(voltage)/gb
        field_rate=(avr.K*(Ca*x[8]+Da*error)-x[9])/avr.TE
        if (x[9]>=avr.EMAX and field_rate>0) or (x[9]<=avr.EMIN and field_rate<0):field_rate=0.
        eq,ed,psid,psiq=x[10:14]
        pd=gd1*eq+gd2*(xd1-xl)*psid;pq=gq1*ed+(1-gq1)*psiq
        curr=ig/gib*cmath.exp(-1j*x[3]);id_,iq=-curr.imag,curr.real
        se=saturation_curve(machine,math.hypot(pd,pq))
        ad=eq+(xd-xd1)*(gd1*id_-gd2*psid+gd2*eq)+se*pd
        aq=ed+(xq-xq1)*(gq2*ed-gq2*psiq-gq1*iq)+se*pq*gqd
        result=[df[0].real,df[0].imag,(te-load_scale*mechanical_torque_reference(m,x[2]))/(2*m.inertia_s),
                wb*(x[4]-1),(pout-pe-machine.damping_pu*(x[4]-1))/(2*machine.inertia_s),dtheta,
                valve_rate,Ag*x[7]+Bg*valve,Aa*x[8]+Ba*error,field_rate,
                (np.clip(x[9],avr.EMIN,avr.EMAX)-ad)/machine.td_prime_s,-aq/machine.tq_prime_s,
                (eq-psid-(xd1-xl)*id_)/machine.td_subtransient_s,(ed-psiq+(xq1-xl)*iq)/machine.tq_subtransient_s]
        return result+([df[1].real,df[1].imag] if cages==2 else [])
    hybrid=None
    if hybrid_runner is not None:
        def get_context():return dict(connected=connected,latched=latched,scale=scale,load_scale=load_scale,pm=pm,vref=vref)
        def set_context(cfg):
            nonlocal connected,latched,scale,load_scale,pm,vref
            connected,latched,scale,load_scale,pm,vref=[cfg[k] for k in ['connected','latched','scale','load_scale','pm','vref']]
        source_node=terminals[source.terminal_id]
        if m.contactor.sense_node_id not in {m.node_id,source_node}:raise ValueError('independent reference sensing node outside circuit')
        if any(level.nominal_ll_v!=m.rated_ll_v for level in n.voltage_levels):raise ValueError('independent controller requires common nominal voltage base')
        def sensed(x):
            im,ir,ig,voltage,eg=algebraic(x)
            return abs(voltage if m.contactor.sense_node_id==m.node_id else es*scale-zs*(im-ig))/vb
        segments,changes,trip_time,hybrid=hybrid_runner(settings=specification,motor=m,state=state,rhs=derivative,voltage=sensed,get_context=get_context,set_context=set_context)
    else:
        def trip(time,x):return x[5]-m.thermal.trip_level
        trip.terminal=True;trip.direction=1
        event_map={}
        for event in specification.events:event_map.setdefault(event.time_s,[]).append(event)
        boundaries=[0.,*sorted(event_map),specification.duration_s]
        segments=[];changes=[];trip_time=None
        for left,right in zip(boundaries,boundaries[1:]):
            cursor=left
            while cursor<right:
                sol=solve_ivp(derivative,(cursor,right),state,method='DOP853',rtol=2e-11,atol=1e-12,
                              max_step=min(.0005,specification.step_s/2),dense_output=True,
                              events=trip if m.thermal and not latched else None)
                assert sol.success,sol.message
                end=float(sol.t[-1]);segments.append((cursor,end,sol.sol,connected,scale,load_scale,pm,vref))
                state=sol.y[:,-1];triggered=sol.status==1
                scheduled=event_map.get(right,[]) if end==right else []
                if triggered:connected=False;latched=True;trip_time=end
                for event in scheduled:
                    if event.kind=='source_voltage':scale=event.value
                    elif event.kind=='load_torque_scale':load_scale=event.value
                    elif event.kind=='governor_step':pm+=event.value
                    elif event.kind=='voltage_reference_step':vref+=event.value
                    elif event.kind=='generator_mechanical_step':raise AssertionError('governed fixture cannot bypass turbine')
                    elif event.kind=='motor_open':connected=False
                    elif not latched:connected=True
                if triggered or scheduled:changes.append(end)
                assert end>cursor or triggered
                cursor=end
    channels={m.id+'.'+k:[] for k in ['speed_pu','e_real_pu','e_imag_pu','current_pu','bus_voltage_pu','p_w','q_var']}
    channels.update({g.id+'.'+k:[] for k in ['delta','omega','p_w','q_var','current_pu','e1q','e1d','e2d','e2q','psi2d','psi2q','Se','field_pu']})
    channels.update({g.id+'.'+key:[] for key in ['TGOV1.valve_pu','TGOV1.turbine_state_pu','TGOV1.output_pu','SEXS.field_pu','SEXS.filter_pu']})
    channels[source.id+'.p_w']=[];channels[source.id+'.q_var']=[]
    if m.thermal:channels[m.id+'.thermal_level']=[]
    if hybrid is not None:channels.update({key:[] for key in hybrid['channel_names']})
    for t,side in zip(report['time_s'],report['sample_side']):
        nearby=next((change for change in changes if abs(change-t)<1e-6),None)
        at=t if nearby is None else nearby
        candidates=[segment for segment in segments if segment[0]-1e-12<=at<=segment[1]+1e-12]
        segment=candidates[0] if side=='pre_event' else candidates[-1]
        left,right,interpolant,connected,scale,load_scale,pm,vref,*control=segment
        x=interpolant(min(right,max(left,at)));im,ir,ig,voltage,eg=algebraic(x)
        em=1j*wb*lm*sum(np.linalg.solve(rotor_l,winding_flux(x)))/vb
        sm=3*voltage*np.conj(im);sg=3*voltage*np.conj(ig)
        vsource=es*scale-zs*(im-ig);ssource=3*vsource*np.conj(im-ig)
        values={m.id+'.speed_pu':x[2],m.id+'.e_real_pu':em.real,m.id+'.e_imag_pu':em.imag,
                m.id+'.current_pu':abs(im)/ib,m.id+'.bus_voltage_pu':abs(voltage)/vb,m.id+'.p_w':sm.real,m.id+'.q_var':sm.imag,
                g.id+'.delta':x[3],g.id+'.omega':x[4],g.id+'.p_w':sg.real,g.id+'.q_var':sg.imag,g.id+'.current_pu':abs(ig)/gib,
                source.id+'.p_w':ssource.real,source.id+'.q_var':ssource.imag}
        values[g.id+'.TGOV1.valve_pu']=float(np.clip(x[6],gov.VMIN,gov.VMAX))
        values[g.id+'.TGOV1.turbine_state_pu']=float(x[7]/gov.T3)
        values[g.id+'.TGOV1.output_pu']=float(Cg*x[7]+Dg*np.clip(x[6],gov.VMIN,gov.VMAX)-gov.Dt*(x[4]-1))
        values[g.id+'.SEXS.field_pu']=float(np.clip(x[9],avr.EMIN,avr.EMAX))
        values[g.id+'.SEXS.filter_pu']=float(x[8]/avr.TB)
        pd=gd1*x[10]+gd2*(xd1-xl)*x[12];pq=gq1*x[11]+(1-gq1)*x[13]
        values.update({g.id+'.'+key:float(value) for key,value in zip(
            ['e1q','e1d','e2d','e2q','psi2d','psi2q','Se','field_pu'],
            [*x[10:14],pd,pq,saturation_curve(machine,math.hypot(pd,pq)),np.clip(x[9],avr.EMIN,avr.EMAX)])})
        if m.thermal:values[m.id+'.thermal_level']=x[5]
        if hybrid is not None:values.update(hybrid['signals'](control[0],at,sensed(x),connected))
        for key,value in values.items():channels[key].append(float(value))
    return {'channels':channels,'trip_time_s':trip_time,**({'independent_event_log':hybrid['event_log']} if hybrid is not None else {})}


def native_controlled_limit(saturated=False):
    from power_grid_env.rms_study import RMSSettings,RMSEvent
    spec=StudySpec.model_validate_json(Path('cases/studies/genrou_coupled_setpoint_steps.json').read_text())
    if saturated:
        spec.coupled.machines[0].saturation='quadratic'
        spec.coupled.machines[0].saturation_s10=.1;spec.coupled.machines[0].saturation_s12=.4
    spec.coupled.motors[0].connected=False
    actual=run_study(spec);assert actual['completed'],actual
    native=spec.model_copy(deep=True);native.study='rms';native.backend='andes';native.coupled=None
    native.rms=RMSSettings(duration_s=spec.coupled.duration_s,step_s=.004,machines=spec.coupled.machines,
        events=[RMSEvent(time_s=e.time_s,kind=e.kind,equipment_id=e.equipment_id,value=e.value) for e in spec.coupled.events])
    times=np.array(actual['time_s']);keep=np.r_[np.diff(times)!=0,True]
    factor=spec.coupled.machines[0].rated_va/spec.network.s_base_va
    errors=[]
    for step in [.004,.002,.001]:
        native.rms.step_s=step;expected=run_study(native);assert expected['completed'],expected
        row={'step_s':step}
        for key,native_key,scale in [('g.delta','g.delta',1.),('g.omega','g.omega',1.),
                                     ('g.TGOV1.valve_pu','g.TGOV1.LAG_y',1/factor),('g.SEXS.field_pu','g.SEXS.vout',1.),*[("g."+k,"g."+k,1.) for k in ['e1q','e1d','e2d','e2q']]]:
            sampled=np.interp(times[keep],expected['time_s'],np.array(expected['channels'][native_key])*scale)
            row[key]=float(max(abs(np.array(actual['channels'][key])[keep]-sampled)))
        errors.append(row)
    limits={'g.delta':1e-4,'g.omega':1e-5,'g.TGOV1.valve_pu':1e-4,'g.SEXS.field_pu':1e-3,**{'g.'+k:1e-3 for k in ['e1q','e1d','e2d','e2q']}}
    return {'saturated':saturated,'case':'controlled GENROU, disconnected motor only','errors':errors,'finest_limits':limits,
            'passed':all(errors[-1][key]<limit for key,limit in limits.items()) and errors[-1]['g.delta']<errors[0]['g.delta']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--check-andes',action='store_true')
    args=parser.parse_args();checks=[]
    for name in ['hold','motor_load','voltage_sag','setpoint_steps','limits_recovery','thermal_trip','saturated_hold','saturated_sag']:
        path=Path('cases/studies/genrou_coupled_'+name+'.json');spec=StudySpec.model_validate_json(path.read_text())
        result=run_study(spec)
        if not result.get('completed'):raise RuntimeError(result)
        independent=reference(spec,result)
        errors={key:float(max(abs(np.array(values)-result['channels'][key]))) for key,values in independent['channels'].items()}
        limits={key:100. if key.endswith(('.p_w','.q_var')) else 1e-5 if key.endswith(('.omega','.speed_pu','.delta')) else 1e-4 for key in errors}
        actual_trip=next((e['time_s'] for e in result['event_log'] if e['kind']=='thermal_trip'),None)
        expected_trip=independent['trip_time_s']
        same_trip=(actual_trip is None)==(expected_trip is None)
        trip_error=abs(actual_trip-expected_trip) if actual_trip is not None and expected_trip is not None else None
        passed=all(errors[key]<=limits[key] for key in errors) and same_trip and (trip_error is None or trip_error<=1e-6)
        write_json(args.out/(name+'.json'),result)
        write_json(args.out/(name+'-reference.json'),dict(time_s=result['time_s'],sample_side=result['sample_side'],**independent))
        checks.append(dict(case=str(path),case_sha256=sha256_file(path),maximum_errors=errors,limits=limits,
                           trip_time_error_s=trip_error,trip_time_tolerance_s=1e-6,passed=passed))
    write_json(args.out/'reference.json',dict(cases=checks,reference_sha256=sha256_file(Path(__file__)),
        load_reference_sha256=sha256_file(Path('benchmarks/induction_reference.py')),
        saturation_reference_sha256=sha256_file(Path('benchmarks/genrou_reference.py')),
        method='Independent SI stator/rotor/generator circuit and six-state round rotor with quadratic saturation, scipy.signal lead-lag realizations, DOP853',
        field_validated=False,industrial_profile_qualified=False))
    if args.check_andes:
        for saturated in [False,True]:
            native=native_controlled_limit(saturated)
            write_json(args.out/('native-saturated-limit.json' if saturated else 'native-controlled-limit.json'),native)
            if not native['passed']:raise SystemExit(1)
    print([(row['case'],row['passed'],row['maximum_errors']['g.SEXS.field_pu']) for row in checks])
    if not all(row['passed'] for row in checks):raise SystemExit(1)


if __name__=='__main__':main()
