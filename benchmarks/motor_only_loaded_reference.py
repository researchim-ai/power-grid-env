"""Independent loaded T-circuit root and physical SI rotor-flux trajectories.

One single-cage motor, balanced radial feeder, constant-Z load, fan or explicit
smooth mechanical law. Finite grid/plant shunts and scheduled source/load/
motor commands are supported. Active relays/UV/thermal trips are rejected;
this does not independently implement network protection clocks.
"""
import cmath
import math
import numpy as np
from scipy.integrate import solve_ivp
from scipy.linalg import lu_factor,lu_solve
from scipy.optimize import brentq
from power_grid_env.study_spec import StudySpec


class WindingReference:
    def __init__(self,study):
        self.study=StudySpec.model_validate(study);n=self.study.network;s=self.study.coupled
        if s is None or s.machines or s.operating_point is None or len(s.motors)!=1 or len(n.lines)!=1 or len(n.sources)!=1:
            raise ValueError('loaded motor-only single-motor radial reference required')
        if n.generators or n.transformers or n.switches or n.shunts or n.inverters or n.storages:raise ValueError('additional equipment outside reference')
        self.n=n;self.s=s;self.m=m=s.motors[0];source=n.sources[0];line=n.lines[0]
        if m.model!='single_cage' or not m.connected:raise ValueError('connected single cage required')
        terminals={t.id:t.connectivity_node_id for t in n.terminals}
        self.up=terminals[source.terminal_id];self.down=m.node_id
        if terminals[line.terminal_from]!=self.up or terminals[line.terminal_to]!=self.down:raise ValueError('oriented two-node radial feeder required')
        if np.any(line.b_siemens) or source.phase_pu is not None:raise ValueError('balanced source without line charging required')
        for load in n.loads:
            if load.model!='constant_z' or terminals[load.terminal_id]!=self.down or any(len(set(v.values()))!=1 for v in [load.p_w,load.q_var]):raise ValueError('balanced downstream constant-Z load required')
        if any(f.node_id not in (self.up,self.down) for f in s.faults):raise ValueError('fault outside two-node circuit')
        if any(e.kind not in ('source_voltage','motor_open','motor_close','load_torque_scale') for e in s.events):raise ValueError('unsupported reference event')
        if any(e.equipment_id!=(source.id if e.kind=='source_voltage' else m.id) for e in s.events):raise ValueError('unknown reference target')
        if m.contactor and (m.contactor.sense_node_id!=m.node_id or m.contactor.maximum_automatic_restarts):raise ValueError('local PT without automatic restart required')
        self.auto=s.network_automation
        if self.auto is None or self.auto.transfers or any(b.initial_open for b in self.auto.breakers):raise ValueError('initial connected network without ATS required')
        if any(r.kind!='overcurrent' or r.measurement_kind not in ('source','line') or not r.measurement_available for r in self.auto.relays):raise ValueError('available line/source OC measurements required')
        self.wb=2*math.pi*n.frequency_hz;zb=m.rated_ll_v**2/m.rated_va
        self.vb=m.rated_ll_v/math.sqrt(3);self.ib=m.rated_va/(3*self.vb)
        self.rs=m.stator_r_pu*zb;self.rr=m.rotor_r_pu*zb
        self.lm=m.magnetizing_x_pu*zb/self.wb
        self.ls=(m.stator_x_pu+m.magnetizing_x_pu)*zb/self.wb
        self.lr=(m.rotor_x_pu+m.magnetizing_x_pu)*zb/self.wb
        self.zs=complex(source.r_ohm,source.x_ohm)
        self.zl=complex(line.r_ohm[0][0]-line.r_ohm[0][1],line.x_ohm[0][0]-line.x_ohm[0][1])
        self.es=cmath.rect(source.ll_v/math.sqrt(3),source.angle_a_rad)
        self.yl=sum(complex(sum(l.p_w.values()),-sum(l.q_var.values())) for l in n.loads)/m.rated_ll_v**2
        self.faults={f.id:f for f in s.faults};self.factors={}

    def load(self,speed):
        p=self.m.mechanical_load
        if p is None:return self.m.fan_torque_pu*speed*abs(speed)
        return p.viscous_torque_pu*speed+p.quadratic_torque_pu*speed*abs(speed)+(p.friction_torque_pu*math.tanh(speed/p.smoothing_speed_pu) if p.friction_torque_pu else 0.)

    def static(self,speed):
        slip=1-speed
        magnet=1j*self.wb*self.lm
        if abs(slip)<1e-14:parallel=magnet;rotor=complex(np.inf,0)
        else:
            rotor=complex(self.rr/slip,self.wb*(self.lr-self.lm));parallel=1/(1/magnet+1/rotor)
        zm=complex(self.rs,self.wb*(self.ls-self.lm))+parallel
        voltage=self.es/(1+(self.zs+self.zl)*(self.yl+1/zm))
        im=voltage/zm;ir=0j if abs(slip)<1e-14 else -im*parallel/rotor
        flux=self.lm*im+self.lr*ir
        torque=-3*self.wb*self.lm*(ir*np.conj(im)).imag/self.m.rated_va
        return voltage,im,ir,flux,torque

    def circuit(self,state,on=True,scale=1.,active=()):
        key=(on,tuple(sorted(active)))
        if key not in self.factors:
            yf_up=yf_down=0j
            for id in active:
                f=self.faults[id];y=1/complex(f.r_ohm,f.x_ohm)
                if f.node_id==self.up:yf_up+=y
                else:yf_down+=y
            matrix=np.array([[self.rs+1j*self.wb*self.ls,1j*self.wb*self.lm,-1,0],
                [self.lm,self.lr,0,0],[1,0,self.yl+yf_down+1/self.zl,-1/self.zl],
                [0,0,-1/self.zl,1/self.zl+1/self.zs+yf_up]],complex)
            if not on:matrix[0]=[1,0,0,0]
            self.factors[key]=lu_factor(matrix)
        return lu_solve(self.factors[key],np.array([0,complex(state[0],state[1]),0,self.es*scale/self.zs]))

    def rhs(self,state,on=True,scale=1.,active=(),load_scale=1.):
        im,ir,vd,vu=self.circuit(state,on,scale,active)
        flux=complex(state[0],state[1]);df=-self.rr*ir-1j*self.wb*(1-state[2])*flux
        torque=-3*self.wb*self.lm*(ir*np.conj(im)).imag/self.m.rated_va
        heat=((abs(im)/self.ib)**2-state[3])/self.m.thermal.time_constant_s if self.m.thermal else 0.
        return np.array([df.real,df.imag,(torque-load_scale*self.load(state[2]))/(2*self.m.inertia_s),heat])

    def initialize(self):
        low=self.s.operating_point.minimum_speed_pu
        def residual(speed):return self.static(speed)[4]-self.load(speed)
        grid=np.linspace(low,1,257);roots=[]
        for a,b in zip(grid,grid[1:]):
            fa,fb=residual(a),residual(b)
            if abs(fa)<1e-12:roots.append(float(a))
            if fa*fb<0:roots.append(brentq(residual,a,b,xtol=1e-14,rtol=1e-14))
        if abs(residual(1))<1e-12:roots.append(1.)
        if not roots:raise ValueError('no bracketed high-speed T-circuit root; no global nonexistence claim')
        speed=max(roots);voltage,im,ir,flux,torque=self.static(speed)
        heat=self.m.thermal.initial_level if self.m.thermal else 0.
        if self.m.thermal and self.s.operating_point.thermal_state=='equilibrium':heat=(abs(im)/self.ib)**2
        state=np.array([flux.real,flux.imag,speed,heat]);derivative=self.rhs(state)
        matrix=np.empty((3,3))
        for i in range(3):
            h=1e-6*max(1.,abs(state[i]));plus=state.copy();minus=state.copy();plus[i]+=h;minus[i]-=h
            matrix[:,i]=(self.rhs(plus)[:3]-self.rhs(minus)[:3])/(2*h)
        modes=np.linalg.eigvals(matrix)
        if max(modes.real)>=-1e-7 or max(abs(derivative[:3]))>1e-8:raise ValueError('reference root not stationary/stable')
        return state,dict(speed_pu=speed,voltage_pu=abs(voltage)/self.vb,current_pu=abs(im)/self.ib,
            mechanical_electrical_residual_per_s=float(max(abs(derivative[:3]))),thermal_residual_per_s=float(derivative[3]),
            eigenvalues_per_s=[[float(z.real),float(z.imag)] for z in modes])

    def controls(self,time,side):
        on=True;scale=load_scale=1.
        for e in sorted(self.s.events,key=lambda e:e.time_s):
            if time>e.time_s+1e-12 or abs(time-e.time_s)<=1e-12 and side=='post_event':
                if e.kind=='source_voltage':scale=e.value
                elif e.kind=='load_torque_scale':load_scale=e.value
                else:on=e.kind=='motor_close'
        active=[]
        for f in self.s.faults:
            applied=time>f.apply_s+1e-12 or abs(time-f.apply_s)<=1e-12 and side=='post_event'
            cleared=time>f.clear_s+1e-12 or abs(time-f.clear_s)<=1e-12 and side=='post_event'
            if applied and not cleared:active.append(f.id)
        return on,scale,active,load_scale

    def trajectory(self,times,sides):
        state,initial=self.initialize();segments=[]
        boundaries=sorted({0.,self.s.duration_s,*[e.time_s for e in self.s.events],*[t for f in self.s.faults for t in [f.apply_s,f.clear_s]]})
        for left,right in zip(boundaries,boundaries[1:]):
            on,scale,active,load_scale=self.controls(left,'post_event')
            def guard(x):
                im,ir,vd,vu=self.circuit(x,on,scale,active)
                currents=dict(source=abs((self.es*scale-vu)/self.zs),line=abs((vu-vd)/self.zl))
                if any(currents[r.measurement_kind]>=r.pickup for r in self.auto.relays):raise ValueError('active network relay outside independent loaded reference')
                if on and self.m.contactor and abs(vd)/self.vb<=self.m.contactor.dropout_voltage_pu:raise ValueError('UV action outside independent loaded reference')
                if self.m.thermal and x[3]>=self.m.thermal.trip_level:raise ValueError('thermal trip outside independent loaded reference')
            guard(state)
            guards=[]
            for relay in self.auto.relays:
                def oc(t,x,r=relay):
                    im,ir,vd,vu=self.circuit(x,on,scale,active)
                    current=abs((self.es*scale-vu)/self.zs) if r.measurement_kind=='source' else abs((vu-vd)/self.zl)
                    return r.pickup-current
                oc.terminal=True;oc.direction=-1;guards.append(oc)
            if on and self.m.contactor:
                def uv(t,x):return abs(self.circuit(x,on,scale,active)[2])/self.vb-self.m.contactor.dropout_voltage_pu
                uv.terminal=True;uv.direction=-1;guards.append(uv)
            if self.m.thermal:
                def thermal(t,x):return self.m.thermal.trip_level-x[3]
                thermal.terminal=True;thermal.direction=-1;guards.append(thermal)
            sol=solve_ivp(lambda t,x:self.rhs(x,on,scale,active,load_scale),(left,right),state,
                method='DOP853',rtol=1e-11,atol=1e-12,dense_output=True,max_step=.002,events=guards)
            if not sol.success or abs(sol.t[-1]-right)>1e-10:raise ValueError('independent loaded integration failed or reached unmodeled protection')
            for x in sol.y.T:guard(x)
            segments.append((left,right,sol.sol));state=sol.y[:,-1]
        rows=[]
        for t,side in zip(times,sides,strict=True):
            segment=next((s for s in segments if s[0]-1e-10<=t<=s[1]+1e-10),None)
            if segment is None:raise ValueError('sample outside independent trajectory')
            state=segment[2](t);on,scale,active,load_scale=self.controls(t,side)
            im,ir,vd,vu=self.circuit(state,on,scale,active);emf=1j*self.wb*self.lm/self.lr*complex(state[0],state[1])/self.vb
            fault={f.id:abs((vu if f.node_id==self.up else vd)/complex(f.r_ohm,f.x_ohm)) if f.id in active else 0. for f in self.s.faults}
            rows.append(dict(time_s=float(t),sample_side=side,speed_pu=float(state[2]),thermal_level=float(state[3]),
                emf_real_pu=float(emf.real),emf_imag_pu=float(emf.imag),current_pu=float(abs(im)/self.ib),
                downstream_voltage_real_pu=float(vd.real/self.vb),downstream_voltage_imag_pu=float(vd.imag/self.vb),
                upstream_voltage_real_pu=float(vu.real/self.vb),upstream_voltage_imag_pu=float(vu.imag/self.vb),fault_current_a={k:float(v) for k,v in fault.items()}))
        return rows,initial
