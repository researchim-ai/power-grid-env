"""Exact motor/thermal Jacobian after elimination of the fixed linear network.

Network current sensitivity is cached per contactor segment. Rotor-voltage
columns remain globally coupled; this is not a sparse large-machine DAE claim.
"""
import math
import numpy as np


def load_slope(motor,speed):
    if motor.mechanical_load is None:return 2*motor.fan_torque_pu*abs(speed)
    law=motor.mechanical_load
    derivative=law.viscous_torque_pu+2*law.quadratic_torque_pu*abs(speed)
    if law.friction_torque_pu:
        tangent=np.tanh(speed/law.smoothing_speed_pu)
        derivative+=law.friction_torque_pu/law.smoothing_speed_pu*(1-tangent*tangent)
    return derivative


def jacobian(model,state):
    count=len(model.settings.motors);dimension=model.motor_state_end
    if len(state)!=dimension:raise ValueError('motor-only Jacobian cannot include synchronous-machine states')
    if model._motor_current_response is None:
        rhs=np.zeros((len(model.bus_names),count),dtype=complex)
        for j,(bus,y) in enumerate(zip(model.motor_buses,model.motor_y)):rhs[bus,j]=y
        response=model.lu.solve(rhs)[model.motor_buses,:]
        model._motor_current_response=np.where(model.connected[:,None],(response-np.eye(count))/model.zmotor[:,None],0j)
    di=model._motor_current_response
    _,current,_,_=model.electrical(state)
    values=np.asarray(state[:model.electrical_size]).reshape(-1,3);e=values[:,0]+1j*values[:,1];speed=values[:,2]
    tau=np.asarray([c[2] for c in model.constants]);difference=np.asarray([c[1] for c in model.constants]);wb=2*math.pi*model.network.frequency_hz
    de=1j*(difference/tau)[:,None]*di-np.diag(1/tau+1j*wb*(1-speed))
    result=np.zeros((dimension,dimension));real=np.arange(count)*3;imag=real+1;omega=real+2
    result[np.ix_(real,real)]=de.real;result[np.ix_(real,imag)]=-de.imag
    result[np.ix_(imag,real)]=de.imag;result[np.ix_(imag,imag)]=de.real
    result[real,omega]=-wb*e.imag;result[imag,omega]=wb*e.real
    inertia=np.asarray([2*m.inertia_s for m in model.settings.motors])
    product=e[:,None]*np.conj(di)
    result[np.ix_(omega,real)]=(product.real+np.diag(current.real))/inertia[:,None]
    result[np.ix_(omega,imag)]=(product.imag+np.diag(current.imag))/inertia[:,None]
    result[omega,omega]=-np.asarray([load_slope(m,w) for m,w in zip(model.settings.motors,speed)])*model.load_scale/inertia
    for i,address in model.thermal_indices.items():
        thermal_tau=model.settings.motors[i].thermal.time_constant_s
        sensitivity=np.conj(current[i])*di[i,:]
        result[address,real]=2*sensitivity.real/thermal_tau;result[address,imag]=-2*sensitivity.imag/thermal_tau
        result[address,address]=-1/thermal_tau
    for i,address in model.cages.addresses.items():
        _,a,b,_=model.cages.coefficients[i]
        slip_frequency=wb*(1-speed[i]);u1=complex(*state[address])
        for output,row in [(0,real[i]),(1,address.start)]:
            sensitivity=b[output]*di[i,:]
            sensitivity=sensitivity.copy();sensitivity[i]+=a[output,0]-(1j*slip_frequency if output==0 else 0.)
            result[row,real]=sensitivity.real;result[row,imag]=-sensitivity.imag
            result[row+1,real]=sensitivity.imag;result[row+1,imag]=sensitivity.real
            auxiliary=a[output,1]-(1j*slip_frequency if output==1 else 0.)
            result[row,address.start]=auxiliary.real;result[row,address.start+1]=-auxiliary.imag
            result[row+1,address.start]=auxiliary.imag;result[row+1,address.start+1]=auxiliary.real
        result[address.start,omega[i]]=-wb*u1.imag;result[address.start+1,omega[i]]=wb*u1.real
    if not np.isfinite(result).all():raise ValueError('nonfinite motor Jacobian')
    return result
