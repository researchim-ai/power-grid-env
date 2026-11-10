"""Independent closed source/RL/diode/DC/PWM/FOC/Gamma circuit trajectory.

No runtime imports. Mixed physical equations use the independent stage oracle;
controller, observer, clocks and pulse planner are separately expressed here.
Bounded single linear cage, zero Gc, fixed semiconductor resistance, no trips.
"""
import cmath
import math
import numpy as np
from benchmarks.switching_stage_reference import solve_stage


class Controller:
    def __init__(self,study):
        self.cfg=study['control'];self.motor=study['motor'];m=self.motor
        gamma=m['unsaturated_h']/(m['unsaturated_h']+m['cages'][0]['leakage_h'])
        self.L=gamma*m['unsaturated_h'];self.R=gamma*gamma*m['cages'][0]['resistance_ohm'];self.leak=gamma*m['cages'][0]['leakage_h']
        self.psi=0j;self.angle=0.;self.speed_i=0.;self.current_i=0j

    def command(self,time,current,speed,dc,dt,enabled):
        c=self.cfg;amplitude=abs(self.psi)
        if amplitude>1e-10:self.angle=cmath.phase(self.psi)
        rotate=cmath.rect(1,self.angle);measured=current/rotate
        flux_target=c['flux_reference_vs']*max(0.,min(1.,time/c['flux_ramp_s']))
        d=min(c['current_limit_peak_a'],flux_target/self.L)
        qmax=math.sqrt(max(0.,c['current_limit_peak_a']**2-d*d))
        speed_target=c['speed_reference_rad_s']*max(0.,min(1.,time/c['speed_ramp_s']))
        error=speed_target-speed;requested=c['speed_kp_nm_per_rad_s']*error+self.speed_i
        gain=1.5*self.motor['pole_pairs']*amplitude;ready=amplitude>=c['torque_enable_flux_fraction']*c['flux_reference_vs']
        q=max(-qmax,min(qmax,requested/max(gain,1e-12))) if ready and enabled else 0.
        torque=gain*q
        if enabled and ready:
            if abs(torque-requested)<=1e-10 or error*requested<0:self.speed_i+=dt*c['speed_ki_nm_per_rad']*error
        elif not enabled:self.speed_i*=math.exp(-c['antiwindup_per_s']*dt)
        target=complex(d,q) if enabled else 0j
        frequency=self.motor['pole_pairs']*speed+self.R*measured.imag/max(amplitude,.02*c['flux_reference_vs'])
        voltage=self.motor['stator_r_ohm']*target+1j*frequency*(self.leak*measured+amplitude)+c['current_kp_ohm']*(target-measured)+self.current_i
        limit=max(0.,dc)/math.sqrt(3)*c['voltage_utilization'] if enabled else 0.
        applied=voltage*min(1.,limit/abs(voltage)) if voltage else 0j
        self.current_i+=dt*(c['current_ki_ohm_per_s']*(target-measured)+c['antiwindup_per_s']*(applied-voltage))
        return applied*rotate

    def measure(self,current,speed,elapsed):
        rate=-self.R/self.L+1j*self.motor['pole_pairs']*speed
        exponential=cmath.exp(rate*elapsed)
        self.psi=exponential*self.psi+self.R*(elapsed if abs(rate*elapsed)<1e-12 else (exponential-1)/rate)*current


def currents(m,flux):
    stator=complex(*flux[:2]);rotor=complex(*flux[2:4]);ir=(rotor-stator)/m['cages'][0]['leakage_h']
    return stator/m['unsaturated_h']-ir,ir


def validate(p):
    m=p['motor'];h=p['thermal']
    if len(m['cages'])!=1 or m['saturation_beta_per_vs'] or m.get('core_loss_s',0.) or m.get('thermal') or m['locked_rotor']:raise ValueError('reference requires linear single-cage free shaft, zero Gc and no winding thermal')
    if p.get('observer_mode','nominal_single_cage')!='nominal_single_cage' or p.get('observer_motor') or p.get('motor_initial_fluxes_ab_vs'):raise ValueError('reference requires matched nominal observer and zero initial winding flux')
    if p['switching_on_j'] or p['switching_off_j'] or h['resistance_alpha_per_k']:raise ValueError('reference requires zero switching loss and constant device resistance')
    if any(t>=limit for t,limit in zip(h['initial_c'],h['trip_c'])):raise ValueError('initial trips outside reference profile')


