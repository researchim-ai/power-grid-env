"""SI phase-domain RLC networks, nonlinear magnetics and averaged GFL/GFM ports.

Ground is explicit node '0'. Voltage sources are node-to-ground Dirichlet
boundaries; capacitive-free nodes require a nonsingular resistive algebraic
block. Arbitrary ideal-source loops and index-2 inductor cutsets are rejected.
"""
from typing import Literal
import math

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator,model_serializer
from scipy.integrate import solve_ivp
from scipy.linalg import lu_factor, lu_solve, null_space
from power_grid_env.flux_machine import FluxMachinePassport


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Tone(Strict):
    order: int = Field(ge=0, le=100)
    rms: float = Field(ge=0)
    phase_rad: float = 0.

    @model_validator(mode='after')
    def valid(self):
        if self.order==0 and self.phase_rad!=0:raise ValueError('DC tone has no phase; use a signed envelope')
        return self


class VoltageBoundary(Strict):
    id: str
    node: str
    tones: list[Tone] = Field(min_length=1, max_length=101)
    envelope: list[tuple[float, float]] = Field(default_factory=lambda: [(0., 1.)], min_length=1)

    @model_validator(mode='after')
    def valid(self):
        if self.envelope[0][0] != 0 or any(a[0] >= b[0] for a,b in zip(self.envelope,self.envelope[1:])):
            raise ValueError('boundary envelope must start at zero and have increasing times')
        if len({t.order for t in self.tones}) != len(self.tones):
            raise ValueError('unique source harmonic orders required')
        return self

    def value(self, t, frequency, derivative=False):
        points = self.envelope; scale = points[-1][1]; slope = 0.
        for a, b in zip(points, points[1:]):
            if a[0] <= t < b[0]:
                slope = (b[1]-a[1])/(b[0]-a[0]); scale = a[1]+slope*(t-a[0]); break
        total = 0.; dtotal = 0.
        for tone in self.tones:
            omega = 2*math.pi*frequency*tone.order
            amplitude = tone.rms if tone.order == 0 else math.sqrt(2.)*tone.rms
            total += amplitude*math.cos(omega*t+tone.phase_rad)
            dtotal -= amplitude*omega*math.sin(omega*t+tone.phase_rad)
        return slope*total+scale*dtotal if derivative else scale*total


class Resistor(Strict):
    id: str
    frm: str
    to: str = '0'
    resistance_ohm: float = Field(gt=0)


class Capacitor(Strict):
    id: str
    frm: str
    to: str = '0'
    capacitance_f: float = Field(gt=0)


class Inductor(Strict):
    id: str
    frm: str
    to: str = '0'
    resistance_ohm: float = Field(ge=0)
    initial_current_a: float = 0.
    saturation_flux_vs: float = Field(default=0., ge=0)
    saturation_current_a: float = Field(default=1., gt=0)


class CurrentInjection(Strict):
    id: str
    node: str
    tones: list[Tone] = Field(min_length=1, max_length=101)

    @model_validator(mode='after')
    def valid(self):
        if len({t.order for t in self.tones})!=len(self.tones):raise ValueError('unique current-source harmonic orders required')
        return self


class Converter(Strict):
    id: str
    kind: Literal['gfl', 'gfm']
    nodes: tuple[str, str, str]
    nominal_ln_rms_v: float = Field(gt=0)
    frequency_hz: float = Field(default=50., gt=0)
    current_limit_rms_a: float = Field(gt=0)
    current_tau_s: float = Field(gt=0)
    power_tau_s: float = Field(default=.02, gt=0)
    active_ref_w: float = 0.
    reactive_ref_var: float = 0.
    phase_rad: float = 0.
    pll_kp_per_s: float = Field(default=50., ge=0)
    pll_ki_per_s2: float = Field(default=500., ge=0)
    frequency_limit_hz: float = Field(default=5., gt=0)
    virtual_r_ohm: float = Field(default=1., gt=0)
    droop_rad_s_per_w: float = Field(default=0., ge=0)
    droop_v_per_var: float = Field(default=0., ge=0)
    initial_currents: tuple[float, float] = (0., 0.)
    connected: bool = True

    @model_validator(mode='after')
    def valid(self):
        if len(set(self.nodes)) != 3 or '0' in self.nodes:
            raise ValueError('converter needs three distinct explicit phase nodes')
        if math.hypot(*self.initial_currents) > math.sqrt(2)*self.current_limit_rms_a*(1+1e-12):
            raise ValueError('initial converter current exceeds the declared vector limit')
        return self


