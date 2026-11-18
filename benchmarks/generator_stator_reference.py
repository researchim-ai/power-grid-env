"""Independent SI R(T) generator port, joint winding/PV/heat root and energy."""
import numpy as np
from scipy.linalg import lu_factor,lu_solve
from scipy.optimize import least_squares
from benchmarks.generator_energy_reference import GeneratorEnergyReference
from benchmarks.generator_heat_reference import GeneratorHeatReference
from benchmarks.mixed_winding_reference import MixedReference


class GeneratorStatorReference(GeneratorEnergyReference):
    def impedance_at(self,t):
        h=self.heat;return h.resistance_ohm*(1+h.alpha_per_c*(t-h.reference_c))+1j*self.gp.x_d_prime_pu*self.g.ll_v**2/self.gp.rated_va

    def static_stator(self,speed,eg,ts,tr,tg):
        old=self.gz;self.gz=self.impedance_at(tg)
        try:return MixedReference.static_mixed(self,speed,eg,ts,tr)
        finally:self.gz=old

    def initial_conditions(self):
        w,h=self.w,self.h;gheat=self.heat;op=self.study.coupled.operating_point;joint=op.winding_state=='equilibrium'
        seed=np.array([.99,*self.seed_temperatures,1.1,.05,gheat.initial_c]);free=np.arange(6) if joint else np.array([0,3,4])
        lower=np.array([op.minimum_speed_pu,h.ambient_c,h.ambient_c,-10.,-10.,gheat.ambient_c]);upper=np.array([1.,h.maximum_c-1e-7,h.maximum_c-1e-7,10.,10.,gheat.maximum_c-1e-7])
        def expand(x):
            out=seed.copy();out[free]=x;return out
        def residual(values):
            speed,ts,tr,er,ei,tg=expand(values);eg=complex(er,ei)*self.gv
            _,vd,im,ir,flux,ig,torque=self.static_stator(speed,eg,ts,tr,tg);rs,rr=self.resistances(ts,tr);cross=h.cross_w_per_k*(ts-tr)
            error=[torque-w.load(speed),(3*(vd*np.conj(ig)).real-self.g.p_w)/self.gp.rated_va,(abs(vd)/self.gv)**2-self.g.v_set_pu**2]
            if joint:error.extend([(3*rs*abs(im)**2-h.stator_ambient_w_per_k*(ts-h.ambient_c)-cross)/w.m.rated_va,
               (3*sum(rr*abs(ir)**2)-h.rotor_ambient_w_per_k*(tr-h.ambient_c)+cross)/w.m.rated_va,
               (3*self.impedance_at(tg).real*abs(ig)**2+gheat.core_loss_w*(abs(vd)/self.gv)**2-gheat.ambient_w_per_k*(tg-gheat.ambient_c))/self.gp.rated_va])
            return error
        root=None
        for speed in [.99,.96,.88]:
            seed[0]=speed;start=np.clip(seed[free],lower[free]+1e-8,upper[free]-1e-8)
            solved=least_squares(residual,start,bounds=(lower[free],upper[free]),jac='3-point',ftol=1e-14,xtol=1e-14,gtol=1e-14,max_nfev=500)
            if max(abs(np.array(residual(solved.x))))<1e-11:root=expand(solved.x);break
        if root is None:raise ValueError('independent stator/PV/heat root not found')
        speed,ts,tr,er,ei,tg=root;eg=complex(er,ei)*self.gv;_,vd,im,ir,flux,ig,torque=self.static_stator(speed,eg,ts,tr,tg)
        self.g_amplitude=abs(eg);self.g_initial_delta=float(np.angle(eg));self.g_pm=(3*(eg*np.conj(ig)).real+gheat.core_loss_w*(abs(vd)/self.gv)**2)/self.gp.rated_va
        self.initial_temperatures=(float(ts),float(tr));self.temperature_init=float(tg);self.initial_flux2=[float(flux[1].real),float(flux[1].imag)]
        state=np.array([flux[0].real,flux[0].imag,speed,(abs(im)/w.ib)**2]);full=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        indices=np.r_[np.arange(4),np.arange(self.control_start,len(full))] if joint else np.r_[np.arange(3),np.arange(self.control_start,self.control_start+self.control_count),np.arange(len(full)-6,len(full)-2)]
        ctx=dict(on=True,line=True,source=True,faults=set(),relays={r.id:[False,False,r.measurement_available] for r in self.relays});matrix=np.empty((len(indices),len(indices)))
        for j,i in enumerate(indices):
            step=1e-6*max(1.,abs(full[i]));hi=full.copy();lo=full.copy();hi[i]+=step;lo[i]-=step;matrix[:,j]=(self.rhs(0,hi,ctx)[indices]-self.rhs(0,lo,ctx)[indices])/(2*step)
        eigen=np.linalg.eigvals(matrix)
        return state,dict(mode='independent_SI_stator_R_T_joint_root',thermal_equilibrium=joint,generator_temperature_c=float(tg),generator_mechanical_pu=float(self.g_pm),generator_q_var=float(3*(vd*np.conj(ig)).imag),energy_spectral_abscissa_per_s=float(max(eigen.real)),energy_eigenvalues_per_s=[[float(e.real),float(e.imag)] for e in eigen])

    def circuit(self,x,c):
        w=self.w;zg=self.impedance_at(x[self.heat_address]);key=(float(x[-2]),float(x[self.heat_address]),tuple(sorted(c['faults'])))
        if key not in self.factors:
            self.factors.clear();up=down=0j
            for id in c['faults']:
                f=w.faults[id];y=1/complex(f.r_ohm,f.x_ohm)
                if f.node_id==w.up:up+=y
                else:down+=y
            yl=1/w.zl;ys=1/w.zs;rs,_=self.resistances(x[-2],x[-1])
            matrix=np.array([[rs+1j*w.wb*w.ls,1j*w.wb*w.lm,1j*w.wb*w.lm,-1,0],
              [w.lm,self.lr[0,0],self.lr[0,1],0,0],[w.lm,self.lr[1,0],self.lr[1,1],0,0],
              [1,0,0,w.yl+down+yl+1/zg,-yl],[0,0,0,-yl,yl+ys+up]],complex)
            self.factors[key]=lu_factor(matrix)
        q=lu_solve(self.factors[key],[0,complex(x[0],x[1]),complex(x[-4],x[-3]),self.generator_emf(x)/zg,w.es/w.zs])
        return q[0],q[1:3],q[3],q[4]

    def current(self,x,c):return (self.generator_emf(x)-self.circuit(x,c)[2])/self.impedance_at(x[self.heat_address])

    def heat_values(self,x,c):
        p=self.heat;t=x[self.heat_address];cu=3*self.impedance_at(t).real*abs(self.current(x,c))**2;core=p.core_loss_w*(abs(self.circuit(x,c)[2])/self.gv)**2;removed=p.ambient_w_per_k*(t-p.ambient_c)
        return dict(temperature_c=float(t),copper_loss_w=float(cu),core_loss_w=float(core),ambient_heat_w=float(removed),net_heat_w=float(cu+core-removed))

    def rhs(self,t,x,c):
        result=GeneratorHeatReference.rhs(self,t,x,c);heat=self.heat_values(x,c);eg=self.generator_emf(x);ig=self.current(x,c);speed=x[-5]
        if speed<=0:raise ValueError('positive rotor speed required')
        result[-5]=(self.mechanical(x)-3*(eg*np.conj(ig)).real/self.gp.rated_va-heat['core_loss_w']/self.gp.rated_va-self.gp.damping_pu*(speed-1))/(2*self.gp.inertia_s*speed)
        return result

    def sample(self,time,side='regular'):
        values=GeneratorHeatReference.sample(self,time,side);x,c=self.state_at(time,side);heat=self.heat_values(x,c);rate=self.rhs(time,x,c);speed=x[-5];rated=self.gp.rated_va;eg=self.generator_emf(x);ig=self.current(x,c);vd=self.circuit(x,c)[2];port=3*vd*np.conj(ig);internal=3*(eg*np.conj(ig)).real
        values.update({'g.'+k:float(v) for k,v in dict(current_pu=abs(ig)/self.gib,p_w=port.real,q_var=port.imag,electrical_power_pu=internal/rated).items()})
        shaft=self.mechanical(x)*rated;damp=self.gp.damping_pu*(speed-1)*rated;dk=2*self.gp.inertia_s*rated*speed*rate[-5];dt=self.heat.capacity_j_per_k*rate[self.heat_address]
        data=dict(shaft_power_w=shaft,terminal_power_w=port.real,heat_input_w=heat['copper_loss_w']+heat['core_loss_w'],ambient_heat_w=heat['ambient_heat_w'],signed_damping_exchange_w=damp,kinetic_rate_w=dk,thermal_rate_w=dt,balance_residual_w=shaft-port.real-damp-heat['ambient_heat_w']-dk-dt,kinetic_energy_j=self.gp.inertia_s*rated*speed**2,thermal_energy_j=self.heat.capacity_j_per_k*(x[self.heat_address]-self.heat.ambient_c))
        values.update({'g.energy.'+k:float(v) for k,v in data.items()})
        zg=self.impedance_at(x[self.heat_address]);data=dict(resistance_ohm=zg.real,resistance_pu=zg.real*self.gp.rated_va/self.g.ll_v**2,internal_power_w=internal,copper_electrical_loss_w=3*zg.real*abs(ig)**2,power_balance_w=internal-port.real-3*zg.real*abs(ig)**2)
        values.update({'g.stator.'+k:float(v) for k,v in data.items()});return values