def run(p):
    validate(p);m=p['motor'];th=p['thermal'];period=1/p['pwm_frequency_hz'];clock=5e-13
    state=dict(flux=np.zeros(4),line=np.zeros(3),voltage=np.r_[np.zeros(3),p['initial_dc_v']/2,-p['initial_dc_v']/2,np.zeros(3)],speed=m['initial_speed_rad_s'],temperature=np.array(th['initial_c'],float))
    control=Controller(p);bypassed=False;enabled=False;events=[];rows=[];axis=[];t=0.;sample=p['sample_s'];work=heat=shaft=0.;peak_residual=0.;maximum_equation=0.;steps=0
    C=np.array(th['capacity_j_per_k']);G=np.array(th['conductance_w_per_k']);ambient=th['ambient_c']
    def stored():
        f=state['flux'];v=state['voltage'];is_,ir=currents(m,f)
        return .75/m['unsaturated_h']*sum(f[:2]**2)+.75*m['cages'][0]['leakage_h']*abs(ir)**2+.5*m['inertia_kg_m2']*state['speed']**2+.5*p['source_inductance_h']*sum(state['line']**2)+.5*p['dc_capacitance_f']*(v[3]-v[4])**2+.5*p['rail_earth_capacitance_f']*sum(v[3:5]**2)+C@(state['temperature']-ambient)
    initial=stored()
    def record():
        current,_=currents(m,state['flux']);v=state['voltage']
        axis.append(float(t));rows.append([float(v[3]-v[4]),state['speed'],current.real,current.imag,*state['flux'],*state['line'],control.psi.real,control.psi.imag,*state['temperature'],float(enabled and not any(a<=t<b for a,b in p['gate_block_windows']))])
    record()
    for carrier in range(math.ceil(p['duration_s']/period)):
        start=carrier*period;end=min((carrier+1)*period,p['duration_s']);dc=state['voltage'][3]-state['voltage'][4]
        if not bypassed and start>=p['precharge_min_s'] and dc>=p['precharge_ready_v']:
            bypassed=True;events.append(dict(time_s=start,kind='precharge_bypass'))
        enabled=bool(bypassed and dc>=(p['disable_dc_v'] if enabled else p['enable_dc_v']))
        current,_=currents(m,state['flux']);u=control.command(start,current,state['speed'],max(0.,dc),period,enabled and not any(a<=start<b for a,b in p['gate_block_windows']))
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
                h=min(p['max_step_s'],boundary-t,sample-t)
                try:q=solve_stage(p,state,t,h,gates,bypassed)
                except ValueError as exc:raise ValueError(f'independent trajectory failed at t={t}, h={h}, gates={gates.tolist()}: {exc}') from exc
                v=np.array(q['voltage']);im=np.array(q['line_mid']);fm=np.array(q['flux_mid']);wm=q['speed_mid'];is_,ir=currents(m,fm)
                rc=p['mosfet_resistance_ohm'];rd=p['body_diode_resistance_ohm'];rb=p['rectifier_resistance_ohm']
                ru=np.maximum(0.,(v[:3]-v[3]-p['rectifier_drop_v'])/rb);rl=np.maximum(0.,(v[4]-v[:3]-p['rectifier_drop_v'])/rb)
                bu=np.maximum(0.,(v[5:]-v[3]-p['body_diode_drop_v'])/rd);bl=np.maximum(0.,(v[4]-v[5:]-p['body_diode_drop_v'])/rd)
                pre=0. if bypassed else p['precharge_resistance_per_phase_ohm'];brake=min(1.,max(0.,((state['voltage'][3]-state['voltage'][4])-p['brake_start_v'])/(p['brake_full_v']-p['brake_start_v'])))
                heating=np.array([ru@(v[:3]-v[3])+rl@(v[4]-v[:3])+pre*(im@im),bu@(v[5:]-v[3])+bl@(v[4]-v[5:])+sum(np.where(gates[:,0],(v[3]-v[5:])**2/rc,0.))+sum(np.where(gates[:,1],(v[5:]-v[4])**2/rc,0.)),brake*(v[3]-v[4])**2/p['brake_resistance_ohm']])
                before_temperature=state['temperature'].copy();after_temperature=((C/h-G/2)*before_temperature+heating+G*ambient)/(C/h+G/2)
                if any(after_temperature>=np.array(th['trip_c'])-1e-8):raise ValueError('thermal trip reached: unsupported reference profile')
                load=p['load_torque_nm']+m['fan_nm_per_rad_s2']*wm*abs(wm)+m['viscous_nm_per_rad_s']*wm
                scale=np.interp(t+h/2,np.array(p['grid_envelope'])[:,0],np.array(p['grid_envelope'])[:,1]);source=math.sqrt(2)*p['grid_ln_rms_v']*scale*np.cos(2*math.pi*p['grid_frequency_hz']*(t+h/2)+np.array([0.,-2*math.pi/3,2*math.pi/3]))
                work+=h*(source@im);heat+=h*(p['source_resistance_ohm']*(im@im)+(v[3]-v[4])**2/p['bleed_ohm']+1.5*(m['stator_r_ohm']*abs(is_)**2+m['cages'][0]['resistance_ohm']*abs(ir)**2))+h*sum(G*((before_temperature+after_temperature)/2-ambient));shaft+=h*load*wm
                old_rails=state['voltage'][3:5].copy();state['voltage']=v;state['voltage'][3:5]=old_rails+2*np.array(q['rail_increment_mid'])
                state['line']+=2*np.array(q['line_increment_mid']);state['flux']+=2*np.array(q['flux_increment_mid']);state['speed']+=2*q['speed_increment_mid'];state['temperature']=after_temperature
                integrated_i+=h*is_;integrated_w+=h*wm;elapsed+=h;steps+=1;t+=h
                peak_residual=max(peak_residual,abs(work-heat-shaft-(stored()-initial)));maximum_equation=max(maximum_equation,q['residual_max'])
                if abs(t-sample)<clock or t>=p['duration_s']-clock:record()
            t=boundary
            if abs(t-sample)<clock and t>axis[-1]+clock:record()
        if elapsed:control.measure(integrated_i/elapsed,integrated_w/elapsed,elapsed)
    return dict(time_s=axis,rows=rows,event_log=events,maximum_energy_residual_j=float(peak_residual),maximum_equation_residual=maximum_equation,steps=steps,scope='independent mixed equations, separately implemented nominal FOC/observer/PWM/clocks; common discrete-gradient/midpoint mathematical scheme; single linear cage, no Gc/winding thermal/switching energy/trips')
