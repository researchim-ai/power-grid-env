"""Independent SI P/V/load/heat root and full frozen-topology spectrum."""
import copy
import numpy as np
from scipy.optimize import least_squares
from benchmarks.mixed_winding_reference import MixedReference
from power_grid_env.study_spec import StudySpec


class MixedEquilibriumReference(MixedReference):
    def __init__(self,study):
        original=StudySpec.model_validate(study)
        if original.coupled.operating_point.winding_state!='equilibrium':raise ValueError('joint heat profile required')
        surrogate=copy.deepcopy(study);surrogate['coupled']['operating_point']['winding_state']='passport'
        super().__init__(surrogate);self.study=original

    def initial_conditions(self):
        w,h=self.w,self.h;op=self.study.coupled.operating_point
        def residual(x):
            speed,ts,tr,er,ei=x;eg=complex(er,ei)*self.gv
            _,vd,im,ir,flux,ig,torque=self.static_mixed(speed,eg,ts,tr)
            rs,rr=self.resistances(ts,tr);cross=h.cross_w_per_k*(ts-tr)
            return [torque-w.load(speed),
                (3*rs*abs(im)**2-h.stator_ambient_w_per_k*(ts-h.ambient_c)-cross)/w.m.rated_va,
                (3*sum(rr*abs(ir)**2)-h.rotor_ambient_w_per_k*(tr-h.ambient_c)+cross)/w.m.rated_va,
                (3*(vd*np.conj(ig)).real-self.g.p_w)/self.gp.rated_va,(abs(vd)/self.gv)**2-self.g.v_set_pu**2]
        lower=[op.minimum_speed_pu,h.ambient_c,h.ambient_c,-10.,-10.];upper=[1.,h.maximum_c-1e-7,h.maximum_c-1e-7,10.,10.];root=None
        for speed in [.99,.96,.88]:
            seed=np.clip([speed,*self.seed_temperatures,1.1,.05],np.array(lower)+1e-8,np.array(upper)-1e-8)
            result=least_squares(residual,seed,bounds=(lower,upper),jac='3-point',xtol=1e-14,ftol=1e-14,gtol=1e-14,max_nfev=500)
            if max(abs(np.array(residual(result.x))))<1e-11:root=result.x;break
        if root is None:raise ValueError('independent mixed P/V/heat root not found')
        speed,ts,tr,er,ei=root;eg=complex(er,ei)*self.gv
        _,vd,im,ir,flux,ig,torque=self.static_mixed(speed,eg,ts,tr)
        self.g_amplitude=abs(eg);self.g_initial_delta=float(np.angle(eg));self.g_pm=3*(eg*np.conj(ig)).real/self.gp.rated_va
        self.initial_flux2=[float(flux[1].real),float(flux[1].imag)];self.initial_temperatures=(float(ts),float(tr))
        state=np.array([flux[0].real,flux[0].imag,speed,(abs(im)/w.ib)**2]);full=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        context=dict(on=True,line=True,source=True,faults=set(),relays={r.id:[False,False,r.measurement_available] for r in self.relays})
        indices=np.r_[np.arange(4),np.arange(len(full)-6,len(full))];matrix=np.empty((10,10))
        for j,i in enumerate(indices):
            step=1e-6*max(1.,abs(full[i]));a=full.copy();b=full.copy();a[i]+=step;b[i]-=step
            matrix[:,j]=(self.rhs(0,a,context)[indices]-self.rhs(0,b,context)[indices])/(2*step)
        spectrum=np.linalg.eigvals(matrix)
        return state,dict(mode='independent_SI_PV_load_heat_root',speed_pu=float(speed),stator_temperature_c=float(ts),rotor_temperature_c=float(tr),
            generator_delta_rad=self.g_initial_delta,generator_internal_pu=float(self.g_amplitude/self.gv),generator_mechanical_pu=float(self.g_pm),
            generator_q_var=float(3*(vd*np.conj(ig)).imag),full_physical_spectral_abscissa_per_s=float(max(spectrum.real)),
            physical_eigenvalues_per_s=[[float(e.real),float(e.imag)] for e in spectrum],thermal_equilibrium=True)