class Drive(Strict):
    id: str
    nodes: tuple[str,str,str]
    motor: FluxMachinePassport
    grid_ln_rms_v: float = Field(gt=0)
    grid_frequency_hz: float = Field(default=50.,gt=0)
    grid_current_limit_rms_a: float = Field(gt=0)
    rectifier_tau_s: float = Field(default=.002,gt=0)
    dc_capacitance_f: float = Field(gt=0)
    dc_reference_v: float = Field(gt=0)
    initial_dc_v: float = Field(gt=0)
    dc_kp_per_s: float = Field(default=20.,ge=0)
    dc_ki_per_s2: float = Field(default=100.,ge=0)
    bleed_ohm: float = Field(default=1e6,gt=0)
    rectifier_efficiency: float = Field(default=1.,gt=0,le=1)
    inverter_efficiency: float = Field(default=1.,gt=0,le=1)
    maximum_modulation: float = Field(default=1.,gt=0,le=1)
    motor_frequency_hz: float = Field(default=50.,gt=0)
    motor_ln_rms_v: float = Field(gt=0)
    ramp_s: float = Field(gt=0)
    phase_rad: float = 0.
    pll_kp_per_s: float = Field(default=50.,ge=0)
    pll_ki_per_s2: float = Field(default=500.,ge=0)
    connected: bool = True

    @model_validator(mode='after')
    def valid(self):
        if len(set(self.nodes))!=3 or '0' in self.nodes:raise ValueError('drive needs distinct explicit phase nodes')
        if self.motor.core_loss_s:raise ValueError('drive Gamma current-coordinate profile does not support positive core-loss conductance')
        if self.motor.locked_rotor and self.motor.initial_speed_rad_s:raise ValueError('locked rotor requires zero initial speed')
        return self


class MotorPort(Strict):
    initial_currents_ab_a: list[tuple[float,float]] | None=Field(default=None,min_length=2,max_length=5)
    id: str
    nodes: tuple[str,str,str]
    passport: FluxMachinePassport

    @model_serializer(mode='wrap')
    def canonical_initial_state(self,handler):
        data=handler(self)
        if self.initial_currents_ab_a is None:data.pop('initial_currents_ab_a',None)
        return data

    @model_validator(mode='after')
    def valid(self):
        if self.initial_currents_ab_a is not None and len(self.initial_currents_ab_a)!=1+len(self.passport.cages):raise ValueError('initial current inventory must match stator and every rotor cage')
        if len(set(self.nodes))!=3 or '0' in self.nodes:raise ValueError('motor port requires distinct phase nodes')
        if self.passport.core_loss_s:raise ValueError('direct phase motor currently requires zero Gc')
        if self.passport.locked_rotor and self.passport.initial_speed_rad_s:raise ValueError('locked rotor requires zero speed')
        return self


class PhaseEvent(Strict):
    time_s: float = Field(gt=0)
    kind: Literal['resistance', 'converter_connection']
    equipment_id: str
    value: float


