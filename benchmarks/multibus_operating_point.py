"""Independent multibus PV, rotor and controller operating points."""
import math
import numpy as np
from scipy.optimize import root
from power_grid_env.study_spec import StudySpec
from benchmarks.multibus_si_motor import Circuit
from benchmarks.group_si_generator import Generator


def motor_case(spec):
    from benchmarks.multibus_multi_generator import needed,project_motor_case
    if needed(spec):return project_motor_case(spec)
    if spec.study=='motor_start':return spec
    if spec.study!='coupled_rms' or spec.coupled is None:raise ValueError('multibus motor_start or coupled_rms required')
    n,s=spec.network,spec.coupled
    if len(n.generators)!=1 or len(s.machines)!=1 or s.faults:raise ValueError('one multibus generator without faults required')
    g,p=n.generators[0],s.machines[0]
    if p.model!='GENROU' or p.generator_id!=g.id or not p.governor or not p.exciter or p.stabilizer or p.x_d_subtransient_pu!=p.x_q_subtransient_pu or g.mode!='pv':raise ValueError('multibus GENROU/TGOV1/SEXS isotropic PV subset required')
    terminals={t.id:t.connectivity_node_id for t in n.terminals};levels={v.id:v.nominal_ll_v for v in n.voltage_levels};nodes={node.id:node for node in n.connectivity_nodes}
    if sorted(g.phases)!=list('ABC') or not math.isclose(g.ll_v,levels[nodes[terminals[g.terminal_id]].voltage_level_id],rel_tol=1e-10):raise ValueError('generator voltage base mismatch')
    if any(e.kind not in ('source_voltage','motor_open','motor_close') for e in s.events):raise ValueError('multibus coupled source/motor events only')
    data=spec.model_dump(mode='json');data.update(study='motor_start',coupled=None)
    data['network']['generators']=[];data['network']['terminals']=[t for t in data['network']['terminals'] if t['equipment_id']!=g.id]
    data['induction']=s.model_dump(mode='json',exclude={'machines','faults','generator_thermal','overexcitation_limiters','generator_field_thermal','underexcitation_limiters','oel2c_limiters','esst1a_exciters','takeover_oels'})
    return StudySpec.model_validate(data)


def validate_scope(spec):
    # Only geometry/passport validation. No steady-state solution is computed
    # on the native path, which independently initializes its own circuit.
    return Circuit(motor_case(spec),initialize=False)


class CoupledCircuit(Circuit):
    def __init__(self,spec):
        super().__init__(motor_case(spec),initialize=False)
        self.spec=spec;self.n=spec.network;self.s=spec.coupled
        self.term={t.id:t.connectivity_node_id for t in self.n.terminals}
        self.generator_node=self.nodes[self.term[spec.network.generators[0].terminal_id]]
        self.initialize_joint()

    def initialize_joint(self):
        g=self.n.generators[0];p=self.s.machines[0];warm=self.s.operating_point is not None
        magnitude=g.v_set_pu*self.base[self.generator_node]
        guess_w=np.full(self.M,.99) if warm else np.zeros(self.M)
        guess_angle=float(np.angle(self.steady_response(guess_w,cold=not warm)[0][self.generator_node]))
        def response(values):
            speeds=values[:-1] if warm else np.zeros(self.M);v=magnitude*np.exp(1j*values[-1])
            return self.steady_response(speeds,pv_node=self.generator_node,pv_voltage=v,cold=not warm)
        def residual(values):
            voltage,_,_,torque,current=response(values)
            power=(3*voltage[self.generator_node]*np.conj(current)).real
            mechanical=torque-np.array([m.fan_torque_pu*w*abs(w) for m,w in zip(self.s.motors,values[:-1])]) if warm else np.array([])
            return np.r_[mechanical,(power-g.p_w)/p.rated_va]
        guess=np.r_[guess_w,guess_angle] if warm else [guess_angle]
        solved=root(residual,guess,tol=1e-11);error=float(max(abs(residual(solved.x))))
        speeds=solved.x[:-1] if warm else np.zeros(self.M)
        if not np.isfinite(solved.x).all() or error>1e-9 or (warm and (np.any(speeds<self.s.operating_point.minimum_speed_pu) or np.any(speeds>1.))):raise ValueError('independent multibus PV/motor equilibrium failed')
        point=response(solved.x);voltage,_,_,_,current=point
        self.install_motor_point(speeds,point,cold=not warm)
        self.generator=Generator.at_terminal(self.n,self.s,voltage[self.generator_node],current)
        self.generator_slice=slice(self.dimension,self.dimension+len(self.generator.initial));self.dimension+=len(self.generator.initial)
        self.state=np.r_[self.state,self.generator.initial];self.rebuild()
        derivative=self.rhs(0.,self.state);generator_error=float(max(abs(derivative[self.generator_slice])))
        motor_error=float(max(abs(derivative[:self.p[-1]['speed']+1])))
        thermal_error=max((abs(derivative[i]) for i in self.thermal.values()),default=0.)
        if generator_error>1e-8 or (warm and motor_error>1e-8) or (warm and self.s.operating_point.thermal_state=='equilibrium' and thermal_error>1e-8):raise ValueError('independent multibus initial joint RHS not zero')
        self.initialization=dict(method='independent_SI_multibus_PV_and_motor_root',motors_loaded=warm,speeds_pu=speeds.tolist(),
            maximum_static_residual=error,maximum_motor_initial_rhs=motor_error,maximum_generator_initial_rhs=generator_error,maximum_initial_thermal_rhs=float(thermal_error),maximum_joint_initial_rhs=float(max(abs(derivative))),generator_initial_q_var=self.generator.initial_q_var)

    def rhs(self,time,x):
        result=super().rhs(time,x)
        if self.generator:
            voltage=self.electrical(x)[0]
            result[self.generator_slice]=self.generator.rhs(x[self.generator_slice],self.generator_current,voltage[self.generator_node])
        return result


def independent_circuit(spec):
    from benchmarks.multibus_multi_generator import needed,MultiCircuit
    if spec.coupled and spec.coupled.oel2c_limiters:raise ValueError('OEL2C requires the native Model Exchange feedback reference')
    if spec.coupled and (spec.coupled.oel2c_limiters or spec.coupled.underexcitation_limiters or spec.coupled.overexcitation_limiters or spec.coupled.generator_thermal or spec.coupled.generator_field_thermal or any(e.kind=='generator_open' for e in spec.coupled.events)):
        from benchmarks.generator_excitation_si import ExcitationCircuit
        return ExcitationCircuit(spec)
    if spec.coupled:
        from benchmarks.controller_reference_bounds import attach
        return attach(MultiCircuit(spec) if needed(spec) else CoupledCircuit(spec))
    return Circuit(spec)
