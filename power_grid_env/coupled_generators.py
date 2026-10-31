"""Six-state round-rotor components for the shared positive-sequence network.

GENROU uses equal subtransient d/q reactances and the nominal-speed algebraic
stator (Flux0 convention). No stator flux states or silent parameter fitting.
"""
from __future__ import annotations
import math
import numpy as np
from scipy.optimize import root


def saturation(machine,flux):
    if machine.saturation=='disabled' or flux<=0:return 0.
    ratio=math.sqrt(machine.saturation_s10/(1.2*machine.saturation_s12))
    threshold=(1-1.2*ratio)/(1-ratio)
    gain=machine.saturation_s10/(1-threshold)**2
    return gain*max(flux-threshold,0.)**2/flux


class RoundRotorBank:
    def __init__(self,passports,offset):
        self.passports=passports;self.offset=offset;self.addresses={};self.coefficients={}
        for i,m in enumerate(passports):
            if m.model!='GENROU':continue
            self.addresses[i]=slice(offset,offset+4);offset+=4
            self.coefficients[i]=((m.x_d_subtransient_pu-m.leakage_pu)/(m.x_d_prime_pu-m.leakage_pu),
                (m.x_d_prime_pu-m.x_d_subtransient_pu)/(m.x_d_prime_pu-m.leakage_pu)**2,
                (m.x_q_subtransient_pu-m.leakage_pu)/(m.x_q_prime_pu-m.leakage_pu),
                (m.x_q_prime_pu-m.x_q_subtransient_pu)/(m.x_q_prime_pu-m.leakage_pu)**2,
                (m.x_q_pu-m.leakage_pu)/(m.x_d_pu-m.leakage_pu))
        self.end=offset

    def fluxes(self,state,i):
        m=self.passports[i];gd1,gd2,gq1,_,_=self.coefficients[i]
        eq,ed,psid,psiq=state[self.addresses[i]]
        return gd1*eq+gd2*(m.x_d_prime_pu-m.leakage_pu)*psid,gq1*ed+(1-gq1)*psiq

    def internal(self,state,rotor,field):
        emf=field*np.exp(1j*rotor[:,0])
        for i in self.addresses:
            pd,pq=self.fluxes(state,i)
            emf[i]=complex(pd,-pq)*np.exp(1j*rotor[i,0])
        return emf

    def initialize(self,state,rotor,internal,current,field):
        for i,address in self.addresses.items():
            m=self.passports[i];gqd=self.coefficients[i][-1]
            def quantities(delta):
                rotation=np.exp(-1j*delta)
                ir=current[i]*rotation;flux=internal[i]*rotation
                return -ir.imag,ir.real,flux.real,-flux.imag
            def balance(value):
                id_,iq,pd,pq=quantities(value[0])
                se=saturation(m,abs(internal[i]))
                return [(1+se*gqd)*pq-(m.x_q_pu-m.x_q_subtransient_pu)*iq]
            # Terminal + (Ra+jXq)I gives the unsaturated rotor-angle branch.
            guess=np.angle(internal[i]+1j*(m.x_q_pu-m.x_d_subtransient_pu)*current[i])
            solved=root(balance,[guess],tol=1e-11,options={'maxfev':100})
            if not np.isfinite(solved.x).all() or abs(balance(solved.x)[0])>1e-9:
                return {'reason':'round_rotor_initialization_failed','generator':m.generator_id}
            delta=float(solved.x[0]);id_,iq,pd,pq=quantities(delta)
            se=saturation(m,abs(internal[i]));efd=(1+se)*pd+(m.x_d_pu-m.x_d_subtransient_pu)*id_
            if not math.isfinite(efd) or efd<=0:
                return {'reason':'nonpositive_initial_round_rotor_field','generator':m.generator_id}
            rotor[i]=[delta,1.];field[i]=efd
            state[address]=[pd+(m.x_d_prime_pu-m.x_d_subtransient_pu)*id_,
                pq-(m.x_q_prime_pu-m.x_q_subtransient_pu)*iq,
                pd+(m.leakage_pu-m.x_d_subtransient_pu)*id_,
                pq+(m.x_q_subtransient_pu-m.leakage_pu)*iq]
        return None

    def derivatives(self,state,rotor,current,field):
        derivative=np.zeros(self.end-self.offset)
        for i,address in self.addresses.items():
            m=self.passports[i];gd1,gd2,gq1,gq2,gqd=self.coefficients[i]
            eq,ed,psid,psiq=state[address];pd,pq=self.fluxes(state,i)
            ir=current[i]*np.exp(-1j*rotor[i,0]);id_,iq=-ir.imag,ir.real
            se=saturation(m,math.hypot(pd,pq))
            ad=eq+(m.x_d_pu-m.x_d_prime_pu)*(gd1*id_-gd2*psid+gd2*eq)+se*pd
            aq=ed+(m.x_q_pu-m.x_q_prime_pu)*(gq2*ed-gq2*psiq-gq1*iq)+se*pq*gqd
            derivative[address.start-self.offset:address.stop-self.offset]=[
                (field[i]-ad)/m.td_prime_s,-aq/m.tq_prime_s,
                (eq-psid-(m.x_d_prime_pu-m.leakage_pu)*id_)/m.td_subtransient_s,
                (ed-psiq+(m.x_q_prime_pu-m.leakage_pu)*iq)/m.tq_subtransient_s]
        return derivative

    def signals(self,state,field):
        result={}
        for i,address in self.addresses.items():
            gen=self.passports[i].generator_id;pd,pq=self.fluxes(state,i)
            values=dict(zip(['e1q','e1d','e2d','e2q'],state[address]))
            values.update(psi2d=pd,psi2q=pq,Se=saturation(self.passports[i],math.hypot(pd,pq)),field_pu=field[i])
            result.update({gen+'.'+key:float(value) for key,value in values.items()})
        return result

    def channel_names(self):
        return [self.passports[i].generator_id+'.'+key for i in self.addresses for key in
                ['e1q','e1d','e2d','e2q','psi2d','psi2q','Se','field_pu']]
