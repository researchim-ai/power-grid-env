"""Sparse SI winding/MNA oracle for EX-03, independent of production stamps.

Default short/dense reference profiles remain available in their own modules.
"""
import numpy as np
from benchmarks.multibus_si_motor import Circuit
from benchmarks.multibus_multi_generator import MultiCircuit
from benchmarks.controller_reference_bounds import attach

class SparseContext:
    complete_state_channels=True

    def complete_motor_signals(self,i,flux,rot):
        m=self.s.motors[i];p=self.p[i]
        if len(p['rr'])!=2:return {}
        parts=1j*self.wb*p['lm']*np.linalg.solve(p['L'],np.ones(2))*flux/p['vbase']
        out={}
        for j in range(2):
            out.update({f'cage{j+1}_e_real_pu':float(parts[j].real),f'cage{j+1}_e_imag_pu':float(parts[j].imag),f'rotor{j+1}_current_pu':float(abs(rot[p['rotor']][j])/p['ibase']),f'rotor{j+1}_copper_loss_w':float(3*p['rr'][j]*abs(rot[p['rotor']][j])**2)})
        return out
    def set_context(self,cfg):
        changed=not np.array_equal(cfg['connected'],self.cfg['connected'])
        self.cfg={k:v.copy() if isinstance(v,np.ndarray) else v for k,v in cfg.items()};self.cache_x=None
        if changed:self.rebuild()
        for i,(_,g,_) in enumerate(self.generator_ports()):
            if 'generator_pref' in cfg:g.pref=cfg['generator_pref'][i];g.vref=cfg['generator_vref'][i]

    def sample_audit(self,v,current):return self.physical_balance(v,current)

class SparseMotor(SparseContext,Circuit):pass
class SparseCoupled(SparseContext,MultiCircuit):pass


def reference(spec,*,maximum_step_s=.01):
    if spec.coupled:
        s=spec.coupled
        if any(e.kind=='generator_open' for e in s.events):raise ValueError('EX03 sparse oracle excludes generator opening')
        if s.esst1a_exciters or s.takeover_oels or s.underexcitation_limiters or s.generator_thermal or s.generator_field_thermal or s.overexcitation_limiters or s.oel2c_limiters:raise ValueError('EX03 sparse oracle requires declared GENROU/TGOV1/SEXS without limiter adapters')
        c=attach(SparseCoupled(spec,maximum_duration_s=60.,maximum_nodes=2048,sparse_network=True))
    else:c=SparseMotor(spec,maximum_duration_s=60.,maximum_nodes=2048,sparse_network=True)
    if spec.coupled:
        for _,g,_ in c.generator_ports():
            if (g.g.q_min_var is not None and g.initial_q_var<g.g.q_min_var-.01) or (g.g.q_max_var is not None and g.initial_q_var>g.g.q_max_var+.01):raise ValueError('independent generator initial Q limit')
    c.hybrid_options=dict(maximum_step_s=maximum_step_s,wall_budget_s=300.,maximum_rhs=5_000_000)
    return c
