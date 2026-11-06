"""Independent SI MNA winding circuit on a balanced multivoltage network.

No production topology compiler, transformer stamps, motor constants or RHS.
Only schema values and the separately implemented SI hybrid controller are used.
Initial scope: cold, connected fan motors, passive lines/two-winding transformers,
constant-impedance backgrounds and one finite-impedance external source.
"""
import math
import numpy as np
from scipy.linalg import lu_factor,lu_solve
from scipy.optimize import root
from scipy.sparse import lil_matrix,diags,issparse,block_diag,csc_matrix
from scipy.sparse.linalg import splu
from benchmarks.independent_group_contactor import run as hybrid

CLOCKS={'YNyn0':0,'Dyn11':11,'YNd1':1,'Dyn5':5,'YNd5':5}


class Circuit:
    def __init__(self,spec,*,initialize=True,maximum_duration_s=3.,maximum_nodes=64,sparse_network=False,maximum_motors=32):
        n=spec.network;s=spec.induction
        if spec.study!='motor_start' or spec.backend!='scipy' or s is None or spec.coupled:raise ValueError('independent multibus motor_start/scipy required')
        if type(maximum_nodes) is not int or not 2<=maximum_nodes<=2048 or (maximum_nodes>64 and not sparse_network):raise ValueError('bounded sparse reference node profile required')
        self.sparse_network=bool(sparse_network)
        if type(maximum_motors) is not int or not 1<=maximum_motors<=128:raise ValueError('bounded independent motor inventory required')
        if len(n.sources)!=1 or not 1<=len(s.motors)<=maximum_motors or not 2<=len(n.connectivity_nodes)<=maximum_nodes:raise ValueError('bounded multibus circuit required')
        if any([n.switches,n.generators,n.inverters,n.storages,n.shunts,n.neutral_grounds,n.study_faults,n.harmonics,n.omissions]):raise ValueError('unsupported independent multibus equipment')
        if any(m.mechanical_load for m in s.motors):raise ValueError('fan motors required')
        if s.operating_point and any(not m.connected for m in s.motors):raise ValueError('loaded disconnected motor equilibrium outside independent multibus scope')
        if not math.isfinite(maximum_duration_s) or not 0<maximum_duration_s<=60 or not 0<s.duration_s<=maximum_duration_s or not math.isfinite(s.step_s) or s.step_s<=0:raise ValueError('bounded multibus horizon/output required')
        self.spec=spec;self.n=n;self.s=s;self.wb=2*math.pi*n.frequency_hz
        if not math.isfinite(self.wb) or self.wb<=0 or not math.isfinite(n.s_base_va) or n.s_base_va<=0:raise ValueError('positive frequency and system base required')
        self.nodes={node.id:i for i,node in enumerate(n.connectivity_nodes)};self.N=len(self.nodes)
        levels={v.id:v.nominal_ll_v for v in n.voltage_levels}
        self.base=np.array([levels[node.voltage_level_id]/math.sqrt(3) for node in n.connectivity_nodes])
        if not np.isfinite(self.base).all() or np.any(self.base<=0):raise ValueError('positive finite node voltage bases required')
        self.term={t.id:t.connectivity_node_id for t in n.terminals}
        if any(sorted(t.phases)!=list('ABC') for t in [*n.connectivity_nodes,*n.terminals]):raise ValueError('balanced ABC nodes and ports required')
        self.Y=lil_matrix((self.N,self.N),dtype=complex) if self.sparse_network else np.zeros((self.N,self.N),complex);self.branches=[]
        for line in n.lines:
            if list(line.phases)!=list('ABC'):raise ValueError('ABC line required')
            r,x=np.array(line.r_ohm),np.array(line.x_ohm)
            for a in (r,x):
                expected=np.full((3,3),a[0,1]);np.fill_diagonal(expected,a[0,0])
                if not np.allclose(a,expected,atol=1e-12,rtol=0):raise ValueError('unbalanced line')
            if np.any(line.b_siemens):raise ValueError('line charging outside independent scope')
            a,b=[self.nodes[self.term[t]] for t in (line.terminal_from,line.terminal_to)]
            if self.base[a]!=self.base[b]:raise ValueError('line voltage base mismatch')
            self.branch(line.id,a,b,complex(r[0,0]-r[0,1],x[0,0]-x[0,1]),1.)
        for t in n.transformers:
            if len(t.windings)!=2 or t.phases!=3 or t.ltc or t.imag_pct!=0 or t.no_load_loss_pct!=0 or t.vector_group not in CLOCKS:raise ValueError('unsupported independent transformer')
            h,l=t.windings
            if any(not math.isfinite(value) or value<=0 for w in (h,l) for value in (w.tap_pu,w.nominal_ll_v,w.rated_va)):raise ValueError('positive finite transformer winding data required')
            expected=('delta','wye') if t.vector_group.startswith('D') else ('wye','delta') if 'd' in t.vector_group else ('wye','wye')
            if (h.connection,l.connection)!=expected:raise ValueError('transformer connections disagree with clock group')
            if any(w.phase is not None or w.angle_deg!=0 for w in (h,l)) or h.rated_va!=l.rated_va:raise ValueError('unsupported transformer winding data')
            if (h.r_pct is None)!=(l.r_pct is None):raise ValueError('both winding resistances required')
            resistance=t.load_loss_pct if h.r_pct is None else h.r_pct+l.r_pct
            z=complex(resistance,t.x_hl_pct)/100*h.nominal_ll_v**2/h.rated_va*h.tap_pu**2
            ratio=l.nominal_ll_v*l.tap_pu/(h.nominal_ll_v*h.tap_pu)*np.exp(-1j*CLOCKS[t.vector_group]*math.pi/6)
            self.branch(t.id,self.nodes[self.term[h.terminal_id]],self.nodes[self.term[l.terminal_id]],z,ratio)
        for load in n.loads:
            if load.model!='constant_z' or load.opendss_model not in (None,2) or load.rated_kv is not None or load.cold_load_multiplier is not None or load.cold_load_tau_s is not None:raise ValueError('constant-Z backgrounds only')
            if any(set(v)!=set('ABC') or not np.isfinite(list(v.values())).all() for v in (load.p_w,load.q_var)) or sum(load.p_w.values())<0:raise ValueError('finite passive background power required')
            if sorted(load.phases)!=list('ABC') or any(not np.allclose(list(v.values()),list(v.values())[0],atol=1e-8,rtol=0) for v in (load.p_w,load.q_var)):raise ValueError('balanced load required')
            i=self.nodes[self.term[load.terminal_id]]
            self.Y[i,i]+=complex(sum(load.p_w.values()),-sum(load.q_var.values()))/(3*self.base[i]**2)
        src=n.sources[0];self.source=self.nodes[self.term[src.terminal_id]];self.zs=complex(src.r_ohm,src.x_ohm)
        if (src.phase_pu is not None or src.participation is not None or not src.is_slack or self.zs.real<0 or self.zs.imag<=0
                or not np.isfinite([src.ll_v,src.angle_a_rad,src.r_ohm,src.x_ohm]).all() or src.ll_v<=0):raise ValueError('balanced finite-impedance slack required')
        self.es=src.ll_v/math.sqrt(3)*np.exp(1j*src.angle_a_rad);self.Y[self.source,self.source]+=1/self.zs
        reached={self.source}
        for _ in self.nodes:
            for _,a,b,_,_ in self.branches:
                if a in reached or b in reached:reached.update((a,b))
        if len(reached)!=self.N:raise ValueError('independent multibus island or isolated node')
        self.p=[];K=0;dim=0
        for m in s.motors:
            if m.thermal and not (s.operating_point and s.operating_point.thermal_state=='equilibrium') and m.thermal.initial_level>=m.thermal.trip_level:raise ValueError('initial thermal latch outside multibus scope')
            if m.node_id not in self.nodes or not math.isclose(m.rated_ll_v/math.sqrt(3),self.base[self.nodes[m.node_id]],rel_tol=1e-10):raise ValueError('motor voltage base mismatch')
            if m.contactor and m.contactor.sense_node_id not in self.nodes:raise ValueError('unknown sensing node')
            zb=m.rated_ll_v**2/m.rated_va;lm=m.magnetizing_x_pu*zb/self.wb
            rr=np.array([m.rotor_r_pu]+([m.rotor2_r_pu] if m.model=='double_cage' else []))*zb
            leakage=np.array([m.rotor_x_pu]+([m.rotor2_x_pu] if m.model=='double_cage' else []))*zb/self.wb
            k=len(rr);mutual=m.rotor_mutual_x_pu*zb/self.wb if k==2 else 0.
            self.p.append(dict(bus=self.nodes[m.node_id],rs=m.stator_r_pu*zb,lm=lm,lss=lm+m.stator_x_pu*zb/self.wb,rr=rr,L=(lm+mutual)*np.ones((k,k))+np.diag(leakage),rotor=slice(K,K+k),state=slice(dim,dim+2*k),speed=dim+2*k,ibase=m.rated_va/(math.sqrt(3)*m.rated_ll_v),vbase=m.rated_ll_v/math.sqrt(3)))
            K+=k;dim+=2*k+1
        self.K=K;self.M=len(s.motors);self.thermal={i:dim+j for j,i in enumerate(i for i,m in enumerate(s.motors) if m.thermal)};self.dimension=dim+len(self.thermal)
        self.state=np.zeros(self.dimension)
        for i,at in self.thermal.items():self.state[at]=s.motors[i].thermal.initial_level
        self.cfg=dict(connected=np.array([m.connected for m in s.motors],dtype=bool),latched=np.zeros(self.M,dtype=bool),scale=1.,load_scale=np.ones(self.M))
        for event in s.events:
            allowed=[src.id] if event.kind=='source_voltage' else [m.id for m in s.motors] if event.kind in ('motor_open','motor_close','load_torque_scale') else []
            if event.equipment_id not in allowed or not 0<event.time_s<s.duration_s:raise ValueError('unsupported independent multibus event')
        self.generator=None;self.generator_slice=slice(self.dimension,self.dimension)
        self.rebuild();self.initialization=dict(method='cold_prescribed_flux_and_speed')
        if initialize and s.operating_point:self.initialize_equilibrium()

    def branch(self,name,a,b,z,ratio):
        if z.real<0 or z.imag<=0 or abs(ratio)==0 or not np.isfinite([z,ratio]).all():raise ValueError('passive finite branch required')
        y=1/z;self.Y[a,a]+=y;self.Y[a,b]-=y/ratio;self.Y[b,a]-=y/np.conj(ratio);self.Y[b,b]+=y/abs(ratio)**2
        self.branches.append((name,a,b,z,ratio))

    def generator_ports(self):
        return [(self.generator_node,self.generator,self.generator_slice)] if self.generator else []

    def generator_closed(self,j):return True

    def rebuild(self):
        shape=(self.N+self.M+self.K+len(self.generator_ports()),)*2
        if self.sparse_network:
            # LIL slice assignment of a sparse NxN block calls toarray().
            # Assemble the passive and winding blocks without that hidden N^2 copy.
            A=block_diag((self.Y.tocsc(),csc_matrix((shape[0]-self.N,shape[1]-self.N),dtype=complex)),format='lil')
        else:
            A=np.zeros(shape,complex);A[:self.N,:self.N]=self.Y
        for i,p in enumerate(self.p):
            row=self.N+i;rr=slice(self.N+self.M+p['rotor'].start,self.N+self.M+p['rotor'].stop)
            if self.cfg['connected'][i]:
                A[p['bus'],row]=1.;A[row,p['bus']]=-1.;A[row,row]=p['rs']+1j*self.wb*p['lss'];A[row,rr]=1j*self.wb*p['lm']
            else:A[row,row]=1.
            A[rr,row]=self.wb*p['lm'];A[rr,rr]=self.wb*p['L']
        for j,(node,g,_) in enumerate(self.generator_ports()):
            row=self.N+self.M+self.K+j
            A[node,row]=-1.
            if self.generator_closed(j):A[row,node]=1.;A[row,row]=g.z
            else:A[row,row]=1.
        # Diagonal scaling retains SI equations while conditioning MV/LV MNA.
        self.row_scale=abs(A).tocsr().max(axis=1).toarray().ravel() if self.sparse_network else np.max(abs(A),axis=1)
        if not np.isfinite(self.row_scale).all() or np.any(self.row_scale<=0):raise ValueError('singular independent MNA row')
        self.lu=splu((diags(1/self.row_scale)@A).tocsc()) if self.sparse_network else lu_factor(A/self.row_scale[:,None]);self.cache_x=None
        self.factorizations=getattr(self,'factorizations',0)+1
        self.matrix_nnz=int(A.nnz if self.sparse_network else np.count_nonzero(A))

    def electrical(self,x):
        if self.cache_x is not None and np.array_equal(x,self.cache_x):return self.cache
        rhs=np.zeros(self.N+self.M+self.K+len(self.generator_ports()),complex);rhs[self.source]=self.es*self.cfg['scale']/self.zs
        for p in self.p:
            f=x[p['state']].reshape(-1,2);rhs[self.N+self.M+p['rotor'].start:self.N+self.M+p['rotor'].stop]=self.wb*(f[:,0]+1j*f[:,1])
        for j,(_,g,at) in enumerate(self.generator_ports()):rhs[self.N+self.M+self.K+j]=g.emf(x[at]) if self.generator_closed(j) else 0j
        self.linear_solves=getattr(self,'linear_solves',0)+1
        solved=self.lu.solve(rhs/self.row_scale) if self.sparse_network else lu_solve(self.lu,rhs/self.row_scale);v=solved[:self.N];st=solved[self.N:self.N+self.M];rot=solved[self.N+self.M:self.N+self.M+self.K]
        self.generator_currents=solved[self.N+self.M+self.K:]
        if self.generator:self.generator_current=self.generator_currents[0]
        torque=np.array([-3*self.wb*p['lm']*(sum(rot[p['rotor']])*np.conj(st[i])).imag/m.rated_va for i,(m,p) in enumerate(zip(self.s.motors,self.p))])
        self.cache_x=x.copy();self.cache=(v,st,rot,torque);return self.cache

    def rhs(self,t,x):
        v,st,rot,torque=self.electrical(x);out=np.zeros(self.dimension)
        for i,(m,p) in enumerate(zip(self.s.motors,self.p)):
            flux=x[p['state']].reshape(-1,2);flux=flux[:,0]+1j*flux[:,1]
            derivative=-p['rr']*rot[p['rotor']]-1j*self.wb*(1-x[p['speed']])*flux
            out[p['state']]=np.column_stack([derivative.real,derivative.imag]).ravel()
            out[p['speed']]=(torque[i]-self.cfg['load_scale'][i]*m.fan_torque_pu*x[p['speed']]*abs(x[p['speed']]))/(2*m.inertia_s)
            if i in self.thermal:out[self.thermal[i]]=((abs(st[i])/p['ibase'])**2-x[self.thermal[i]])/m.thermal.time_constant_s
        return out

    def steady_response(self,speeds,*,pv_node=None,pv_voltage=None,cold=False):
        """Physical rotor steady state and linear network, independently of EMF constants."""
        admittances=[];responses=[]
        for i,(p,w) in enumerate(zip(self.p,speeds)):
            if cold:response=-p['lm']*np.linalg.solve(p['L'],np.ones(len(p['rr'])))
            else:response=np.linalg.solve(np.diag(p['rr'])+1j*self.wb*(1-w)*p['L'],-1j*self.wb*(1-w)*p['lm']*np.ones(len(p['rr'])))
            admittances.append(1/(p['rs']+1j*self.wb*p['lss']+1j*self.wb*p['lm']*sum(response)) if self.cfg['connected'][i] else 0j);responses.append(response)
        Y=self.Y.copy();drive=np.zeros(self.N,complex);drive[self.source]=self.es/self.zs
        for p,admittance in zip(self.p,admittances):Y[p['bus'],p['bus']]+=admittance
        if pv_node is None:voltage=self.network_solve(Y,drive);ig=0j
        else:
            other=np.array([i for i in range(self.N) if i!=pv_node]);voltage=np.empty(self.N,complex);voltage[pv_node]=pv_voltage
            voltage[other]=self.network_solve(Y[np.ix_(other,other)],drive[other]-np.asarray(Y[other,pv_node].toarray() if issparse(Y) else Y[other,pv_node]).ravel()*pv_voltage)
            ig=complex(np.asarray(Y[pv_node]@voltage).item()-drive[pv_node])
        currents=np.array([voltage[p['bus']]*admittance for p,admittance in zip(self.p,admittances)])
        rotor=[response*current for response,current in zip(responses,currents)]
        flux=[p['lm']*current+p['L']@ir for p,current,ir in zip(self.p,currents,rotor)]
        torque=np.array([-3*self.wb*p['lm']*(sum(ir)*np.conj(current)).imag/m.rated_va for m,p,ir,current in zip(self.s.motors,self.p,rotor,currents)])
        return voltage,currents,flux,torque,ig

    def physical_balance(self,v,current):
        residual=np.asarray(self.Y@v).ravel();residual[self.source]-=self.es*self.cfg['scale']/self.zs
        for p,i in zip(self.p,current):residual[p['bus']]+=i
        generated=0j
        for j,(node,g,at) in enumerate(self.generator_ports()):
            residual[node]-=self.generator_currents[j];generated+=3*v[node]*np.conj(self.generator_currents[j])
        source=3*v[self.source]*np.conj((self.es*self.cfg['scale']-v[self.source])/self.zs)
        passive=np.asarray(self.Y@v).ravel();passive[self.source]-=v[self.source]/self.zs
        demand=sum(3*v[p['bus']]*np.conj(i) for p,i in zip(self.p,current))
        balance=(source+generated-demand-np.sum(3*v*np.conj(passive)))/self.n.s_base_va
        return dict(kcl_pu=float(max(abs(residual)*3*self.base/self.n.s_base_va)),power_balance_pu=float(abs(balance)))

    def network_solve(self,matrix,rhs):
        return splu(matrix.tocsc()).solve(np.asarray(rhs).ravel()) if issparse(matrix) else np.linalg.solve(matrix,rhs)

    def install_motor_point(self,speeds,response,cold=False):
        voltage,currents,flux,_,_=response
        for p,w,f in zip(self.p,speeds,flux):
            self.state[p['speed']]=w
            self.state[p['state']]=0. if cold else np.column_stack([f.real,f.imag]).ravel()
        if self.s.operating_point and self.s.operating_point.thermal_state=='equilibrium':
            for i,at in self.thermal.items():self.state[at]=(abs(currents[i])/self.p[i]['ibase'])**2
        for i,at in self.thermal.items():
            if self.state[at]>=self.s.motors[i].thermal.trip_level:raise ValueError('independent initial equilibrium thermal latch outside scope')
        self.rebuild()

    def initialize_equilibrium(self):
        def residual(w):
            torque=self.steady_response(w)[3]
            return torque-np.array([m.fan_torque_pu*speed*abs(speed) for m,speed in zip(self.s.motors,w)])
        solved=root(residual,np.full(self.M,.99),tol=1e-11)
        error=float(max(abs(residual(solved.x))))
        if error>1e-9 or not np.isfinite(solved.x).all() or np.any(solved.x<self.s.operating_point.minimum_speed_pu) or np.any(solved.x>1.):raise ValueError('independent multibus high-speed equilibrium failed')
        self.install_motor_point(solved.x,self.steady_response(solved.x))
        derivative=self.rhs(0.,self.state);motor_error=float(max(abs(derivative[:self.p[-1]['speed']+1])))
        thermal_error=max((abs(derivative[i]) for i in self.thermal.values()),default=0.)
        if motor_error>1e-8 or (self.s.operating_point.thermal_state=='equilibrium' and thermal_error>1e-8):raise ValueError('independent multibus initial RHS not zero')
        self.initialization=dict(method='independent_SI_multibus_high_speed_root',maximum_torque_residual_pu=error,maximum_initial_rhs=float(max(abs(derivative))),maximum_motor_initial_rhs=motor_error,maximum_initial_thermal_rhs=float(thermal_error),speeds_pu=solved.x.tolist())

    def set_context(self,cfg):
        self.cfg={k:v.copy() if isinstance(v,np.ndarray) else v for k,v in cfg.items()};self.rebuild()

    def sensed(self,x,i):
        at=self.nodes[self.s.motors[i].contactor.sense_node_id]
        return abs(self.electrical(x)[0][at])/self.base[at]

    def run(self,report):
        segments,changes,trips,controls=hybrid(settings=self.s,state=self.state.copy(),rhs=self.rhs,voltage=self.sensed,
            heating=lambda x:np.array([(abs(i)/p['ibase'])**2 for i,p in zip(self.electrical(x)[1],self.p)]),
            thermal_indices=self.thermal,get_context=lambda:self.cfg,set_context=self.set_context,event_handler=getattr(self,'handle_event',None),extra_guards=getattr(self,'extra_guards',None),extra_apply=getattr(self,'extra_apply',None),after_commands=getattr(self,'after_commands',None),**getattr(self,'hybrid_options',{}))
        sides=report['sample_side'];times=report['time_s'];groups=[t for t,side in zip(times,sides) if side=='pre_event']
        alignment_audit=None
        if getattr(self,'allow_commuting_controller_alignment',False):
            from benchmarks.controller_reference_bounds import align_commuting_groups
            alignment,alignment_audit=align_commuting_groups(groups,changes,report['event_log'],controls['event_log'],[e.time_s for e in self.s.events])
        else:
            if len(groups)!=len(changes) or any(abs(a-b)>1e-6 for a,b in zip(groups,changes)):
                error=ValueError('independent multibus event groups mismatch')
                error.group_diagnostic=dict(primary_groups=groups,independent_groups=changes,independent_event_log=controls['event_log'])
                raise error
            alignment=dict(zip(groups,changes))
        channels={};node_voltage={node:[] for node in self.nodes};balance_audit=[]
        for time,side in zip(times,sides):
            at=alignment.get(time,time) if side in ('pre_event','post_event') else time
            choices=[seg for seg in segments if seg[0]<=at<=seg[1]]
            if not choices:raise ValueError('independent multibus sample outside segments')
            left,right,dense,connected,scale,load_scale,snapshot=choices[0] if side=='pre_event' else choices[-1]
            self.set_context(dict(connected=connected,latched=snapshot['latched'],scale=scale,load_scale=load_scale,**snapshot.get('extra_context',{})))
            x=dense(at);v,st,rot,torque=self.electrical(x)
            if hasattr(self,'sample_audit'):balance_audit.append(self.sample_audit(v,st))
            for node,i in self.nodes.items():node_voltage[node].append(complex(v[i]))
            for i,(m,p) in enumerate(zip(self.s.motors,self.p)):
                flux=x[p['state']].reshape(-1,2);flux=flux[:,0]+1j*flux[:,1]
                emf=1j*self.wb*(p['lm']*np.linalg.solve(p['L'],np.ones(len(p['rr']))))@flux/p['vbase']
                power=3*v[p['bus']]*np.conj(st[i])
                row=dict(speed_pu=x[p['speed']],e_real_pu=emf.real,e_imag_pu=emf.imag,current_pu=abs(st[i])/p['ibase'],bus_voltage_pu=abs(v[p['bus']])/p['vbase'],p_w=power.real,q_var=power.imag,te_pu=torque[i])
                if m.thermal:row['thermal_level']=x[self.thermal[i]]
                if hasattr(self,'complete_motor_signals'):row.update(self.complete_motor_signals(i,flux,rot))
                for key,value in row.items():channels.setdefault(m.id+'.'+key,[]).append(float(value))
            sensed={i:self.sensed(x,i) for i,m in enumerate(self.s.motors) if m.contactor}
            for key,value in controls['signals'](snapshot,at,connected,sensed).items():channels.setdefault(key,[]).append(value)
            for j,(node,g,at) in enumerate(self.generator_ports()):
                terminal=v[node] if self.generator_closed(j) else g.emf(x[at])
                values=g.signals(x[at],self.generator_currents[j],terminal)
                suffixes=('delta','omega','e1q','e1d','e2d','e2q','p_w','q_var','TGOV1.valve_pu','SEXS.field_pu')
                for key in (values if getattr(self,'complete_state_channels',False) else [g.g.id+'.'+k for k in suffixes]):channels.setdefault(key,[]).append(values[key])
                if getattr(self,'complete_state_channels',False):
                    for suffix,value in [('TGOV1.valve_state_pu',x[at][2]),('SEXS.field_state_pu',x[at][5])]:channels.setdefault(g.g.id+'.'+suffix,[]).append(float(value))
            if hasattr(self,'extra_signals'):
                for key,value in self.extra_signals(x).items():channels.setdefault(key,[]).append(float(value))
        return dict(time_s=list(times),sample_side=list(sides),channels=channels,event_log=controls['event_log'],
                    node_voltage={k:{'real_v':[z.real for z in values],'imag_v':[z.imag for z in values]} for k,values in node_voltage.items()},
                    independent_SI_network=True,production_stamps_used=False,initialization=self.initialization,controller_event_alignment=alignment_audit,balance_audit=balance_audit,integration=controls.get('integration'),sparse_MNA=self.sparse_network,network_work=dict(factorizations=self.factorizations,linear_solves=getattr(self,'linear_solves',0),matrix_nnz=self.matrix_nnz),field_validated=False)
