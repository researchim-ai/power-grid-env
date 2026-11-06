"""Independent dense SI network + original motulator flux-state machines.

Does not import production topology/stamps, GammaWinding or FluxNetwork. The
native differential AC ports use motulator's original finite-difference Y;
its original (undifferentiated) KCL is monitored, never projected away.
"""
from pathlib import Path
import argparse, hashlib, json, math, re, zipfile
import numpy as np
from scipy.integrate import solve_ivp
from power_grid_env.build import NetBuilder
from power_grid_env.flux_machine import FluxMachinePassport,WindingThermal
from power_grid_env.flux_network import FluxNetworkSettings,run_flux_network
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint
from benchmarks.extended_equipment_validation import verify_native,axis,PUBLISHED
from benchmarks.motulator_motor_validation import WHEEL_SHA256

NAMES=('radial_pair','meshed_pair','meshed_thermal_sag','meshed_recovery','inductive_source','transformer_tap','transformer_mismatch','transformer_clock1','two_sources',
       'meshed_two_cages','meshed_four_cages_thermal','core_radial','meshed_core_thermal','transformer_core_loss','transformer_core_excitation','meshed_four_cages_core_thermal')
LIMITS=dict(current_a=3e-4,voltage_v=1e-3,flux_vs=3e-6,speed_rad_s=3e-4,torque_nm=3e-4,temperature_c=3e-5)
MAXIMUM_NATIVE_KCL_RELATIVE=1e-6


def example(name):
    if name not in NAMES:raise ValueError('unknown native network case')
    transformer=name.startswith('transformer_')
    b=NetBuilder('native-flux-'+name,'Authored network; published saturated Gamma windings',10. if transformer else .4,s_base_va=10000)
    b.node('grid');b.node('a',.4);b.node('b',.4)
    b.source('grid',.05 if not transformer else 1.,.1 if name=='inductive_source' else 0.)
    if transformer:
        b.transformer('grid','a',hv_kv=9. if name=='transformer_mismatch' else 10.,lv_kv=.38 if name=='transformer_mismatch' else .4,kva=50.,x_pct=5.,r_pct=1.,vector_group='Dyn1' if name=='transformer_clock1' else 'Dyn11',hv_ground='not_applicable',lv_ground='solid',transformer_id='tx')
    else:b.line_z1('grid','a',.12+.2j,line_id='l1')
    b.line_z1('a','b',.09+.17j,line_id='l2')
    if name.startswith('meshed'):b.line_z1('grid','b',.2+.3j,line_id='tie')
    if name=='two_sources':b.source('b',.07,.08,source_id='reserve')
    b.load('b',100.,80.,model='constant_z',load_id='rl-load')
    motors=[]
    for id_,node,beta in [('m1','a',.84),('m2','b',.65)]:
        motors.append(FluxMachinePassport(id=id_,node_id=node,stator_r_ohm=3.7,unsaturated_h=.34,saturation_beta_per_vs=beta,
            cages=[dict(resistance_ohm=2.5,leakage_h=.023)],pole_pairs=2,inertia_kg_m2=.03,
            fan_nm_per_rad_s2=14.6/(math.pi*50)**2,
            provenance=dict(electrical=PUBLISHED,magnetics=PUBLISHED if beta==.84 else 'Authored perturbation of published saturation coefficient',mechanical='Authored J=0.03 kg m2 and fan-load stress')))
    events=[]
    if name in {'meshed_thermal_sag','meshed_four_cages_thermal','meshed_core_thermal','meshed_four_cages_core_thermal'}:
        motors[0].thermal=WindingThermal(stator_capacity_j_per_k=10.,rotor_capacity_j_per_k=8.,stator_ambient_w_per_k=1.,rotor_ambient_w_per_k=1.,cross_w_per_k=.2)
        motors[0].provenance['thermal']='Authored accelerated thermal benchmark, not OEM heat capacities'
        events=[dict(time_s=.025,source_id='source',voltage_pu=.45),dict(time_s=.045,source_id='source',voltage_pu=1.)]
    for p in motors:
        if name in {'meshed_two_cages','meshed_four_cages_thermal','meshed_four_cages_core_thermal'}:
            from power_grid_env.flux_machine import CageWinding
            p.cages=[CageWinding(resistance_ohm=2.5+j,leakage_h=.023+.011*j) for j in range(2 if name=='meshed_two_cages' else 4)]
            p.provenance['extra_cages']='Authored passive cage spectrum; not measured OEM skin-depth data'
        if 'core' in name:
            p.core_loss_s=.001;p.provenance['core_loss']='Published motulator constant eddy-current conductance equations; Gc=0.001 S is authored stress data, not a measured passport'
    if name=='meshed_recovery':events=[dict(time_s=.12,source_id='source',voltage_pu=0.),dict(time_s=.2,source_id='source',voltage_pu=1.)]
    net=b.build()
    if transformer:net.transformers[0].windings[0].tap_pu=1.025
    if name=='transformer_core_excitation':
        net.transformers[0].no_load_loss_pct=.5;net.transformers[0].imag_pct=2.
        b.load('a',200.,0.,model='constant_z',load_id='resistive')
        addition=b.build();net.loads=addition.loads;net.terminals=addition.terminals
    if name=='two_sources':net.sources[1].angle_a_rad=.05
    return net,FluxNetworkSettings(motors=motors,duration_s=.4 if name=='meshed_recovery' else .06,step_s=.0005,events=events)


