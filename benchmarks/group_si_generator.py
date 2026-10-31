"""Independent SI six-state round rotor and transfer-block controls for groups."""
import cmath,math
import numpy as np
from scipy import signal
from scipy.optimize import root,brentq
from benchmarks.genrou_reference import saturation_curve
from benchmarks.induction_reference import mechanical_torque_reference


class Generator:
    def __init__(self,network,settings,feed,source_v,motor_steady,cold_current):
        if len(network.generators)!=1 or len(settings.machines)!=1:raise ValueError('one independent group generator required')
        self.g=network.generators[0];self.p=settings.machines[0];p=self.p;g=self.g
        if p.model=='GENROU' and p.x_d_subtransient_pu!=p.x_q_subtransient_pu:raise ValueError('independent group requires isotropic subtransient stator')
        if g.mode!='pv':raise ValueError('independent group generator requires PV initialization')
        if p.model!='GENROU' or p.generator_id!=g.id or not p.governor or not p.exciter or p.stabilizer:raise ValueError('independent group GENROU/TGOV1/SEXS without PSS required')
        self.wb=2*math.pi*network.frequency_hz;self.vbase=g.ll_v/math.sqrt(3);self.ibase=p.rated_va/(3*self.vbase)
        self.z=complex(p.armature_r_pu,p.x_d_subtransient_pu)*g.ll_v**2/p.rated_va
        self.gd1=(p.x_d_subtransient_pu-p.leakage_pu)/(p.x_d_prime_pu-p.leakage_pu)
        self.gd2=(p.x_d_prime_pu-p.x_d_subtransient_pu)/(p.x_d_prime_pu-p.leakage_pu)**2
        self.gq1=(p.x_q_subtransient_pu-p.leakage_pu)/(p.x_q_prime_pu-p.leakage_pu)
        self.gq2=(p.x_q_prime_pu-p.x_q_subtransient_pu)/(p.x_q_prime_pu-p.leakage_pu)**2
        self.gqd=(p.x_q_pu-p.leakage_pu)/(p.x_d_pu-p.leakage_pu)
        gov,avr=p.governor,p.exciter
        self.Ag,self.Bg,self.Cg,self.Dg=[float(x.item()) for x in signal.tf2ss([gov.T2,1.],[gov.T3,1.])]
        self.Aa,self.Ba,self.Ca,self.Da=[float(x.item()) for x in signal.tf2ss([avr.TATB*avr.TB,1.],[avr.TB,1.])]
        magnitude=g.v_set_pu*self.vbase;self.speeds=np.zeros(len(settings.motors))
        if settings.operating_point:
            # The PV magnitude fixes each motor's torque-speed curve; currents
            # still share the circuit in the phase/power and time-domain solves.
            for i,m in enumerate(settings.motors):
                if not m.connected:continue
                def residual(w):
                    self.speeds[i]=w
                    return motor_steady(self.speeds,magnitude)[1][i]-mechanical_torque_reference(m,w)
                grid=np.linspace(settings.operating_point.minimum_speed_pu,1.,501);roots=[]
                for lo,hi in zip(grid,grid[1:]):
                    if residual(lo)*residual(hi)<=0:roots.append(brentq(residual,lo,hi,xtol=1e-13))
                if not roots:raise ValueError('independent group motor equilibrium missing')
                self.speeds[i]=max(roots)
        def currents(v):return motor_steady(self.speeds,v)[2] if settings.operating_point else cold_current(v)
        def phase_error(value):
            v=magnitude*cmath.exp(1j*value[0]);ig=sum(currents(v))-(source_v-v)/feed
            return [(3*(v*np.conj(ig)).real-g.p_w)/p.rated_va]
        solved=root(phase_error,[cmath.phase(source_v)],tol=1e-11)
        if max(abs(np.array(phase_error(solved.x))))>1e-9:raise ValueError('independent group PV phase root failed')
        self.voltage=magnitude*cmath.exp(1j*solved.x[0]);ig=sum(currents(self.voltage))-(source_v-self.voltage)/feed;eg=self.voltage+self.z*ig
        q_initial=float((3*self.voltage*np.conj(ig)).imag)
        if (g.q_min_var is not None and q_initial<g.q_min_var) or (g.q_max_var is not None and q_initial>g.q_max_var):raise ValueError('independent group generator initial Q limit')
        def dq(delta):
            emf=eg/self.vbase*cmath.exp(-1j*delta);current=ig/self.ibase*cmath.exp(-1j*delta)
            return -current.imag,current.real,emf.real,-emf.imag
        def qerror(value):
            id_,iq,pd,pq=dq(value[0]);return [(1+saturation_curve(p,abs(eg)/self.vbase)*self.gqd)*pq-(p.x_q_pu-p.x_q_subtransient_pu)*iq]
        guess=cmath.phase(self.voltage/self.vbase+complex(p.armature_r_pu,p.x_q_pu)*ig/self.ibase)
        rotor=root(qerror,[guess],tol=1e-11)
        if max(abs(np.array(qerror(rotor.x))))>1e-9:raise ValueError('independent group GENROU initialization failed')
        delta=float(rotor.x[0]);id_,iq,pd,pq=dq(delta)
        field=(1+saturation_curve(p,abs(eg)/self.vbase))*pd+(p.x_d_pu-p.x_d_subtransient_pu)*id_
        self.pref=g.p_w/p.rated_va+p.armature_r_pu*abs(ig/self.ibase)**2;self.vref=abs(self.voltage)/self.vbase+field/avr.K
        if not avr.EMIN<field<avr.EMAX or not gov.VMIN<self.pref<gov.VMAX:raise ValueError('independent group controller initialization limit')
        self.initial=np.array([delta,1.,self.pref,-self.Bg/self.Ag*self.pref,-self.Ba/self.Aa*(field/avr.K),field,
            pd+(p.x_d_prime_pu-p.x_d_subtransient_pu)*id_,pq-(p.x_q_prime_pu-p.x_q_subtransient_pu)*iq,
            pd+(p.leakage_pu-p.x_d_subtransient_pu)*id_,pq+(p.x_q_subtransient_pu-p.leakage_pu)*iq])
        if max(abs(self.rhs(self.initial,ig,self.voltage)))>1e-8:raise ValueError('independent generator initial RHS not zero')
    def flux(self,x):
        p=self.p;return self.gd1*x[6]+self.gd2*(p.x_d_prime_pu-p.leakage_pu)*x[8],self.gq1*x[7]+(1-self.gq1)*x[9]
    def emf(self,x):
        pd,pq=self.flux(x);return complex(pd,-pq)*self.vbase*cmath.exp(1j*x[0])
    def rhs(self,x,current,voltage):
        p=self.p;gov=p.governor;avr=p.exciter;pd,pq=self.flux(x);eq,ed,psid,psiq=x[6:10]
        own=current/self.ibase*cmath.exp(-1j*x[0]);id_,iq=-own.imag,own.real;se=saturation_curve(p,math.hypot(pd,pq))
        ad=eq+(p.x_d_pu-p.x_d_prime_pu)*(self.gd1*id_-self.gd2*psid+self.gd2*eq)+se*pd
        aq=ed+(p.x_q_pu-p.x_q_prime_pu)*(self.gq2*ed-self.gq2*psiq-self.gq1*iq)+se*pq*self.gqd
        valve=np.clip(x[2],gov.VMIN,gov.VMAX);power=self.Cg*x[3]+self.Dg*valve-gov.Dt*(x[1]-1)
        dr=(self.pref-(x[1]-1)/gov.R-x[2])/gov.T1
        if (x[2]>=gov.VMAX and dr>0) or (x[2]<=gov.VMIN and dr<0):dr=0.
        error=self.vref-abs(voltage)/self.vbase;df=(avr.K*(self.Ca*x[4]+self.Da*error)-x[5])/avr.TE
        if (x[5]>=avr.EMAX and df>0) or (x[5]<=avr.EMIN and df<0):df=0.
        pe=3*(self.emf(x)*np.conj(current)).real/p.rated_va
        return np.array([self.wb*(x[1]-1),(power-pe-p.damping_pu*(x[1]-1))/(2*p.inertia_s),dr,self.Ag*x[3]+self.Bg*valve,self.Aa*x[4]+self.Ba*error,df,
            (np.clip(x[5],avr.EMIN,avr.EMAX)-ad)/p.td_prime_s,-aq/p.tq_prime_s,
            (eq-psid-(p.x_d_prime_pu-p.leakage_pu)*id_)/p.td_subtransient_s,(ed-psiq+(p.x_q_prime_pu-p.leakage_pu)*iq)/p.tq_subtransient_s])
    def signals(self,x,current,voltage):
        p=self.p;gov=p.governor;avr=p.exciter;pd,pq=self.flux(x);power=3*voltage*np.conj(current)
        values=dict(delta=x[0],omega=x[1],p_w=power.real,q_var=power.imag,current_pu=abs(current)/self.ibase,
            e1q=x[6],e1d=x[7],e2d=x[8],e2q=x[9],psi2d=pd,psi2q=pq,Se=saturation_curve(p,math.hypot(pd,pq)),field_pu=np.clip(x[5],avr.EMIN,avr.EMAX))
        values.update({'TGOV1.valve_pu':np.clip(x[2],gov.VMIN,gov.VMAX),'TGOV1.turbine_state_pu':x[3]/gov.T3,
            'TGOV1.output_pu':self.Cg*x[3]+self.Dg*np.clip(x[2],gov.VMIN,gov.VMAX)-gov.Dt*(x[1]-1),
            'SEXS.field_pu':np.clip(x[5],avr.EMIN,avr.EMAX),'SEXS.filter_pu':x[4]/avr.TB})
        return {self.g.id+'.'+k:float(v) for k,v in values.items()}
