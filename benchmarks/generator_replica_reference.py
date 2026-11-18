"""Independent generator stator I2 filter atop SI controlled machine equations.

The public corpus keeps the replica below its trip threshold. A replica is not
winding temperature and does not change electrical resistance or losses.
"""
import copy
import numpy as np
from power_grid_env.study_spec import StudySpec
from benchmarks.controlled_winding_reference import ControlledReference


class GeneratorReplicaReference(ControlledReference):
    def __init__(self,study):
        original=StudySpec.model_validate(study);s=copy.deepcopy(study)
        if len(original.coupled.generator_thermal)!=1:raise ValueError('one stator replica required')
        s['coupled']['generator_thermal']=[];s['coupled']['winding_generator_profile']='GENCLS_TGOV1_SEXS'
        super().__init__(s);self.study=original;self.replica=original.coupled.generator_thermal[0];self.theta_init=self.replica.initial_level
        self.replica_address=self.control_start+self.control_count

    def initial_tail(self):
        tail=ControlledReference.initial_tail(self)
        return [*tail[:-6],self.theta_init,*tail[-6:]]

    def initial_conditions(self):
        state,a=ControlledReference.initial_conditions(self)
        if self.replica.initialization=='equilibrium':self.theta_init=((self.g.p_w/self.gp.rated_va)**2+(a['generator_q_var']/self.gp.rated_va)**2)/self.g.v_set_pu**2
        else:self.theta_init=self.replica.initial_level
        if self.theta_init>=self.replica.trip_level:raise ValueError('initial replica limit')
        a.update(generator_replica_initial_level=float(self.theta_init),generator_replica_eigenvalue_per_s=-1/self.replica.time_constant_s)
        return state,a

    def rhs(self,t,x,c):
        base=ControlledReference.rhs(self,t,x,c);eg=self.generator_emf(x);ig=(eg-self.circuit(x,c)[2])/self.gz
        rate=((abs(ig)/self.gib)**2-x[self.replica_address])/self.replica.time_constant_s
        return np.r_[base[:-6],rate,base[-6:]]

    def sample(self,time,side='regular'):
        values=ControlledReference.sample(self,time,side);x,c=self.state_at(time,side)
        if x[self.replica_address]>=self.replica.trip_level:raise ValueError('replica trip requires separate hybrid reference')
        values.update({'g.stator_thermal_level':float(x[self.replica_address]),'g.stator_thermal_latched':0.})
        return values
