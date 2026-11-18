"""Independent SI lumped-loss shaft/kinetic/thermal energy reference."""
import numpy as np
from benchmarks.generator_heat_reference import GeneratorHeatReference


class GeneratorEnergyReference(GeneratorHeatReference):
    def initial_conditions(self):
        state,a=GeneratorHeatReference.initial_conditions(self)
        # Solve the electrical P/V/heat root first, then supply both output
        # power and loss heat from the shaft at w=1. Reinitialize the governor.
        iq2=(self.g.p_w**2+a['generator_q_var']**2)/(3*self.g.ll_v**2*self.g.v_set_pu**2)
        h=self.heat;cu=3*h.resistance_ohm*(1+h.alpha_per_c*(self.temperature_init-h.reference_c))*iq2;core=h.core_loss_w*self.g.v_set_pu**2
        self.g_pm+=(cu+core)/self.gp.rated_va
        full=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        indices=np.r_[np.arange(4),np.arange(self.control_start,len(full))] if a['thermal_equilibrium'] else np.r_[np.arange(3),np.arange(self.control_start,self.control_start+self.control_count),np.arange(len(full)-6,len(full)-2)]
        ctx=dict(on=True,line=True,source=True,faults=set(),relays={r.id:[False,False,r.measurement_available] for r in self.relays})
        matrix=np.empty((len(indices),len(indices)))
        for j,i in enumerate(indices):
            step=1e-6*max(1.,abs(full[i]));hi=full.copy();lo=full.copy();hi[i]+=step;lo[i]-=step
            matrix[:,j]=(self.rhs(0,hi,ctx)[indices]-self.rhs(0,lo,ctx)[indices])/(2*step)
        eigen=np.linalg.eigvals(matrix)
        a.update(generator_mechanical_pu=float(self.g_pm),energy_spectral_abscissa_per_s=float(max(eigen.real)),energy_eigenvalues_per_s=[[float(e.real),float(e.imag)] for e in eigen])
        return state,a

    def rhs(self,t,x,c):
        result=GeneratorHeatReference.rhs(self,t,x,c);heat=self.heat_values(x,c);eg=self.generator_emf(x);ig=(eg-self.circuit(x,c)[2])/self.gz;speed=x[-5]
        if speed<=0:raise ValueError('positive rotor speed required')
        result[-5]=(self.mechanical(x)-3*(eg*np.conj(ig)).real/self.gp.rated_va-(heat['copper_loss_w']+heat['core_loss_w'])/self.gp.rated_va-self.gp.damping_pu*(speed-1))/(2*self.gp.inertia_s*speed)
        return result

    def sample(self,time,side='regular'):
        values=GeneratorHeatReference.sample(self,time,side);x,c=self.state_at(time,side);heat=self.heat_values(x,c);rate=self.rhs(time,x,c);speed=x[-5];rated=self.gp.rated_va
        shaft=self.mechanical(x)*rated;ig=(self.generator_emf(x)-self.circuit(x,c)[2])/self.gz;port=3*(self.circuit(x,c)[2]*np.conj(ig)).real
        damping=self.gp.damping_pu*(speed-1)*rated;kinetic_rate=2*self.gp.inertia_s*rated*speed*rate[-5];thermal_rate=self.heat.capacity_j_per_k*rate[self.heat_address]
        data=dict(shaft_power_w=shaft,terminal_power_w=port,heat_input_w=heat['copper_loss_w']+heat['core_loss_w'],ambient_heat_w=heat['ambient_heat_w'],signed_damping_exchange_w=damping,
          kinetic_rate_w=kinetic_rate,thermal_rate_w=thermal_rate,balance_residual_w=shaft-port-damping-heat['ambient_heat_w']-kinetic_rate-thermal_rate,
          kinetic_energy_j=self.gp.inertia_s*rated*speed**2,thermal_energy_j=self.heat.capacity_j_per_k*(x[self.heat_address]-self.heat.ambient_c))
        values.update({'g.energy.'+k:float(v) for k,v in data.items()});return values