class PhaseCase(Strict):
    schema_version: Literal['power-grid-env.phase-network/v1'] = 'power-grid-env.phase-network/v1'
    id: str
    nodes: list[str] = Field(min_length=1, max_length=128)
    frequency_hz: float = Field(default=50., gt=0)
    sources: list[VoltageBoundary] = Field(default_factory=list)
    resistors: list[Resistor] = Field(default_factory=list)
    capacitors: list[Capacitor] = Field(default_factory=list)
    inductors: list[Inductor] = Field(default_factory=list, max_length=256)
    inductance_h: list[list[float]] = Field(default_factory=list)
    injections: list[CurrentInjection] = Field(default_factory=list)
    converters: list[Converter] = Field(default_factory=list, max_length=16)
    drives: list[Drive] = Field(default_factory=list,max_length=8)
    motors: list[MotorPort] = Field(default_factory=list,max_length=16)
    events: list[PhaseEvent] = Field(default_factory=list, max_length=256)
    initial_voltage_v: dict[str, float] = Field(default_factory=dict)
    sequence_monitors: dict[str, tuple[str, str, str]] = Field(default_factory=dict)
    sequence_neutrals: dict[str,str] = Field(default_factory=dict)
    provenance: dict[str, str] = Field(min_length=1)

    @model_validator(mode='after')
    def valid(self):
        if len(set(self.nodes)) != len(self.nodes) or '0' in self.nodes or any(not n for n in self.nodes):
            raise ValueError('unique nonempty phase node IDs excluding ground 0 required')
        all_components = [*self.sources,*self.resistors,*self.capacitors,*self.inductors,*self.injections,*self.converters,*self.drives,*self.motors]
        ids = [x.id for x in all_components]
        if len(ids) != len(set(ids)) or any(not k for k in ids):
            raise ValueError('component IDs must be globally unique and nonempty')
        if len(all_components) > 2048: raise ValueError('component budget exceeded')
        known = set(self.nodes)|{'0'}
        for b in [*self.resistors,*self.capacitors,*self.inductors]:
            if b.frm not in known or b.to not in known or b.frm == b.to:
                raise ValueError('invalid component endpoints')
        for source in [*self.sources,*self.injections]:
            if source.node not in self.nodes: raise ValueError('unknown source node')
        if len({s.node for s in self.sources}) != len(self.sources): raise ValueError('one ideal boundary per node required')
        for converter in [*self.converters,*self.drives,*self.motors]:
            if any(n not in self.nodes for n in converter.nodes): raise ValueError('unknown converter phase node')
        if any(n not in self.nodes for n in self.initial_voltage_v): raise ValueError('unknown initial-voltage node')
        for group in self.sequence_monitors.values():
            if len(set(group)) != 3 or any(n not in self.nodes for n in group): raise ValueError('invalid abc sequence monitor')
        if any(k not in self.sequence_monitors or n not in known for k,n in self.sequence_neutrals.items()):raise ValueError('invalid sequence neutral reference')
        matrix = np.asarray(self.inductance_h, dtype=float)
        if self.inductors:
            if matrix.shape != (len(self.inductors),)*2 or not np.array_equal(matrix,matrix.T):
                raise ValueError('symmetric full inductance matrix required')
            if np.linalg.eigvalsh(matrix)[0] <= 0: raise ValueError('inductance matrix must be positive definite')
        elif matrix.size: raise ValueError('inductance matrix without inductors')
        seen = set(); resistor_ids = {r.id for r in self.resistors}; converter_ids = {c.id for c in [*self.converters,*self.drives]}
        for event in self.events:
            key=(event.time_s,event.kind,event.equipment_id)
            if key in seen: raise ValueError('ambiguous simultaneous action on one component')
            seen.add(key)
            if event.kind == 'resistance' and (event.equipment_id not in resistor_ids or event.value <= 0): raise ValueError('resistance event needs a known resistor and positive R')
            if event.kind == 'converter_connection' and (event.equipment_id not in converter_ids or event.value not in (0.,1.)): raise ValueError('connection event needs a converter and 0/1')
        return self


def _abc(z):
    return np.array([z.real, (z*np.exp(-2j*np.pi/3)).real, (z*np.exp(2j*np.pi/3)).real])


def _space(v):
    a=np.exp(2j*np.pi/3)
    return 2./3*(v[0]+a*v[1]+a*a*v[2])