def _real_matrix(matrix):
    out=np.empty((2*matrix.shape[0],2*matrix.shape[1]))
    out[::2,::2]=matrix.real;out[1::2,1::2]=matrix.real
    out[::2,1::2]=-matrix.imag;out[1::2,::2]=matrix.imag
    return out


def _complex(vector):return vector[::2]+1j*vector[1::2]
def _vector(z):return np.column_stack([np.asarray(z).real,np.asarray(z).imag]).ravel()
def _bus(node):return 'bus_'+hashlib.sha256(node.encode()).hexdigest()[:16]


class NativeCircuit:
    def __init__(self,network,settings):
        from motulator.drive.model import InductionMachine,InductionMachinePars,MechanicalSystem
        # This parser deliberately covers a bounded unswitched three-phase profile.
        if any([network.switches,network.generators,network.shunts,network.inverters,network.storages,network.study_faults,network.harmonics,network.omissions]):raise ValueError('native reference requires fixed RL network')
        self.network=network;self.settings=settings;self.w=2*math.pi*network.frequency_hz
        nodes={n.id:n for n in network.connectivity_nodes};self.names=sorted(nodes);self.count=len(nodes)
        self.index={n:i for i,n in enumerate(self.names)};term={t.id:self.index[t.connectivity_node_id] for t in network.terminals}
        nominal={v.id:v.nominal_ll_v for v in network.voltage_levels};levels={n.id:nominal[n.voltage_level_id] for n in nodes.values()}
        columns=[];resistance=[];inductance=[];self.branch_ids=[];self.passive_conductance=[]
        def add(id_,left,right,r,l,tap=1+0j):
            if r<0 or l<=0:raise ValueError('positive native RL branch required')
            col={left:1/np.conj(tap)}
            if right is not None:col[right]=col.get(right,0)-1
            columns.append(col);resistance.append(r);inductance.append(l);self.branch_ids.append(id_)
        def positive(a):
            a=np.array(a,float)
            if a.shape!=(3,3) or not np.allclose(a,a.T,atol=1e-12,rtol=0) or not np.allclose(a,np.roll(np.roll(a,1,0),1,1),atol=1e-12,rtol=0):raise ValueError('native balanced phase matrix required')
            return float(a[0,0]-a[0,1])
        for line in network.lines:
            if np.any(np.asarray(line.b_siemens)!=0):raise ValueError('native line capacitance is unsupported')
            r=positive(line.r_ohm)
            if line.temperature_c is not None:r*=1+line.resistance_alpha_per_c*(line.temperature_c-20.)
            add(line.id,term[line.terminal_from],term[line.terminal_to],r,positive(line.x_ohm)/self.w)
        for tx in network.transformers:
            if len(tx.windings)!=2 or ((tx.imag_pct or tx.no_load_loss_pct) and not getattr(self,'conductive_rows',False)):raise ValueError('native linear two-winding leakage only')
            # The clock gives angle(LV/HV)=-clock*pi/6; tap is HV/LV.
            a,b=tx.windings;clock=int(re.search(r'(\d+)$',tx.vector_group)[1]);angle=clock*math.pi/6
            tap=a.nominal_ll_v/b.nominal_ll_v*a.tap_pu/b.tap_pu*np.exp(1j*angle)
            zbase=b.nominal_ll_v**2/a.rated_va*b.tap_pu**2/100
            r=tx.load_loss_pct if a.r_pct is None else a.r_pct+b.r_pct
            add(tx.id,term[a.terminal_id],term[b.terminal_id],r*zbase,tx.x_hl_pct*zbase/self.w,tap)
            idle=tx.imag_pct or 0.;loss=tx.no_load_loss_pct or 0.
            if getattr(self,'conductive_rows',False) and (idle or loss):
                if idle<loss:raise ValueError('invalid native transformer idle power')
                rated=a.nominal_ll_v*a.tap_pu;g=a.rated_va*loss/100/rated**2
                if g:self.passive_conductance.append((tx.id+'.core',term[a.terminal_id],g))
                q=a.rated_va/100*math.sqrt(idle**2-loss**2)
                if q:add('@magnetizing:'+tx.id,term[a.terminal_id],None,0.,rated**2/(self.w*q))
        for load in network.loads:
            p=[load.p_w.get(k,0.) for k in 'ABC'];q=[load.q_var.get(k,0.) for k in 'ABC']
            if load.model!='constant_z' or not np.allclose(p,p[0]) or not np.allclose(q,q[0]) or q[0]<0:raise ValueError('native inductive balanced load required')
            node=next(t.connectivity_node_id for t in network.terminals if t.id==load.terminal_id)
            if q[0]==0:
                if not getattr(self,'conductive_rows',False) or sum(p)<=0:raise ValueError('resistive load requires native conductive profile')
                self.passive_conductance.append((load.id,term[load.terminal_id],sum(p)/levels[node]**2));continue
            z=levels[node]**2/complex(sum(p),-sum(q));add(load.id,term[load.terminal_id],None,z.real,z.imag/self.w)
        self.sources=[];self.scales={s.id:1. for s in network.sources}
        for source in network.sources:
            if source.phase_pu is not None or source.participation is not None or not source.is_slack or source.r_ohm<0 or source.x_ohm<0:
                raise ValueError('native balanced voltage-source passport required')
            if source.x_ohm>0:
                boundary=len(self.names);self.names.append('@source:'+source.id);branch_index=len(columns)
                add('@impedance:'+source.id,boundary,term[source.terminal_id],source.r_ohm,source.x_ohm/self.w)
            else:boundary=term[source.terminal_id];branch_index=None
            self.sources.append((source,boundary,branch_index))
        if not self.sources or len({b for _,b,_ in self.sources})!=len(self.sources):raise ValueError('native distinct source boundary buses required')
        self.B=np.zeros((len(self.names),len(columns)),complex)
        for j,column in enumerate(columns):
            for i,value in column.items():self.B[i,j]=value
        self.r=np.array(resistance);self.l=np.array(inductance)
        self.k0=_real_matrix((self.B/self.l)@self.B.conj().T)
        self.fixed=np.array([2*b+d for _,b,_ in self.sources for d in [0,1]],int)
        self.free=np.array([i for i in range(2*len(self.names)) if i not in set(self.fixed)],int)
        self.interior=np.array([i for i in range(len(self.names)) if 2*i not in set(self.fixed)],int)
        self.entries=[];offset=2*len(columns)
        for p in settings.motors:
            if len(p.cages)>1:
                from benchmarks.multicage_flux_reference import CageFluxMachine
            cage=p.cages[0]
            native=InductionMachine(InductionMachinePars(n_p=p.pole_pairs,R_s=p.stator_r_ohm,R_r=cage.resistance_ohm,L_ell=cage.leakage_h,
                L_s=lambda rho,p=p:p.unsaturated_h/(1+(p.saturation_beta_per_vs*rho)**p.saturation_exponent)))
            if len(p.cages)>1:native=CageFluxMachine(p)
            mechanical=MechanicalSystem(p.inertia_kg_m2,B_L=lambda w,p=p:p.fan_nm_per_rad_s2*abs(w)+p.viscous_nm_per_rad_s)
            self.entries.append((p,self.index[p.node_id],offset,native,mechanical));offset+=2*(len(p.cages)+1)+1+(2 if p.thermal else 0)
        self.dimension=offset

    def initial(self):
        x=np.zeros(self.dimension)
        for p,_,a,_,_ in self.entries:
            speed=a+2*(1+len(p.cages));x[speed]=p.initial_speed_rad_s
            if p.thermal:x[speed+1:speed+3]=p.thermal.ambient_c
        return x

    def quantities(self,t,x):
        edge=_complex(x[:2*len(self.l)]);outgoing=self.B@edge;k=self.k0.copy();rhs=_vector(self.B@(self.r/self.l*edge));ports=[]
        for p,b,a,m,mechanical in self.entries:
            speed=a+2*(1+len(p.cages));m.state.psi_s_ab=complex(*x[a:a+2]);rotors=_complex(x[a+2:speed])
            m.state.psi_r_ab=rotors[0] if len(p.cages)==1 else rotors;m.inp.w_M=x[speed]
            if p.thermal:
                m.par.R_s=p.stator_r_ohm*(1+p.thermal.alpha_per_c*(x[speed+1]-p.thermal.reference_c))
                m.par.R_r=(p.cages[0].resistance_ohm if len(p.cages)==1 else np.array([c.resistance_ohm for c in p.cages]))*(1+p.thermal.alpha_per_c*(x[speed+2]-p.thermal.reference_c))
            port=m.compute_ac_port();outgoing[b]+=port.i;ports.append(port)
            k[2*b:2*b+2,2*b:2*b+2]+=port.Y;rhs[2*b:2*b+2]+=port.Y@np.array([port.e.real,port.e.imag])
        v=np.zeros(2*len(self.names))
        for source,b,branch in self.sources:
            emf=source.ll_v*math.sqrt(2/3)*self.scales[source.id]*np.exp(1j*(self.w*t+source.angle_a_rad))
            value=emf-source.r_ohm*outgoing[b] if branch is None else emf;v[2*b:2*b+2]=[value.real,value.imag]
        if len(self.free):v[self.free]=np.linalg.solve(k[np.ix_(self.free,self.free)],rhs[self.free]-k[np.ix_(self.free,self.fixed)]@v[self.fixed])
        voltage=_complex(v);kcl=max(abs(outgoing[self.interior]),default=0.)/max(1.,max(abs(outgoing),default=0.),max(abs(edge),default=0.))
        return edge,voltage,ports,float(kcl),outgoing

    def rhs(self,t,x):
        edge,v,ports,_,_=self.quantities(t,x);dx=np.zeros_like(x)
        dx[:2*len(self.l)]=_vector((self.B.conj().T@v-self.r*edge)/self.l)
        for (p,b,a,m,mechanical),port in zip(self.entries,ports):
            speed=a+2*(1+len(p.cages));m.inp.u_s_ab=v[b];m.set_outputs(t);ds,dr=m.rhs(t)
            mechanical.state.w_M=complex(x[speed]);mechanical.inp.tau_M=m.out.tau_M;_,dw=mechanical.rhs(t)
            dx[a:speed]=_vector(np.r_[ds,dr]);dx[speed]=0. if p.locked_rotor else float(np.real(dw))
            if p.thermal:
                h=p.thermal;ts,tr=x[speed+1:speed+3];cross=h.cross_w_per_k*(ts-tr)
                rotor=m.compute_outputs(m.state,m.inp)[1]
                dx[speed+1]=(1.5*m.par.R_s*abs(port.i)**2-h.stator_ambient_w_per_k*(ts-h.ambient_c)-cross)/h.stator_capacity_j_per_k
                dx[speed+2]=(1.5*np.sum(m.par.R_r*abs(rotor)**2)-h.rotor_ambient_w_per_k*(tr-h.ambient_c)+cross)/h.rotor_capacity_j_per_k
        return dx

    def outputs(self,t,x):
        edge,v,ports,kcl,outgoing=self.quantities(t,x);row={}
        for i,node in enumerate(self.names[:self.count]):
            row[_bus(node)+'.voltage_alpha_v']=v[i].real;row[_bus(node)+'.voltage_beta_v']=v[i].imag
        for id_,current in zip(self.branch_ids,edge):row[id_+'.current_alpha_a']=current.real;row[id_+'.current_beta_a']=current.imag
        for source,b,branch in self.sources:
            current=outgoing[b] if branch is None else edge[branch];row[source.id+'.current_alpha_a']=current.real;row[source.id+'.current_beta_a']=current.imag
        for (p,b,a,m,_),port in zip(self.entries,ports):
            speed=a+2*(1+len(p.cages));m.inp.u_s_ab=v[b];m.set_outputs(t);ir=np.atleast_1d(m.compute_outputs(m.state,m.inp)[1])
            for key,value in dict(current_alpha_a=port.i.real,current_beta_a=port.i.imag,
                stator_flux_alpha_vs=x[a],stator_flux_beta_vs=x[a+1],speed_rad_s=x[speed],torque_nm=m.out.tau_M,
                stator_copper_w=1.5*m.par.R_s*abs(port.i)**2,rotor_copper_w=1.5*np.sum(m.par.R_r*abs(ir)**2)).items():row[p.id+'.'+key]=float(value)
            for j,current in enumerate(ir,1):
                row[f'{p.id}.cage{j}_current_alpha_a']=current.real;row[f'{p.id}.cage{j}_current_beta_a']=current.imag
                row[f'{p.id}.cage{j}_flux_alpha_vs']=x[a+2*j];row[f'{p.id}.cage{j}_flux_beta_vs']=x[a+2*j+1]
            if p.thermal:row[p.id+'.stator_temperature_c']=x[speed+1];row[p.id+'.rotor_temperature_c']=x[speed+2]
        return row,kcl


