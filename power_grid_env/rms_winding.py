"""Opt-in single/two-cage RMS winding heat and exact state-dependent Norton actions.

Algebraic stator, own-rated RMS pu currents, no iron loss/saturation. Physical
winding temperatures are separate from the independent I-squared relay replica.
"""
import math
import numpy as np
from pydantic import Field, model_validator
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import splu
from power_grid_env.flux_machine import WindingThermal


class RMSWindingValidityError(ValueError):
    def __init__(self,motor_id,temperatures,maximum_c):
        super().__init__('RMS winding temperature outside passport validity')
        self.motor_id=motor_id
        self.temperatures=[float(t) if np.isfinite(t) else None for t in temperatures]
        self.maximum_c=maximum_c


class RMSWindingThermal(WindingThermal):
    initial_temperature_c: tuple[float, float] | None = None
    provenance: str = Field(min_length=1)

    @model_validator(mode='after')
    def initial_valid(self):
        if not self.provenance.strip():raise ValueError('winding thermal provenance required')
        temperatures=self.initial_temperature_c or (self.ambient_c,self.ambient_c)
        if any(t < -273.15 or t >= self.maximum_c or 1+self.alpha_per_c*(t-self.reference_c)<=0 for t in temperatures):
            raise ValueError('initial RMS winding temperature outside passport validity')
        return self


def operator(model,state):
    bank=getattr(model,'windings',None)
    return bank.operator(state) if bank is not None else (model.matrix,model.lu)


