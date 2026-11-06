"""Original motulator flux RHS + independently authored three-phase RC/MOV.

No production Gamma/current coordinates, phase stamps, event clocks, relay
implementation or engineering classifications are used to integrate this peer.
"""
import math,time
import numpy as np
from scipy.integrate import solve_ivp


def reference(study):
    from motulator.drive.model import InductionMachine,InductionMachinePars,MechanicalSystem
    case=study.case;p=case.motors[0].passport
    if len(p.cages)!=1 or p.core_loss_s or case.inductors or case.injections or case.converters or case.drives:raise ValueError('registered one-cage source/R/contact/C reference required')
    phase=('a','b','c');r={v.id:v for v in case.resistors};caps={v.id:v for v in case.capacitors}
    if {v.id for v in case.sources}!={'source_'+v for v in phase} or set(r)!={'grid_'+v for v in phase}|{'contact_'+v for v in phase}:raise ValueError('native reference geometry differs')
    grid=np.array([r['grid_'+v].resistance_ohm for v in phase]);contacts=np.array([r['contact_'+v].resistance_ohm for v in phase]);cap=np.array([caps['snubber_'+v].capacitance_f for v in phase])
    for v in phase:
        if (r['grid_'+v].frm,r['grid_'+v].to)!=('s'+v,'f'+v) or (r['contact_'+v].frm,r['contact_'+v].to)!=('f'+v,'m'+v):raise ValueError('native contact endpoints differ')
        if {caps['snubber_'+v].frm,caps['snubber_'+v].to}!={'f'+v,'m'+v}:raise ValueError('native snubber endpoints differ')
    if len(case.capacitors)!=3:raise ValueError('native snubber inventory differs')
    for source in case.sources:
        if source.node!='s'+source.id[-1] or len(source.tones)!=1 or source.tones[0].order!=1:raise ValueError('native fundamental source profile required')
    def source(t):
        result=[]
        for label in phase:
            s=next(s for s in case.sources if s.id=='source_'+label);scale=s.envelope[-1][1]
            for a,b in zip(s.envelope,s.envelope[1:]):
                if a[0]<=t<b[0]:scale=a[1]+(b[1]-a[1])*(t-a[0])/(b[0]-a[0]);break
            tone=s.tones[0];result.append(math.sqrt(2)*tone.rms*scale*math.cos(2*math.pi*case.frequency_hz*t+tone.phase_rad))
        return np.array(result)
    def abc(z):return np.array([z.real,-z.real/2+math.sqrt(3)*z.imag/2,-z.real/2-math.sqrt(3)*z.imag/2])
    def space(v):return 2/3*(v[0]+v[1]*np.exp(2j*math.pi/3)+v[2]*np.exp(4j*math.pi/3))
    winding=p.cages[0];machine=InductionMachine(InductionMachinePars(n_p=p.pole_pairs,R_s=p.stator_r_ohm,R_r=winding.resistance_ohm,L_ell=winding.leakage_h,
        L_s=lambda rho:p.unsaturated_h/(1+(p.saturation_beta_per_vs*rho)**p.saturation_exponent)))
    mechanics=MechanicalSystem(p.inertia_kg_m2,B_L=lambda w:p.fan_nm_per_rad_s2*abs(w)+p.viscous_nm_per_rad_s)
    thermal=p.thermal;nt=2 if thermal else 0;u0=5+nt;theta=u0+3;entered=theta+1;loss=theta+2;arc=theta+3
    x=np.r_[np.zeros(4),p.initial_speed_rad_s,*([thermal.ambient_c]*2 if thermal else []),np.zeros(3),study.relay.initial_level,0.,0.,0.]
    latched=False;opened=False;pending=None;trip=None;acceleration=[];log=[];rows=[];times=[];energy=[]
    def quantities(t,state):
        machine.state.psi_s_ab=complex(*state[:2]);machine.state.psi_r_ab=complex(*state[2:4]);machine.inp.w_M=state[4]
        if thermal:
            machine.par.R_s=p.stator_r_ohm*(1+thermal.alpha_per_c*(state[5]-thermal.reference_c));machine.par.R_r=winding.resistance_ohm*(1+thermal.alpha_per_c*(state[6]-thermal.reference_c))
        current,rotor,torque=machine.compute_outputs(machine.state,machine.inp);i=abc(current);e=source(t);f=e-grid*i;u=state[u0:u0+3];v=f-u
        ic=np.sign(u)*np.maximum(abs(u)-study.clamp_knee_v,0.)/study.clamp_resistance_ohm
        machine.inp.u_s_ab=space(v);machine.set_outputs(t);mechanics.state.w_M=complex(state[4]);mechanics.inp.tau_M=machine.out.tau_M
        ds,dr=machine.rhs(t);_,dw=mechanics.rhs(t);rate=[ds.real,ds.imag,dr.real,dr.imag,0. if p.locked_rotor else float(np.real(dw))]
        stator_loss=1.5*machine.par.R_s*abs(current)**2;rotor_loss=1.5*machine.par.R_r*abs(rotor)**2;external=stator_loss+rotor_loss
        if thermal:
            ts,tr=state[5:7];cross=thermal.cross_w_per_k*(ts-tr);hs=thermal.stator_ambient_w_per_k*(ts-thermal.ambient_c);hr=thermal.rotor_ambient_w_per_k*(tr-thermal.ambient_c)
            rate.extend([(stator_loss-hs-cross)/thermal.stator_capacity_j_per_k,(rotor_loss-hr+cross)/thermal.rotor_capacity_j_per_k]);external=hs+hr
        du=(i-u/contacts-ic)/cap;heat=(abs(current)/math.sqrt(2)/study.relay.rated_current_rms_a)**2
        clamp=float(u@ic);mechanical=p.fan_nm_per_rad_s2*abs(state[4])**3+p.viscous_nm_per_rad_s*state[4]**2
        rate=np.r_[rate,du,(heat-state[theta])/study.relay.time_constant_s,float(e@i),float(np.sum(grid*i*i+u*u/contacts)+clamp+external+mechanical),clamp]
        psi=machine.state.psi_s_ab;saturation=(p.saturation_beta_per_vs*abs(psi))**p.saturation_exponent
        stored=1.5/p.unsaturated_h*(abs(psi)**2/2+saturation*abs(psi)**2/(p.saturation_exponent+2))+.75*winding.leakage_h*abs(rotor)**2+.5*p.inertia_kg_m2*state[4]**2+.5*float(np.sum(cap*u*u))
        if thermal:stored+=thermal.stator_capacity_j_per_k*(state[5]-thermal.ambient_c)+thermal.rotor_capacity_j_per_k*(state[6]-thermal.ambient_c)
        row={'motor.current_alpha_a':current.real,'motor.current_beta_a':current.imag,'motor.motor_speed_rad_s':state[4],'motor.torque_nm':float(machine.out.tau_M),
             'motor.stator_flux_alpha_vs':state[0],'motor.stator_flux_beta_vs':state[1],'motor.cage1_flux_alpha_vs':state[2],'motor.cage1_flux_beta_vs':state[3],
             'motor.stator_copper_w':stator_loss,'motor.rotor_copper_w':rotor_loss,'relay.level':state[theta],'relay.latched':float(latched),'breaker.opened':float(opened),'breaker.clamp_energy_j':state[arc]}
        for k,label in enumerate(phase):
            row['s'+label+'.voltage_v']=e[k];row['f'+label+'.voltage_v']=f[k];row['m'+label+'.voltage_v']=v[k]
            row['contact_'+label+'.ohmic_current_a']=u[k]/contacts[k];row['contact_'+label+'.capacitor_current_a']=i[k]-u[k]/contacts[k]-ic[k]
            row['contact_'+label+'.voltage_v']=u[k];row['contact_'+label+'.clamp_current_a']=ic[k]
        if thermal:row.update({'motor.stator_temperature_c':state[5],'motor.rotor_temperature_c':state[6]})
        return rate,row,stored
    initial=quantities(0.,x)[2];started=time.monotonic();calls=0
    def rhs(t,state):
        nonlocal calls
        calls+=1
        if calls>1000000 or time.monotonic()-started>600.:raise ValueError('native protected-motor work budget exceeded')
        return quantities(t,state)[0]
    def jac(t,state):
        matrix=np.zeros((len(state),len(state)));base=rhs(t,state)
        for k in range(theta):
            h=1e-7*max(1.,abs(state[k]));a=state.copy();a[k]+=h;matrix[:,k]=(rhs(t,a)-base)/h
        matrix[theta,theta]=-1/study.relay.time_constant_s
        return matrix
    def record(t,state):
        _,row,stored=quantities(t,state);rows.append(row);times.append(float(t));energy.append(float(state[entered]-state[loss]-(stored-initial)))
    record(0.,x);sample=np.arange(math.ceil(study.duration_s/study.sample_s))*study.sample_s
    commands={}
    for e in case.events:commands.setdefault(e.time_s,[]).append(e)
    resets={r.time_s for r in study.reset_commands};boundaries=sorted({study.duration_s,*commands,*resets,*[t for s in case.sources for t,_ in s.envelope if t>0]});cursor=0.
    for boundary in boundaries:
        while cursor<boundary:
            target=min(boundary,pending) if pending is not None else boundary;guards=[];names=[]
            if not latched:
                def replica(t,state):return state[theta]-study.relay.trip_level
                replica.direction=1;replica.terminal=True;guards.append(replica);names.append('thermal_i2t')
                if study.relay.stator_trip_c is not None:
                    def stator(t,state):return state[5]-study.relay.stator_trip_c
                    stator.direction=1;stator.terminal=True;guards.append(stator);names.append('stator_temperature')
            def speed(t,state):return state[4]-study.required_speed_pu*2*math.pi*case.frequency_hz/p.pole_pairs
            speed.direction=1;speed.terminal=False;guards.append(speed)
            sol=solve_ivp(rhs,(cursor,target),x,method='Radau',rtol=2e-10,atol=1e-12,max_step=.0005,dense_output=True,events=guards,jac=jac)
            if not sol.success:raise ValueError('native protected-motor integration failed: '+sol.message)
            stop=float(sol.t[-1]);x=sol.y[:,-1].copy();epsilon=64*np.finfo(float).eps*max(1.,abs(stop))
            if abs(stop-target)<=epsilon:stop=target
            for at in sol.t_events[-1]:
                if not acceleration or at-acceleration[-1]>epsilon:acceleration.append(float(at))
            for t in sample[(sample>cursor+epsilon)&(sample<stop-epsilon)]:record(float(t),sol.sol(t))
            hits=[name for name,found in zip(names,sol.t_events[:-1]) if len(found)]
            due=pending is not None and abs(stop-pending)<=epsilon
            if hits:
                latched=True
                if trip is None:trip=stop
                pending=stop+study.relay.opening_delay_s;due=study.relay.opening_delay_s==0
                log.append(dict(time_s=stop,kind='motor_relay_trip',equipment_id=study.relay.id,causes=sorted(hits),outcome='latched'))
            if due:
                contacts[:]=study.off_resistance_ohm;opened=True;pending=None;log.append(dict(time_s=stop,kind='breaker_open',equipment_id=study.relay.id,outcome='applied'))
            if stop==boundary:
                if boundary in resets:
                    permitted=pending is None and x[theta]<=study.relay.reset_level and (not thermal or study.relay.stator_trip_c is None or x[5]<study.relay.stator_trip_c)
                    if permitted:latched=False
                    log.append(dict(time_s=stop,kind='relay_reset',equipment_id=study.relay.id,outcome='applied' if permitted else 'blocked_trip_pending' if pending is not None else 'blocked_hot'))
                for e in sorted(commands.get(boundary,[]),key=lambda e:e.equipment_id):
                    closing=e.equipment_id in study.contact_ids and e.value<study.off_resistance_ohm;blocked=closing and (latched or pending is not None)
                    if not blocked:
                        contacts[study.contact_ids.index(e.equipment_id)]=e.value
                        if closing:opened=False
                    log.append(dict(time_s=stop,kind=e.kind,equipment_id=e.equipment_id,outcome='blocked_thermal_latch' if blocked else 'applied'))
            opened=bool(np.all(contacts>=study.off_resistance_ohm))
            if any(abs(sample-stop)<=epsilon) or stop==study.duration_s:record(stop,x)
            cursor=stop
    channels={k:[float(r[k]) for r in rows] for k in rows[0]}
    return dict(completed=True,time_s=times,channels=channels,event_log=log,acceleration_times_s=acceleration,
                maximum_energy_residual_j=max(abs(e) for e in energy),first_required_speed_time_s=acceleration[0] if acceleration else None,
                thermal_trip_time_s=trip,rhs_evaluations=calls,elapsed_s=time.monotonic()-started,channel_inventory=sorted(channels),original_motulator_flux_rhs=True,independent_three_phase_snubber_and_relay=True,production_event_clocks_used=False)


