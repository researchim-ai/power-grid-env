"""Independent SI MNA with multiple PV machines and individual controls.

The public primary solver is never used for initialization or integration.
Native ANDES scope only uses the passport/geometry projection, not these roots.
"""
import math
import numpy as np
from scipy.optimize import root
from power_grid_env.study_spec import StudySpec
from benchmarks.multibus_si_motor import Circuit
from benchmarks.group_si_generator import Generator

CONTROL_EVENTS={'governor_step','voltage_reference_step'}


def needed(spec):
    return bool(spec.coupled and (len(spec.network.generators)!=1 or spec.coupled.generator_thermal or spec.coupled.generator_field_thermal or spec.coupled.overexcitation_limiters or any(e.kind in CONTROL_EVENTS for e in spec.coupled.events)))


def project_motor_case(spec,*,maximum_generators=4):
    if spec.study!='coupled_rms' or spec.coupled is None:raise ValueError('coupled RMS required')
    if spec.coupled.esst1a_exciters:raise ValueError('old SI projection cannot silently remove an ESST1A exciter')
    n,s=spec.network,spec.coupled
    if type(maximum_generators) is not int or not 1<=maximum_generators<=32:raise ValueError('bounded generator reference profile required')
    if not 1<=len(n.generators)<=maximum_generators or len(s.machines)!=len(n.generators) or s.faults:raise ValueError('one to four GENROU generators without faults required')
    passports={p.generator_id:p for p in s.machines};gens={g.id:g for g in n.generators}
    if len(passports)!=len(s.machines) or len(gens)!=len(n.generators) or passports.keys()!=gens.keys():raise ValueError('unique generator/passport identity bijection required')
    terminals={t.id:t.connectivity_node_id for t in n.terminals};levels={v.id:v.nominal_ll_v for v in n.voltage_levels};nodes={p.id:p for p in n.connectivity_nodes};seen=set()
    for g in n.generators:
        p=passports[g.id];node=terminals.get(g.terminal_id)
        if node not in nodes or node in seen:raise ValueError('distinct existing PV generator nodes required')
        seen.add(node)
        if sorted(g.phases)!=list('ABC') or not math.isclose(g.ll_v,levels[nodes[node].voltage_level_id],rel_tol=1e-10):raise ValueError('generator voltage base mismatch')
        if p.model!='GENROU' or p.x_d_subtransient_pu!=p.x_q_subtransient_pu or not p.governor or not p.exciter or p.stabilizer or g.mode!='pv':raise ValueError('isotropic GENROU/TGOV1/SEXS PV subset required')
    for e in s.events:
        if e.kind not in {'source_voltage','motor_open','motor_close','generator_open',*CONTROL_EVENTS}:raise ValueError('unsupported multi-generator event')
        if e.kind=='generator_open' and e.equipment_id not in gens:raise ValueError('generator opening requires the protected SI reference')
        if e.kind in CONTROL_EVENTS and (e.equipment_id not in gens or not math.isfinite(e.value)):raise ValueError('unknown/nonfinite generator control target')
    data=spec.model_dump(mode='json');data.update(study='motor_start',coupled=None)
    data['network']['generators']=[];data['network']['terminals']=[t for t in data['network']['terminals'] if t['equipment_id'] not in gens]
    data['induction']=s.model_dump(mode='json',exclude={'monitored_lines','machines','faults','generator_thermal','overexcitation_limiters','generator_field_thermal','underexcitation_limiters','oel2c_limiters','esst1a_exciters','takeover_oels'})
    data['induction']['events']=[e for e in data['induction']['events'] if e['kind'] not in CONTROL_EVENTS|{'generator_open'}]
    return StudySpec.model_validate(data)


