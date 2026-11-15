"""Independent two SI rotor fluxes + fixed-field generator swing/PV root.

Single authored downstream generator. Network unknowns remain SI currents and
voltages; no native motor/generator initialization or tangent helper is used.
"""
import copy
import math
import numpy as np
from scipy.linalg import lu_factor,lu_solve
from scipy.optimize import least_squares

from benchmarks.two_cage_winding_reference import TwoCageReference
from power_grid_env.study_spec import StudySpec


class MixedReference(TwoCageReference):
    def __init__(self,study):
        spec=StudySpec.model_validate(study)
        if spec.coupled.winding_generator_profile!='GENCLS_fixed_field' or len(spec.network.generators)!=1 or len(spec.coupled.machines)!=1:
            raise ValueError('one explicit fixed-field generator required')
        g=spec.network.generators[0];p=spec.coupled.machines[0]
        if p.model!='GENCLS' or any([p.governor,p.exciter,p.stabilizer]) or spec.coupled.operating_point is None or spec.coupled.operating_point.winding_state!='passport':raise ValueError('loaded frozen-temperature GENCLS reference required')
        terminals={t.id:t.connectivity_node_id for t in spec.network.terminals}
        if terminals[g.terminal_id]!=spec.coupled.motors[0].node_id:raise ValueError('reference generator must share downstream motor PCC')
        surrogate=copy.deepcopy(study);surrogate['network']['generators']=[];surrogate['network']['terminals']=[t for t in surrogate['network']['terminals'] if t['id']!=g.terminal_id];surrogate['coupled']['machines']=[]
        super().__init__(surrogate);self.study=spec;self.g=g;self.gp=p;self.gv=g.ll_v/math.sqrt(3);self.gib=p.rated_va/(3*self.gv)
        self.gz=1j*p.x_d_prime_pu*g.ll_v**2/p.rated_va
        self.w.initialize=self.initial_conditions

    def static_mixed(self,speed,eg,ts,tr):
        w=self.w;rs,rr=self.resistances(ts,tr);slip=1-speed
        if abs(slip)<1e-14:ratio=np.zeros(2,complex)
        else:ratio=np.linalg.solve(np.diag(rr/slip)+1j*w.wb*self.lr,np.full(2,-1j*w.wb*w.lm))
        zm=rs+1j*w.wb*w.ls+1j*w.wb*w.lm*sum(ratio)
        yl,ys,yg=1/w.zl,1/w.zs,1/self.gz
        matrix=np.array([[ys+yl,-yl],[-yl,yl+w.yl+1/zm+yg]],complex)
        vu,vd=np.linalg.solve(matrix,[ys*w.es,yg*eg])
        im=vd/zm;ir=ratio*im;flux=w.lm*im+self.lr@ir;ig=(eg-vd)/self.gz
        torque=-3*w.wb*w.lm*(sum(ir)*np.conj(im)).imag/w.m.rated_va
        return vu,vd,im,ir,flux,ig,float(torque)

    def initial_conditions(self):
        w=self.w;ts,tr=self.seed_temperatures;op=self.study.coupled.operating_point
        def residual(x):
            eg=complex(x[1],x[2])*self.gv
            _,vd,im,ir,flux,ig,torque=self.static_mixed(x[0],eg,ts,tr)
            return [torque-w.load(x[0]),(3*(vd*np.conj(ig)).real-self.g.p_w)/self.gp.rated_va,(abs(vd)/self.gv)**2-self.g.v_set_pu**2]
        root=None
        for speed in [.99,.96,.88]:
            result=least_squares(residual,[speed,1.1,.05],bounds=([op.minimum_speed_pu,-10.,-10.],[1.,10.,10.]),jac='3-point',xtol=1e-14,ftol=1e-14,gtol=1e-14,max_nfev=500)
            if max(abs(np.array(residual(result.x))))<1e-11:root=result.x;break
        if root is None:raise ValueError('independent mixed PV/motor root not found')
        speed=root[0];eg=complex(root[1],root[2])*self.gv
        _,vd,im,ir,flux,ig,torque=self.static_mixed(speed,eg,ts,tr)
        self.g_amplitude=abs(eg);self.g_initial_delta=float(np.angle(eg));self.g_pm=3*(eg*np.conj(ig)).real/self.gp.rated_va
        self.initial_flux2=[float(flux[1].real),float(flux[1].imag)];self.initial_temperatures=(ts,tr)
        state=np.array([flux[0].real,flux[0].imag,speed,(abs(im)/w.ib)**2])
        full=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        context=dict(on=True,line=True,source=True,faults=set(),relays={r.id:[False,False,r.measurement_available] for r in self.relays})
        # Frozen-temperature electromechanical subspace, matching native scope.
        indices=np.array([0,1,2,len(full)-6,len(full)-5,len(full)-4,len(full)-3]);matrix=np.empty((7,7))
        for j,i in enumerate(indices):
            h=1e-6*max(1.,abs(full[i]));a=full.copy();b=full.copy();a[i]+=h;b[i]-=h
            matrix[:,j]=(self.rhs(0,a,context)[indices]-self.rhs(0,b,context)[indices])/(2*h)
        spectrum=np.linalg.eigvals(matrix)
        return state,dict(mode='independent_SI_PV_two_cage_root',speed_pu=float(speed),generator_delta_rad=self.g_initial_delta,
            generator_internal_pu=float(self.g_amplitude/self.gv),generator_mechanical_pu=float(self.g_pm),generator_q_var=float(3*(vd*np.conj(ig)).imag),
            frozen_temperature_spectral_abscissa_per_s=float(max(spectrum.real)),thermal_equilibrium=False)

    def initial_tail(self):return [self.g_initial_delta,1.,*super().initial_tail()]

    def generator_emf(self,x):return self.g_amplitude*np.exp(1j*x[-6])

    def circuit(self,x,c):
        w=self.w;key=(c['on'],c['line'],c['source'],tuple(sorted(c['faults'])),float(x[-2]))
        if key not in self.factors:
            self.factors.clear();up=down=0j
            for id in c['faults']:
                f=w.faults[id];y=1/complex(f.r_ohm,f.x_ohm)
                if f.node_id==w.up:up+=y
                else:down+=y
            yl=1/w.zl if c['line'] else 0j;ys=1/w.zs if c['source'] else 0j;rs,_=self.resistances(x[-2],x[-1]);yg=1/self.gz
            a=np.array([[rs+1j*w.wb*w.ls,1j*w.wb*w.lm,1j*w.wb*w.lm,-1,0],
                        [w.lm,self.lr[0,0],self.lr[0,1],0,0],[w.lm,self.lr[1,0],self.lr[1,1],0,0],
                        [1,0,0,w.yl+down+yl+yg,-yl],[0,0,0,-yl,yl+ys+up]],complex)
            if not c['on']:a[0]=[1,0,0,0,0]
            if not np.any(a[4]):a[4,4]=1.
            self.factors[key]=lu_factor(a)
        q=lu_solve(self.factors[key],[0,complex(x[0],x[1]),complex(x[-4],x[-3]),self.generator_emf(x)/self.gz,w.es/w.zs if c['source'] else 0])
        return q[0],q[1:3],q[3],q[4]

    def rhs(self,t,x,c):
        motor=super().rhs(t,x,c);vd=self.circuit(x,c)[2];eg=self.generator_emf(x);ig=(eg-vd)/self.gz
        pe=3*(eg*np.conj(ig)).real/self.gp.rated_va
        swing=[self.w.wb*(x[-5]-1),(self.g_pm-pe-self.gp.damping_pu*(x[-5]-1))/(2*self.gp.inertia_s)]
        return np.r_[motor[:-4],swing,motor[-4:]]

    def sample(self,time,side='regular'):
        channels=super().sample(time,side);x,c=self.state_at(time,side);vd=self.circuit(x,c)[2];eg=self.generator_emf(x);ig=(eg-vd)/self.gz;power=3*vd*np.conj(ig)
        values=dict(delta=x[-6],omega=x[-5],rotor_frequency_hz=x[-5]*self.w.n.frequency_hz,current_pu=abs(ig)/self.gib,bus_voltage_pu=abs(vd)/self.gv,
            p_w=power.real,q_var=power.imag,electrical_power_pu=3*(eg*np.conj(ig)).real/self.gp.rated_va,mechanical_power_pu=self.g_pm,connected=1.,terminal_voltage_pu=abs(vd)/self.gv,terminal_angle_rad=np.angle(vd))
        channels.update({self.g.id+'.'+k:float(v) for k,v in values.items()});return channels
