"""Independent extended full drive reference. No production imports.

The stage oracle solves network, all fluxes, shaft and winding temperatures
simultaneously. Endpoint Gc current is solved as a separate three-node passive
network. Controller/observer/PWM are the independently expressed nominal model,
with its explicit observer passport (never hidden plant flux or temperature).
"""
import math
import numpy as np
from scipy.optimize import root
from benchmarks.closed_loop_reference import Controller
from benchmarks.switching_stage_reference import solve_stage


def channels(p):
    result=['dc_voltage_v','speed_rad_s','motor_current_alpha_a','motor_current_beta_a','motor_stator_flux_alpha_vs','motor_stator_flux_beta_vs','rotor_flux_alpha_vs','rotor_flux_beta_vs','source_current_a','source_current_b','source_current_c','flux_estimate_alpha_vs','flux_estimate_beta_vs','rectifier_temperature_c','inverter_temperature_c','brake_temperature_c','gates_enabled']
    for k in range(2,len(p['motor']['cages'])+1):result.extend([f'rotor_cage_{k}_flux_alpha_vs',f'rotor_cage_{k}_flux_beta_vs'])
    if p['motor'].get('thermal'):result.extend(['motor_stator_temperature_c','motor_rotor_temperature_c'])
    return result


def resistances(m,temperature):
    rs=m['stator_r_ohm'];rr=np.array([c['resistance_ohm'] for c in m['cages']])
    if m.get('thermal'):
        w=m['thermal'];factor=1+w['alpha_per_c']*(np.asarray(temperature)-w['reference_c'])
        if np.any(factor<=0) or not np.all(np.isfinite(factor)):raise ValueError('invalid winding resistance')
        rs*=factor[0];rr=rr*factor[1]
    return rs,rr


def magnetic(m,flux):
    z=np.asarray(flux).reshape(-1,2);ps=complex(*z[0]);pr=z[1:,0]+1j*z[1:,1];ir=(pr-ps)/np.array([c['leakage_h'] for c in m['cages']])
    return ps*(1+(m['saturation_beta_per_vs']*abs(ps))**m['saturation_exponent'])/m['unsaturated_h']-sum(ir),ir


def magnetic_energy(m,flux):
    z=np.asarray(flux).reshape(-1,2);radius=np.linalg.norm(z[0]);ll=np.array([c['leakage_h'] for c in m['cages']]);ir=(z[1:]-z[0])/ll[:,None];e=m['saturation_exponent']
    return 1.5/m['unsaturated_h']*(radius**2/2+m['saturation_beta_per_vs']**e*radius**(e+2)/(e+2))+.75*sum(ll*np.sum(ir*ir,axis=1))


def winding_energy(m,temperature):
    if not m.get('thermal'):return 0.
    w=m['thermal'];return np.array([w['stator_capacity_j_per_k'],w['rotor_capacity_j_per_k']])@(temperature-w['ambient_c'])


def stage_currents(m,old,mid,increment,h):
    z=np.asarray(mid).reshape(-1,2);ps=complex(*z[0]);pr=z[1:,0]+1j*z[1:,1];ll=np.array([c['leakage_h'] for c in m['cages']]);ir=(pr-ps)/ll
    a=np.linalg.norm(np.asarray(old[:2],np.longdouble));b=np.linalg.norm(np.asarray(old[:2],np.longdouble)+2*np.asarray(increment[:2],np.longdouble));delta=b*b-a*a;e=m['saturation_exponent'];beta=m['saturation_beta_per_vs']
    coefficient=(1+(beta*((a+b)/2))**e)/(2*m['unsaturated_h']) if abs(delta)<1e-13*max(1.,a*a,b*b) else (.5+beta**e*(b**(e+2)-a**(e+2))/((e+2)*delta))/m['unsaturated_h']
    return 2*ps*float(coefficient)-sum(ir)+m.get('core_loss_s',0.)*2/h*complex(*increment[:2]),ir


