"""Exact two-parallel-cage rotor reduction with algebraic stator elimination.

State coordinates are effective stator EMF E=u1+u2 plus cage contribution u1.
The rotor inductance matrix is positive definite for positive leakage values.
No magnetic saturation, iron loss or frequency-dependent rotor resistance.
"""
import math
import numpy as np


def double_cage_coefficients(motor,frequency):
    xm=motor.magnetizing_x_pu
    matrix=np.array([[xm+motor.rotor_x_pu,xm],[xm,xm+motor.rotor2_x_pu]])+motor.rotor_mutual_x_pu
    inverse=np.linalg.inv(matrix);c=xm*inverse@np.ones(2)
    if not np.isfinite(c).all() or min(c)<=0:raise ValueError('unrepresentable double-cage flux weights')
    wb=2*math.pi*frequency;resistance=np.diag([motor.rotor_r_pu,motor.rotor2_r_pu])
    au=-wb*np.diag(c)@resistance@inverse@np.diag(1/c)
    bu=1j*wb*np.diag(c)@resistance@inverse@(xm*np.ones(2))
    transform=np.array([[1.,1.],[1.,0.]])
    a=transform@au@np.linalg.inv(transform);b=transform@bu
    leakage=motor.stator_x_pu+xm-xm*sum(c)
    if leakage<=0 or not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('unrepresentable double-cage dynamics')
    return leakage,a,b,c


class MotorCageBank:
    def __init__(self,motors,frequency,start):
        self.addresses={};self.coefficients={};self.inverse={};self.end=start;self.frequency=frequency
        for i,m in enumerate(motors):
            if m.model!='double_cage':continue
            self.addresses[i]=slice(self.end,self.end+2);self.end+=2
            self.coefficients[i]=double_cage_coefficients(m,frequency)
            self.inverse[i]=np.linalg.inv(np.array([[m.magnetizing_x_pu+m.rotor_x_pu,m.magnetizing_x_pu],[m.magnetizing_x_pu,m.magnetizing_x_pu+m.rotor2_x_pu]])+m.rotor_mutual_x_pu)

    def steady(self,i,speed):
        _,a,b,_=self.coefficients[i]
        return np.linalg.solve(1j*2*math.pi*self.frequency*(1-speed)*np.eye(2)-a,b)

    def response(self,constants,speed):
        wb=2*math.pi*self.frequency
        return np.array([self.steady(i,w)[0] if i in self.addresses else 1j*c[1]/(1+1j*wb*c[2]*(1-w)) for i,(c,w) in enumerate(zip(constants,speed))])

    def initialize(self,state,current,speed):
        for i,address in self.addresses.items():
            aux=self.steady(i,speed[i])[1]*current[i]
            state[address]=[aux.real,aux.imag]

    def derivatives(self,state,current,base_derivative):
        extra=np.zeros(self.end-(min((a.start for a in self.addresses.values()),default=self.end)))
        for j,(i,address) in enumerate(self.addresses.items()):
            e=complex(state[3*i],state[3*i+1]);u1=complex(*state[address]);w=state[3*i+2]
            _,a,b,_=self.coefficients[i];z=np.array([e,u1]);dz=a@z+b*current[i]-1j*2*math.pi*self.frequency*(1-w)*z
            base_derivative[3*i:3*i+2]=[dz[0].real,dz[0].imag];extra[2*j:2*j+2]=[dz[1].real,dz[1].imag]
        return extra

    def channels(self,motors):
        return {motors[i].id+'.'+key:[] for i in self.addresses for key in ['cage1_e_real_pu','cage1_e_imag_pu','cage2_e_real_pu','cage2_e_imag_pu','rotor1_current_pu','rotor2_current_pu','rotor1_copper_loss_w','rotor2_copper_loss_w']}

    def signals(self,state,motors,current):
        out={}
        for i,address in self.addresses.items():
            e=complex(state[3*i],state[3*i+1]);u1=complex(*state[address]);u2=e-u1
            m=motors[i];c=self.coefficients[i][3];rotor=self.inverse[i]@(np.array([u1,u2])/(1j*c)-m.magnetizing_x_pu*current[i])
            out.update({m.id+'.rotor1_current_pu':float(abs(rotor[0])),m.id+'.rotor2_current_pu':float(abs(rotor[1])),m.id+'.rotor1_copper_loss_w':float(m.rated_va*m.rotor_r_pu*abs(rotor[0])**2),m.id+'.rotor2_copper_loss_w':float(m.rated_va*m.rotor2_r_pu*abs(rotor[1])**2)})
            out.update({motors[i].id+'.'+k:float(v) for k,v in dict(cage1_e_real_pu=u1.real,cage1_e_imag_pu=u1.imag,cage2_e_real_pu=u2.real,cage2_e_imag_pu=u2.imag).items()})
        return out
