"""Continuous hybrid drive reference, original pinned motulator machine RHS.

No production model, solver, controller or PWM imports. Source RL diodes are
hybrid modes; the positive-Gc motor bridge is a passive algebraic three-node
network. SciPy integrates flux, shaft, rails, thermal states and quadratures.
"""
import argparse
import cmath
import hashlib
import importlib.abc
import importlib.metadata
import itertools
import json
import math
from pathlib import Path
import sys
import zipfile
import numpy as np
from scipy.integrate import solve_ivp

VERSION='0.23.1'
WHEEL_SHA256='dc184858cead49aa31c2e9f4bfbb149b6489850aad2360f1005b012fa3218000'
PHASE=np.array([[1.,0.],[-.5,math.sqrt(3)/2],[-.5,-math.sqrt(3)/2]])
CLARKE=2/3*PHASE.T
MODES=list(itertools.product([-1,0,1],repeat=3))


class BlockProduction(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0]=='power_grid_env':raise ImportError('production imports forbidden in continuous reference')


def authenticate(wheel):
    if hashlib.sha256(Path(wheel).read_bytes()).hexdigest()!=WHEEL_SHA256:raise ValueError('native wheel SHA256 differs')
    distribution=importlib.metadata.distribution('motulator')
    if distribution.version!=VERSION:raise ValueError('native package version differs')
    root=Path(distribution.locate_file('motulator'));hashes={}
    with zipfile.ZipFile(wheel) as archive:
        expected={n for n in archive.namelist() if n.startswith('motulator/') and n.endswith('.py')}
        for path in sorted(root.rglob('*.py')):
            name='motulator/'+path.relative_to(root).as_posix();raw=path.read_bytes()
            if name not in expected or raw!=archive.read(name):raise ValueError('native installed source differs: '+name)
            hashes[name]=hashlib.sha256(raw).hexdigest()
        if set(hashes)!=expected:raise ValueError('native source inventory differs')
    return hashes


def read_document(path):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>2_000_000:raise ValueError('unsafe continuous study document')
    def pairs(items):
        out={}
        for k,v in items:
            if k in out:raise ValueError('duplicate continuous study key')
            out[k]=v
        return out
    def number(value):
        n=float(value)
        if not math.isfinite(n):raise ValueError('nonfinite continuous study number')
        return n
    def constant(value):raise ValueError('nonfinite continuous study constant')
    return json.loads(path.read_text(),object_pairs_hook=pairs,parse_float=number,parse_constant=constant)


class Controller:
    def __init__(self,p):
        self.p=p['control'];self.m=p.get('observer_motor') or p['motor'];m=self.m;c=m['cages'][0]
        gamma=m['unsaturated_h']/(m['unsaturated_h']+c['leakage_h'])
        self.lm=gamma*m['unsaturated_h'];self.rr=gamma**2*c['resistance_ohm'];self.leak=gamma*c['leakage_h'];self.psi=0j;self.angle=0.;self.si=0.;self.ci=0j
        self.last=dict(flux_estimate_alpha_vs=0.,flux_estimate_beta_vs=0.,torque_enabled=False,voltage_limited=False)

    def command(self,t,current,speed,dc,period,enabled):
        c=self.p;flux=abs(self.psi)
        if flux>1e-10:self.angle=cmath.phase(self.psi)
        rotation=cmath.rect(1,self.angle);measured=current/rotation
        d=min(c['current_limit_peak_a'],c['flux_reference_vs']*max(0.,min(1.,t/c['flux_ramp_s']))/self.lm)
        qmax=math.sqrt(max(0.,c['current_limit_peak_a']**2-d*d));error=c['speed_reference_rad_s']*max(0.,min(1.,t/c['speed_ramp_s']))-speed
        requested=c['speed_kp_nm_per_rad_s']*error+self.si;gain=1.5*self.m['pole_pairs']*flux;ready=flux>=c['torque_enable_flux_fraction']*c['flux_reference_vs']
        q=max(-qmax,min(qmax,requested/max(gain,1e-12))) if ready and enabled else 0.;torque=gain*q
        if enabled and ready:
            if abs(torque-requested)<=1e-10 or error*requested<0:self.si+=period*c['speed_ki_nm_per_rad']*error
        elif not enabled:self.si*=math.exp(-c['antiwindup_per_s']*period)
        target=complex(d,q) if enabled else 0j;omega=self.m['pole_pairs']*speed+self.rr*measured.imag/max(flux,.02*c['flux_reference_vs'])
        demanded=self.m['stator_r_ohm']*target+1j*omega*(self.leak*measured+flux)+c['current_kp_ohm']*(target-measured)+self.ci
        limit=max(0.,dc)/math.sqrt(3)*c['voltage_utilization'] if enabled else 0.;applied=demanded*min(1.,limit/abs(demanded)) if demanded else 0j
        self.ci+=period*(c['current_ki_ohm_per_s']*(target-measured)+c['antiwindup_per_s']*(applied-demanded))
        self.last=dict(flux_estimate_alpha_vs=self.psi.real,flux_estimate_beta_vs=self.psi.imag,torque_enabled=ready and enabled,voltage_limited=abs(demanded)>limit)
        return applied*rotation

    def observe(self,current,speed,h):
        rate=-self.rr/self.lm+1j*self.m['pole_pairs']*speed;factor=cmath.exp(rate*h)
        self.psi=factor*self.psi+self.rr*(h if abs(rate*h)<1e-12 else (factor-1)/rate)*current