class PhaseNetwork:
    def __init__(self, case):
        self.case=case; self.index={n:i for i,n in enumerate(case.nodes)}; self.n=len(case.nodes)
        self.known=[self.index[s.node] for s in case.sources]; self.free=[i for i in range(self.n) if i not in self.known]
        self.a=self._incidence(case.inductors); self.l=np.asarray(case.inductance_h,dtype=float).reshape((len(case.inductors),)*2)
        self.r=np.array([b.resistance_ohm for b in case.inductors]); self.sat=np.array([b.saturation_flux_vs for b in case.inductors]); self.isat=np.array([b.saturation_current_a for b in case.inductors])
        self.c=np.zeros((self.n,self.n))
        for b in case.capacitors:
            a=self._incidence([b])[:,0]; self.c+=b.capacitance_f*np.outer(a,a)
        cff=self.c[np.ix_(self.free,self.free)]
        # The capacitive nullspace follows connectivity, not an eigenvalue cutoff.
        # A tiny physical capacitor must not silently disappear as a "zero" mode.
        free_set=set(self.free); edges={i:set() for i in self.free}; anchored=set()
        for cap in case.capacitors:
            i=self.index.get(cap.frm); j=self.index.get(cap.to)
            if i in free_set and j in free_set:edges[i].add(j);edges[j].add(i)
            elif i in free_set:anchored.add(i)
            elif j in free_set:anchored.add(j)
        unseen=set(self.free); null=[]; position={n:i for i,n in enumerate(self.free)}
        while unseen:
            stack=[min(unseen)]; group=set()
            while stack:
                node=stack.pop()
                if node not in group:group.add(node);stack.extend(edges[node]-group)
            unseen-=group
            if not group&anchored:
                vector=np.zeros(len(self.free))
                for n in group:vector[position[n]]=1/math.sqrt(len(group))
                null.append(vector)
        self.ua=np.array(null).T if null else np.zeros((len(self.free),0))
        span=null_space(self.ua.T) if len(self.free) else np.zeros((0,0))
        if span.shape[1]:
            eig,u=np.linalg.eigh(span.T@cff@span)
            if np.any(eig<=0):raise ValueError('capacitance matrix is numerically nonpositive on its dynamic subspace')
            self.ud=span@u;self.ceig=eig
        else:self.ud=span;self.ceig=np.zeros(0)
        self.linear_l=lu_factor(self.l) if len(case.inductors) and not np.any(self.sat) else None
        self.nd=len(self.ceig); self.ni=len(case.inductors); self.slices={}; cursor=self.nd+self.ni
        for converter in case.converters:
            size=6 if converter.kind=='gfl' else 5
            self.slices[converter.id]=slice(cursor,cursor+size); cursor+=size
        from power_grid_env.vf_drive import VfDrive
        self.drives={d.id:VfDrive(d) for d in case.drives}
        for name,drive in self.drives.items():self.slices[name]=slice(cursor,cursor+drive.size);cursor+=drive.size
        from power_grid_env.vf_drive import PhaseMotor
        self.motors={m.id:PhaseMotor(m) for m in case.motors}
        for name,motor in self.motors.items():self.slices[name]=slice(cursor,cursor+motor.size);cursor+=motor.size
        self.size=cursor
        if self.size > 512: raise ValueError('phase dynamic-state budget exceeded')
        self.context(-1.)

    def _incidence(self, branches):
        a=np.zeros((self.n,len(branches)))
        for i,b in enumerate(branches):
            if b.frm!='0':a[self.index[b.frm],i]=1.
            if b.to!='0':a[self.index[b.to],i]=-1.
        return a

    def context(self,t):
        resistances={b.id:b.resistance_ohm for b in self.case.resistors}; self.connected={c.id:c.connected for c in [*self.case.converters,*self.case.drives]}
        for event in sorted(self.case.events,key=lambda e:(e.time_s,e.kind,e.equipment_id)):
            if event.time_s<=t:
                if event.kind=='resistance':resistances[event.equipment_id]=event.value
                else:self.connected[event.equipment_id]=bool(event.value)
        self.resistances=resistances; self.g=np.zeros((self.n,self.n))
        for b in self.case.resistors:
            a=self._incidence([b])[:,0]; self.g+=np.outer(a,a)/resistances[b.id]
        self.gff=self.g[np.ix_(self.free,self.free)]; self.gfk=self.g[np.ix_(self.free,self.known)]
        if self.ua.shape[1]:
            gaa=self.ua.T@self.gff@self.ua
            if np.linalg.matrix_rank(gaa)<len(gaa):raise ValueError('floating algebraic node or inductor cutset; explicit resistive reference required')
            self.gaa=lu_factor(gaa)

    def boundary(self,t,derivative=False):
        return np.array([s.value(t,self.case.frequency_hz,derivative) for s in self.case.sources])

    def injection(self,t,x):
        j=np.zeros(self.n)
        for s in self.case.injections:
            j[self.index[s.node]]+=sum((tone.rms if tone.order==0 else math.sqrt(2)*tone.rms)*math.cos(2*np.pi*self.case.frequency_hz*tone.order*t+tone.phase_rad) for tone in s.tones)
        conv={}
        for c in self.case.converters:
            state=x[self.slices[c.id]]
            actual=complex(state[2],state[3])*np.exp(1j*state[0]) if c.kind=='gfl' else complex(state[1],state[2])
            if not self.connected[c.id]:actual=0j
            currents=_abc(actual); conv[c.id]=actual
            j[[self.index[n] for n in c.nodes]]+=currents
        for drive in self.case.drives:
            actual=self.drives[drive.id].current(x[self.slices[drive.id]]) if self.connected[drive.id] else 0j
            j[[self.index[n] for n in drive.nodes]]+=_abc(actual)
        for motor in self.case.motors:j[[self.index[n] for n in motor.nodes]]-=_abc(self.motors[motor.id].current(x[self.slices[motor.id]]))
        return j,conv

    def fields(self,t,x):
        v=np.zeros(self.n); v[self.known]=self.boundary(t)
        current=x[self.nd:self.nd+self.ni]; j,conv=self.injection(t,x)
        vf=self.ud@x[:self.nd]
        forcing=j[self.free]-self.gfk@v[self.known]-self.a[self.free]@current
        if self.ua.shape[1]:vf+=self.ua@lu_solve(self.gaa,self.ua.T@(forcing-self.gff@vf))
        v[self.free]=vf
        return v,current,j,conv

    def magnetic(self,current):
        u=current/self.isat; a=np.abs(u)
        logcosh=a+np.log1p(np.exp(-2*a))-np.log(2.)
        return float(.5*current@self.l@current+np.sum(self.sat*self.isat*(u*np.tanh(u)-logcosh)))

    def rhs(self,t,x,diagnostic=False):
        v,current,j,actual=self.fields(t,x); dx=np.zeros_like(x)
        forcing=j[self.free]-self.g[self.free]@v-self.a[self.free]@current-self.c[np.ix_(self.free,self.known)]@self.boundary(t,True)
        if self.nd:dx[:self.nd]=(self.ud.T@forcing)/self.ceig
        if self.ni:
            incremental=self.l+np.diag(self.sat/self.isat*(1.-np.tanh(current/self.isat)**2))
            dx[self.nd:self.nd+self.ni]=(lu_solve(self.linear_l,self.a.T@v-self.r*current) if self.linear_l is not None else np.linalg.solve(incremental,self.a.T@v-self.r*current))
        info={}
        for c in self.case.converters:
            sl=self.slices[c.id]; state=x[sl]; voltage=_space(v[[self.index[n] for n in c.nodes]])
            power=1.5*voltage*np.conj(actual[c.id]); limit=math.sqrt(2)*c.current_limit_rms_a
            if c.kind=='gfl':
                theta,xi,id_,iq,pf,qf=state; rot=voltage*np.exp(-1j*theta)
                error=rot.imag/max(abs(voltage),.1*math.sqrt(2)*c.nominal_ln_rms_v)
                correction=c.pll_kp_per_s*error+xi; cap=2*np.pi*c.frequency_limit_hz
                frequency=2*np.pi*c.frequency_hz+np.clip(correction,-cap,cap)
                xidot=c.pll_ki_per_s2*error if abs(correction)<cap or correction*error<0 else 0.
                reference=complex(c.active_ref_w,-c.reactive_ref_var)/(1.5*max(abs(voltage),.1*math.sqrt(2)*c.nominal_ln_rms_v))
                if not self.connected[c.id]:reference=0j
                reference*=min(1.,limit/max(abs(reference),np.finfo(float).tiny))
                dx[sl]=[frequency,xidot,(reference.real-id_)/c.current_tau_s,(reference.imag-iq)/c.current_tau_s,(power.real-pf)/c.power_tau_s,(power.imag-qf)/c.power_tau_s]
            else:
                theta,ia,ib,pf,qf=state
                nominal=np.clip(c.nominal_ln_rms_v-c.droop_v_per_var*(qf-c.reactive_ref_var),.2*c.nominal_ln_rms_v,1.5*c.nominal_ln_rms_v)
                frequency=2*np.pi*c.frequency_hz+np.clip(-c.droop_rad_s_per_w*(pf-c.active_ref_w),-2*np.pi*c.frequency_limit_hz,2*np.pi*c.frequency_limit_hz)
                reference=(math.sqrt(2)*nominal*np.exp(1j*theta)-voltage)/c.virtual_r_ohm
                if not self.connected[c.id]:reference=0j
                reference*=min(1.,limit/max(abs(reference),np.finfo(float).tiny))
                dx[sl]=[frequency,(reference.real-ia)/c.current_tau_s,(reference.imag-ib)/c.current_tau_s,(power.real-pf)/c.power_tau_s,(power.imag-qf)/c.power_tau_s]
            info[c.id]=dict(p_w=float(power.real),q_var=float(power.imag),frequency_hz=float(frequency/(2*np.pi)),current_vector_peak_a=abs(actual[c.id]),limited=abs(reference)>=limit*(1-1e-10),connected=self.connected[c.id])
        for drive in self.case.drives:
            rate,values=self.drives[drive.id].rates(t,_space(v[[self.index[n] for n in drive.nodes]]),x[self.slices[drive.id]],self.connected[drive.id])
            dx[self.slices[drive.id]]=rate;info[drive.id]=values
        for motor in self.case.motors:
            rate,values=self.motors[motor.id].rates(t,_space(v[[self.index[n] for n in motor.nodes]]),x[self.slices[motor.id]])
            dx[self.slices[motor.id]]=rate;info[motor.id]=values
        if not np.all(np.isfinite(dx)):raise ValueError('nonfinite phase-network derivative')
        if diagnostic:return dx,(v,current,j,info)
        return dx

    def initial(self):
        x=np.zeros(self.size); initial=np.array([self.case.initial_voltage_v.get(self.case.nodes[i],0.) for i in self.free])
        x[:self.nd]=self.ud.T@initial; x[self.nd:self.nd+self.ni]=[b.initial_current_a for b in self.case.inductors]
        for c in self.case.converters:
            state=[c.phase_rad,0.,*c.initial_currents,0.,0.] if c.kind=='gfl' else [c.phase_rad,*c.initial_currents,0.,0.]
            x[self.slices[c.id]]=state
        for name,drive in self.drives.items():x[self.slices[name]]=drive.initial()
        for name,motor in self.motors.items():x[self.slices[name]]=motor.initial()
        return x


