"""Independent extension of R06.18; historical reference remains unchanged.

Native motulator RHS and independently assembled hybrid circuit. Switching
losses transfer DC energy into inverter thermal storage, never external loss.
Protection blocks controlled gates; passive diodes retain stored-energy paths.
"""
import argparse
import copy
import json
import math
from pathlib import Path
import sys
import numpy as np
from scipy.integrate import solve_ivp
from benchmarks.continuous_drive_reference import (
    Circuit, Controller, PHASE, VERSION, BlockProduction, authenticate, read_document,
)


class ElectrothermalCircuit(Circuit):
    def __init__(self,p):
        initialization=copy.deepcopy(p)
        initialization.update(switching_on_j=0.,switching_off_j=0.)
        initialization['thermal'].update(resistance_alpha_per_k=0.,trip_c=[1e99]*3)
        super().__init__(initialization)
        self.p=copy.deepcopy(p);self.size+=1
        self.latched=np.array(p['thermal']['initial_c'])>=np.array(p['thermal']['trip_c'])
        self.winding_latched=np.zeros(2,bool)
        if self.winding and p.get('motor_temperature_trip_c'):
            self.winding_latched=np.array(p.get('motor_initial_temperature_c') or [self.winding['ambient_c']]*2)>=np.array(p['motor_temperature_trip_c'])
        self.thermal_events=[];self.switching_heat=0.;self.jump_residual=0.;self.gate_edges=0
        for k in np.flatnonzero(self.winding_latched):
            self.thermal_events.append(dict(time_s=0.,kind='thermal_trip',component=['motor_stator','motor_rotor'][k],temperature_c=float((p.get('motor_initial_temperature_c') or [self.winding['ambient_c']]*2)[k]),cause='initial_temperature'))

    def tripped(self):return bool(any(self.latched) or any(self.winding_latched))

    def factors(self,x):
        factors=1+self.p['thermal']['resistance_alpha_per_k']*(x[10:13]-25.)
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
        dx[3:5]=[common+diff/2,common-diff/2];dx[5:9]=[ds.real,ds.imag,dr.real,dr.imag]
        load=p['load_torque_nm']+m['fan_nm_per_rad_s2']*x[9]*abs(x[9])+m['viscous_nm_per_rad_s']*x[9]
        dx[9]=(torque-load)/m['inertia_kg_m2']
        rectifier_heat=float(sum(rd*line*line+drop*self.mode*line)+rpre*(line@line))
        ambient=np.array(th['conductance_w_per_k'])*(x[10:13]-th['ambient_c'])
        dx[10:13]=(np.array([rectifier_heat,inverter_heat,dc*brake])-ambient)/np.array(th['capacity_j_per_k'])
        core=1.5*m['core_loss_s']*abs(ds)**2;stator=1.5*rs*abs(current)**2+core;rotor=1.5*rr*abs(self.machine.out.i_r_ab)**2;winding_out=stator+rotor
        if self.winding:
            h=self.winding;tw=x[13:15];out=np.array([h['stator_ambient_w_per_k'],h['rotor_ambient_w_per_k']])*(tw-h['ambient_c'])
            cross=h['cross_w_per_k']*(tw[0]-tw[1]);dx[13:15]=[(stator-out[0]-cross)/h['stator_capacity_j_per_k'],(rotor-out[1]+cross)/h['rotor_capacity_j_per_k']];winding_out=sum(out)
        dx[self.base:]=[source@line,p['source_resistance_ohm']*(line@line)+dc*bleed+sum(ambient)+winding_out,load*x[9],stator,rotor,core,current.real,current.imag,x[9],dc*brake]
        self.constitutive=max(self.constitutive,abs(ds+rs*current-self.machine.inp.u_s_ab))
        return dx

    def latch(self,t,x,forced=(),cause=None):
        limits=list(self.p['thermal']['trip_c'])
        if self.winding and self.p.get('motor_temperature_trip_c'):limits+=list(self.p['motor_temperature_trip_c'])
        values=np.r_[x[10:13],x[13:15] if len(limits)>3 else []]
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
            def event(t,x,i=i,limit=limit):return x[10+i]-limit
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
        p=self.p;dc=max(0.,x[3]-x[4]);scale=dc/p['switching_reference_v']/p['switching_reference_a']*(1+p['switching_temperature_per_k']*(x[11]-25.))
        if scale<0:raise ValueError('negative reference switching energy factor')
        energy=scale*sum(p['switching_on_j']*abs(after_current[i]) if gates[i,j] else p['switching_off_j']*abs(before_current[i]) for i,j in zip(*np.nonzero(changed)))
        effective=p['dc_capacitance_f']+p['rail_earth_capacitance_f']/2
        squared=dc**2-2*energy/effective
        if squared<0:
            self.gates=old_gates
            raise ValueError('switching loss exceeds available DC energy')
        center=(x[3]+x[4])/2;new_dc=math.sqrt(squared)
        x[3:5]=[center+new_dc/2,center-new_dc/2];x[11]+=energy/p['thermal']['capacity_j_per_k'][1]
        self.switching_heat+=float(energy);self.gate_edges+=int(sum(changed.ravel()))
        self.jump_residual=max(self.jump_residual,abs(self.stored(x)-before))
        self.latch(t,x,cause='switching_energy')


