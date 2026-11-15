"""Independent two SI rotor-flux windings + 5-row KCL and common rotor heat.

The inherited benchmark hybrid engine supplies independent clocks/actuators.
Native cage reduction, initial states, network stamps and tangents are unused.
"""
import copy
import numpy as np
from scipy.linalg import lu_factor,lu_solve
from scipy.optimize import brentq,least_squares

from benchmarks.rms_winding_reference import ElectrothermalReference
from power_grid_env.study_spec import StudySpec


class TwoCageReference(ElectrothermalReference):
    def __init__(self,study):
        original=StudySpec.model_validate(study);m=original.coupled.motors[0]
        if m.model!='double_cage' or m.winding_thermal is None:raise ValueError('two-cage winding passport required')
        surrogate=copy.deepcopy(study);p=surrogate['coupled']['motors'][0]
        p.update(model='single_cage',rotor2_r_pu=None,rotor2_x_pu=None,rotor_mutual_x_pu=0.)
        super().__init__(surrogate)
        self.study=original;self.motor=m;self.seed_temperatures=self.initial_temperatures
        self.zb=m.rated_ll_v**2/m.rated_va
        self.lr=self.zb/self.w.wb*(np.array([[m.magnetizing_x_pu+m.rotor_x_pu,m.magnetizing_x_pu],
                                          [m.magnetizing_x_pu,m.magnetizing_x_pu+m.rotor2_x_pu]])+m.rotor_mutual_x_pu)
        self.weights=self.w.lm*np.linalg.solve(self.lr,np.ones(2))
        self.rr20=m.rotor2_r_pu*self.zb
        self.w.initialize=self.initial_conditions

    def resistances(self,ts,tr):
        h=self.h
        return self.rs0*(1+h.alpha_per_c*(ts-h.reference_c)),np.array([self.rr0,self.rr20])*(1+h.alpha_per_c*(tr-h.reference_c))

    def static(self,speed,ts,tr):
        w=self.w;rs,rr=self.resistances(ts,tr);feed=w.zs+w.zl;scale=1+feed*w.yl;slip=1-speed
        if abs(slip)<1e-14:
            im=w.es/(scale*(rs+1j*w.wb*w.ls)+feed);ir=np.zeros(2,complex)
        else:
            matrix=np.empty((3,3),complex)
            matrix[0]=[scale*(rs+1j*w.wb*w.ls)+feed,scale*1j*w.wb*w.lm,scale*1j*w.wb*w.lm]
            matrix[1:,0]=1j*w.wb*w.lm;matrix[1:,1:]=np.diag(rr/slip)+1j*w.wb*self.lr
            solved=np.linalg.solve(matrix,[w.es,0,0]);im,ir=solved[0],solved[1:]
        vd=(rs+1j*w.wb*w.ls)*im+1j*w.wb*w.lm*sum(ir)
        flux=w.lm*im+self.lr@ir
        torque=-3*w.wb*w.lm*(sum(ir)*np.conj(im)).imag/w.m.rated_va
        return vd,im,ir,flux,float(torque)

    def initial_conditions(self):
        w,h=self.w,self.h;op=self.study.coupled.operating_point
        if op is None:
            self.initial_flux2=[0.,0.];self.initial_temperatures=self.seed_temperatures
            return np.array([0.,0.,0.,w.m.thermal.initial_level if w.m.thermal else 0.]),dict(mode='cold_two_rotor_zero_flux')
        def residual(v):
            speed,ts,tr=v;_,im,ir,_,torque=self.static(speed,ts,tr);rs,rr=self.resistances(ts,tr);cross=h.cross_w_per_k*(ts-tr)
            return [torque-w.load(speed),(3*rs*abs(im)**2-h.stator_ambient_w_per_k*(ts-h.ambient_c)-cross)/w.m.rated_va,
                    (3*sum(rr*abs(ir)**2)-h.rotor_ambient_w_per_k*(tr-h.ambient_c)+cross)/w.m.rated_va]
        if op.winding_state=='equilibrium':
            lower=[op.minimum_speed_pu,h.ambient_c,h.ambient_c];upper=[1.,h.maximum_c-1e-7,h.maximum_c-1e-7]
            found=None
            for guess in [.98,.94,.88]:
                seed=np.clip([guess,*self.seed_temperatures],np.array(lower)+1e-7,np.array(upper)-1e-7)
                solved=least_squares(residual,seed,bounds=(lower,upper),jac='3-point',xtol=1e-14,ftol=1e-14,gtol=1e-14,max_nfev=500)
                if max(abs(np.array(residual(solved.x))))<1e-11:found=solved.x;break
            if found is None:raise ValueError('independent bounded two-cage heat root not found')
            speed,ts,tr=found
        else:
            ts,tr=self.seed_temperatures;grid=np.linspace(op.minimum_speed_pu,1,257);roots=[]
            def balance(speed):return self.static(speed,ts,tr)[4]-w.load(speed)
            for a,b in zip(grid,grid[1:]):
                if balance(a)*balance(b)<0:roots.append(brentq(balance,a,b,xtol=1e-14,rtol=1e-14))
            if abs(balance(1.))<1e-12:roots.append(1.)
            if not roots:raise ValueError('independent high-speed two-cage root not bracketed')
            speed=max(roots)
        vd,im,ir,flux,torque=self.static(speed,ts,tr)
        heat=(abs(im)/w.ib)**2 if op.thermal_state=='equilibrium' and w.m.thermal else w.m.thermal.initial_level if w.m.thermal else 0.
        self.initial_flux2=[float(flux[1].real),float(flux[1].imag)];self.initial_temperatures=(float(ts),float(tr))
        state=np.array([flux[0].real,flux[0].imag,speed,heat])
        full=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        context=dict(on=True,line=True,source=True,faults=set(),relays={r.id:[False,False,r.measurement_available] for r in self.relays})
        indices=np.array([0,1,2,3,len(full)-4,len(full)-3,len(full)-2,len(full)-1]);jac=np.empty((8,8))
        for j,i in enumerate(indices):
            step=1e-6*max(1.,abs(full[i]));a=full.copy();b=full.copy();a[i]+=step;b[i]-=step
            jac[:,j]=(self.rhs(0,a,context)[indices]-self.rhs(0,b,context)[indices])/(2*step)
        modes=np.linalg.eigvals(jac);rates=self.rhs(0,full,context)
        return state,dict(mode='independent_two_rotor_T_circuit',speed_pu=float(speed),stator_temperature_c=float(ts),rotor_temperature_c=float(tr),
            current_pu=float(abs(im)/w.ib),voltage_pu=float(abs(vd)/w.vb),maximum_electromechanical_residual_per_s=float(max(abs(rates[[0,1,2,len(full)-4,len(full)-3]]))),
            eigenvalues_per_s=[[float(v.real),float(v.imag)] for v in modes])

    def initial_tail(self):return [*self.initial_flux2,*self.initial_temperatures]

    def circuit(self,x,c):
        w=self.w;key=(c['on'],c['line'],c['source'],tuple(sorted(c['faults'])),float(x[-2]))
        if key not in self.factors:
            self.factors.clear();up=down=0j
            for id in c['faults']:
                f=w.faults[id];y=1/complex(f.r_ohm,f.x_ohm)
                if f.node_id==w.up:up+=y
                else:down+=y
            yl=1/w.zl if c['line'] else 0j;ys=1/w.zs if c['source'] else 0j;rs,_=self.resistances(x[-2],x[-1])
            a=np.array([[rs+1j*w.wb*w.ls,1j*w.wb*w.lm,1j*w.wb*w.lm,-1,0],
                        [w.lm,self.lr[0,0],self.lr[0,1],0,0],[w.lm,self.lr[1,0],self.lr[1,1],0,0],
                        [1,0,0,w.yl+down+yl,-yl],[0,0,0,-yl,yl+ys+up]],complex)
            if not c['on']:a[0]=[1,0,0,0,0]
            if not np.any(a[4]):a[4,4]=1.
            self.factors[key]=lu_factor(a)
        q=lu_solve(self.factors[key],[0,complex(x[0],x[1]),complex(x[-4],x[-3]),0,w.es/w.zs if c['source'] else 0])
        return q[0],q[1:3],q[3],q[4]

    def rhs(self,t,x,c):
        w,h=self.w,self.h;im,ir,_,_=self.circuit(x,c);rs,rr=self.resistances(x[-2],x[-1])
        flux=np.array([complex(x[0],x[1]),complex(x[-4],x[-3])]);df=-rr*ir-1j*w.wb*(1-x[2])*flux
        torque=-3*w.wb*w.lm*(sum(ir)*np.conj(im)).imag/w.m.rated_va
        heat=((abs(im)/w.ib)**2-x[3])/w.m.thermal.time_constant_s if w.m.thermal else 0.
        rates=[df[0].real,df[0].imag,(torque-w.load(x[2]))/(2*w.m.inertia_s),heat];currents=self.currents(x,c)
        for i,r in enumerate(self.relays):
            active,latched,available=c['relays'][r.id];rate=0.
            if not latched:
                if not active:rate=-x[4+i]/r.reset_s
                elif available:
                    multiple=currents[r.measurement_kind]/r.pickup
                    if multiple>1:
                        if r.curve=='definite':rate=1/r.time_multiplier_s
                        else:
                            k,alpha={'standard_inverse':(.14,.02),'very_inverse':(13.5,1.),'extremely_inverse':(80.,2.)}[r.curve]
                            rate=np.expm1(alpha*np.log(multiple))/(k*r.time_multiplier_s)
            rates.append(rate)
        ts,tr=x[-2:];cross=h.cross_w_per_k*(ts-tr)
        rates.extend([df[1].real,df[1].imag,(3*rs*abs(im)**2-h.stator_ambient_w_per_k*(ts-h.ambient_c)-cross)/h.stator_capacity_j_per_k,
                      (3*sum(rr*abs(ir)**2)-h.rotor_ambient_w_per_k*(tr-h.ambient_c)+cross)/h.rotor_capacity_j_per_k])
        return np.array(rates)

    def sample(self,time,side='regular'):
        x,c=self.state_at(time,side);w,h=self.w,self.h;im,ir,vd,vu=self.circuit(x,c);rs,rr=self.resistances(x[-2],x[-1])
        flux=np.array([complex(x[0],x[1]),complex(x[-4],x[-3])]);u=1j*w.wb*self.weights*flux/w.vb;e=sum(u)
        values=dict(speed_pu=x[2],thermal_level=x[3],e_real_pu=e.real,e_imag_pu=e.imag,current_pu=abs(im)/w.ib,bus_voltage_pu=abs(vd)/w.vb,
                    connected=float(c['on']),thermal_latched=float(c['thermal']),stator_temperature_c=x[-2],rotor_temperature_c=x[-1],stator_resistance_pu=rs/self.zb,
                    rotor_resistance_pu=rr[0]/self.zb,stator_copper_loss_w=3*rs*abs(im)**2,rotor_copper_loss_w=3*sum(rr*abs(ir)**2))
        cross=h.cross_w_per_k*(x[-2]-x[-1]);values['stator_net_heat_w']=values['stator_copper_loss_w']-h.stator_ambient_w_per_k*(x[-2]-h.ambient_c)-cross
        values['rotor_net_heat_w']=values['rotor_copper_loss_w']-h.rotor_ambient_w_per_k*(x[-1]-h.ambient_c)+cross
        for j in range(2):
            values.update({f'cage{j+1}_e_real_pu':u[j].real,f'cage{j+1}_e_imag_pu':u[j].imag,f'rotor{j+1}_current_pu':abs(ir[j])/w.ib,
                           f'rotor{j+1}_resistance_pu':rr[j]/self.zb,f'rotor{j+1}_copper_loss_w':3*rr[j]*abs(ir[j])**2})
        channels={w.m.id+'.'+k:float(v) for k,v in values.items()}
        for i,r in enumerate(self.relays):
            f=c['relays'][r.id];channels.update({r.id+'.'+k:float(v) for k,v in dict(progress=x[4+i],active=f[0],latched=f[1],measurement_valid=f[2],measurement=self.currents(x,c)[r.measurement_kind] if f[2] else 0.).items()})
        if w.m.contactor:channels[w.m.id+'.contactor_voltage_available']=float(c['available'])
        for b in self.breakers.values():channels[b.id+'.closed']=float(c[b.target_kind])
        for f in w.s.faults:channels[f.id+'.current_a']=float(abs((vu if f.node_id==w.up else vd)/complex(f.r_ohm,f.x_ohm))) if f.id in c['faults'] else 0.
        return channels