def terminal_current(p,state,gates):
    m=p['motor'];force,_=magnetic(m,state['flux']);gc=m.get('core_loss_s',0.)
    if not gc:return force
    rs,_=resistances(m,state['winding_temperature']);g=gc/(1+rs*gc);offset=force/(1+rs*gc);rails=state['voltage'][3:5]
    rc=p['mosfet_resistance_ohm'];rd=p['body_diode_resistance_ohm'];drop=p['body_diode_drop_v']
    transform=np.array([[1.,0.],[-.5,math.sqrt(3)/2],[-.5,-math.sqrt(3)/2]])
    def residual(v):
        u=2/3*complex(v[0]-.5*v[1]-.5*v[2],math.sqrt(3)/2*(v[1]-v[2]));current=offset+g*u
        phase=transform@np.array([current.real,current.imag])
        return phase+np.where(gates[:,0],(v-rails[0])/rc,0.)+np.where(gates[:,1],(v-rails[1])/rc,0.)+np.maximum(v-rails[0]-drop,0.)/rd-np.maximum(rails[1]-v-drop,0.)/rd
    initial=np.full(3,np.mean(rails));solution=root(residual,initial,method='lm',options={'ftol':1e-13,'xtol':1e-13,'gtol':1e-13,'maxiter':2000})
    if not np.all(np.isfinite(solution.x)) or max(abs(residual(solution.x)))>1e-7:raise ValueError('independent endpoint core-current residual exceeds bound')
    v=solution.x;return offset+g*2/3*complex(v[0]-.5*v[1]-.5*v[2],math.sqrt(3)/2*(v[1]-v[2]))


def validate(p):
    m=p['motor'];h=p['thermal'];observer=p.get('observer_motor') or m
    if not 1<=len(m['cages'])<=4 or m['locked_rotor']:raise ValueError('reference requires 1-4 cage free shaft')
    if p.get('observer_mode','nominal_single_cage')!='nominal_single_cage' or len(observer['cages'])!=1 or observer['saturation_beta_per_vs'] or observer.get('core_loss_s',0.) or observer.get('thermal') or observer['pole_pairs']!=m['pole_pairs']:raise ValueError('reference requires explicit linear nominal observer for extended plants')
    if p['switching_on_j'] or p['switching_off_j'] or h['resistance_alpha_per_k']:raise ValueError('reference requires zero switching loss and constant device resistance')
    if any(t>=limit for t,limit in zip(h['initial_c'],h['trip_c'])):raise ValueError('initial electronics trips outside reference profile')
    if m.get('thermal'):
        temp=p.get('motor_initial_temperature_c') or [m['thermal']['ambient_c']]*2
        if any(t>=limit for t,limit in zip(temp,p.get('motor_temperature_trip_c') or [1e99]*2)):raise ValueError('initial winding trips outside reference profile')


