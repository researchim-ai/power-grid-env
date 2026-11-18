"""Independent SI generator winding/core heat observer; no native heat helpers."""
import copy
import numpy as np
from power_grid_env.study_spec import StudySpec
from benchmarks.controlled_winding_reference import ControlledReference


class GeneratorHeatReference(ControlledReference):
    def __init__(self,study):
        original=StudySpec.model_validate(study);s=copy.deepcopy(study)
        if len(original.coupled.generator_heat)!=1 or original.coupled.generator_thermal:raise ValueError('one thermal observer without I2 trip required')
        s['coupled']['generator_heat']=[];s['coupled']['winding_generator_profile']='GENCLS_TGOV1_SEXS'
        super().__init__(s);self.study=original;self.heat=original.coupled.generator_heat[0];self.temperature_init=self.heat.initial_c
        self.heat_address=self.control_start+self.control_count

    def initial_tail(self):
        tail=ControlledReference.initial_tail(self);return [*tail[:-6],self.temperature_init,*tail[-6:]]

    def initial_conditions(self):
        state,a=ControlledReference.initial_conditions(self);p=self.heat
        amperes2=(self.g.p_w**2+a['generator_q_var']**2)/(3*self.g.ll_v**2*self.g.v_set_pu**2)
        q=3*p.resistance_ohm*amperes2;den=p.ambient_w_per_k-q*p.alpha_per_c
        if p.initialization=='equilibrium':
            if den<=0:raise ValueError('no stable heat equilibrium')
            self.temperature_init=(q*(1-p.alpha_per_c*p.reference_c)+p.core_loss_w*self.g.v_set_pu**2+p.ambient_w_per_k*p.ambient_c)/den
        full=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        indices=np.r_[np.arange(3),np.arange(self.control_start,self.control_start+self.control_count),np.arange(len(full)-6,len(full)-2)]
        ctx=dict(on=True,line=True,source=True,faults=set(),relays={r.id:[False,False,r.measurement_available] for r in self.relays})
        matrix=np.empty((len(indices),len(indices)))
        for j,i in enumerate(indices):
            step=1e-6*max(1.,abs(full[i]));hi=full.copy();lo=full.copy();hi[i]+=step;lo[i]-=step
            matrix[:,j]=(self.rhs(0,hi,ctx)[indices]-self.rhs(0,lo,ctx)[indices])/(2*step)
        a['frozen_heat_excluded_spectral_abscissa_per_s']=float(max(np.linalg.eigvals(matrix).real))
        a.update(generator_temperature_c=float(self.temperature_init),generator_heat_pole_per_s=float(-den/p.capacity_j_per_k))
        return state,a

    def heat_values(self,x,c):
        h=self.heat;t=x[self.heat_address];ig=(self.generator_emf(x)-self.circuit(x,c)[2])/self.gz;terminal=self.circuit(x,c)[2]
        copper=3*h.resistance_ohm*(1+h.alpha_per_c*(t-h.reference_c))*abs(ig)**2
        core=h.core_loss_w*(abs(terminal)/self.gv)**2;ambient=h.ambient_w_per_k*(t-h.ambient_c)
        return dict(temperature_c=float(t),copper_loss_w=float(copper),core_loss_w=float(core),ambient_heat_w=float(ambient),net_heat_w=float(copper+core-ambient))

    def rhs(self,t,x,c):
        base=ControlledReference.rhs(self,t,x,c);rate=self.heat_values(x,c)['net_heat_w']/self.heat.capacity_j_per_k
        return np.r_[base[:-6],rate,base[-6:]]

    def sample(self,time,side='regular'):
        values=ControlledReference.sample(self,time,side);x,c=self.state_at(time,side)
        values.update({'g.heat.'+k:v for k,v in self.heat_values(x,c).items()});return values
