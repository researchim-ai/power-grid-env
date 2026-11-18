"""Independent SI stator R(T)/heat/energy model with one latched I2 opening.

No native trip/current/network/thermal helpers; external source stays connected.
"""
import copy
import numpy as np
from scipy.linalg import lu_factor,lu_solve
from power_grid_env.study_spec import StudySpec
from benchmarks.generator_stator_reference import GeneratorStatorReference
from benchmarks.generator_trip_reference import GeneratorTripReference


class StatorTripReference(GeneratorStatorReference):
    def __init__(self,study):
        original=StudySpec.model_validate(study);s=copy.deepcopy(study)
        if len(original.coupled.generator_thermal)!=1:raise ValueError('one generator I2 replica required')
        s['coupled']['generator_thermal']=[];super().__init__(s);self.study=original
        if original.coupled.events or self.w.m.contactor or any(r.pickup<1e8 for r in self.relays):raise ValueError('inactive motor/network protection without topology commands required')
        self.replica=original.coupled.generator_thermal[0];self.replica_init=self.replica.initial_level;self.replica_address=self.heat_address+1

    def initial_tail(self):
        tail=super().initial_tail();return [*tail[:-6],self.replica_init,*tail[-6:]]

    def initial_conditions(self):
        state,a=GeneratorStatorReference.initial_conditions(self)
        if self.replica.initialization=='equilibrium':self.replica_init=((self.g.p_w/self.gp.rated_va)**2+(a['generator_q_var']/self.gp.rated_va)**2)/self.g.v_set_pu**2
        if self.replica_init>=self.replica.trip_level:raise ValueError('initial replica at trip limit')
        a['generator_replica_initial_level']=float(self.replica_init);return state,a

    def circuit(self,x,c):
        w=self.w;connected=c.get('generator_on',True);zg=self.impedance_at(x[self.heat_address]);key=(connected,float(x[-2]),float(x[self.heat_address]),tuple(sorted(c['faults'])))
        if key not in self.factors:
            self.factors.clear();up=down=0j
            for id in c['faults']:
                f=w.faults[id];y=1/complex(f.r_ohm,f.x_ohm)
                if f.node_id==w.up:up+=y
                else:down+=y
            yl=1/w.zl;ys=1/w.zs;rs,_=self.resistances(x[-2],x[-1]);yg=1/zg if connected else 0j
            matrix=np.array([[rs+1j*w.wb*w.ls,1j*w.wb*w.lm,1j*w.wb*w.lm,-1,0],
             [w.lm,self.lr[0,0],self.lr[0,1],0,0],[w.lm,self.lr[1,0],self.lr[1,1],0,0],
             [1,0,0,w.yl+down+yl+yg,-yl],[0,0,0,-yl,yl+ys+up]],complex)
            self.factors[key]=lu_factor(matrix)
        q=lu_solve(self.factors[key],[0,complex(x[0],x[1]),complex(x[-4],x[-3]),self.generator_emf(x)/zg if connected else 0j,w.es/w.zs])
        return q[0],q[1:3],q[3],q[4]

    def current(self,x,c):return super().current(x,c) if c.get('generator_on',True) else 0j

    def control_values(self,x,c):return GeneratorTripReference.control_values(self,x,c)

    def heat_values(self,x,c):
        p=self.heat;t=x[self.heat_address];terminal=self.circuit(x,c)[2] if c.get('generator_on',True) else self.generator_emf(x)
        cu=3*self.impedance_at(t).real*abs(self.current(x,c))**2;core=p.core_loss_w*(abs(terminal)/self.gv)**2;removed=p.ambient_w_per_k*(t-p.ambient_c)
        return dict(temperature_c=float(t),copper_loss_w=float(cu),core_loss_w=float(core),ambient_heat_w=float(removed),net_heat_w=float(cu+core-removed))

    def rhs(self,t,x,c):
        base=GeneratorStatorReference.rhs(self,t,x,c)
        rate=((abs(self.current(x,c))/self.gib)**2-x[self.replica_address])/self.replica.time_constant_s
        return np.r_[base[:-6],rate,base[-6:]]

    def sample(self,time,side='regular'):
        values=GeneratorStatorReference.sample(self,time,side);x,c=self.state_at(time,side);connected=c.get('generator_on',True)
        terminal=self.circuit(x,c)[2] if connected else self.generator_emf(x)
        values.update({'g.connected':float(connected),'g.terminal_voltage_pu':float(abs(terminal)/self.gv),'g.terminal_angle_rad':float(np.angle(terminal)),
            'g.stator_thermal_level':float(x[self.replica_address]),'g.stator_thermal_latched':float(not connected)})
        return values

    def run(self,**kwargs):return GeneratorTripReference.run(self,**kwargs)