def run(p):
    validate(p);m=p['motor'];th=p['thermal'];period=1/p['pwm_frequency_hz'];clock=5e-13
    state=dict(flux=np.asarray(p.get('motor_initial_fluxes_ab_vs') or np.zeros((len(m['cages'])+1,2)),float).ravel(),line=np.zeros(3),voltage=np.r_[np.zeros(3),p['initial_dc_v']/2,-p['initial_dc_v']/2,np.zeros(3)],speed=m['initial_speed_rad_s'],temperature=np.array(th['initial_c'],float))
    winding=m.get('thermal');state['winding_temperature']=np.array(p.get('motor_initial_temperature_c') or ([winding['ambient_c']]*2 if winding else []),float)
    previous_gates=np.zeros((3,2),bool)
    control=Controller(dict(p,motor=p.get('observer_motor') or m));bypassed=False;enabled=False;events=[];rows=[];axis=[];t=0.;sample=p['sample_s'];work=heat=shaft=0.;peak_residual=0.;maximum_equation=0.;steps=0;stator_total=rotor_total=core_total=0.
    C=np.array(th['capacity_j_per_k']);G=np.array(th['conductance_w_per_k']);ambient=th['ambient_c']
    def stored():
        f=state['flux'];v=state['voltage'];is_,ir=magnetic(m,f)
        return magnetic_energy(m,f)+winding_energy(m,state['winding_temperature'])+.5*m['inertia_kg_m2']*state['speed']**2+.5*p['source_inductance_h']*sum(state['line']**2)+.5*p['dc_capacitance_f']*(v[3]-v[4])**2+.5*p['rail_earth_capacitance_f']*sum(v[3:5]**2)+C@(state['temperature']-ambient)
    initial=stored()
    def record():
        current=terminal_current(p,state,previous_gates);v=state['voltage']
        axis.append(float(t));rows.append([float(v[3]-v[4]),state['speed'],current.real,current.imag,*state['flux'][:4],*state['line'],control.psi.real,control.psi.imag,*state['temperature'],float(enabled and not any(a<=t<b for a,b in p['gate_block_windows'])),*state['flux'][4:],*state['winding_temperature']])
    record()
    for carrier in range(math.ceil(p['duration_s']/period)):
        start=carrier*period;end=min((carrier+1)*period,p['duration_s']);dc=state['voltage'][3]-state['voltage'][4]
        if not bypassed and start>=p['precharge_min_s'] and dc>=p['precharge_ready_v']:
            bypassed=True;events.append(dict(time_s=start,kind='precharge_bypass'))
        enabled=bool(bypassed and dc>=(p['disable_dc_v'] if enabled else p['enable_dc_v']))
        current=terminal_current(p,state,previous_gates);u=control.command(start,current,state['speed'],max(0.,dc),period,enabled and not any(a<=start<b for a,b in p['gate_block_windows']))
        phase=np.array([u.real,-u.real/2+math.sqrt(3)/2*u.imag,-u.real/2-math.sqrt(3)/2*u.imag]);phase-=(max(phase)+min(phase))/2
        margin=2*p['dead_time_s']/period;duty=np.clip(.5+phase/max(dc,1e-300),margin,1-margin) if dc>0 else np.full(3,.5)
        on=(1-duty)*period/2;off=(1+duty)*period/2
        boundaries={end,*[start+float(x) for x in np.r_[on,on+p['dead_time_s'],off,off+p['dead_time_s']] if start+float(x)<end-1e-14]}
        boundaries.update(x for pair in p['gate_block_windows'] for x in pair if start<x<end)
        boundaries.update(x for x,_ in p['grid_envelope'] if start<x<end)
        integrated_i=0j;integrated_w=elapsed=0.
        for boundary in sorted(boundaries):
            while t<boundary-clock:
                local=t-start+1e-13;gates=np.column_stack([(local>=on+p['dead_time_s'])&(local<off),(local<on)|(local>=off+p['dead_time_s'])])
                if not enabled or any(a<=t+1e-13<b for a,b in p['gate_block_windows']):gates[:]=False
                if sample<=t+clock:sample+=p['sample_s']
                previous_gates=gates.copy()
                h=min(p['max_step_s'],boundary-t,sample-t)
                try:q=solve_stage(p,state,t,h,gates,bypassed)
                except ValueError as exc:raise ValueError(f'independent trajectory failed at t={t}, h={h}, gates={gates.tolist()}: {exc}') from exc
                v=np.array(q['voltage']);im=np.array(q['line_mid']);fm=np.array(q['flux_mid']);wm=q['speed_mid'];is_,ir=stage_currents(m,state['flux'],fm,np.array(q['flux_increment_mid']),h)
                winding_mid=np.array(q['winding_temperature_mid_c']);rs,rr=resistances(m,winding_mid)
                stator_heat=1.5*(rs*abs(is_)**2+m.get('core_loss_s',0.)*abs(2/h*complex(*q['flux_increment_mid'][:2]))**2);rotor_heat=1.5*sum(rr*abs(ir)**2)
                if winding:
                    if any(2*winding_mid-state['winding_temperature']>=np.array(p.get('motor_temperature_trip_c') or [1e99]*2)-1e-8):raise ValueError('winding trip reached: unsupported reference profile')
                    if max(2*winding_mid-state['winding_temperature'])>=winding['maximum_c']:raise ValueError('winding temperature exceeded model range')
                    winding_out=winding['stator_ambient_w_per_k']*(winding_mid[0]-winding['ambient_c'])+winding['rotor_ambient_w_per_k']*(winding_mid[1]-winding['ambient_c'])
                else:winding_out=stator_heat+rotor_heat
                rc=p['mosfet_resistance_ohm'];rd=p['body_diode_resistance_ohm'];rb=p['rectifier_resistance_ohm']
                ru=np.maximum(0.,(v[:3]-v[3]-p['rectifier_drop_v'])/rb);rl=np.maximum(0.,(v[4]-v[:3]-p['rectifier_drop_v'])/rb)
                bu=np.maximum(0.,(v[5:]-v[3]-p['body_diode_drop_v'])/rd);bl=np.maximum(0.,(v[4]-v[5:]-p['body_diode_drop_v'])/rd)
                pre=0. if bypassed else p['precharge_resistance_per_phase_ohm'];brake=min(1.,max(0.,((state['voltage'][3]-state['voltage'][4])-p['brake_start_v'])/(p['brake_full_v']-p['brake_start_v'])))
                heating=np.array([ru@(v[:3]-v[3])+rl@(v[4]-v[:3])+pre*(im@im),bu@(v[5:]-v[3])+bl@(v[4]-v[5:])+sum(np.where(gates[:,0],(v[3]-v[5:])**2/rc,0.))+sum(np.where(gates[:,1],(v[5:]-v[4])**2/rc,0.)),brake*(v[3]-v[4])**2/p['brake_resistance_ohm']])
                before_temperature=state['temperature'].copy();after_temperature=((C/h-G/2)*before_temperature+heating+G*ambient)/(C/h+G/2)
                if any(after_temperature>=np.array(th['trip_c'])-1e-8):raise ValueError('thermal trip reached: unsupported reference profile')
                load=p['load_torque_nm']+m['fan_nm_per_rad_s2']*wm*abs(wm)+m['viscous_nm_per_rad_s']*wm
                scale=np.interp(t+h/2,np.array(p['grid_envelope'])[:,0],np.array(p['grid_envelope'])[:,1]);source=math.sqrt(2)*p['grid_ln_rms_v']*scale*np.cos(2*math.pi*p['grid_frequency_hz']*(t+h/2)+np.array([0.,-2*math.pi/3,2*math.pi/3]))
                work+=h*(source@im);heat+=h*(p['source_resistance_ohm']*(im@im)+(v[3]-v[4])**2/p['bleed_ohm']+winding_out)+h*sum(G*((before_temperature+after_temperature)/2-ambient));shaft+=h*load*wm
                stator_total+=h*stator_heat;rotor_total+=h*rotor_heat;core_total+=h*1.5*m.get('core_loss_s',0.)*abs(2/h*complex(*q['flux_increment_mid'][:2]))**2
                old_rails=state['voltage'][3:5].copy();state['voltage']=v;state['voltage'][3:5]=old_rails+2*np.array(q['rail_increment_mid'])
                state['line']+=2*np.array(q['line_increment_mid']);state['flux']+=2*np.array(q['flux_increment_mid']);state['speed']+=2*q['speed_increment_mid'];state['temperature']=after_temperature
                if winding:state['winding_temperature']=2*winding_mid-state['winding_temperature']
                integrated_i+=h*is_;integrated_w+=h*wm;elapsed+=h;steps+=1;t+=h
                peak_residual=max(peak_residual,abs(work-heat-shaft-(stored()-initial)));maximum_equation=max(maximum_equation,q['residual_max'])
                if abs(t-sample)<clock or t>=p['duration_s']-clock:record()
            t=boundary
            if abs(t-sample)<clock and t>axis[-1]+clock:record()
        if elapsed:control.measure(integrated_i/elapsed,integrated_w/elapsed,elapsed)
    return dict(time_s=axis,rows=rows,motor_stator_heat_j=float(stator_total),motor_rotor_heat_j=float(rotor_total),motor_core_heat_j=float(core_total),event_log=events,maximum_energy_residual_j=float(peak_residual),maximum_equation_residual=maximum_equation,steps=steps,row_channels=channels(p),scope='independent mixed equations and nominal FOC/PWM; 1-4 saturated cages, Gc, two winding RC/R(T); common midpoint/discrete-gradient scheme; fixed device resistance, no switching energy or trips')