def reference(network,settings,*,rtol=2e-11,max_step=.0001):
    if any(p.core_loss_s for p in settings.motors):
        from benchmarks.core_flux_reference import NativeCoreCircuit
        circuit=NativeCoreCircuit(network,settings)
    else:circuit=NativeCircuit(network,settings)
    state=circuit.initial();times=axis(settings);states={0.:state.copy()}
    events={}
    for event in settings.events:events.setdefault(event.time_s,[]).append(event)
    boundaries=[0.,*sorted(events),settings.duration_s]
    for left,right in zip(boundaries,boundaries[1:]):
        grid=[t for t in times if left<t<=right]
        # A positive core conductance introduces a fast physical RL decay.
        # Use an independently implemented stiff method, not an unstable explicit
        # reference step and not a suppressed overflow on rejected trial states.
        sol=solve_ivp(circuit.rhs,(left,right),state,method='BDF' if any(p.core_loss_s for p in settings.motors) else 'DOP853',rtol=rtol,atol=rtol/20,max_step=max_step,t_eval=grid)
        if not sol.success or not np.isfinite(sol.y).all():raise ValueError('native network integration failed')
        states.update(zip(sol.t,sol.y.T.copy()));state=sol.y[:,-1].copy()
        for event in events.get(right,[]):circuit.scales[event.source_id]=event.voltage_pu
    channels={};maximum_kcl=0.
    for t in times:
        circuit.scales={s.id:1. for s in network.sources}
        for event in sorted(settings.events,key=lambda e:(e.time_s,e.source_id)):
            if event.time_s<=t:circuit.scales[event.source_id]=event.voltage_pu
        row,kcl=circuit.outputs(t,states[t]);maximum_kcl=max(maximum_kcl,kcl)
        if not channels:channels={k:[] for k in row}
        for key,value in row.items():channels[key].append(float(value))
    if maximum_kcl>MAXIMUM_NATIVE_KCL_RELATIVE:raise ValueError('native original KCL exceeds declared uncertainty')
    return dict(completed=True,time_s=times,channels=channels,channel_inventory=sorted(channels),maximum_kcl_relative=maximum_kcl,original_motulator_rhs_and_ac_port=all(len(p.cages)==1 and not p.core_loss_s for p in settings.motors),
        native_core_loss_original_rhs=any(p.core_loss_s for p in settings.motors) and all(len(p.cages)==1 for p in settings.motors),
        component_origins={p.id:('original_motulator_0.19.0' if len(p.cages)==1 else 'independently_authored_multicage_flux_equations') for p in settings.motors},
        dense_independent_network=True,flux_state_coordinates=True,thermal_parameters_authored=True,rtol=rtol,max_step_s=max_step,
        integration_method='BDF' if any(p.core_loss_s for p in settings.motors) else 'DOP853')