def _sequence(values):
    a=np.exp(2j*np.pi/3); va,vb,vc=values
    positive=(va+a*vb+a*a*vc)/3; negative=(va+a*a*vb+a*vc)/3; zero=(va+vb+vc)/3
    return dict(positive_rms=abs(positive),negative_rms=abs(negative),zero_rms=abs(zero),negative_to_positive=abs(negative)/abs(positive) if abs(positive)>1e-12 else None)


def harmonic_study(case, orders):
    model=PhaseNetwork(case)
    if case.converters or case.drives or case.motors or case.events or any(b.saturation_flux_vs for b in case.inductors) or any(len(s.envelope)>1 or s.envelope[0][1]!=1 for s in case.sources):
        raise ValueError('harmonic superposition requires a linear stationary case with explicit injected spectra')
    if not orders or len(set(orders))!=len(orders) or any(type(k) is not int or not 1<=k<=100 for k in orders):raise ValueError('unique harmonic orders 1..100 required')
    frames=[]
    for order in sorted(orders):
        omega=2*np.pi*case.frequency_hz*order; y=model.g.astype(complex)+1j*omega*model.c
        z=np.diag(model.r).astype(complex)+1j*omega*model.l
        if model.ni:y+=model.a@np.linalg.solve(z,model.a.T)
        known=np.array([next((t.rms*np.exp(1j*t.phase_rad) for t in s.tones if t.order==order),0j) for s in case.sources])
        j=np.zeros(model.n,dtype=complex)
        for s in case.injections:j[model.index[s.node]]+=next((t.rms*np.exp(1j*t.phase_rad) for t in s.tones if t.order==order),0j)
        v=np.zeros(model.n,dtype=complex);v[model.known]=known
        try:v[model.free]=np.linalg.solve(y[np.ix_(model.free,model.free)],j[model.free]-y[np.ix_(model.free,model.known)]@known)
        except np.linalg.LinAlgError as exc:raise ValueError('singular harmonic network; floating component or ideal resonance') from exc
        currents=np.linalg.solve(z,model.a.T@v) if model.ni else np.zeros(0,dtype=complex)
        residual=(y@v-j)[model.free]
        frames.append(dict(order=order,voltage={n:[float(v[i].real),float(v[i].imag)] for i,n in enumerate(case.nodes)},inductor_current={b.id:[float(currents[i].real),float(currents[i].imag)] for i,b in enumerate(case.inductors)},
                           kcl_max_a=float(np.max(abs(residual),initial=0.)),sequences={k:_sequence([v[model.index[n]]-(v[model.index[case.sequence_neutrals[k]]] if case.sequence_neutrals.get(k,'0')!='0' else 0j) for n in group]) for k,group in case.sequence_monitors.items()}))
    thd={}
    for node in case.nodes:
        amplitudes={f['order']:abs(complex(*f['voltage'][node])) for f in frames}; fundamental=amplitudes.get(1,0.)
        thd[node]=math.sqrt(sum(v*v for k,v in amplitudes.items() if k!=1))/fundamental if fundamental>1e-12 else None
    return dict(completed=True,frames=frames,voltage_thd=thd,scope='declared finite harmonic orders, RMS cosine phasors; no nonlinear superposition')