class RMSWindingBank:
    def __init__(self,model,start):
        if model.settings.jacobian_network!='direct_solve':raise ValueError('RMS winding feedback requires direct_solve cage profile')
        if model.network.generators or getattr(model.settings,'machines',[]):
            from power_grid_env.mixed_winding import supported
            if not supported(model.settings):raise ValueError('RMS winding generators require GENCLS_fixed_field profile')
        self.model=model;self.addresses={};self.end=start;self.cache=None
        self.cage_reference={i:(v[1].copy(),v[2].copy()) for i,v in model.cages.coefficients.items()}
        for i,m in enumerate(model.settings.motors):
            if m.winding_thermal is None:continue
            h=m.winding_thermal;self.addresses[i]=slice(self.end,self.end+2);self.end+=2
            ts,tr=h.initial_temperature_c or (h.ambient_c,h.ambient_c)
            a=1+h.alpha_per_c*(ts-h.reference_c);b=1+h.alpha_per_c*(tr-h.reference_c)
            model.zmotor[i]=complex(m.stator_r_pu*a,model.constants[i][0])
            xp,diff,tau=model.constants[i];model.constants[i]=(xp,diff,tau/b)
            if i in self.cage_reference:
                xp,aa,bb,c=model.cages.coefficients[i];model.cages.coefficients[i]=(xp,aa*b,bb*b,c)

    def cage_factor(self,i,state):
        if i not in self.addresses:return 1.
        h=self.model.settings.motors[i].winding_thermal
        return 1+h.alpha_per_c*(state[self.addresses[i].start+1]-h.reference_c)

    def cage_steady(self,i,speed,state):
        aa,bb=self.cage_reference[i];factor=self.cage_factor(i,state)
        wb=2*math.pi*self.model.network.frequency_hz
        matrix=1j*wb*(1-speed)*np.eye(2)-factor*aa
        value=np.linalg.solve(matrix,factor*bb)
        dw=np.linalg.solve(matrix,1j*wb*value)
        alpha=self.model.settings.motors[i].winding_thermal.alpha_per_c if i in self.addresses else 0.
        dt=np.linalg.solve(matrix,alpha*(aa@value+bb))
        return value,dw,dt

    def rotor_values(self,i,state,current):
        m=self.model.settings.motors[i];e=complex(*state[3*i:3*i+2])
        if i in self.cage_reference:
            u1=complex(*state[self.model.cages.addresses[i]])
            c=self.model.cages.coefficients[i][3]
            ir=self.model.cages.inverse[i]@(np.array([u1,e-u1])/(1j*c)-m.magnetizing_x_pu*current)
            rr=np.array([m.rotor_r_pu,m.rotor2_r_pu])*self.cage_factor(i,state)
        else:
            ir=np.array([e/(1j*m.magnetizing_x_pu)-m.magnetizing_x_pu/(m.rotor_x_pu+m.magnetizing_x_pu)*current])
            rr=np.array([m.rotor_r_pu*self.cage_factor(i,state)])
        return ir,rr

    def initialize(self,state):
        for i,a in self.addresses.items():
            h=self.model.settings.motors[i].winding_thermal
            state[a]=h.initial_temperature_c or (h.ambient_c,h.ambient_c)
        return state

    def parameters(self,state):
        model=self.model
        z=model.zmotor.copy();rr=np.array([m.rotor_r_pu for m in model.settings.motors])
        for i,a in self.addresses.items():
            m=model.settings.motors[i];h=m.winding_thermal;ts,tr=state[a]
            factors=1+h.alpha_per_c*(np.array([ts,tr])-h.reference_c)
            if not np.isfinite(factors).all() or min(factors)<=0 or min(ts,tr)<-273.15 or max(ts,tr)>h.maximum_c:
                raise RMSWindingValidityError(m.id,(ts,tr),h.maximum_c)
            z[i]=complex(m.stator_r_pu*factors[0],model.constants[i][0]);rr[i]=m.rotor_r_pu*factors[1]
        return z,rr

    def admittance(self,state):
        z,_=self.parameters(state)
        return np.where(self.model.connected,self.model.factors/z,0j)

    def operator(self,state):
        model=self.model
        key=(model.lu,tuple(float(v) for a in self.addresses.values() for v in state[a]))
        if self.cache is not None and self.cache[0]==key:return self.cache[1:]
        delta=self.admittance(state)-model.motor_y
        matrix=model.matrix.tolil(copy=True)
        for bus,value in zip(model.motor_buses,delta):matrix[bus,bus]+=value
        matrix=csc_matrix(matrix);factor=splu(matrix)
        self.cache=(key,matrix,factor)
        return matrix,factor

    def injection(self,state,rhs):
        delta=self.admittance(state)-self.model.motor_y
        e=state[:self.model.electrical_size].reshape(-1,3)
        np.add.at(rhs,self.model.motor_buses,delta*(e[:,0]+1j*e[:,1]))
        return rhs

    def effective_injection(self,state,u,voltage):
        model=self.model;z,_=self.parameters(state);y=self.admittance(state)
        re=np.arange(len(model.settings.motors))*3;e=state[re]+1j*state[re+1]
        out=np.zeros((len(model.bus_names),u.shape[1]),complex)
        np.add.at(out,model.motor_buses,y[:,None]*(u[re]+1j*u[re+1]))
        for i,a in self.addresses.items():
            m=model.settings.motors[i];h=m.winding_thermal
            dy=-model.factors[i]*m.stator_r_pu*h.alpha_per_c/z[i]**2 if model.connected[i] else 0j
            out[model.motor_buses[i]]+=dy*(e[i]-voltage[model.motor_buses[i]])*u[a.start]
        return out

    def derivatives(self,state,current,torque):
        model=self.model;z,rr=self.parameters(state);wb=2*math.pi*model.network.frequency_hz
        result=np.zeros(model.motor_state_end)
        for i,m in enumerate(model.settings.motors):
            e=complex(*state[3*i:3*i+2]);speed=state[3*i+2]
            rate=wb*rr[i]/(m.rotor_x_pu+m.magnetizing_x_pu)
            de=-(rate+1j*wb*(1-speed))*e+1j*model.constants[i][1]*rate*current[i]
            if i in self.cage_reference:
                address=model.cages.addresses[i];aa,bb=self.cage_reference[i]
                rotor=np.array([e,complex(*state[address])])
                dz=self.cage_factor(i,state)*(aa@rotor+bb*current[i])-1j*wb*(1-speed)*rotor
                de=dz[0];result[address]=[dz[1].real,dz[1].imag]
            result[3*i:3*i+3]=[de.real,de.imag,(torque[i]-m.load_torque(speed)*model.load_scale[i])/(2*m.inertia_s)]
            if i in model.thermal_indices:
                a=model.thermal_indices[i];result[a]=(abs(current[i])**2-state[a])/m.thermal.time_constant_s
            if i in self.addresses:
                a=self.addresses[i];h=m.winding_thermal;ts,tr=state[a]
                ir=e/(1j*m.magnetizing_x_pu)-m.magnetizing_x_pu/(m.rotor_x_pu+m.magnetizing_x_pu)*current[i]
                ps=m.rated_va*z[i].real*abs(current[i])**2;pr=m.rated_va*rr[i]*abs(ir)**2
                if i in self.cage_reference:
                    irs,rs_=self.rotor_values(i,state,current[i]);pr=m.rated_va*float(sum(rs_*abs(irs)**2))
                cross=h.cross_w_per_k*(ts-tr)
                result[a]=[(ps-h.stator_ambient_w_per_k*(ts-h.ambient_c)-cross)/h.stator_capacity_j_per_k,
                           (pr-h.rotor_ambient_w_per_k*(tr-h.ambient_c)+cross)/h.rotor_capacity_j_per_k]
        return result

    def actions(self,time,state,u,ctx,voltage_directions=None):
        from power_grid_env.motor_jacobian import load_slope
        model=self.model;voltage,current=ctx[:2];z,rr=self.parameters(state)
        dv=operator(model,state)[1].solve(self.effective_injection(state,u,voltage)) if voltage_directions is None else voltage_directions
        re=np.arange(len(model.settings.motors))*3;de=u[re]+1j*u[re+1]
        di=np.where(model.connected[:,None],(dv[model.motor_buses]-de)/z[:,None],0j)
        for i,a in self.addresses.items():
            m=model.settings.motors[i];di[i]-=current[i]*m.stator_r_pu*m.winding_thermal.alpha_per_c/z[i]*u[a.start]
        out=np.zeros((model.motor_state_end,u.shape[1]));wb=2*math.pi*model.network.frequency_hz
        for i,m in enumerate(model.settings.motors):
            e=complex(*state[3*i:3*i+2]);speed=state[3*i+2];rate=wb*rr[i]/(m.rotor_x_pu+m.magnetizing_x_pu)
            d=-(rate+1j*wb*(1-speed))*de[i]+1j*wb*e*u[3*i+2]+1j*model.constants[i][1]*rate*di[i]
            if i in self.addresses:
                a=self.addresses[i];h=m.winding_thermal
                drate=wb*m.rotor_r_pu*h.alpha_per_c/(m.rotor_x_pu+m.magnetizing_x_pu)*u[a.start+1]
                d+=(-e+1j*model.constants[i][1]*current[i])*drate
            if i in self.cage_reference:
                address=model.cages.addresses[i];aa,bb=self.cage_reference[i]
                rotor=np.array([e,complex(*state[address])]);du=np.vstack([de[i],u[address.start]+1j*u[address.start+1]])
                df=(m.winding_thermal.alpha_per_c*u[self.addresses[i].start+1]) if i in self.addresses else np.zeros(u.shape[1])
                dz=self.cage_factor(i,state)*(aa@du+bb[:,None]*di[i])+((aa@rotor+bb*current[i])[:,None]*df)-1j*wb*(1-speed)*du+1j*wb*rotor[:,None]*u[3*i+2]
                d=dz[0];out[address.start]=dz[1].real;out[address.start+1]=dz[1].imag
            out[3*i]=d.real;out[3*i+1]=d.imag
            out[3*i+2]=((de[i]*np.conj(current[i])+e*np.conj(di[i])).real-load_slope(m,speed)*model.load_scale[i]*u[3*i+2])/(2*m.inertia_s)
            if i in model.thermal_indices:
                a=model.thermal_indices[i];out[a]=(2*(np.conj(current[i])*di[i]).real-u[a])/m.thermal.time_constant_s
            if i in self.addresses:
                a=self.addresses[i];h=m.winding_thermal;ds,dr=u[a.start],u[a.start+1]
                k=m.magnetizing_x_pu/(m.rotor_x_pu+m.magnetizing_x_pu)
                ir=e/(1j*m.magnetizing_x_pu)-k*current[i];dir_=de[i]/(1j*m.magnetizing_x_pu)-k*di[i]
                ps=m.rated_va*(m.stator_r_pu*h.alpha_per_c*ds*abs(current[i])**2+2*z[i].real*(np.conj(current[i])*di[i]).real)
                pr=m.rated_va*(m.rotor_r_pu*h.alpha_per_c*dr*abs(ir)**2+2*rr[i]*(np.conj(ir)*dir_).real)
                if i in self.cage_reference:
                    address=model.cages.addresses[i];du1=u[address.start]+1j*u[address.start+1]
                    weights=model.cages.coefficients[i][3]
                    irs,rs_=self.rotor_values(i,state,current[i])
                    dirs=model.cages.inverse[i]@(np.vstack([du1,de[i]-du1])/(1j*weights[:,None])-m.magnetizing_x_pu*di[i])
                    base=np.array([m.rotor_r_pu,m.rotor2_r_pu])
                    pr=m.rated_va*(sum(base*h.alpha_per_c*abs(irs)**2)*dr+2*np.sum(rs_[:,None]*(np.conj(irs[:,None])*dirs).real,axis=0))
                cross=h.cross_w_per_k*(ds-dr)
                out[a.start]=(ps-h.stator_ambient_w_per_k*ds-cross)/h.stator_capacity_j_per_k
                out[a.start+1]=(pr-h.rotor_ambient_w_per_k*dr+cross)/h.rotor_capacity_j_per_k
        return out

    def callback(self):
        from power_grid_env.jacobian_columns import attach,seeds
        model=self.model
        def columns(t,x,cols,ctx):
            _,u=seeds(model.motor_state_end,cols);return self.actions(t,x,u,ctx)
        def evaluate(t,x):
            return columns(t,x,np.arange(len(x)),model.electrical(x))
        cb=attach(evaluate,model,columns,self.actions);cb.jacobian_kind='analytic_RMS_winding_state_dependent_Norton'
        return cb

    def channels(self):
        extra={self.model.settings.motors[i].id+'.'+key:[] for i in self.addresses if i in self.cage_reference for key in ['rotor1_resistance_pu','rotor2_resistance_pu','rotor1_copper_loss_w','rotor2_copper_loss_w']}
        return extra | {self.model.settings.motors[i].id+'.'+k:[] for i in self.addresses for k in ['stator_temperature_c','rotor_temperature_c','stator_resistance_pu','rotor_resistance_pu','stator_copper_loss_w','rotor_copper_loss_w','stator_net_heat_w','rotor_net_heat_w']}

    def signals(self,state,current):
        z,rr=self.parameters(state);rates=self.derivatives(state,current,np.zeros(len(current)));out={}
        for i,a in self.addresses.items():
            m=self.model.settings.motors[i];h=m.winding_thermal;ts,tr=state[a];e=complex(*state[3*i:3*i+2])
            ir=e/(1j*m.magnetizing_x_pu)-m.magnetizing_x_pu/(m.rotor_x_pu+m.magnetizing_x_pu)*current[i]
            values=dict(stator_temperature_c=ts,rotor_temperature_c=tr,stator_resistance_pu=z[i].real,rotor_resistance_pu=rr[i],
                        stator_copper_loss_w=m.rated_va*z[i].real*abs(current[i])**2,rotor_copper_loss_w=m.rated_va*rr[i]*abs(ir)**2,
                        stator_net_heat_w=rates[a.start]*h.stator_capacity_j_per_k,rotor_net_heat_w=rates[a.start+1]*h.rotor_capacity_j_per_k)
            if i in self.cage_reference:
                irs,rs_=self.rotor_values(i,state,current[i]);loss=m.rated_va*rs_*abs(irs)**2
                values['rotor_copper_loss_w']=float(sum(loss))
                for j in range(2):
                    values['rotor'+str(j+1)+'_resistance_pu']=rs_[j]
                    values['rotor'+str(j+1)+'_copper_loss_w']=loss[j]
            out.update({m.id+'.'+k:float(v) for k,v in values.items()})
        return out