def run(p,*,method='DOP853',rtol=1e-10,atol=1e-12,max_step=5e-5):
    if method not in {'DOP853','Radau'} or not 0<atol<rtol<1e-3 or not 0<max_step<=1e-3:raise ValueError('invalid continuous integration controls')
    c=ElectrothermalCircuit(p);x=c.initial();initial=c.stored(x);controller=Controller(p);period=1/p['pwm_frequency_hz'];duration=p['duration_s'];sample=p['sample_s'];clock=1e-14
    times=np.arange(math.floor(duration/sample+1e-10)+1)*sample
    if duration-times[-1]>clock:times=np.r_[times,duration]
    else:times[-1]=duration
    rows=[];axis=[];cursor=0;enabled=False;events=[];t=0.;peak_energy=0.;steps=0;njev=nlu=0
    def record(time,state):
        nonlocal peak_energy
        if c.winding and max(state[13:15])>=c.winding['maximum_c']:raise ValueError('winding validity ceiling exceeded')
        current,*_=c.port(state);entered,external,load=state[c.base:c.base+3];relative=abs(entered-external-load-(c.stored(state)-initial))/max(1.,abs(initial),abs(entered),abs(external));peak_energy=max(peak_energy,float(relative))
        row=dict(dc_voltage_v=state[3]-state[4],speed_rad_s=state[9],motor_current_alpha_a=current.real,motor_current_beta_a=current.imag,motor_stator_flux_alpha_vs=state[5],motor_stator_flux_beta_vs=state[6],rotor_cage_1_flux_alpha_vs=state[7],rotor_cage_1_flux_beta_vs=state[8],source_current_a=state[0],source_current_b=state[1],source_current_c=state[2],rectifier_temperature_c=state[10],inverter_temperature_c=state[11],brake_temperature_c=state[12],gates_enabled=enabled and not c.tripped() and not any(a<=time<b for a,b in p['gate_block_windows']),thermal_trip=c.tripped(),precharge_bypassed=c.bypassed,**controller.last)
        if c.winding:row.update(motor_stator_temperature_c=state[13],motor_rotor_temperature_c=state[14],motor_stator_thermal_trip=bool(c.winding_latched[0]),motor_rotor_thermal_trip=bool(c.winding_latched[1]))
        axis.append(float(time));rows.append({k:float(v) for k,v in row.items()})
    record(0.,x);cursor=1
    for carrier in range(math.ceil(duration/period)):
        start=carrier*period;end=min((carrier+1)*period,duration)
        if end<=start:continue
        dc=x[3]-x[4]
        if not c.tripped() and not c.bypassed and start>=p['precharge_min_s'] and dc>=p['precharge_ready_v']:
            c.bypassed=True;events.append(dict(time_s=start,kind='precharge_bypass',dc_voltage_v=float(dc)))
        enabled=bool(not c.tripped() and c.bypassed and dc>=(p['disable_dc_v'] if enabled else p['enable_dc_v']))
        current=c.port(x)[0];u=controller.command(start,current,x[9],max(0.,dc),period,enabled and not any(a<=start<b for a,b in p['gate_block_windows']))
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
    return dict(completed=True,time_s=axis,channels={k:[r[k] for r in rows] for k in rows[0]},event_log=sorted(events+c.thermal_events,key=lambda e:e['time_s']),source_event_log=c.source_events,metrics=dict(final_energy_relative_error=float(record_energy),maximum_energy_relative_error=peak_energy,kcl_max_a=c.kcl,motor_constitutive_residual_v=c.constitutive,switching_heat_j=c.switching_heat,brake_heat_j=float(x[c.base+9]),maximum_switch_energy_residual_j=c.jump_residual,gate_edges=c.gate_edges,steps=steps,rhs_evaluations=c.evaluations,njev=njev,nlu=nlu,source_root_energy_correction_j=c.reset_energy,motor_stator_heat_j=float(x[c.base+3]),motor_rotor_heat_j=float(x[c.base+4]),motor_core_heat_j=float(x[c.base+5])),reference=dict(method=method,rtol=rtol,atol=atol,max_step_s=max_step,production_imports_blocked=any(isinstance(v,BlockProduction) for v in sys.meta_path),machine_version=VERSION),scope='positive-Gc single-cage external machine; independent continuous electrothermal circuit with device R(T), brake, switching-energy jumps and latched thermal roots; not charge-dynamic semiconductors or OEM validation')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study',required=True);parser.add_argument('--wheel',required=True);parser.add_argument('--out',required=True)
    parser.add_argument('--method',choices=['DOP853','Radau'],default='DOP853');parser.add_argument('--rtol',type=float,default=1e-10);parser.add_argument('--atol',type=float,default=1e-12);parser.add_argument('--max-step',type=float,default=2.5e-5)
    a=parser.parse_args()
    if any(n=='power_grid_env' or n.startswith('power_grid_env.') for n in sys.modules):raise RuntimeError('production modules were loaded before isolation')
    sys.meta_path.insert(0,BlockProduction());identity=authenticate(a.wheel)
    result=run(read_document(a.study),method=a.method,rtol=a.rtol,atol=a.atol,max_step=a.max_step)
    result['native_source_sha256']=identity;Path(a.out).write_text(json.dumps(result,sort_keys=True,allow_nan=False,separators=(',',':')))
    print(json.dumps(dict(completed=True,metrics=result['metrics'])),flush=True)