class MultiCircuit(Circuit):
    def __init__(self,spec,*,maximum_duration_s=3.,current_constraints=None,maximum_nodes=64,sparse_network=False,maximum_generators=4,maximum_motors=32):
        self.ports=[];self.current_constraints=current_constraints or {}
        super().__init__(project_motor_case(spec,maximum_generators=maximum_generators),initialize=False,maximum_duration_s=maximum_duration_s,maximum_nodes=maximum_nodes,sparse_network=sparse_network,maximum_motors=maximum_motors)
        self.spec=spec;self.n=spec.network;self.s=spec.coupled
        self.term={t.id:t.connectivity_node_id for t in self.n.terminals}
        self.initialize_joint()

    def generator_ports(self):return self.ports

    def steady_multi(self,speeds,pv_voltage,cold):
        # Build each rotor admittance from SI R/L winding equations, then
        # eliminate all non-PV nodes together; no production stamps are used.
        responses=[];admittances=[]
        for i,(p,w) in enumerate(zip(self.p,speeds)):
            response=-p['lm']*np.linalg.solve(p['L'],np.ones(len(p['rr']))) if cold else np.linalg.solve(np.diag(p['rr'])+1j*self.wb*(1-w)*p['L'],-1j*self.wb*(1-w)*p['lm']*np.ones(len(p['rr'])))
            responses.append(response)
            admittances.append(1/(p['rs']+1j*self.wb*p['lss']+1j*self.wb*p['lm']*sum(response)) if self.cfg['connected'][i] else 0j)
        Y=self.Y.copy();drive=np.zeros(self.N,complex);drive[self.source]=self.es/self.zs
        for p,y in zip(self.p,admittances):Y[p['bus'],p['bus']]+=y
        pv=self.pv_nodes;other=np.array([i for i in range(self.N) if i not in pv],dtype=int)
        voltage=np.empty(self.N,complex);voltage[pv]=pv_voltage
        if len(other):voltage[other]=self.network_solve(Y[np.ix_(other,other)],drive[other]-Y[np.ix_(other,pv)]@pv_voltage)
        ig=Y[pv]@voltage-drive[pv]
        currents=np.array([voltage[p['bus']]*y for p,y in zip(self.p,admittances)])
        rotor=[r*i for r,i in zip(responses,currents)];flux=[p['lm']*i+p['L']@r for p,i,r in zip(self.p,currents,rotor)]
        torque=np.array([-3*self.wb*p['lm']*(sum(r)*np.conj(i)).imag/m.rated_va for m,p,r,i in zip(self.s.motors,self.p,rotor,currents)])
        return voltage,currents,flux,torque,ig

    def initialize_joint(self):
        gens=self.n.generators;passports={p.generator_id:p for p in self.s.machines};warm=self.s.operating_point is not None
        self.pv_nodes=np.array([self.nodes[self.term[g.terminal_id]] for g in gens]);ng=len(gens)
        magnitude=np.array([g.v_set_pu*self.base[node] for g,node in zip(gens,self.pv_nodes)])
        speed0=np.full(self.M,.99) if warm else np.zeros(self.M)
        passive=self.steady_response(speed0,cold=not warm)[0]
        selected=[i for i,g in enumerate(gens) if g.id in self.current_constraints];off=self.M if warm else 0
        def response(values):
            mag=magnitude.copy()
            for j,i in enumerate(selected):mag[i]=values[off+ng+j]*self.base[self.pv_nodes[i]]
            return self.steady_multi(values[:self.M] if warm else np.zeros(self.M),mag*np.exp(1j*values[off:off+ng]),not warm)
        def field_constraints(point):
            v,_,_,_,ig=point;out=[]
            for i in selected:
                g=gens[i];p=self.current_constraints[g.id]
                network=self.n.model_copy(update={'generators':[g]});settings=self.s.model_copy(update={'machines':[passports[g.id]]})
                from benchmarks.oel2c_current_network import stationary_field
                field=stationary_field(network,settings,v[self.pv_nodes[i]],ig[i])
                out.append(p.KAct*p.KScale*abs(field)/p.rated_field_reaction_pu-p.ILimPu)
            return out
        def residual(values):
            v,_,_,torque,ig=response(values)
            mechanical=torque-np.array([m.fan_torque_pu*w*abs(w) for m,w in zip(self.s.motors,values[:self.M])]) if warm else []
            power=(3*v[self.pv_nodes]*np.conj(ig)).real
            return np.r_[mechanical,[(value-g.p_w)/passports[g.id].rated_va for value,g in zip(power,gens)],field_constraints(response(values))]
        guess=np.r_[speed0,np.angle(passive[self.pv_nodes])] if warm else np.angle(passive[self.pv_nodes])
        if selected:guess=np.r_[guess,[gens[i].v_set_pu for i in selected]]
        solved=root(residual,guess,tol=1e-11);error=float(np.max(abs(residual(solved.x))))
        speeds=solved.x[:self.M] if warm else np.zeros(self.M)
        if not np.isfinite(solved.x).all() or error>1e-9 or (warm and (np.any(speeds<self.s.operating_point.minimum_speed_pu) or np.any(speeds>1))):raise ValueError('independent multi-PV/motor equilibrium failed')
        point=response(solved.x)
        for i in selected:
            p=self.current_constraints[gens[i].id];voltage_pu=abs(point[0][self.pv_nodes[i]])/self.base[self.pv_nodes[i]]
            if not p.regulating_voltage_bounds_pu[0]<voltage_pu<p.regulating_voltage_bounds_pu[1]:raise ValueError('independent regulating voltage outside declared bounds')
        self.install_motor_point(speeds,point,cold=not warm)
        voltage,_,_,_,ig=point
        for g,node,current in zip(gens,self.pv_nodes,ig):
            network=self.n.model_copy(update={'generators':[g]});settings=self.s.model_copy(update={'machines':[passports[g.id]]})
            machine=Generator.at_terminal(network,settings,voltage[node],current);at=slice(self.dimension,self.dimension+len(machine.initial));self.dimension=at.stop
            self.state=np.r_[self.state,machine.initial];self.ports.append((node,machine,at))
        self.cfg['generator_pref']=np.array([g.pref for _,g,_ in self.ports]);self.cfg['generator_vref']=np.array([g.vref for _,g,_ in self.ports])
        self.rebuild();derivative=self.rhs(0.,self.state)
        gen_error=max(float(np.max(abs(derivative[at]))) for _,_,at in self.ports)
        motor_error=float(np.max(abs(derivative[:self.p[-1]['speed']+1])))
        thermal_error=max((abs(derivative[a]) for a in self.thermal.values()),default=0.)
        if gen_error>1e-8 or (warm and motor_error>1e-8) or (warm and self.s.operating_point.thermal_state=='equilibrium' and thermal_error>1e-8):raise ValueError('independent multi-generator initial RHS not zero')
        self.initialization=dict(method='independent_SI_joint_field_current_PV_motor_root' if selected else 'independent_SI_joint_multi_PV_motor_root',maximum_static_residual=error,maximum_generator_initial_rhs=gen_error,maximum_motor_initial_rhs=motor_error,generator_initial_q_var={g.g.id:g.initial_q_var for _,g,_ in self.ports},speeds_pu=speeds.tolist())

    def set_context(self,cfg):
        super().set_context(cfg)
        for i,(_,g,_) in enumerate(self.ports):
            if 'generator_pref' in cfg:g.pref=cfg['generator_pref'][i];g.vref=cfg['generator_vref'][i]

    def handle_event(self,event,cfg):
        i=next(i for i,(_,g,_) in enumerate(self.ports) if g.g.id==event.equipment_id)
        key='generator_pref' if event.kind=='governor_step' else 'generator_vref' if event.kind=='voltage_reference_step' else None
        if key is None:raise ValueError('unsupported multi-generator control event')
        def references():return {g.g.id:dict(governor=float(cfg['generator_pref'][j]),voltage=float(cfg['generator_vref'][j])) for j,(_,g,_) in enumerate(self.ports)}
        before=references();cfg[key][i]+=event.value
        return dict(references_before=before,references_after=references())

    def rhs(self,time,x):
        result=super().rhs(time,x);voltage=self.electrical(x)[0]
        for j,(node,g,at) in enumerate(self.ports):result[at]=g.rhs(x[at],self.generator_currents[j],voltage[node] if self.generator_closed(j) else g.emf(x[at]))
        return result
