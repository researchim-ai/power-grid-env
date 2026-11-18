"""Independent SI machine network plus published-form TGOV1/SEXS equations.

Only authored interior controller trajectories are qualified. No native machine,
controller, tangent or initialization equations are called.
"""
import copy
import numpy as np
from power_grid_env.study_spec import StudySpec
from benchmarks.mixed_winding_reference import MixedReference
from benchmarks.mixed_equilibrium_reference import MixedEquilibriumReference


class ControlledReference(MixedEquilibriumReference):
    def __init__(self,study):
        spec=StudySpec.model_validate(study);p=spec.coupled.machines[0]
        if spec.coupled.winding_generator_profile!='GENCLS_TGOV1_SEXS' or p.stabilizer is not None or p.model!='GENCLS':raise ValueError('explicit GENCLS TGOV1/SEXS reference required')
        surrogate=copy.deepcopy(study);surrogate['coupled']['winding_generator_profile']='GENCLS_fixed_field';surrogate['coupled']['operating_point']['winding_state']='passport'
        surrogate['coupled']['machines'][0].update(governor=None,exciter=None)
        MixedReference.__init__(self,surrogate)
        self.study=spec;self.gp=p;self.control_start=4+len(self.relays)
        self.gov_address=self.control_start if p.governor else None
        self.avr_address=self.control_start+(2 if p.governor else 0) if p.exciter else None
        self.control_count=2*bool(p.governor)+2*bool(p.exciter)

    def initial_tail(self):
        values=[]
        if self.gp.governor:
            self.pref=self.g_pm;values.extend([self.g_pm,self.g_pm])
        if self.gp.exciter:
            field=self.g_amplitude/self.gv;self.vref=self.g.v_set_pu+field/self.gp.exciter.K
            values.extend([field/self.gp.exciter.K,field])
        return [*values,*MixedReference.initial_tail(self)]

    def initial_conditions(self):
        if self.study.coupled.operating_point.winding_state=='equilibrium':state,a=MixedEquilibriumReference.initial_conditions(self)
        else:state,a=MixedReference.initial_conditions(self)
        full=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        indices=np.r_[np.arange(4),np.arange(self.control_start,len(full))]
        if not a['thermal_equilibrium']:indices=np.r_[np.arange(3),np.arange(self.control_start,len(full)-2)]
        context=dict(on=True,line=True,source=True,faults=set(),relays={r.id:[False,False,r.measurement_available] for r in self.relays})
        matrix=np.empty((len(indices),len(indices)))
        for j,i in enumerate(indices):
            step=1e-6*max(1.,abs(full[i]));hi=full.copy();lo=full.copy();hi[i]+=step;lo[i]-=step
            matrix[:,j]=(self.rhs(0,hi,context)[indices]-self.rhs(0,lo,context)[indices])/(2*step)
        eigen=np.linalg.eigvals(matrix)
        a.update(mode='independent_SI_controlled_PV_motor_root',controlled_spectral_abscissa_per_s=float(max(eigen.real)),controlled_eigenvalues_per_s=[[float(z.real),float(z.imag)] for z in eigen])
        return state,a

    def generator_emf(self,x):
        amplitude=self.g_amplitude if self.avr_address is None else self.gv*np.clip(x[self.avr_address+1],self.gp.exciter.EMIN,self.gp.exciter.EMAX)
        return amplitude*np.exp(1j*x[-6])

    def mechanical(self,x):
        if self.gov_address is None:return self.g_pm
        a=self.gov_address;p=self.gp.governor
        return p.T2/p.T3*np.clip(x[a],p.VMIN,p.VMAX)+(1-p.T2/p.T3)*x[a+1]-p.Dt*(x[-5]-1)

    def control_values(self,x,c):
        rates=[];channels={};p=self.gp
        if self.gov_address is not None:
            a=self.gov_address;g=p.governor;valve=np.clip(x[a],g.VMIN,g.VMAX);rate=(self.pref-(x[-5]-1)/g.R-x[a])/g.T1
            if (x[a]>=g.VMAX and rate>0) or (x[a]<=g.VMIN and rate<0):rate=0.
            rates.extend([rate,(valve-x[a+1])/g.T3])
            channels.update({'TGOV1.'+k:float(v) for k,v in dict(valve_pu=valve,valve_state_pu=x[a],turbine_state_pu=x[a+1],reference_pu=self.pref,output_pu=self.mechanical(x)).items()})
        if self.avr_address is not None:
            a=self.avr_address;e=p.exciter;error=self.vref-abs(self.circuit(x,c)[2])/self.gv;field=np.clip(x[a+1],e.EMIN,e.EMAX)
            rate=(e.K*(e.TATB*error+(1-e.TATB)*x[a])-x[a+1])/e.TE
            if (x[a+1]>=e.EMAX and rate>0) or (x[a+1]<=e.EMIN and rate<0):rate=0.
            rates.extend([(error-x[a])/e.TB,rate])
            channels.update({'SEXS.'+k:float(v) for k,v in dict(field_pu=field,field_state_pu=x[a+1],filter_pu=x[a],reference_pu=self.vref,error_pu=error).items()})
        return np.array(rates),channels

    def rhs(self,t,x,c):
        base=MixedReference.rhs(self,t,x,c);rates,_=self.control_values(x,c)
        eg=self.generator_emf(x);ig=(eg-self.circuit(x,c)[2])/self.gz
        base[-5]=(self.mechanical(x)-3*(eg*np.conj(ig)).real/self.gp.rated_va-self.gp.damping_pu*(x[-5]-1))/(2*self.gp.inertia_s)
        return np.r_[base[:-6],rates,base[-6:]]

    def sample(self,time,side='regular'):
        channels=MixedReference.sample(self,time,side);x,c=self.state_at(time,side)
        channels.update({self.g.id+'.'+k:v for k,v in self.control_values(x,c)[1].items()});channels[self.g.id+'.mechanical_power_pu']=float(self.mechanical(x))
        return channels
