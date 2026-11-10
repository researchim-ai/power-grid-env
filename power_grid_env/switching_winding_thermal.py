"""Two-lump winding thermal midpoint with explicit stator/rotor heat paths.

Copper and eddy-current heat become stored thermal energy; only heat to the
ambient reservoir leaves the drive audit. Rotor cages share one rotor lump.
"""
import numpy as np


def winding_thermal_step(parameters,temperature,power,elapsed):
    h=parameters;C=np.array([h.stator_capacity_j_per_k,h.rotor_capacity_j_per_k])
    ambient_g=np.array([h.stator_ambient_w_per_k,h.rotor_ambient_w_per_k])
    cross=h.cross_w_per_k;H=np.diag(ambient_g)+cross*np.array([[1.,-1.],[-1.,1.]])
    before=np.asarray(temperature,dtype=float)
    after=np.linalg.solve(np.diag(C/elapsed)+H/2,(np.diag(C/elapsed)-H/2)@before+np.asarray(power)+ambient_g*h.ambient_c)
    ambient_heat=elapsed*ambient_g*((before+after)/2-h.ambient_c)
    return after,ambient_heat
