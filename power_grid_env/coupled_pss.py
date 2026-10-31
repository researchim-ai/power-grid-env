"""Explicit seven-state IEEEST MODE=1 cascade, without hidden parameter changes."""
from __future__ import annotations
import numpy as np

STATE_NAMES=['F1.rate','F1.output','F2.rate','F2.state','LL1.state','LL2.state','WO.state']


class CoupledPSS:
    def __init__(self,passports,addresses):
        self.passports=passports;self.addresses=addresses

    def evaluate(self,state,speed,voltage):
        derivatives={};outputs=np.zeros(len(self.passports));signals={}
        for i,address in self.addresses.items():
            m=self.passports[i];p=m.stabilizer;u=speed[i]-1
            a,b,c,d,l1,l2,w=state[address]
            da=(u-b-p.A1*a)/p.A2;db=a
            dc=(b-d-p.A3*c)/p.A4;dd=c
            f2=d+p.A5*c+p.A6*dc
            dl1=(f2-l1)/p.T2;v1=l1+p.T1*dl1
            dl2=(v1-l2)/p.T4;v2=l2+p.T3*dl2
            dw=(p.KS*v2-w)/p.T6;raw=p.T5*dw
            limited=float(np.clip(raw,p.LSMIN,p.LSMAX))
            enabled=p.VCL<abs(voltage[i])<p.VCU
            out=limited if enabled else 0.
            derivatives[i]=np.array([da,db,dc,dd,dl1,dl2,dw]);outputs[i]=out
            values=dict(zip(STATE_NAMES,state[address]))
            values.update(sig=u,raw_pu=raw,limited_pu=limited,output_pu=out,voltage_gate=float(enabled))
            signals.update({m.generator_id+'.IEEEST.'+key:float(value) for key,value in values.items()})
        return derivatives,outputs,signals

    def channel_names(self):
        return [self.passports[i].generator_id+'.IEEEST.'+key for i in self.addresses
                for key in [*STATE_NAMES,'sig','raw_pu','limited_pu','output_pu','voltage_gate']]

    def boundaries(self,state,voltage,margin=1e-7):
        flags=[]
        for i in self.addresses:
            m=self.passports[i];p=m.stabilizer
            if min(abs(abs(voltage[i])-p.VCL),abs(abs(voltage[i])-p.VCU))<=margin:
                flags.append(m.generator_id+'.IEEEST.voltage_gate')
            # At equilibrium MODE=1 has zero washout output.
            if min(abs(p.LSMIN),abs(p.LSMAX))<=margin:
                flags.append(m.generator_id+'.IEEEST.output_limiter')
        return flags