class Circuit:
    def __init__(self,p):
        m=p['motor'];th=p['thermal']
        if len(m['cages'])!=1 or m['locked_rotor'] or not math.isfinite(m.get('core_loss_s',0.)) or m.get('core_loss_s',0.)<=0:raise ValueError('continuous reference requires a positive-Gc single-cage free shaft')
        if p['switching_on_j'] or p['switching_off_j'] or th['resistance_alpha_per_k']:raise ValueError('switching losses and device R(T) are outside the continuous profile')
        if p.get('observer_mode','nominal_single_cage')!='nominal_single_cage':raise ValueError('continuous reference requires nominal observer')
        observer=p.get('observer_motor') or m
        if len(observer['cages'])!=1 or observer['saturation_beta_per_vs'] or observer.get('core_loss_s',0.) or observer.get('thermal') or observer['pole_pairs']!=m['pole_pairs']:raise ValueError('continuous reference requires an explicit linear nominal observer')
        if any(a>=b for a,b in zip(th['initial_c'],th['trip_c'])):raise ValueError('initial electronics trip outside continuous profile')
        from motulator.drive.model import InductionMachine,InductionMachinePars
        c=m['cages'][0]
        self.machine=InductionMachine(InductionMachinePars(n_p=m['pole_pairs'],R_s=m['stator_r_ohm'],R_r=c['resistance_ohm'],L_ell=c['leakage_h'],L_s=lambda rho:m['unsaturated_h']/(1+(m['saturation_beta_per_vs']*rho)**m['saturation_exponent']),G_c=m['core_loss_s']))
        self.p=p;self.m=m;self.winding=m.get('thermal');self.base=15 if self.winding else 13
        # I_source[3], rails[2], flux[4], speed, electronics[3], winding[2?]
        # Integrals: entered, external, load, stator, rotor, core, obs_a/b/w.
        self.size=self.base+9;self.active=(0,0,0);self.mode=np.zeros(3,dtype=int);self.bypassed=False;self.gates=np.zeros((3,2),bool)
        self.kcl=0.;self.constitutive=0.;self.evaluations=0;self.reset_energy=0.;self.source_events=[]

    def initial(self):
        x=np.zeros(self.size);x[3:5]=[self.p['initial_dc_v']/2,-self.p['initial_dc_v']/2]
        x[5:9]=np.asarray(self.p.get('motor_initial_fluxes_ab_vs') or [[0.,0.],[0.,0.]]).ravel();x[9]=self.m['initial_speed_rad_s'];x[10:13]=self.p['thermal']['initial_c']
        if self.winding:x[13:15]=self.p.get('motor_initial_temperature_c') or [self.winding['ambient_c']]*2
        return x

    def source(self,t):
        env=np.asarray(self.p['grid_envelope']);scale=np.interp(t,env[:,0],env[:,1]);slope=0.
        j=np.searchsorted(env[:,0],t,side='right')-1
        if 0<=j<len(env)-1:slope=(env[j+1,1]-env[j,1])/(env[j+1,0]-env[j,0])
        angle=2*math.pi*self.p['grid_frequency_hz']*t-np.arange(3)*2*math.pi/3;amplitude=math.sqrt(2)*self.p['grid_ln_rms_v'];w=2*math.pi*self.p['grid_frequency_hz']
        return amplitude*scale*np.cos(angle),amplitude*(slope*np.cos(angle)-scale*w*np.sin(angle))

    def prepare(self,x):
        machine=self.machine;machine.state.psi_s_ab=complex(*x[5:7]);machine.state.psi_r_ab=complex(*x[7:9]);machine.inp.w_M=x[9]
        rs=self.m['stator_r_ohm'];rr=self.m['cages'][0]['resistance_ohm']
        if self.winding:
            factor=1+self.winding['alpha_per_c']*(x[13:15]-self.winding['reference_c'])
            if min(factor)<=0:raise ValueError('invalid reference winding resistance')
            rs*=factor[0];rr*=factor[1]
        machine.par.R_s=rs;machine.par.R_r=rr
        return rs,rr

    def port(self,x):
        self.prepare(x);machine=self.machine;machine.inp.u_s_ab=0j;offset=machine._compute_outputs(machine.state,machine.inp)[0]
        machine.inp.u_s_ab=1024+0j;gain=(machine._compute_outputs(machine.state,machine.inp)[0]-offset)/1024
        if gain.real<=0 or abs(gain.imag)>1e-12:raise ValueError('nonpassive native terminal feedthrough')
        g=gain.real;vp,vn=x[3:5];rd=self.p['body_diode_resistance_ohm'];drop=self.p['body_diode_drop_v'];rc=self.p['mosfet_resistance_ohm'];gates=self.gates
        channel=np.sum(gates,axis=1)/rc;rhs=(gates[:,0]*vp+gates[:,1]*vn)/rc-PHASE@np.array([offset.real,offset.imag]);matrix=g*(PHASE@CLARKE)+np.diag(channel)
        order=[self.active]+[s for s in MODES if s!=self.active];v=None
        for status in order:
            s=np.asarray(status);on=s!=0
            if not np.any(channel) and not np.any(on):
                base=PHASE@np.array([-offset.real/g,-offset.imag/g]);low=max(vn-drop-base);high=min(vp+drop-base)
                if low>high+1e-12:continue
                common=(low+high)/2 if low>high else min(high,max(low,(vp+vn)/2));candidate=base+common
            else:
                candidate=np.linalg.solve(matrix+np.diag(on/rd),rhs+np.where(s>0,(vp+drop)/rd,np.where(s<0,(vn-drop)/rd,0.)))
            upper=candidate-vp-drop;lower=vn-candidate-drop;tol=1e-11
            if np.any((s==0)&((upper>tol)|(lower>tol))) or np.any((s==1)&(upper < -tol)) or np.any((s==-1)&(lower < -tol)):continue
            v=candidate;self.active=status;break
        if v is None:raise ValueError('no feasible passive motor bridge active set')
        voltage=CLARKE@v;machine.inp.u_s_ab=complex(*voltage);machine.set_outputs(0.)
        current=machine.out.i_s_ab;top=gates[:,0]*(vp-v)/rc;bottom=gates[:,1]*(v-vn)/rc;up=np.maximum(0.,(v-vp-drop)/rd);low=np.maximum(0.,(vn-v-drop)/rd)
        residual=PHASE@np.array([current.real,current.imag])-top+bottom+up-low
        maximum=float(max(abs(residual)));self.kcl=max(self.kcl,maximum)
        if maximum>2e-8:raise ValueError('continuous motor-port KCL exceeds bound')
        heat=float(sum(top*(vp-v)+bottom*(v-vn)+up*(v-vp)+low*(vn-v)))
        return current,v,top,bottom,up,low,heat

    def rhs(self,t,x):
        self.evaluations+=1
        if self.evaluations>2_000_000:raise RuntimeError('continuous reference RHS budget exhausted')
        p=self.p;m=self.m;th=p['thermal'];rs,rr=self.prepare(x);current,v,top,bottom,up,low,inverter_heat=self.port(x);machine=self.machine;ds,dr=machine.rhs(t);torque=machine.out.tau_M
        source,_=self.source(t);line=x[:3];rpre=0. if self.bypassed else p['precharge_resistance_per_phase_ohm'];dx=np.zeros_like(x);rd=p['rectifier_resistance_ohm'];drop=p['rectifier_drop_v'];vp,vn=x[3:5];dc=vp-vn
        if dc>=p['brake_start_v']:raise ValueError('active brake is outside continuous reference profile')
        ac=np.where(self.mode>0,vp+drop+rd*line,np.where(self.mode<0,vn-drop+rd*line,source))
        dx[:3]=np.where(self.mode!=0,(source-ac-(p['source_resistance_ohm']+rpre)*line)/p['source_inductance_h'],0.)
        positive=np.where(self.mode>0,line,0.);negative=np.where(self.mode<0,-line,0.);bleed=dc/p['bleed_ohm']
        plus=sum(positive)-sum(top)+sum(up)-bleed;minus=-sum(negative)+sum(bottom)-sum(low)+bleed
        common=(plus+minus)/(2*p['rail_earth_capacitance_f']);diff=(plus-minus)/(2*p['dc_capacitance_f']+p['rail_earth_capacitance_f']);dx[3:5]=[common+diff/2,common-diff/2]
        dx[5:9]=[ds.real,ds.imag,dr.real,dr.imag];load=p['load_torque_nm']+m['fan_nm_per_rad_s2']*x[9]*abs(x[9])+m['viscous_nm_per_rad_s']*x[9];dx[9]=(torque-load)/m['inertia_kg_m2']
        rectifier_heat=float(sum(rd*line*line+drop*self.mode*line)+rpre*(line@line));heats=np.array([rectifier_heat,inverter_heat,0.]);conductance=np.array(th['conductance_w_per_k']);capacities=np.array(th['capacity_j_per_k']);ambient=conductance*(x[10:13]-th['ambient_c']);dx[10:13]=(heats-ambient)/capacities
        core=1.5*m['core_loss_s']*abs(ds)**2;stator=1.5*rs*abs(current)**2+core;rotor=1.5*rr*abs(machine.out.i_r_ab)**2;winding_out=stator+rotor
        if self.winding:
            h=self.winding;tw=x[13:15];out=np.array([h['stator_ambient_w_per_k'],h['rotor_ambient_w_per_k']])*(tw-h['ambient_c']);cross=h['cross_w_per_k']*(tw[0]-tw[1]);dx[13:15]=[(stator-out[0]-cross)/h['stator_capacity_j_per_k'],(rotor-out[1]+cross)/h['rotor_capacity_j_per_k']];winding_out=sum(out)
        dx[self.base:]=[source@line,p['source_resistance_ohm']*(line@line)+dc*bleed+sum(ambient)+winding_out,load*x[9],stator,rotor,core,current.real,current.imag,x[9]]
        self.constitutive=max(self.constitutive,abs(ds+rs*current-machine.inp.u_s_ab))
        return dx

    def stored(self,x):
        m=self.m;s=complex(*x[5:7]);r=complex(*x[7:9]);radius=abs(s);e=m['saturation_exponent']
        magnetic=1.5/m['unsaturated_h']*(radius**2/2+m['saturation_beta_per_vs']**e*radius**(e+2)/(e+2))+.75*abs(r-s)**2/m['cages'][0]['leakage_h']
        result=magnetic+.5*m['inertia_kg_m2']*x[9]**2+.5*self.p['source_inductance_h']*(x[:3]@x[:3])+.5*self.p['dc_capacitance_f']*(x[3]-x[4])**2+.5*self.p['rail_earth_capacitance_f']*(x[3:5]@x[3:5])+np.array(self.p['thermal']['capacity_j_per_k'])@(x[10:13]-self.p['thermal']['ambient_c'])
        if self.winding:result+=np.array([self.winding['stator_capacity_j_per_k'],self.winding['rotor_capacity_j_per_k']])@(x[13:15]-self.winding['ambient_c'])
        return float(result)

    def guards(self):
        events=[];keys=[]
        for k,mode in enumerate(self.mode):
            if mode:
                def event(t,x,k=k,mode=mode):return mode*x[k]
                event.direction=-1.;event.terminal=True;events.append(event);keys.append((k,0))
            else:
                for side in [1,-1]:
                    def event(t,x,k=k,side=side):
                        emf=self.source(t)[0][k]
                        return emf-x[3]-self.p['rectifier_drop_v'] if side==1 else x[4]-self.p['rectifier_drop_v']-emf
                    event.direction=1.;event.terminal=True;events.append(event);keys.append((k,side))
        return events,keys

    def synchronize(self,t,x,triggered=()):
        emf,d_emf=self.source(t);dx=self.rhs(t,x);old=self.mode.copy();forced=dict(triggered)
        for k,mode in enumerate(old):
            if mode and (forced.get(k)==0 or (abs(x[k])<=1e-10 and mode*dx[k]<=0)):
                if abs(x[k])>1e-8:raise ValueError('unresolved reference source zero')
                self.reset_energy+=.5*self.p['source_inductance_h']*x[k]**2;x[k]=0.;self.mode[k]=0
        for k,mode in enumerate(self.mode):
            if not mode:
                upper=emf[k]-x[3]-self.p['rectifier_drop_v'];lower=x[4]-self.p['rectifier_drop_v']-emf[k]
                if forced.get(k)==1 or upper>1e-9 or (abs(upper)<=1e-9 and d_emf[k]-dx[3]>0):self.mode[k]=1
                elif forced.get(k)==-1 or lower>1e-9 or (abs(lower)<=1e-9 and dx[4]-d_emf[k]>0):self.mode[k]=-1
        for k in np.flatnonzero(old!=self.mode):self.source_events.append(dict(time_s=float(t),phase=int(k),before=int(old[k]),after=int(self.mode[k])))