def compare(primary,native):
    if not primary.get('completed') or not native.get('completed') or not native.get('channels'):raise ValueError('complete nonempty traces required')
    if sorted(native['channels'])!=native['channel_inventory']:raise ValueError('native channel inventory differs')
    if not 0<=native['maximum_kcl_relative']<=MAXIMUM_NATIVE_KCL_RELATIVE:raise ValueError('invalid native KCL')
    if len(native['time_s'])<2 or not np.isfinite(native['time_s']).all() or np.any(np.diff(native['time_s'])<=0):raise ValueError('invalid native axis')
    if not np.isfinite(primary['time_s']).all() or np.any(np.diff(primary['time_s'])<0):raise ValueError('invalid primary axis')
    index={t:i for i,t in enumerate(primary['time_s'])}
    if set(index)!=set(native['time_s']):raise ValueError('network sample axes differ')
    errors={};limits={}
    for key,wanted in native['channels'].items():
        if key not in primary['channels']:raise ValueError('missing network channel '+key)
        all_values=np.asarray(primary['channels'][key])
        if all_values.shape!=(len(primary['time_s']),) or not np.isfinite(all_values).all():raise ValueError('invalid primary channel')
        actual=np.array([all_values[index[t]] for t in native['time_s']]);wanted=np.array(wanted)
        if actual.shape!=wanted.shape or not np.isfinite(actual).all() or not np.isfinite(wanted).all():raise ValueError('invalid network trace')
        if 'copper' in key or 'core_loss' in key or key.endswith('.loss_w'):limit=.02
        else:
            kind='voltage_v' if 'voltage' in key else 'current_a' if 'current' in key else 'flux_vs' if 'flux' in key else 'temperature_c' if 'temperature' in key else 'torque_nm' if 'torque' in key else 'speed_rad_s';limit=LIMITS[kind]
        errors[key]=float(max(abs(actual-wanted)));limits[key]=limit
    return dict(passed=all(errors[k]<=limits[k] for k in errors),errors=errors,limits=limits)


