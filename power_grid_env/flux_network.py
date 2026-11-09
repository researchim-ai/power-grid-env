"""Full-winding balanced RL network with saturated, electrothermal Gamma motors.

Current coordinates preserve the original linear KCL constraints. Nodal
voltages follow from differentiated KCL and differential winding inductances;
no artificial capacitance or grounding conductance is inserted. This declared
profile excludes interior pure-resistance loads, line capacitance and switching.
"""
import math
import numpy as np
from scipy.integrate import solve_ivp
from scipy.sparse import csc_matrix, bmat, diags
from scipy.sparse.linalg import splu
from pydantic import BaseModel,ConfigDict,Field,model_validator,model_serializer
from power_grid_env.flux_machine import FluxMachinePassport,GammaWinding
from power_grid_env.runtime_limits import RuntimeLimits,IntegrationBudget,RuntimeLimitExceeded
from power_grid_env.topology import compile_topology
from power_grid_env.model import canonical_hash,validate_network_rules
from power_grid_env.rms_study import positive_sequence,transformer_parameters
from power_grid_env.evidence import package_fingerprint


class FluxSourceEvent(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    time_s: float=Field(gt=0)
    source_id: str
    voltage_pu: float=Field(ge=0,le=2)


from power_grid_env.flux_initialization import FluxOperatingPoint


class FluxNetworkSettings(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    operating_point: FluxOperatingPoint | None=None
    motors: list[FluxMachinePassport]=Field(min_length=1,max_length=16)
    duration_s: float=Field(default=.5,gt=0,le=5)
    step_s: float=Field(default=.00025,gt=0)
    rtol: float=Field(default=2e-9,gt=0,le=1e-6)
    atol: float=Field(default=2e-11,gt=0,le=1e-8)
    maximum_kcl_relative_residual: float=Field(default=1e-7,gt=0,le=1e-5)
    maximum_energy_relative_residual: float=Field(default=2e-6,gt=0,le=1e-4)
    events: list[FluxSourceEvent]=Field(default_factory=list,max_length=64)
    runtime_limits: RuntimeLimits=Field(default_factory=RuntimeLimits)

    @model_serializer(mode='wrap')
    def canonical_initialization(self,handler):
        data=handler(self)
        if self.operating_point is None:data.pop('operating_point',None)
        return data

    @model_validator(mode='after')
    def bounded(self):
        if self.operating_point and any(p.thermal or p.core_loss_s or p.initial_speed_rad_s!=0 for p in self.motors):raise ValueError('periodic initialization requires zero passport speed and no thermal/core-loss states')
        if self.step_s>self.duration_s or math.ceil(self.duration_s/self.step_s)>100000:
            raise ValueError('flux network sampling budget')
        if len({m.id for m in self.motors})!=len(self.motors):raise ValueError('duplicate flux motor ID')
        if any(e.time_s>=self.duration_s for e in self.events):raise ValueError('flux source event outside horizon')
        if len({(e.time_s,e.source_id) for e in self.events})!=len(self.events):raise ValueError('conflicting simultaneous flux source events')
        return self


def _balanced(matrix):
    a=np.asarray(matrix,float)
    return a.shape==(3,3) and np.isfinite(a).all() and np.allclose(a,a.T,rtol=0,atol=1e-12) and np.allclose(a,np.roll(np.roll(a,1,0),1,1),rtol=0,atol=1e-12)


class FluxNetwork:
    def __init__(self,network,settings):
        self.network=network;self.settings=settings;self.factorizations=0;errors=validate_network_rules(network)
        if type(self) is FluxNetwork and any(p.core_loss_s for p in settings.motors):raise ValueError('positive core loss requires the flux-coordinate CoreFluxNetwork profile')
        if errors:raise ValueError('; '.join(errors))
        if any([network.switches,network.generators,network.inverters,network.storages,network.shunts,network.study_faults,network.harmonics,network.omissions]):
            raise ValueError('flux profile accepts fixed RL topology, voltage sources, RL loads, two-winding leakage transformers and Gamma motors')
        self.topology=compile_topology(network);self.names=sorted(self.topology.buses);self.node_count=len(self.names)
        if not 1<=self.node_count<=min(64,settings.runtime_limits.maximum_network_buses):raise ValueError('flux network supports 1..64 buses within runtime limits')
        if any(not set('ABC')<=set(bus.phases) for bus in self.topology.buses.values()):raise ValueError('all flux buses must carry three balanced phases')
        self.lookup={n:i for i,n in enumerate(self.names)};wb=2*math.pi*network.frequency_hz
        if not 0<network.frequency_hz<=1000:raise ValueError('flux frequency range')
        self.wb=wb;columns=[];resistance=[];inductance=[];branch_ids=[]
        def branch(id_,a,b,r,l,tap=1+0j):
            if r<0 or l<=0 or not np.isfinite([r,l,tap.real,tap.imag]).all() or tap==0:raise ValueError('finite nonnegative resistance and positive inductance required')
            col={a:1/np.conj(tap)}
            if b is not None:col[b]=col.get(b,0)-1
            columns.append(col);resistance.append(r);inductance.append(l);branch_ids.append(id_)
        for line in network.lines:
            if not all(_balanced(x) for x in [line.r_ohm,line.x_ohm,line.b_siemens]) or np.any(np.asarray(line.b_siemens)!=0):
                raise ValueError('balanced 3-wire RL lines without shunt capacitance required')
            a,b=[self.lookup[self.topology.terminal_bus[t]] for t in [line.terminal_from,line.terminal_to]]
            if self.topology.buses[self.names[a]].nominal_ll_v!=self.topology.buses[self.names[b]].nominal_ll_v:raise ValueError('line cannot change voltage base')
            r=positive_sequence(line.r_ohm).real
            if (line.temperature_c is None)!=(line.resistance_alpha_per_c is None):raise ValueError('line temperature requires resistance coefficient')
            if line.temperature_c is not None:r*=1+line.resistance_alpha_per_c*(line.temperature_c-20.)
            branch(line.id,a,b,r,positive_sequence(line.x_ohm).real/wb)
        for tr in network.transformers:
            p=self.transformer_parameters(tr)
            a,b=[self.lookup[self.topology.terminal_bus[w.terminal_id]] for w in tr.windings]
            v1=self.topology.buses[self.names[a]].nominal_ll_v;v2=self.topology.buses[self.names[b]].nominal_ll_v
            first,second=tr.windings
            tap=first.nominal_ll_v/second.nominal_ll_v*first.tap_pu/second.tap_pu*np.exp(1j*p['phi'])
            zb=second.nominal_ll_v**2/first.rated_va*second.tap_pu**2/100
            resistance_pct=tr.load_loss_pct if first.r_pct is None else first.r_pct+second.r_pct
            branch(tr.id,a,b,resistance_pct*zb,tr.x_hl_pct*zb/wb,tap)
            self.transformer_excitation(tr,a,branch)
        for load in network.loads:
            p=np.array([load.p_w.get(k,0.) for k in 'ABC']);q=np.array([load.q_var.get(k,0.) for k in 'ABC'])
            if load.model!='constant_z' or not np.allclose(p,p[0]) or not np.allclose(q,q[0]) or p[0]<0 or q[0]<0:
                raise ValueError('background load must be balanced positive-inductance constant-Z RL')
            bus=self.lookup[self.topology.terminal_bus[load.terminal_id]];v=self.topology.buses[self.names[bus]].nominal_ll_v
            if q[0]==0:
                self.resistive_load(load,bus,sum(p)/v**2);continue
            z=v*v/complex(sum(p),-sum(q));branch(load.id,bus,None,z.real,z.imag/wb)
        self.sources=[];source_buses=set()
        if not network.sources or len(network.sources)>8:raise ValueError('one to eight voltage sources required')
        for source in network.sources:
            if not source.is_slack or source.participation is not None or source.phase_pu is not None or source.r_ohm<0 or source.x_ohm<0 or source.ll_v<=0:
                raise ValueError('balanced nonnegative source impedance required')
            bus=self.lookup[self.topology.terminal_bus[source.terminal_id]]
            if source.x_ohm>0:
                virtual=len(self.names);self.names.append('@source:'+source.id)
                branch('@impedance:'+source.id,virtual,bus,source.r_ohm,source.x_ohm/wb)
                boundary=virtual;branch_index=len(columns)-1
            else:boundary=bus;branch_index=None
            if boundary in source_buses:raise ValueError('duplicate source boundary bus')
            source_buses.add(boundary);self.sources.append((source,boundary,branch_index))
        if len(columns)>128:raise ValueError('flux branch limit 128')
        all_ids=[item.id for group in [network.sources,network.lines,network.loads,network.transformers] for item in group]
        if any(id_.startswith('@') for id_ in all_ids+[p.id for p in settings.motors]):raise ValueError('flux equipment IDs reserve the @ prefix for virtual branches')
        if len(set(all_ids))!=len(all_ids) or len(set(branch_ids))!=len(branch_ids) or len(set(self.names))!=len(self.names):raise ValueError('unique network equipment and virtual branch identities required')
        self.R=np.asarray(resistance);self.L=np.asarray(inductance);self.branch_ids=branch_ids
        if not np.isfinite(1/self.L).all():raise ValueError('unrepresentable reciprocal branch inductance')
        self.B=csc_matrix((len(self.names),len(columns)),dtype=complex).tolil()
        for j,col in enumerate(columns):
            for i,value in col.items():self.B[i,j]=value
        self.B=self.B.tocsc();self.boundary=np.array(sorted(source_buses),dtype=int);self.interior=np.array([i for i in range(len(self.names)) if i not in source_buses],dtype=int)
        self.motors=[];self.motor_buses=[];self.addresses=[];offset=2*len(columns)
        ids={x.id for group in [network.lines,network.loads,network.transformers,network.sources] for x in group}
        for motor in settings.motors:
            if motor.id in ids or motor.node_id not in self.topology.node_to_bus:raise ValueError('invalid flux motor identity/node')
            ids.add(motor.id);bus=self.lookup[self.topology.node_to_bus[motor.node_id]]
            count=2*(len(motor.cages)+1);self.addresses.append((slice(offset,offset+count),offset+count,offset+count+1 if motor.thermal else None));offset+=count+1+(2 if motor.thermal else 0)
            self.motors.append(GammaWinding(motor));self.motor_buses.append(bus)
        self.dynamic_end=offset;self.dimension=offset+5
        if self.dimension>512:raise ValueError('flux dynamic-state limit 512')
        if any(e.source_id not in {s.id for s,_,_ in self.sources} for e in settings.events):raise ValueError('unknown flux event source')
        self.source_scales={s.id:1. for s,_,_ in self.sources}
        kc=self.B@diags(1/self.L)@self.B.conj().T if len(self.L) else csc_matrix((len(self.names),len(self.names)),dtype=complex)
        self.K0=bmat([[kc.real,-kc.imag],[kc.imag,kc.real]],format='csc');self.linear=all(m.p.saturation_beta_per_vs==0 for m in self.motors);self.cached_lu=None
        self.free=np.r_[self.interior,self.interior+len(self.names)];self.fixed=np.r_[self.boundary,self.boundary+len(self.names)]
        # Reject electrically floating unused graph components before integration.
        self.quantities(0.,self.initial_state())

    @staticmethod
    def complex_values(values):return values[::2]+1j*values[1::2]

    def transformer_parameters(self,tr):return transformer_parameters(tr,self.topology,self.network.s_base_va)
    def transformer_excitation(self,tr,bus,branch):pass
    def resistive_load(self,load,bus,conductance):raise ValueError('pure-resistance load requires a conductive flux-coordinate profile')

    def initial_state(self):
        if hasattr(self,'_periodic_initial_state'):return self._periodic_initial_state.copy()
        x=np.zeros(self.dimension)
        for model,(_,speed,temperature) in zip(self.motors,self.addresses):
            x[speed]=model.p.initial_speed_rad_s
            if temperature is not None:x[temperature:temperature+2]=model.p.thermal.ambient_c
        return x

    def quantities(self,time,state):
        n=len(self.names);edge=self.complex_values(state[:2*len(self.L)]);outgoing=np.asarray(self.B@edge).ravel();rows=[]
        for model,bus,(a,speed,temp) in zip(self.motors,self.motor_buses,self.addresses):
            current=self.complex_values(state[a]);temperature=state[temp:temp+2] if temp is not None else None
            q=model.quantities(current,state[speed],temperature);rows.append((current,temperature,q));outgoing[bus]+=current[0]
        voltage=np.zeros(n,complex);source_values=[]
        for source,bus,edge_index in self.sources:
            emf=source.ll_v*math.sqrt(2/3)*self.source_scales[source.id]*np.exp(1j*(self.wb*time+source.angle_a_rad))
            voltage[bus]=emf-source.r_ohm*outgoing[bus] if edge_index is None else emf
            source_values.append((source,emf,bus,edge_index))
        rhs=np.asarray(self.B@(self.R/self.L*edge)).ravel() if len(self.L) else np.zeros(n,complex)
        K=self.K0.tolil(copy=True) if not self.linear or self.cached_lu is None else None
        for bus,(_,_,q) in zip(self.motor_buses,rows):
            rhs[bus]-=complex(*q['offset'])
            if K is not None:
                for i in range(2):
                    for j in range(2):K[bus+i*n,bus+j*n]+=q['Y'][i,j]
        vr=np.r_[voltage.real,voltage.imag];rr=np.r_[rhs.real,rhs.imag]
        if len(self.free):
            if K is not None:
                K=K.tocsc();lu=splu(K[self.free,:][:,self.free]);self.factorizations+=1;coupling=K[self.free,:][:,self.fixed]
                if self.linear:self.cached_lu=(lu,coupling)
            else:lu,coupling=self.cached_lu
            vr[self.free]=lu.solve(rr[self.free]-coupling@vr[self.fixed]);voltage=vr[:n]+1j*vr[n:]
        source_power=source_resistor=0.;source_currents={}
        for source,emf,bus,edge_index in source_values:
            current=outgoing[bus] if edge_index is None else edge[edge_index]
            source_currents[source.id]=current;source_power+=1.5*(emf*np.conj(current)).real
            if edge_index is None:source_resistor+=1.5*source.r_ohm*abs(current)**2
        kcl=outgoing[self.interior]
        kcl_relative=float(max(abs(kcl),default=0.)/max(1.,max(abs(outgoing),default=0.),max(abs(edge),default=0.)))
        return edge,rows,voltage,float(source_power),float(source_resistor),source_currents,kcl_relative

    def rhs(self,time,state):
        edge,rows,v,pin,rsource,_,_=self.quantities(time,state);derivative=np.zeros_like(state)
        if len(self.L):
            di=(np.asarray(self.B.conj().T@v).ravel()-self.R*edge)/self.L
            derivative[:2*len(self.L):2]=di.real;derivative[1:2*len(self.L):2]=di.imag
        mechanical=ambient=copper=0.
        for model,bus,(a,speed,temp),(current,temperature,q) in zip(self.motors,self.motor_buses,self.addresses,rows):
            di,dw,dt,ha=model.derivatives(current,state[speed],temperature,v[bus],q)
            derivative[a.start:a.stop:2]=di.real;derivative[a.start+1:a.stop:2]=di.imag;derivative[speed]=dw
            if temp is not None:derivative[temp:temp+2]=dt
            else:copper+=q['stator_copper_w']+q['rotor_copper_w']
            mechanical+=model.p.fan_nm_per_rad_s2*abs(state[speed])**3+model.p.viscous_nm_per_rad_s*state[speed]**2;ambient+=ha
        derivative[self.dynamic_end:]=[pin,rsource+1.5*float(sum(self.R*abs(edge)**2)),mechanical,ambient,copper]
        if not np.isfinite(derivative).all():raise ValueError('nonfinite flux-network derivatives')
        return derivative

    def stored_energy(self,state,quantities=None):
        edge,rows,*_=self.quantities(0.,state) if quantities is None else quantities
        energy=.75*float(sum(self.L*abs(edge)**2))
        for model,(_,speed,temp),(_,_,q) in zip(self.motors,self.addresses,rows):
            energy+=q['energy_j']+.5*model.p.inertia_kg_m2*state[speed]**2
            if temp is not None:
                h=model.p.thermal;energy+=h.stator_capacity_j_per_k*(state[temp]-h.ambient_c)+h.rotor_capacity_j_per_k*(state[temp+1]-h.ambient_c)
        return float(energy)

    def numerical_jacobian(self,time,state,rhs):
        """Bounded central differences; energy quadratures have exact zero columns.

        SciPy's adaptive numerical-Jacobian factors can grow without bound on
        structurally zero accumulator columns during long coast/recovery runs.
        This local approximation never adapts those factors or perturbs an
        accumulator. Every physical RHS evaluation still passes the work budget.
        """
        matrix=np.zeros((self.dimension,self.dimension))
        relative_step=np.cbrt(np.finfo(float).eps)
        for column in range(self.dynamic_end):
            step=relative_step*max(1.,abs(state[column]))
            plus=state.copy();minus=state.copy();plus[column]+=step;minus[column]-=step
            matrix[:,column]=(rhs(time,plus)-rhs(time,minus))/(plus[column]-minus[column])
        if not np.isfinite(matrix).all():raise ValueError('nonfinite flux numerical Jacobian')
        return csc_matrix(matrix)


def run_flux_network(network,settings):
    budget=IntegrationBudget(settings.runtime_limits);stage='network_scope'
    try:
        if any(p.core_loss_s for p in settings.motors):
            from power_grid_env.core_flux_network import CoreFluxNetwork
            circuit=CoreFluxNetwork(network,settings)
        else:circuit=FluxNetwork(network,settings)
        if settings.operating_point:
            stage='periodic_initialization'
            from power_grid_env.flux_initialization import initialize_periodic
            initialize_periodic(circuit)
    except (ValueError,ArithmeticError,RuntimeError) as exc:
        return dict(completed=False,solved=False,status='initialization_failed' if stage=='periodic_initialization' else 'rejected',reason=stage if stage=='periodic_initialization' else 'flux_network_scope',detail=str(exc),code_sha256=package_fingerprint())
    state=circuit.initial_state();initial_energy=circuit.stored_energy(state);channels={};times=[];sides=[];max_kcl=0.;max_energy=0.;nfev=njev=nlu=0
    def values(time,x):
        quantities=circuit.quantities(time,x);edge,rows,voltage,pin,source_resistor,source_currents,kcl=quantities
        energy=circuit.stored_energy(x,quantities);entered,line_loss,mechanical,ambient,copper=x[circuit.dynamic_end:]
        residual=entered-line_loss-mechanical-ambient-copper-(energy-initial_energy)
        error=abs(residual)/max(1.,abs(entered),abs(energy),abs(initial_energy))
        result=dict(source_power_w=pin,stored_energy_j=energy,entered_energy_j=entered,external_resistance_energy_j=line_loss,mechanical_load_energy_j=mechanical,
                    ambient_heat_j=ambient,external_copper_energy_j=copper,energy_balance_j=residual,energy_relative_residual=error,kcl_relative_residual=kcl)
        if any(p.core_loss_s for p in settings.motors):
            result['external_winding_and_core_loss_energy_j']=result.pop('external_copper_energy_j')
        for i,name in enumerate(circuit.names[:circuit.node_count]):
            result[name+'.voltage_alpha_v']=voltage[i].real;result[name+'.voltage_beta_v']=voltage[i].imag
        for id_,current in zip(circuit.branch_ids,edge):result[id_+'.current_alpha_a']=current.real;result[id_+'.current_beta_a']=current.imag
        for id_,current in source_currents.items():result[id_+'.current_alpha_a']=current.real;result[id_+'.current_beta_a']=current.imag
        if hasattr(circuit,'additional_flux_channels'):result.update(circuit.additional_flux_channels(voltage))
        for model,(_,speed,temp),(current,temperature,q) in zip(circuit.motors,circuit.addresses,rows):
            p=model.p;prefix=p.id+'.'
            row=dict(current_alpha_a=current[0].real,current_beta_a=current[0].imag,phase_a_a=current[0].real,
                     phase_b_a=-.5*current[0].real+math.sqrt(3)/2*current[0].imag,phase_c_a=-.5*current[0].real-math.sqrt(3)/2*current[0].imag,
                     current_amplitude_rms_equivalent_a=abs(current[0])/math.sqrt(2),speed_rad_s=x[speed],torque_nm=q['torque_nm'],
                     stator_flux_alpha_vs=q['psi'].real,stator_flux_beta_vs=q['psi'].imag,magnetic_energy_j=q['energy_j'],
                     stator_copper_w=q['stator_copper_w'],rotor_copper_w=q['rotor_copper_w'],stator_resistance_ohm=q['rs'])
            for j,(ir,psi,rr) in enumerate(zip(current[1:],q['rotor_flux'],q['rr'])):
                row.update({f'cage{j+1}_current_alpha_a':ir.real,f'cage{j+1}_current_beta_a':ir.imag,f'cage{j+1}_flux_alpha_vs':psi.real,f'cage{j+1}_flux_beta_vs':psi.imag,f'cage{j+1}_resistance_ohm':rr})
            if temp is not None:row.update(stator_temperature_c=temperature[0],rotor_temperature_c=temperature[1])
            if p.core_loss_s:row['core_loss_w']=q['core_loss_w']
            for key,value in row.items():result[prefix+key]=value
        return result,kcl,float(error)
    def record(time,x,side):
        nonlocal max_kcl,max_energy
        budget.check_time();row,kcl,error=values(time,x)
        if not channels:channels.update({key:[] for key in row})
        if channels.keys()!=row.keys() or not np.isfinite(list(row.values())).all():raise ValueError('invalid flux output inventory')
        if kcl>settings.maximum_kcl_relative_residual or error>settings.maximum_energy_relative_residual:
            raise ValueError('original KCL or total-energy residual exceeded its declared limit')
        budget.record(len(row)+len(x)+3);times.append(float(time));sides.append(side);max_kcl=max(max_kcl,kcl);max_energy=max(max_energy,error)
        for key,value in row.items():channels[key].append(float(value))
    events={}
    for event in settings.events:events.setdefault(event.time_s,[]).append(event)
    boundaries=[0.,*sorted(events),settings.duration_s];regular=np.arange(math.ceil(settings.duration_s/settings.step_s))*settings.step_s;event_log=[]
    try:
        row,_,_=values(0.,state);planned=math.ceil(settings.duration_s/settings.step_s)+2*len(events)+2
        if planned*(len(row)+len(state)+3)>settings.runtime_limits.maximum_retained_scalars:
            raise RuntimeLimitExceeded('retained_scalars',settings.runtime_limits.maximum_retained_scalars,planned*(len(row)+len(state)+3))
        record(0.,state,'initial')
        for left,right in zip(boundaries,boundaries[1:]):
            grid=np.r_[regular[(regular>left+1e-13)&(regular<right-1e-13)],right]
            rhs=budget.rhs(circuit.rhs)
            sol=solve_ivp(rhs,(left,right),state,method='Radau',rtol=settings.rtol,atol=settings.atol,
                          jac=lambda time,x:circuit.numerical_jacobian(time,x,rhs),
                          max_step=min(settings.step_s,1/(64*network.frequency_hz)),t_eval=grid)
            nfev+=sol.nfev;njev+=sol.njev;nlu+=sol.nlu
            if not sol.success or not np.isfinite(sol.y).all():raise ValueError('flux network nonlinear integration failed')
            for time,x in zip(sol.t,sol.y.T):record(time,x,'pre_event' if time==right and right in events else 'regular')
            state=sol.y[:,-1].copy()
            if right in events:
                budget.event_batch()
                for event in sorted(events[right],key=lambda e:e.source_id):
                    circuit.source_scales[event.source_id]=event.voltage_pu;event_log.append(dict(time_s=right,kind='source_voltage',equipment_id=event.source_id,value=event.voltage_pu,outcome='applied'))
                record(right,state,'post_event')
            del sol
        budget.check_time()
    except RuntimeLimitExceeded as exc:
        return dict(completed=False,solved=True,status='runtime_limit_exceeded',reason=exc.resource,runtime_budget=budget.report(),event_log=event_log,code_sha256=package_fingerprint())
    except (ValueError,ArithmeticError,RuntimeError) as exc:
        return dict(completed=False,solved=True,status='numerically_not_converged',detail=str(exc),runtime_budget=budget.report(),event_log=event_log,code_sha256=package_fingerprint())
    core=any(p.core_loss_s for p in settings.motors)
    return dict(**({'initialization':circuit.initialization} if settings.operating_point else {}),completed=True,solved=True,status='completed',model='balanced_RL_full_winding_Gamma_network',network_sha256=canonical_hash(network),code_sha256=package_fingerprint(),
                settings=settings.model_dump(mode='json'),time_s=times,sample_side=sides,channels=channels,event_log=event_log,
                maximum_kcl_relative_residual=max_kcl,maximum_energy_relative_residual=max_energy,runtime_budget=budget.report(),
                integration=dict(method='Radau_full_winding_flux_coordinates_with_core_loss' if core else 'Radau_full_winding_current_coordinates',nfev=nfev,njev=njev,nlu=nlu,states=circuit.dimension,network_sparse_factorizations=circuit.factorizations,original_KCL_preserved=True,artificial_grounding_or_capacitance=False,
                                 jacobian='bounded_central_difference_dynamic_columns_exact_zero_quadratures'),
                scope=dict(balanced_alpha_beta_peak_SI=True,stator_dynamics_preserved=True,core_loss_model='constant_eddy_current_conductance' if core else False,continuous_skin_depth_model=False,breaker_transients=False,
                           universal_EMT=False,field_validated=False,industrial_release_qualified=False))