def run(p,*,method='DOP853',rtol=1e-10,atol=1e-12,max_step=5e-5):
    if method not in {'DOP853','Radau'} or not 0<atol<rtol<1e-3 or not 0<max_step<=1e-3:raise ValueError('invalid continuous integration controls')
    c=Circuit(p);x=c.initial();initial=c.stored(x);controller=Controller(p);period=1/p['pwm_frequency_hz'];duration=p['duration_s'];sample=p['sample_s'];clock=1e-14
    times=np.arange(math.floor(duration/sample+1e-10)+1)*sample
    if duration-times[-1]>clock:times=np.r_[times,duration]
    else:times[-1]=duration
    rows=[];axis=[];cursor=0;enabled=False;events=[];t=0.;peak_energy=0.;steps=0;njev=nlu=0
    def record(time,state):
        nonlocal peak_energy
        if max(state[10:13]-np.array(p['thermal']['trip_c']))>=0:raise ValueError('electronics trip outside continuous reference profile')
        if c.winding and (max(state[13:15])>=c.winding['maximum_c'] or np.any(state[13:15]>=np.array(p.get('motor_temperature_trip_c') or [1e99,1e99]))):raise ValueError('winding trip/range outside continuous reference profile')
        current,*_=c.port(state);entered,external,load=state[c.base:c.base+3];relative=abs(entered-external-load-(c.stored(state)-initial))/max(1.,abs(initial),abs(entered),abs(external));peak_energy=max(peak_energy,float(relative))
        row=dict(dc_voltage_v=state[3]-state[4],speed_rad_s=state[9],motor_current_alpha_a=current.real,motor_current_beta_a=current.imag,motor_stator_flux_alpha_vs=state[5],motor_stator_flux_beta_vs=state[6],rotor_cage_1_flux_alpha_vs=state[7],rotor_cage_1_flux_beta_vs=state[8],source_current_a=state[0],source_current_b=state[1],source_current_c=state[2],rectifier_temperature_c=state[10],inverter_temperature_c=state[11],brake_temperature_c=state[12],gates_enabled=enabled and not any(a<=time<b for a,b in p['gate_block_windows']),thermal_trip=False,precharge_bypassed=c.bypassed,**controller.last)
        if c.winding:row.update(motor_stator_temperature_c=state[13],motor_rotor_temperature_c=state[14],motor_stator_thermal_trip=False,motor_rotor_thermal_trip=False)
        axis.append(float(time));rows.append({k:float(v) for k,v in row.items()})
    record(0.,x);cursor=1
    for carrier in range(math.ceil(duration/period)):
        start=carrier*period;end=min((carrier+1)*period,duration)
        if end<=start:continue
        dc=x[3]-x[4]
        if not c.bypassed and start>=p['precharge_min_s'] and dc>=p['precharge_ready_v']:
            c.bypassed=True;events.append(dict(time_s=start,kind='precharge_bypass',dc_voltage_v=float(dc)))
        enabled=bool(c.bypassed and dc>=(p['disable_dc_v'] if enabled else p['enable_dc_v']))
        current=c.port(x)[0];u=controller.command(start,current,x[9],max(0.,dc),period,enabled and not any(a<=start<b for a,b in p['gate_block_windows']))
        abc=PHASE@np.array([u.real,u.imag]);abc-=.5*(max(abc)+min(abc));margin=2*p['dead_time_s']/period
        duty=np.clip(.5+abc/dc,margin,1-margin) if dc>0 else np.full(3,.5);on=(1-duty)*period/2;off=(1+duty)*period/2
        boundaries={end,*[start+float(v) for v in np.r_[on,on+p['dead_time_s'],off,off+p['dead_time_s']] if start+float(v)<end-clock]}
        boundaries.update(v for pair in p['gate_block_windows'] for v in pair if start<v<end)
        boundaries.update(v for v,_ in p['grid_envelope'] if start<v<end)
        x[c.base+6:]=0.
        for right in sorted(boundaries):
            if right<=t+clock:t=right;continue
            local=t-start+1e-13;c.gates=np.column_stack([(local>=on+p['dead_time_s'])&(local<off),(local<on)|(local>=off+p['dead_time_s'])])
            if not enabled or any(a<=t+1e-13<b for a,b in p['gate_block_windows']):c.gates[:]=False
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
                    old=c.mode.copy();c.synchronize(t,x,triggered);batches+=1
                    if (t<=left+1e-16 and np.array_equal(old,c.mode)) or batches>64:raise RuntimeError('continuous source-event progress exhausted')
            t=right
        elapsed=end-start
        controller.observe(complex(*x[c.base+6:c.base+8])/elapsed,x[c.base+8]/elapsed,elapsed)
    if cursor!=len(times):raise ValueError('continuous reference sample coverage differs')
    record_energy=abs(x[c.base]-x[c.base+1]-x[c.base+2]-(c.stored(x)-initial))/max(1.,abs(initial),abs(x[c.base]),abs(x[c.base+1]));peak_energy=max(peak_energy,float(record_energy))
    return dict(completed=True,time_s=axis,channels={k:[r[k] for r in rows] for k in rows[0]},event_log=events,source_event_log=c.source_events,metrics=dict(final_energy_relative_error=float(record_energy),maximum_energy_relative_error=peak_energy,kcl_max_a=c.kcl,motor_constitutive_residual_v=c.constitutive,steps=steps,rhs_evaluations=c.evaluations,njev=njev,nlu=nlu,source_root_energy_correction_j=c.reset_energy,motor_stator_heat_j=float(x[c.base+3]),motor_rotor_heat_j=float(x[c.base+4]),motor_core_heat_j=float(x[c.base+5])),reference=dict(method=method,rtol=rtol,atol=atol,max_step_s=max_step,production_imports_blocked=any(isinstance(v,BlockProduction) for v in sys.meta_path),machine_version=VERSION),scope='single-cage positive-Gc original motulator machine; separately assembled full source RL/diodes/DC/body-diode/channel/FOC/PWM/thermal ODE; no production solver/stamps; source hybrid guards; no active brake, switching energy, electronics R(T) or physical thermal trips')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--study',required=True);parser.add_argument('--wheel',required=True);parser.add_argument('--out',required=True);parser.add_argument('--method',choices=['DOP853','Radau'],default='DOP853');parser.add_argument('--rtol',type=float,default=1e-10);parser.add_argument('--atol',type=float,default=1e-12);parser.add_argument('--max-step',type=float,default=5e-5)
    a=parser.parse_args()
    if any(name=='power_grid_env' or name.startswith('power_grid_env.') for name in sys.modules):raise RuntimeError('production modules were loaded before isolation')
    sys.meta_path.insert(0,BlockProduction());identity=authenticate(a.wheel);p=read_document(a.study);result=run(p,method=a.method,rtol=a.rtol,atol=a.atol,max_step=a.max_step)
    result['native_source_sha256']=identity;Path(a.out).write_text(json.dumps(result,sort_keys=True,allow_nan=False,separators=(',',':')))
    print(json.dumps(dict(completed=True,metrics=result['metrics'])),flush=True)