def run(out,wheel):
    out=Path(out)
    if out.exists() and any(out.iterdir()):raise ValueError('fresh output directory required')
    native_sources=verify_native(wheel);code=package_fingerprint();producer=sha256_file(Path(__file__))
    out.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(wheel) as z:(out/'motulator-LICENSE').write_bytes(z.read('motulator-0.19.0.dist-info/licenses/LICENSE'))
    write_json(out/'protocol.json',dict(cases={n:dict(network=example(n)[0].model_dump(mode='json'),settings=example(n)[1].model_dump(mode='json')) for n in NAMES},
        limits=LIMITS,copper_loss_limit_w=.02,maximum_native_kcl_relative=MAXIMUM_NATIVE_KCL_RELATIVE,wheel_sha256=WHEEL_SHA256,native_source_sha256=native_sources,
        code_sha256=code,producer_sha256=producer))
    write_json(out/'summary.json',dict(passed=False,status='running'));checks=[]
    for name in NAMES:
        print(name,flush=True);network,settings=example(name);native=reference(network,settings);write_json(out/name/'native.json',native)
        primary=run_flux_network(network,settings);write_json(out/name/'primary.json',primary);check=compare(primary,native);write_json(out/name/'comparison.json',check);checks.append(check)
    if code!=package_fingerprint() or producer!=sha256_file(Path(__file__)):raise ValueError('producer changed during native network run')
    result=dict(passed=all(c['passed'] for c in checks),status='complete',cases=len(checks),code_sha256=code,producer_sha256=producer,
        independently_authored_multi_cage=True,native_core_loss_original_rhs_available=True,independent_host=False,industrial_release_qualified=False,
        artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='summary.json'})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);p.add_argument('--wheel',required=True,type=Path);a=p.parse_args();r=run(a.out,a.wheel);print(json.dumps(r,indent=2));raise SystemExit(0 if r['passed'] else 1)