def run_emt(case, *, duration_s, sample_s, max_step_s, rtol=1e-7, atol=1e-9, method='Radau'):
    if not all(math.isfinite(v) and v>0 for v in (duration_s,sample_s,max_step_s,rtol,atol)) or duration_s>10 or duration_s/sample_s>100_000 or method not in {'Radau','DOP853'}:
        raise ValueError('bounded positive EMT duration/grid/tolerances and Radau/DOP853 required')
    model=PhaseNetwork(case)
    if model.size==0:raise ValueError('time-domain study needs at least one dynamic state; use harmonic study for a static circuit')
    if any(e.time_s>=duration_s for e in case.events):raise ValueError('all switching events must be inside the study window')
    if any(t>duration_s for s in case.sources for t,_ in s.envelope):raise ValueError('envelope breakpoint exceeds the study window')
    scalars=(int(duration_s/sample_s)+2*len(case.events)+sum(len(s.envelope) for s in case.sources)+3)*(model.n+model.ni+16*(len(case.converters)+len(case.drives)+len(case.motors))+6)
    if scalars>2_000_000 or duration_s/max_step_s>200_000:raise ValueError('EMT sample/step budget exceeded')
    times=[];sides=[];channels={};event_log=[];energy=[];residuals=[];baseline=None
    x=np.r_[model.initial(),0.,0.];t0=0.;calls=0
    def evaluate(t,state):
        dx,(v,i,j,converters)=model.rhs(t,state[:model.size],True)
        dv=np.zeros(model.n);dv[model.known]=model.boundary(t,True);dv[model.free]=model.ud@dx[:model.nd]
        kcl=model.c@dv+model.g@v+model.a@i-j
        power=float(v[model.known]@kcl[model.known]+v@j)
        p_loss=float(v@model.g@v+np.sum(model.r*i*i))
        for drive in case.drives:
            values=converters[drive.id];power+=values['grid_power_w'];p_loss+=values['loss_w']+values['mechanical_power_w']
        for motor in case.motors:
            values=converters[motor.id];power+=values['motor_power_w'];p_loss+=values['loss_w']+values['mechanical_power_w']
        return np.r_[dx,power,p_loss],(v,i,j,converters,kcl)
    def rhs(t,state):
        nonlocal calls
        calls+=1
        if calls>250_000:raise RuntimeError('EMT derivative-evaluation budget exceeded')
        return evaluate(t,state)[0]
    def record(t,state,side):
        nonlocal baseline
        dx,(v,i,j,converters,kcl)=evaluate(t,state)
        residuals.append(float(np.max(abs(kcl[model.free]),initial=0.)))
        stored=float(.5*v@model.c@v+model.magnetic(i)+sum(converters[d.id]['stored_j'] for d in [*case.drives,*case.motors]))
        if baseline is None:baseline=stored
        energy.append(dict(stored_j=stored,entered_j=float(state[-2]),loss_j=float(state[-1]),residual_j=float(state[-2]-state[-1]-(stored-baseline))))
        row={**{n+'.voltage_v':float(v[k]) for k,n in enumerate(case.nodes)},**{b.id+'.current_a':float(i[k]) for k,b in enumerate(case.inductors)}}
        for cid,values in converters.items():
            for key,value in values.items():row[cid+'.'+key]=float(value)
        for key,value in row.items():channels.setdefault(key,[]).append(value)
        times.append(float(t));sides.append(side)
    boundaries=sorted({duration_s,*[e.time_s for e in case.events],*[t for s in case.sources for t,_ in s.envelope if t>0]})
    grid=np.arange(0,duration_s+sample_s*.1,sample_s);record(0.,x,'initial')
    for end in boundaries:
        events=[e for e in case.events if e.time_s==end]
        wanted=grid[(grid>t0+1e-14)&(grid<end-1e-14)].tolist()+[end]
        solution=solve_ivp(rhs,(t0,end),x,method=method,rtol=rtol,atol=atol,max_step=max_step_s,dense_output=True)
        if not solution.success:raise RuntimeError('EMT integration failed: '+solution.message)
        for t in wanted:record(t,solution.y[:,-1] if t==end else solution.sol(t),'pre_event' if t==end and events else 'regular')
        x=solution.y[:,-1]
        if events:
            model.context(end)
            for e in sorted(events,key=lambda e:(e.kind,e.equipment_id)):event_log.append(e.model_dump())
            record(end,x,'post_event')
        t0=end
    # Cosine phasors from a full final period and actual time stamps; duplicate event samples excluded.
    last=times[-1];indices=[k for k,t in enumerate(times) if last-1/case.frequency_hz-1e-12<=t<last-1e-12 and sides[k]!='pre_event']
    phasors={};sequences={}
    if len(indices)>=8:
        ts=np.array(times)[indices];design=np.column_stack([np.ones(len(ts)),np.cos(2*np.pi*case.frequency_hz*ts),np.sin(2*np.pi*case.frequency_hz*ts)])
        if np.ptp(ts)>.8/case.frequency_hz:
            for key,values in channels.items():
                if key.endswith(('.voltage_v','.current_a')):
                    coeff=np.linalg.lstsq(design,np.array(values)[indices],rcond=None)[0];phasors[key]=[float(coeff[1]/math.sqrt(2)),float(-coeff[2]/math.sqrt(2))]
            sequences={name:_sequence([complex(*phasors[n+'.voltage_v'])-(complex(*phasors[case.sequence_neutrals[name]+'.voltage_v']) if case.sequence_neutrals.get(name,'0')!='0' else 0j) for n in group]) for name,group in case.sequence_monitors.items()}
    max_i=max([0.,*[max(abs(v) for v in values) for key,values in channels.items() if key.endswith('.current_a')]])
    return dict(completed=True,extrema_scope='exported samples including event sides; not certified continuous extrema',phasor_projection_frequency_hz=case.frequency_hz,phasor_stationarity_assessed=False,time_s=times,sample_side=sides,channels=channels,event_log=event_log,final_phasors=phasors,sequences=sequences,
                kcl_max_a=max(residuals,default=0.),energy=energy,rhs_evaluations=calls,state_count=model.size,
                metrics=dict(max_branch_current_a=max_i,max_node_voltage_v=max(abs(v) for key,values in channels.items() if key.endswith('.voltage_v') for v in values),
                             final_energy_error_j=energy[-1]['residual_j']),
                scope='phase-domain index-1 lumped RLC with monotone magnetic saturation and averaged three-wire converter controls; no semiconductor switching or manufacturer qualification',
                energy_audit='source work and dissipation integrated as quadrature states; explicit magnetic energy for the monotone saturating law')


def periodic_initialization(case):
    """Linear frequency-domain→EMT bridge, including all declared AC tones."""
    orders=sorted({t.order for s in [*case.sources,*case.injections] for t in s.tones})
    if not orders or 0 in orders:raise ValueError('periodic bridge requires explicit AC tones and no DC offset')
    result=harmonic_study(case,orders);prepared=case.model_copy(deep=True)
    prepared.initial_voltage_v={n:sum(math.sqrt(2)*f['voltage'][n][0] for f in result['frames']) for n in case.nodes}
    for branch in prepared.inductors:branch.initial_current_a=sum(math.sqrt(2)*f['inductor_current'][branch.id][0] for f in result['frames'])
    prepared.provenance['periodic_initialization']='all declared AC harmonics; RMS cosine phasors reconstructed at t=0'
    return PhaseCase.model_validate(prepared.model_dump())
