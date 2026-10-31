"""Independent SI mixed-machine reference with state-space governor/exciter blocks.

One source, one transposed line, one classical generator and one motor. Solves
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


def reference(spec,report):
    n,specification=spec.network,spec.coupled
    assert len(n.generators)==len(n.lines)==len(n.sources)==len(specification.motors)==len(specification.machines)==1
    assert not(n.loads or n.transformers or n.inverters or n.shunts)
    m=specification.motors[0];g=n.generators[0];machine=specification.machines[0];source=n.sources[0];line=n.lines[0]
    gov,avr=machine.governor,machine.exciter
    assert gov is not None and avr is not None and machine.stabilizer is None
    terminals={t.id:t.connectivity_node_id for t in n.terminals}
    assert terminals[g.terminal_id]==m.node_id and terminals[source.terminal_id]!=m.node_id
    assert not np.any(line.b_siemens)
    wb=2*math.pi*n.frequency_hz;vb=m.rated_ll_v/math.sqrt(3);ib=m.rated_va/(3*vb)
    gb=g.ll_v/math.sqrt(3);gib=machine.rated_va/(3*gb)
    zb=m.rated_ll_v**2/m.rated_va
    if m.model!='single_cage':raise ValueError('this coupled SI reference is single-cage only')
    rs=m.stator_r_pu*zb;rr=m.rotor_r_pu*zb
    lm=m.magnetizing_x_pu*zb/wb;ls=(m.stator_x_pu+m.magnetizing_x_pu)*zb/wb;lr=(m.rotor_x_pu+m.magnetizing_x_pu)*zb/wb
    zline=complex(line.r_ohm[0][0]-line.r_ohm[0][1],line.x_ohm[0][0]-line.x_ohm[0][1])
    zs=complex(source.r_ohm,source.x_ohm);feed=zs+zline
    xg=machine.x_d_prime_pu*g.ll_v**2/machine.rated_va
    es=source.ll_v/math.sqrt(3)*cmath.exp(1j*source.angle_a_rad)
    steady=specification.operating_point is not None
    def initial_current(voltage,speed):
        if not m.connected:return 0j,0j
        if not steady:
            istator=voltage/complex(rs,wb*(ls-lm*lm/lr))
            return istator,-lm/lr*istator
        slip=1-speed
        rotor=complex(rr/slip,wb*(lr-lm)) if abs(slip)>1e-14 else complex(np.inf,0.)
        parallel=1/(1/(1j*wb*lm)+1/rotor)
        istator=voltage/(complex(rs,wb*(ls-lm))+parallel)
        return istator,-istator*parallel/rotor
    def initial_error(values):
        voltage=g.v_set_pu*gb*cmath.exp(1j*values[0]);speed=values[1] if steady and m.connected else 0.
        istator,irotor=initial_current(voltage,speed)
        ig=istator-(es-voltage)/feed
        error=[((3*voltage*np.conj(ig)).real-g.p_w)/machine.rated_va]
        if steady and m.connected:
            torque=-3*wb*lm*(irotor*np.conj(istator)).imag/m.rated_va
            error.append(torque-mechanical_torque_reference(m,speed))
        return error
    guess=[source.angle_a_rad,.99] if steady and m.connected else [source.angle_a_rad]
    solved=root(initial_error,guess,tol=1e-11)
    assert solved.success and max(abs(np.array(initial_error(solved.x))))<1e-9,solved.message
    voltage=g.v_set_pu*gb*cmath.exp(1j*solved.x[0]);speed=solved.x[1] if steady and m.connected else 0.
    if steady and m.connected:assert specification.operating_point.minimum_speed_pu<=speed<=1.
    istator,irotor=initial_current(voltage,speed)
    ig=istator-(es-voltage)/feed;eg=voltage+1j*xg*ig
    flux=lm*istator+lr*irotor
    theta=m.thermal.initial_level if m.thermal else 0.
    if steady and specification.operating_point.thermal_state=='equilibrium' and m.thermal:theta=(abs(istator)/ib)**2
    # Independent state-space realizations of the two lead-lag blocks.
    Ag,Bg,Cg,Dg=[float(x.item()) for x in signal.tf2ss([gov.T2,1.],[gov.T3,1.])]
    Aa,Ba,Ca,Da=[float(x.item()) for x in signal.tf2ss([avr.TATB*avr.TB,1.],[avr.TB,1.])]
    initial_power=g.p_w/machine.rated_va;initial_field=abs(eg)/gb
    vref=abs(voltage)/gb+initial_field/avr.K
    state=np.array([flux.real,flux.imag,speed,np.angle(eg),1.,theta,
                    initial_power,-Bg/Ag*initial_power,-Ba/Aa*(initial_field/avr.K),initial_field])
    connected=m.connected;latched=False;scale=1.;load_scale=1.;pm=g.p_w/machine.rated_va
    def algebraic(x):
        eg=np.clip(x[9],avr.EMIN,avr.EMAX)*gb*cmath.exp(1j*x[3]);flux=complex(x[0],x[1])
        matrix=np.array([[rs+1j*wb*ls+feed,1j*wb*lm,-feed],[lm,lr,0.],[feed,0.,-feed-1j*xg]])
        rhs=np.array([es*scale,flux,es*scale-eg])
        if not connected:
            matrix[0]=[1.,0.,0.];rhs[0]=0.
        im,ir,ig=np.linalg.solve(matrix,rhs)
        voltage=es*scale-feed*(im-ig)
        return im,ir,ig,voltage,eg
    def derivative(time,x):
        im,ir,ig,voltage,eg=algebraic(x)
        df=-rr*ir-1j*wb*(1-x[2])*complex(x[0],x[1])
        te=-3*wb*lm*(ir*np.conj(im)).imag/m.rated_va
        pe=3*(eg*np.conj(ig)).real/machine.rated_va
        dtheta=((abs(im)/ib)**2-x[5])/m.thermal.time_constant_s if m.thermal else 0.
        valve=np.clip(x[6],gov.VMIN,gov.VMAX)
        pout=Cg*x[7]+Dg*valve-gov.Dt*(x[4]-1)
        valve_rate=(pm-(x[4]-1)/gov.R-x[6])/gov.T1
        if (x[6]>=gov.VMAX and valve_rate>0) or (x[6]<=gov.VMIN and valve_rate<0):valve_rate=0.
        error=vref-abs(voltage)/gb
        field_rate=(avr.K*(Ca*x[8]+Da*error)-x[9])/avr.TE
        if (x[9]>=avr.EMAX and field_rate>0) or (x[9]<=avr.EMIN and field_rate<0):field_rate=0.
        return [df.real,df.imag,(te-load_scale*mechanical_torque_reference(m,x[2]))/(2*m.inertia_s),
                wb*(x[4]-1),(pout-pe-machine.damping_pu*(x[4]-1))/(2*machine.inertia_s),dtheta,
                valve_rate,Ag*x[7]+Bg*valve,Aa*x[8]+Ba*error,field_rate]
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
    channels.update({g.id+'.'+k:[] for k in ['delta','omega','p_w','q_var','current_pu']})
    channels.update({g.id+'.'+key:[] for key in ['TGOV1.valve_pu','TGOV1.turbine_state_pu','TGOV1.output_pu','SEXS.field_pu','SEXS.filter_pu']})
    channels[source.id+'.p_w']=[];channels[source.id+'.q_var']=[]
    if m.thermal:channels[m.id+'.thermal_level']=[]
    for t,side in zip(report['time_s'],report['sample_side']):
        nearby=next((change for change in changes if abs(change-t)<1e-6),None)
        at=t if nearby is None else nearby
        candidates=[segment for segment in segments if segment[0]-1e-12<=at<=segment[1]+1e-12]
        segment=candidates[0] if side=='pre_event' else candidates[-1]
        left,right,interpolant,connected,scale,load_scale,pm,vref=segment
        x=interpolant(min(right,max(left,at)));im,ir,ig,voltage,eg=algebraic(x)
        em=1j*wb*lm/lr*complex(x[0],x[1])/vb
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
        if m.thermal:values[m.id+'.thermal_level']=x[5]
        for key,value in values.items():channels[key].append(float(value))
    return {'channels':channels,'trip_time_s':trip_time}


def native_controlled_limit():
    from power_grid_env.rms_study import RMSSettings,RMSEvent
    spec=StudySpec.model_validate_json(Path('cases/studies/regulated_coupled_setpoint_steps.json').read_text())
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
                                     ('g.TGOV1.valve_pu','g.TGOV1.LAG_y',1/factor),('g.SEXS.field_pu','g.SEXS.vout',1.)]:
            sampled=np.interp(times[keep],expected['time_s'],np.array(expected['channels'][native_key])*scale)
            row[key]=float(max(abs(np.array(actual['channels'][key])[keep]-sampled)))
        errors.append(row)
    limits={'g.delta':1e-4,'g.omega':1e-5,'g.TGOV1.valve_pu':1e-4,'g.SEXS.field_pu':1e-3}
    return {'case':'controlled GENCLS, disconnected motor only','errors':errors,'finest_limits':limits,
            'passed':all(errors[-1][key]<limit for key,limit in limits.items()) and errors[-1]['g.delta']<errors[0]['g.delta']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--check-andes',action='store_true')
    args=parser.parse_args();checks=[]
    for name in ['hold','motor_load','voltage_sag','setpoint_steps','limits_recovery','thermal_trip']:
        path=Path('cases/studies/regulated_coupled_'+name+'.json');spec=StudySpec.model_validate_json(path.read_text())
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
        method='Independent SI stator/rotor/generator circuit, scipy.signal lead-lag realizations, DOP853 anti-windup trajectories',
        field_validated=False,industrial_profile_qualified=False))
    if args.check_andes:
        native=native_controlled_limit();write_json(args.out/'native-controlled-limit.json',native)
        if not native['passed']:raise SystemExit(1)
    print([(row['case'],row['passed'],row['maximum_errors']['g.SEXS.field_pu']) for row in checks])
    if not all(row['passed'] for row in checks):raise SystemExit(1)


if __name__=='__main__':main()