def compare(primary,native):
    if not primary.get('completed') or not native.get('completed'):raise ValueError('complete protected-motor studies required')
    required={'motor.'+k for k in ['current_alpha_a','current_beta_a','motor_speed_rad_s','torque_nm','stator_flux_alpha_vs','stator_flux_beta_vs','cage1_flux_alpha_vs','cage1_flux_beta_vs','stator_copper_w','rotor_copper_w']}|{'relay.level','relay.latched','breaker.opened','breaker.clamp_energy_j'}
    required|={prefix+phase+'.voltage_v' for prefix in ['s','f','m'] for phase in 'abc'}|{'contact_'+phase+'.'+key for phase in 'abc' for key in ['voltage_v','clamp_current_a','ohmic_current_a','capacitor_current_a']}
    if primary['settings']['case']['motors'][0]['passport']['thermal'] is not None:required|={'motor.stator_temperature_c','motor.rotor_temperature_c'}
    if not required<=native.get('channels',{}).keys() or native.get('channel_inventory')!=sorted(native['channels']):raise ValueError('incomplete protected-motor native channel inventory')
    if not np.isfinite(native['time_s']).all() or np.any(np.diff(native['time_s'])<=0):raise ValueError('invalid protected-motor reference axis')
    sig=lambda e:(e['kind'],e['equipment_id'],e.get('outcome'),tuple(e.get('causes',[])))
    if [sig(e) for e in primary['event_log']]!=[sig(e) for e in native['event_log']]:raise ValueError('independent protected-motor event decisions/order differ')
    event_error=max([0.,*[abs(a['time_s']-b['time_s']) for a,b in zip(primary['event_log'],native['event_log'])]])
    axis=np.array(primary['time_s']);indices=[]
    for t in native['time_s']:
        found=np.flatnonzero(abs(axis-t)<=1e-12)
        if not len(found):raise ValueError('missing protected-motor reference grid sample')
        indices.append(int(found[-1]))
    errors={};limits={}
    for key,wanted in native['channels'].items():
        if not np.isfinite(wanted).all() or len(wanted)!=len(native['time_s']):raise ValueError('invalid native motor channel')
        actual=np.array(primary['channels'][key])[indices]
        if not np.isfinite(actual).all():raise ValueError('invalid primary motor channel')
        error=float(max(abs(actual-np.array(wanted))))
        limit=0. if key in {'relay.latched','breaker.opened'} else .05 if key.endswith('_w') else .002 if 'voltage_v' in key else 1e-5 if 'flux_' in key else .001+2e-7*max(abs(np.array(wanted))) if 'current_' in key or 'speed_' in key else 3e-4
        errors[key]=error;limits[key]=limit
    first=primary['engineering']['first_required_speed_time_s'];peer=native['first_required_speed_time_s']
    runup_error=0. if first is None and peer is None else None if first is None or peer is None else abs(first-peer)
    return dict(passed=bool(event_error<=2e-6 and runup_error is not None and runup_error<=2e-6 and all(e<=limits[k] for k,e in errors.items()) and native['maximum_energy_residual_j']<=.02 and primary['maximum_energy_relative_residual']<=2e-5),
        errors=errors,limits=limits,event_time_error_s=event_error,runup_time_error_s=runup_error,
        native_energy_residual_j=native['maximum_energy_residual_j'],primary_energy_relative_residual=primary['maximum_energy_relative_residual'])
