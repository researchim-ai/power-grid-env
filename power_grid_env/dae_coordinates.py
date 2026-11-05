"""Exact rotating coordinates for disconnected motor electrical states.

A constant slip reference is chosen separately for each computational segment.
Only coordinates change: physical slip, nonlinear mechanical torque, cage
coupling and thermal equations remain in the original RHS. Real rotations also
act on complex Krylov directions without complex conjugation.
"""
import math
import numpy as np


class RotatingMotorCoordinates:
    def __init__(self,model,time,state):
        self.time=float(time);pairs=[];frequencies=[]
        for i,on in enumerate(model.connected):
            if on:continue
            frequency=-2*math.pi*model.network.frequency_hz*(1-state[3*i+2])
            pairs.append((3*i,3*i+1));frequencies.append(frequency)
            if i in model.cages.addresses:
                a=model.cages.addresses[i];pairs.append((a.start,a.start+1));frequencies.append(frequency)
        self.pairs=np.asarray(pairs,dtype=int).reshape(-1,2);self.frequencies=np.asarray(frequencies);self.active=bool(len(pairs))

    def rotate(self,time,value,*,inverse=False):
        value=np.asarray(value)
        if not self.active:return value
        out=value.copy();phase=(np.asarray(time)-self.time)*self.frequencies if value.ndim==1 else self.frequencies[:,None]*(np.asarray(time)-self.time)
        if inverse:phase=-phase
        c=np.cos(phase);s=np.sin(phase);a=value[self.pairs[:,0]];b=value[self.pairs[:,1]]
        out[self.pairs[:,0]]=c*a-s*b;out[self.pairs[:,1]]=s*a+c*b
        return out

    def decode(self,time,value):return self.rotate(time,value)
    def encode(self,time,value):return self.rotate(time,value,inverse=True)

    def rate(self,value):
        value=np.asarray(value);out=np.zeros_like(value)
        if self.active:
            frequency=self.frequencies if value.ndim==1 else self.frequencies[:,None]
            out[self.pairs[:,0]]=-frequency*value[self.pairs[:,1]];out[self.pairs[:,1]]=frequency*value[self.pairs[:,0]]
        return out

    def block(self,time,indices,matrix):
        if not self.active:return matrix
        lookup={int(j):k for k,j in enumerate(indices)};rotation=np.eye(len(indices));rate=np.zeros_like(rotation)
        for (a,b),frequency in zip(self.pairs,self.frequencies):
            if a not in lookup and b not in lookup:continue
            if a not in lookup or b not in lookup:raise ValueError('rotating electrical pair split by preconditioner block')
            i,j=lookup[a],lookup[b];phase=frequency*(time-self.time);c=np.cos(phase);s=np.sin(phase)
            rotation[i,i]=rotation[j,j]=c;rotation[i,j]=-s;rotation[j,i]=s;rate[i,j]=-frequency;rate[j,i]=frequency
        return rotation.T@matrix@rotation-rate

    def physical_solution(self,solution):
        if not self.active:return solution
        solution.y=self.decode(solution.t,solution.y)
        solution.sol=PhysicalDenseSolution(solution.sol,self)
        if solution.y_events is not None:
            solution.y_events=[self.decode(t,y.T).T if len(t) else y for t,y in zip(solution.t_events,solution.y_events)]
        return solution


class PhysicalDenseSolution:
    def __init__(self,original,frame):
        self.original=original;self.frame=frame
        self.interpolants=[PhysicalInterpolant(x,frame) for x in original.interpolants]
    def __call__(self,time):return self.frame.decode(time,self.original(time))


class PhysicalInterpolant:
    def __init__(self,original,frame):self.original=original;self.frame=frame;self.t=original.t;self.t_old=original.t_old
    def __call__(self,time):return self.frame.decode(time,self.original(time))


class RestartInterpolant:
    def __init__(self,original,previous,current):self.original=original;self.previous=previous;self.current=current
    def __call__(self,time):return self.current.encode(time,self.previous.decode(time,self.original(time)))
