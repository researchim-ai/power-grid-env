"""Machine-base TGOV1/SEXS blocks for the coupled classical generator profile.

Limited outputs are projected; raw anti-windup state overshoot is retained and
must pass a declared numerical tolerance. No parameters are auto-adjusted.
"""
from __future__ import annotations
import numpy as np
from power_grid_env.coupled_pss import CoupledPSS


def limited_rate(state, rate, low, high):
    if (state>=high and rate>0) or (state<=low and rate<0):
        return 0.
    return rate


class CoupledControllerBank:
    def __init__(self, passports, offset):
        self.passports=passports;self.offset=offset
        self.gov={};self.avr={};self.pss_addresses={};self.pref={};self.vref={}
        address=offset
        for i,machine in enumerate(passports):
            if machine.governor:
                self.gov[i]=(address,address+1);address+=2
            if machine.exciter:
                self.avr[i]=(address,address+1);address+=2
            if machine.stabilizer:
                self.pss_addresses[i]=slice(address,address+7);address+=7
        self.pss=CoupledPSS(passports,self.pss_addresses)
        self.end=address
        self.observed_overshoot={}

    def initialize(self,state,voltage,field,power,tolerance):
        epsilon=max(1e-8,10*tolerance)
        for i,(valve,lag) in self.gov.items():
            passport=self.passports[i].governor
            if power[i]<=passport.VMIN+epsilon or power[i]>=passport.VMAX-epsilon:
                return {'reason':'initial_governor_limit_or_boundary','generator':self.passports[i].generator_id}
            self.pref[i]=float(power[i]);state[valve]=state[lag]=power[i]
        for i,(filter_state,field_state) in self.avr.items():
            passport=self.passports[i].exciter
            if field[i]<=passport.EMIN+epsilon or field[i]>=passport.EMAX-epsilon:
                return {'reason':'initial_exciter_limit_or_boundary','generator':self.passports[i].generator_id}
            self.vref[i]=float(abs(voltage[i])+field[i]/passport.K)
            state[filter_state]=field[i]/passport.K;state[field_state]=field[i]
        for address in self.pss_addresses.values():state[address]=0.
        return None

    def field(self,state,fixed):
        values=np.array(fixed,copy=True)
        for i,(_,address) in self.avr.items():
            p=self.passports[i].exciter
            values[i]=np.clip(state[address],p.EMIN,p.EMAX)
        return values

    def mechanical(self,state,speed,fixed):
        values=np.array(fixed,copy=True)
        for i,(valve,lag) in self.gov.items():
            p=self.passports[i].governor
            bounded=np.clip(state[valve],p.VMIN,p.VMAX)
            values[i]=p.T2/p.T3*bounded+(1-p.T2/p.T3)*state[lag]-p.Dt*(speed[i]-1)
        return values

    def derivatives(self,state,speed,voltage):
        result=np.zeros(self.end-self.offset)
        pss_derivatives,pss_output,_=self.pss.evaluate(state,speed,voltage)
        for i,address in self.pss_addresses.items():
            result[address.start-self.offset:address.stop-self.offset]=pss_derivatives[i]
        for i,(valve,lag) in self.gov.items():
            p=self.passports[i].governor
            bounded=np.clip(state[valve],p.VMIN,p.VMAX)
            demand=self.pref[i]-(speed[i]-1)/p.R
            rate=(demand-state[valve])/p.T1
            result[valve-self.offset]=limited_rate(state[valve],rate,p.VMIN,p.VMAX)
            result[lag-self.offset]=(bounded-state[lag])/p.T3
        for i,(filter_state,field) in self.avr.items():
            p=self.passports[i].exciter
            error=self.vref[i]-abs(voltage[i])+pss_output[i]
            bounded=np.clip(state[field],p.EMIN,p.EMAX)
            rate=(p.K*(p.TATB*error+(1-p.TATB)*state[filter_state])-state[field])/p.TE
            result[filter_state-self.offset]=(error-state[filter_state])/p.TB
            result[field-self.offset]=limited_rate(state[field],rate,p.EMIN,p.EMAX)
        return result

    def channel_names(self):
        keys=[]
        for i in self.gov:
            gen=self.passports[i].generator_id
            keys.extend(gen+'.TGOV1.'+key for key in ['valve_pu','valve_state_pu','turbine_state_pu','reference_pu','output_pu'])
        for i in self.avr:
            gen=self.passports[i].generator_id
            keys.extend(gen+'.SEXS.'+key for key in ['field_pu','field_state_pu','filter_pu','reference_pu','error_pu'])
        return keys+self.pss.channel_names()

    def signals(self,state,speed,voltage,fixed_power):
        _,pss_output,result=self.pss.evaluate(state,speed,voltage)
        mechanical=self.mechanical(state,speed,fixed_power)
        for i,(valve,lag) in self.gov.items():
            p=self.passports[i].governor;gen=self.passports[i].generator_id
            values=dict(valve_pu=np.clip(state[valve],p.VMIN,p.VMAX),valve_state_pu=state[valve],
                        turbine_state_pu=state[lag],reference_pu=self.pref[i],output_pu=mechanical[i])
            result.update({gen+'.TGOV1.'+k:float(v) for k,v in values.items()})
        for i,(filter_state,field) in self.avr.items():
            p=self.passports[i].exciter;gen=self.passports[i].generator_id
            values=dict(field_pu=np.clip(state[field],p.EMIN,p.EMAX),field_state_pu=state[field],
                        filter_pu=state[filter_state],reference_pu=self.vref[i],error_pu=self.vref[i]-abs(voltage[i])+pss_output[i])
            result.update({gen+'.SEXS.'+k:float(v) for k,v in values.items()})
        return result

    def difference_step(self,state,address,default):
        """Keep an interior equilibrium Jacobian on the same limiter branch."""
        for i,(valve,_) in self.gov.items():
            if address==valve:
                p=self.passports[i].governor
                return min(default,.25*min(state[address]-p.VMIN,p.VMAX-state[address]))
        for i,(_,field) in self.avr.items():
            if address==field:
                p=self.passports[i].exciter
                return min(default,.25*min(state[address]-p.EMIN,p.EMAX-state[address]))
        return default

    def observe_accepted_states(self,states):
        for group,mapping,slot in [('TGOV1',self.gov,0),('SEXS',self.avr,1)]:
            for i,addresses in mapping.items():
                machine=self.passports[i];p=machine.governor if group=='TGOV1' else machine.exciter
                low,high=(p.VMIN,p.VMAX) if group=='TGOV1' else (p.EMIN,p.EMAX)
                values=states[addresses[slot]]
                key=(i,group)
                self.observed_overshoot[key]=max(self.observed_overshoot.get(key,0.),float(np.max(low-values)),float(np.max(values-high)))

    def validate_channels(self,channels,rtol,atol):
        limits={}
        for group,mapping,signal,raw,low_name,high_name in [
            ('TGOV1',self.gov,'valve_pu','valve_state_pu','VMIN','VMAX'),
            ('SEXS',self.avr,'field_pu','field_state_pu','EMIN','EMAX')]:
            for i in mapping:
                machine=self.passports[i];p=machine.governor if group=='TGOV1' else machine.exciter
                low,high=getattr(p,low_name),getattr(p,high_name)
                prefix=machine.generator_id+'.'+group+'.'
                bounded=np.asarray(channels[prefix+signal]);state=np.asarray(channels[prefix+raw])
                overshoot=float(max(0.,np.max(low-state),np.max(state-high),self.observed_overshoot.get((i,group),0.)))
                tolerance=max(20*atol,20*rtol*max(1.,abs(low),abs(high)))
                limits[prefix+signal]={'lower':low,'upper':high,'minimum':float(min(bounded)),'maximum':float(max(bounded)),
                    'maximum_raw_state_overshoot':overshoot,'state_overshoot_tolerance':tolerance,
                    'within_limits':bool(min(bounded)>=low and max(bounded)<=high and overshoot<=tolerance)}
        pss_limits={}
        for i in self.pss_addresses:
            m=self.passports[i];p=m.stabilizer;prefix=m.generator_id+'.IEEEST.'
            limited=np.asarray(channels[prefix+'limited_pu']);output=np.asarray(channels[prefix+'output_pu']);gate=np.asarray(channels[prefix+'voltage_gate'])
            valid=bool(np.all(limited>=p.LSMIN) and np.all(limited<=p.LSMAX) and np.all((gate==0)|(gate==1)) and np.all(output==np.where(gate,limited,0.)))
            pss_limits[m.generator_id]={'within_limits':valid,'lower':p.LSMIN,'upper':p.LSMAX,
                'minimum_limited_pu':float(min(limited)),'maximum_limited_pu':float(max(limited)),
                'sampled_blocked_outputs':int(sum(gate==0))}
        return {'ok':all(row['within_limits'] for row in [*limits.values(),*pss_limits.values()]),'controller_limits':limits,'pss_limits':pss_limits,
                'state_check_sampling':'accepted integration mesh and saved outputs; not continuous extremum certification'}
