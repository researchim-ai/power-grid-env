"""Event-exact centered PWM for MOSFET half bridges with body diodes.

Gate-off is an open channel, not an open diode. PWM pulses shorter than twice
the dead time are excluded by an explicit duty bound. No overlap of channels.
"""
import math
import numpy as np

T=np.array([[1.,0.],[-.5,math.sqrt(3)/2],[-.5,-math.sqrt(3)/2]])
AB=2/3*T.T


def pwm_plan(voltage_ab,vdc,period,dead_time):
    if not 0<dead_time<period/10:raise ValueError('positive dead time below one tenth of PWM period required')
    if vdc<=0:return np.full(3,.5),[0.,period]
    abc=T@np.array([complex(voltage_ab).real,complex(voltage_ab).imag])
    abc-=.5*(max(abc)+min(abc))
    margin=2*dead_time/period
    duty=np.clip(.5+abc/vdc,margin,1-margin)
    edges={0.,period}
    for d in duty:
        on=.5*(1-d)*period;off=.5*(1+d)*period
        edges.update([on,on+dead_time,off,off+dead_time])
    return duty,sorted(edges)


def gates_at(local_time,duty,period,dead_time,enabled=True):
    if not enabled:return np.zeros((3,2),dtype=bool)
    on=.5*(1-np.asarray(duty))*period;off=.5*(1+np.asarray(duty))*period
    upper=(local_time>=on+dead_time)&(local_time<off)
    lower=(local_time<on)|(local_time>=off+dead_time)
    return np.column_stack([upper,lower])


def thermal_step(temperature,power,elapsed,capacity,conductance,ambient):
    """Implicit midpoint thermal RC; returns Tnext and integrated ambient heat."""
    temperature=np.asarray(temperature);power=np.asarray(power)
    result=((capacity/elapsed-conductance/2)*temperature+power+conductance*ambient)/(capacity/elapsed+conductance/2)
    ambient_j=elapsed*conductance*((temperature+result)/2-ambient)
    return result,ambient_j
