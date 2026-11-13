"""PHY-20 independent continuous all-flux machine and full hybrid drive.

The machine is separately expressed, not a patched motulator machine. Single-
case reduction is tested against original motulator; no external multicage RHS
or production solver/stamps are imported. Circuit/control infrastructure is
shared with the independently assembled PHY-19 reference, not with runtime.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import sys
import numpy as np
from scipy.integrate import solve_ivp
from benchmarks.continuous_drive_reference import Circuit,Controller,PHASE,BlockProduction,read_document


class AllFluxMachine:
    """Passive saturated Gamma branches in stationary complex SI coordinates."""
    def __init__(self,m):
        self.m=copy.deepcopy(m);self.count=len(m['cages'])
        self.state=SimpleNamespace(psi_s_ab=0j,psi_r_ab=np.zeros(self.count,complex))
        self.inp=SimpleNamespace(u_s_ab=0j,w_M=0.)
        self.par=SimpleNamespace(R_s=m['stator_r_ohm'],R_r=np.array([c['resistance_ohm'] for c in m['cages']]))
        self.out=SimpleNamespace()
        self.leakage=np.array([c['leakage_h'] for c in m['cages']])

    def _compute_outputs(self,state,inp):
        m=self.m;s=state.psi_s_ab;r=state.psi_r_ab
        rotor=(r-s)/self.leakage
        magnetic=s*(1+(m['saturation_beta_per_vs']*abs(s))**m['saturation_exponent'])/m['unsaturated_h']-sum(rotor)
        stator=(magnetic+m['core_loss_s']*inp.u_s_ab)/(1+self.par.R_s*m['core_loss_s'])
        torque=float(1.5*m['pole_pairs']*(magnetic*s.conjugate()).imag)
        return stator,rotor,torque

    def set_outputs(self,t):
        stator,rotor,torque=self._compute_outputs(self.state,self.inp)
        self.out=SimpleNamespace(i_s_ab=stator,i_r_ab=rotor,tau_M=torque)

    def rhs(self,t):
        self.set_outputs(t)
        return self.inp.u_s_ab-self.par.R_s*self.out.i_s_ab, -self.par.R_r*self.out.i_r_ab+1j*self.m['pole_pairs']*self.inp.w_M*self.state.psi_r_ab


class MulticageCircuit(Circuit):
    def __init__(self,p):
        self.p=copy.deepcopy(p);self.m=self.p['motor'];m=self.m;count=len(m['cages'])
        if not 1<=count<=4 or m['locked_rotor'] or not math.isfinite(m.get('core_loss_s',0.)) or m['core_loss_s']<=0:
            raise ValueError('independent full circuit requires 1-4 cages, positive Gc and a free shaft')
        values=[m['stator_r_ohm'],m['unsaturated_h'],m['inertia_kg_m2']]+[c[key] for c in m['cages'] for key in ['resistance_ohm','leakage_h']]
        if not all(math.isfinite(v) and v>0 for v in values):raise ValueError('positive finite machine R/L/J required')
        observer=p.get('observer_motor')
        if p.get('observer_mode','nominal_single_cage')!='nominal_single_cage' or observer is None or len(observer['cages'])!=1 or observer['saturation_beta_per_vs'] or observer.get('core_loss_s',0.) or observer.get('thermal') or observer['pole_pairs']!=m['pole_pairs']:
            raise ValueError('explicit linear nominal single-cage observer required')
        self.flux_states=slice(5,7+2*count);self.speed_index=self.flux_states.stop
        self.electronics=slice(self.speed_index+1,self.speed_index+4)
        self.winding_states=slice(self.speed_index+4,self.speed_index+6)
        self.winding=m.get('thermal');self.base=self.speed_index+4+(2 if self.winding else 0)
        self.size=self.base+10+count;self.machine=AllFluxMachine(m)
        self.active=(0,0,0);self.mode=np.zeros(3,dtype=int);self.bypassed=False;self.gates=np.zeros((3,2),bool)
        self.kcl=0.;self.constitutive=0.;self.evaluations=0;self.reset_energy=0.;self.source_events=[]
        self.latched=np.array(p['thermal']['initial_c'])>=np.array(p['thermal']['trip_c']);self.winding_latched=np.zeros(2,bool)
        if self.winding and p.get('motor_temperature_trip_c'):
            self.winding_latched=np.array(p.get('motor_initial_temperature_c') or [self.winding['ambient_c']]*2)>=np.array(p['motor_temperature_trip_c'])
        self.thermal_events=[];self.switching_heat=0.;self.jump_residual=0.;self.gate_edges=0
        for k in np.flatnonzero(self.winding_latched):
            self.thermal_events.append(dict(time_s=0.,kind='thermal_trip',component=['motor_stator','motor_rotor'][k],temperature_c=float((p.get('motor_initial_temperature_c') or [self.winding['ambient_c']]*2)[k]),cause='initial_temperature'))

    def initial(self):
        x=np.zeros(self.size);x[3:5]=[self.p['initial_dc_v']/2,-self.p['initial_dc_v']/2]
        flux=self.p.get('motor_initial_fluxes_ab_vs') or [[0.,0.]]*(1+len(self.m['cages']))
        if np.asarray(flux).shape!=(1+len(self.m['cages']),2):raise ValueError('initial all-flux inventory differs')
        x[self.flux_states]=np.asarray(flux).ravel();x[self.speed_index]=self.m['initial_speed_rad_s']
        x[self.electronics]=self.p['thermal']['initial_c']
        if self.winding:x[self.winding_states]=self.p.get('motor_initial_temperature_c') or [self.winding['ambient_c']]*2
        return x

    def prepare(self,x):
        states=x[self.flux_states].reshape(-1,2)
        self.machine.state.psi_s_ab=complex(*states[0]);self.machine.state.psi_r_ab=states[1:,0]+1j*states[1:,1]
        self.machine.inp.w_M=x[self.speed_index]
        rs=self.m['stator_r_ohm'];rr=np.array([c['resistance_ohm'] for c in self.m['cages']])
        if self.winding:
            factors=1+self.winding['alpha_per_c']*(x[self.winding_states]-self.winding['reference_c'])
            if min(factors)<=0:raise ValueError('nonpositive winding resistance')
            rs*=factors[0];rr*=factors[1]
        self.machine.par.R_s=rs;self.machine.par.R_r=rr
        return rs,rr

    def stored(self,x):
        m=self.m;phi=x[self.flux_states].reshape(-1,2);s=complex(*phi[0]);rotor=phi[1:,0]+1j*phi[1:,1];radius=abs(s);power=m['saturation_exponent']+2
        magnetic=1.5/m['unsaturated_h']*(radius**2/2+m['saturation_beta_per_vs']**m['saturation_exponent']*radius**power/power)+.75*sum(abs(rotor-s)**2/self.machine.leakage)
        energy=magnetic+.5*m['inertia_kg_m2']*x[self.speed_index]**2+.5*self.p['source_inductance_h']*(x[:3]@x[:3])+.5*self.p['dc_capacitance_f']*(x[3]-x[4])**2+.5*self.p['rail_earth_capacitance_f']*(x[3:5]@x[3:5])+np.array(self.p['thermal']['capacity_j_per_k'])@(x[self.electronics]-self.p['thermal']['ambient_c'])
        if self.winding:energy+=np.array([self.winding['stator_capacity_j_per_k'],self.winding['rotor_capacity_j_per_k']])@(x[self.winding_states]-self.winding['ambient_c'])
        return float(energy)

    def tripped(self):return bool(any(self.latched) or any(self.winding_latched))

    def factors(self,x):
        factors=1+self.p['thermal']['resistance_alpha_per_k']*(x[self.electronics]-25.)
        if min(factors)<=0:raise ValueError('reference semiconductor resistance left positive range')
        return factors

    def port(self,x):
        # The historical passive active-set equations are independent of runtime.
        original=self.p;scaled=dict(original);factor=self.factors(x)[1]
        scaled['mosfet_resistance_ohm']*=factor;scaled['body_diode_resistance_ohm']*=factor
        self.p=scaled
        try:return super().port(x)
        finally:self.p=original

    def rhs(self,t,x):
        self.evaluations+=1
        if self.evaluations>2_000_000:raise RuntimeError('continuous reference RHS budget exhausted')
        p=self.p;m=self.m;th=p['thermal'];rs,rr=self.prepare(x)
        current,v,top,bottom,up,low,inverter_heat=self.port(x)
        ds,dr=self.machine.rhs(t);torque=self.machine.out.tau_M
        source,_=self.source(t);line=x[:3];rpre=0. if self.bypassed else p['precharge_resistance_per_phase_ohm']
        dx=np.zeros_like(x);rd=p['rectifier_resistance_ohm']*self.factors(x)[0];drop=p['rectifier_drop_v'];vp,vn=x[3:5];dc=vp-vn
        ac=np.where(self.mode>0,vp+drop+rd*line,np.where(self.mode<0,vn-drop+rd*line,source))
        dx[:3]=np.where(self.mode!=0,(source-ac-(p['source_resistance_ohm']+rpre)*line)/p['source_inductance_h'],0.)
        duty=float(np.clip((dc-p['brake_start_v'])/(p['brake_full_v']-p['brake_start_v']),0.,1.)) if not self.latched[2] else 0.
        brake=duty*dc/p['brake_resistance_ohm'];bleed=dc/p['bleed_ohm']
        plus=sum(np.where(self.mode>0,line,0.))-sum(top)+sum(up)-bleed-brake
        minus=-sum(np.where(self.mode<0,-line,0.))+sum(bottom)-sum(low)+bleed+brake
        common=(plus+minus)/(2*p['rail_earth_capacitance_f']);diff=(plus-minus)/(2*p['dc_capacitance_f']+p['rail_earth_capacitance_f'])
        dx[3:5]=[common+diff/2,common-diff/2];dx[self.flux_states]=np.r_[ds.real,ds.imag,np.column_stack([dr.real,dr.imag]).ravel()]
        load=p['load_torque_nm']+m['fan_nm_per_rad_s2']*x[self.speed_index]*abs(x[self.speed_index])+m['viscous_nm_per_rad_s']*x[self.speed_index]
        dx[self.speed_index]=(torque-load)/m['inertia_kg_m2']
        rectifier_heat=float(sum(rd*line*line+drop*self.mode*line)+rpre*(line@line))
        ambient=np.array(th['conductance_w_per_k'])*(x[self.electronics]-th['ambient_c'])
        dx[self.electronics]=(np.array([rectifier_heat,inverter_heat,dc*brake])-ambient)/np.array(th['capacity_j_per_k'])
        core=1.5*m['core_loss_s']*abs(ds)**2;stator=1.5*rs*abs(current)**2+core;rotor=float(1.5*sum(rr*abs(self.machine.out.i_r_ab)**2));winding_out=stator+rotor
        if self.winding:
            h=self.winding;tw=x[self.winding_states];out=np.array([h['stator_ambient_w_per_k'],h['rotor_ambient_w_per_k']])*(tw-h['ambient_c'])
            cross=h['cross_w_per_k']*(tw[0]-tw[1]);dx[self.winding_states]=[(stator-out[0]-cross)/h['stator_capacity_j_per_k'],(rotor-out[1]+cross)/h['rotor_capacity_j_per_k']];winding_out=sum(out)
        dx[self.base:self.base+10]=[source@line,p['source_resistance_ohm']*(line@line)+dc*bleed+sum(ambient)+winding_out,load*x[self.speed_index],stator,rotor,core,current.real,current.imag,x[self.speed_index],dc*brake]
        dx[self.base+10:]=1.5*rr*abs(self.machine.out.i_r_ab)**2
        self.constitutive=max(self.constitutive,abs(ds+rs*current-self.machine.inp.u_s_ab))
        return dx

    def latch(self,t,x,forced=(),cause=None):
        limits=list(self.p['thermal']['trip_c'])
        if self.winding and self.p.get('motor_temperature_trip_c'):limits+=list(self.p['motor_temperature_trip_c'])
        values=np.r_[x[self.electronics],x[self.winding_states] if len(limits)>3 else []]
        flags=np.r_[self.latched,self.winding_latched] if len(limits)>3 else self.latched.copy()
        for i in range(len(limits)):
            if not flags[i] and (i in forced or values[i]>=limits[i]):
                event=dict(time_s=float(t),kind='thermal_trip',component=['rectifier','inverter','brake','motor_stator','motor_rotor'][i],temperature_c=float(values[i]))
                if cause:event['cause']=cause
                self.thermal_events.append(event)
                if i<3:self.latched[i]=True
                else:self.winding_latched[i-3]=True

    def guards(self):
        events,keys=super().guards()
        limits=list(self.p['thermal']['trip_c']);flags=list(self.latched)
        if self.winding and self.p.get('motor_temperature_trip_c'):
            limits+=list(self.p['motor_temperature_trip_c']);flags+=list(self.winding_latched)
        for i,(limit,latched) in enumerate(zip(limits,flags)):
            if latched:continue
            def event(t,x,i=i,limit=limit):return x[self.speed_index+1+i]-limit
            event.direction=1.;event.terminal=True;events.append(event);keys.append(('thermal',i))
        return events,keys

    def switch(self,t,x,gates):
        gates=np.asarray(gates,bool)
        if gates.shape!=(3,2) or np.any(gates[:,0]&gates[:,1]):raise ValueError('invalid reference gate state')
        changed=gates!=self.gates
        if not np.any(changed):return
        old_gates=self.gates.copy();before_current=PHASE@np.array([self.port(x)[0].real,self.port(x)[0].imag])
        before=self.stored(x);self.gates=gates.copy()
        after_complex=self.port(x)[0];after_current=PHASE@np.array([after_complex.real,after_complex.imag])
        p=self.p;dc=max(0.,x[3]-x[4]);scale=dc/p['switching_reference_v']/p['switching_reference_a']*(1+p['switching_temperature_per_k']*(x[self.speed_index+2]-25.))
        if scale<0:raise ValueError('negative reference switching energy factor')
        energy=scale*sum(p['switching_on_j']*abs(after_current[i]) if gates[i,j] else p['switching_off_j']*abs(before_current[i]) for i,j in zip(*np.nonzero(changed)))
        effective=p['dc_capacitance_f']+p['rail_earth_capacitance_f']/2
        squared=dc**2-2*energy/effective
        if squared<0:
            self.gates=old_gates
            raise ValueError('switching loss exceeds available DC energy')
        center=(x[3]+x[4])/2;new_dc=math.sqrt(squared)
        x[3:5]=[center+new_dc/2,center-new_dc/2];x[self.speed_index+2]+=energy/p['thermal']['capacity_j_per_k'][1]
        self.switching_heat+=float(energy);self.gate_edges+=int(sum(changed.ravel()))
        self.jump_residual=max(self.jump_residual,abs(self.stored(x)-before))
        self.latch(t,x,cause='switching_energy')
def run(p,*,method='DOP853',rtol=1e-10,atol=1e-12,max_step=5e-5):
    if method not in {'DOP853','Radau'} or not 0<atol<rtol<1e-3 or not 0<max_step<=1e-3:raise ValueError('invalid continuous integration controls')
    c=MulticageCircuit(p);x=c.initial();initial=c.stored(x);controller=Controller(p);period=1/p['pwm_frequency_hz'];duration=p['duration_s'];sample=p['sample_s'];clock=1e-14
    times=np.arange(math.floor(duration/sample+1e-10)+1)*sample
    if duration-times[-1]>clock:times=np.r_[times,duration]
    else:times[-1]=duration
    rows=[];axis=[];cursor=0;enabled=False;events=[];t=0.;peak_energy=0.;steps=0;njev=nlu=0
    def record(time,state):
        nonlocal peak_energy
        if c.winding and max(state[c.winding_states])>=c.winding['maximum_c']:raise ValueError('winding validity ceiling exceeded')
        current,*_=c.port(state);entered,external,load=state[c.base:c.base+3];relative=abs(entered-external-load-(c.stored(state)-initial))/max(1.,abs(initial),abs(entered),abs(external));peak_energy=max(peak_energy,float(relative))
        row=dict(dc_voltage_v=state[3]-state[4],speed_rad_s=state[c.speed_index],motor_current_alpha_a=current.real,motor_current_beta_a=current.imag,motor_stator_flux_alpha_vs=state[5],motor_stator_flux_beta_vs=state[6],rotor_cage_1_flux_alpha_vs=state[7],rotor_cage_1_flux_beta_vs=state[8],source_current_a=state[0],source_current_b=state[1],source_current_c=state[2],rectifier_temperature_c=state[c.speed_index+1],inverter_temperature_c=state[c.speed_index+2],brake_temperature_c=state[c.speed_index+3],gates_enabled=enabled and not c.tripped() and not any(a<=time<b for a,b in p['gate_block_windows']),thermal_trip=c.tripped(),precharge_bypassed=c.bypassed,**controller.last)
        if c.winding:row.update(motor_stator_temperature_c=state[c.speed_index+4],motor_rotor_temperature_c=state[c.speed_index+5],motor_stator_thermal_trip=bool(c.winding_latched[0]),motor_rotor_thermal_trip=bool(c.winding_latched[1]))
        for k in range(1,len(c.m['cages'])):
            row[f'rotor_cage_{k+1}_flux_alpha_vs']=state[7+2*k]
            row[f'rotor_cage_{k+1}_flux_beta_vs']=state[8+2*k]
        axis.append(float(time));rows.append({k:float(v) for k,v in row.items()})
    record(0.,x);cursor=1
    for carrier in range(math.ceil(duration/period)):
        start=carrier*period;end=min((carrier+1)*period,duration)
        if end<=start:continue
        dc=x[3]-x[4]
        if not c.tripped() and not c.bypassed and start>=p['precharge_min_s'] and dc>=p['precharge_ready_v']:
            c.bypassed=True;events.append(dict(time_s=start,kind='precharge_bypass',dc_voltage_v=float(dc)))
        enabled=bool(not c.tripped() and c.bypassed and dc>=(p['disable_dc_v'] if enabled else p['enable_dc_v']))
        current=c.port(x)[0];u=controller.command(start,current,x[c.speed_index],max(0.,dc),period,enabled and not any(a<=start<b for a,b in p['gate_block_windows']))
        abc=PHASE@np.array([u.real,u.imag]);abc-=.5*(max(abc)+min(abc));margin=2*p['dead_time_s']/period
        duty=np.clip(.5+abc/dc,margin,1-margin) if dc>0 else np.full(3,.5);on=(1-duty)*period/2;off=(1+duty)*period/2
        boundaries={end,*[start+float(v) for v in np.r_[on,on+p['dead_time_s'],off,off+p['dead_time_s']] if start+float(v)<end-clock]}
        boundaries.update(v for pair in p['gate_block_windows'] for v in pair if start<v<end)
        boundaries.update(v for v,_ in p['grid_envelope'] if start<v<end)
        x[c.base+6:c.base+9]=0.
        for right in sorted(boundaries):
            if right<=t+clock:t=right;continue
            local=t-start+1e-13;gates=np.column_stack([(local>=on+p['dead_time_s'])&(local<off),(local<on)|(local>=off+p['dead_time_s'])])
            if not enabled or c.tripped() or any(a<=t+1e-13<b for a,b in p['gate_block_windows']):gates[:]=False
            c.switch(t,x,gates)
            if c.tripped() and np.any(gates):c.switch(t,x,np.zeros((3,2),bool))
            c.synchronize(t,x);batches=0
            while t<right-clock:
                guards,keys=c.guards();left=t
                sol=solve_ivp(c.rhs,(t,right),x,method=method,rtol=rtol,atol=atol,max_step=max_step,events=guards,dense_output=True)
                if not sol.success or not np.all(np.isfinite(sol.y)):raise RuntimeError('continuous integration failed: '+sol.message)
                stop=float(sol.t[-1]);steps+=len(sol.t)-1;njev+=getattr(sol,'njev',0);nlu+=getattr(sol,'nlu',0)
                while cursor<len(times) and times[cursor]<=stop+clock:
                    target=float(times[cursor]);record(target,sol.sol(min(target,stop)));cursor+=1
                x=sol.y[:,-1].copy();t=stop
                if sol.status==1:
                    triggered=[keys[i] for i,values in enumerate(sol.t_events) if len(values) and abs(values[-1]-stop)<1e-12]
                    old=c.mode.copy();old_flags=(tuple(c.latched),tuple(c.winding_latched))
                    c.synchronize(t,x,[key for key in triggered if isinstance(key[0],int)])
                    thermal=[key[1] for key in triggered if key[0]=='thermal']
                    c.latch(t,x,thermal)
                    if c.tripped() and np.any(c.gates):c.switch(t,x,np.zeros((3,2),bool))
                    batches+=1
                    if (t<=left+1e-16 and np.array_equal(old,c.mode) and old_flags==(tuple(c.latched),tuple(c.winding_latched))) or batches>64:raise RuntimeError('continuous hybrid-event progress exhausted')
            t=right
        elapsed=end-start
        controller.observe(complex(*x[c.base+6:c.base+8])/elapsed,x[c.base+8]/elapsed,elapsed)
    if cursor!=len(times):raise ValueError('continuous reference sample coverage differs')
    record_energy=abs(x[c.base]-x[c.base+1]-x[c.base+2]-(c.stored(x)-initial))/max(1.,abs(initial),abs(x[c.base]),abs(x[c.base+1]));peak_energy=max(peak_energy,float(record_energy))
    return dict(completed=True,time_s=axis,channels={k:[r[k] for r in rows] for k in rows[0]},event_log=sorted(events+c.thermal_events,key=lambda e:e['time_s']),source_event_log=c.source_events,metrics=dict(final_energy_relative_error=float(record_energy),maximum_energy_relative_error=peak_energy,kcl_max_a=c.kcl,motor_constitutive_residual_v=c.constitutive,rotor_cage_heat_j=x[c.base+10:].tolist(),switching_heat_j=c.switching_heat,brake_heat_j=float(x[c.base+9]),maximum_switch_energy_residual_j=c.jump_residual,gate_edges=c.gate_edges,steps=steps,rhs_evaluations=c.evaluations,njev=njev,nlu=nlu,source_root_energy_correction_j=c.reset_energy,motor_stator_heat_j=float(x[c.base+3]),motor_rotor_heat_j=float(x[c.base+4]),motor_core_heat_j=float(x[c.base+5])),reference=dict(method=method,rtol=rtol,atol=atol,max_step_s=max_step,production_imports_blocked=any(isinstance(v,BlockProduction) for v in sys.meta_path),machine_model='independent_all_flux_Gamma',external_machine=False),scope='independently expressed 1-4 cage saturated Gamma equations and continuous hybrid circuit; original-motulator single-cage reduction tested separately; multicage RHS is not an external simulator; no OEM accuracy or charge dynamics')


def reference_sources():
    names=['multicage_drive_reference.py','continuous_drive_reference.py']
    return {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in names}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study',required=True);parser.add_argument('--out',required=True)
    parser.add_argument('--method',choices=['DOP853','Radau'],default='DOP853');parser.add_argument('--rtol',type=float,default=1e-10);parser.add_argument('--atol',type=float,default=1e-12);parser.add_argument('--max-step',type=float,default=2.5e-5)
    a=parser.parse_args()
    if any(n=='power_grid_env' or n.startswith('power_grid_env.') for n in sys.modules):raise RuntimeError('production modules were loaded before isolation')
    sys.meta_path.insert(0,BlockProduction());before=reference_sources()
    result=run(read_document(a.study),method=a.method,rtol=a.rtol,atol=a.atol,max_step=a.max_step)
    if before!=reference_sources():raise RuntimeError('reference sources changed during integration')
    result['reference_source_sha256']=before
    Path(a.out).write_text(json.dumps(result,sort_keys=True,allow_nan=False,separators=(',',':')))
    print(json.dumps(dict(completed=True,metrics=result['metrics'])),flush=True)
