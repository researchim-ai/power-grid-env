"""Continuous entry/release guards for controller states in excitation studies.

Shared by every coupled generator/controller profile.
A bound is a discrete mode;
while held, the state derivative is exactly zero until the free rate reverses.
"""
import numpy as np


class ControllerBoundaries:
    def __init__(self, model):
        self.model=model;self.active={};self.entries={}
        for i,(valve,_) in model.controllers.gov.items():
            p=model.gen_passports[i].governor
            self.entries[valve]=(i,'TGOV1',p.VMIN,p.VMAX)
        for i,(_,field) in model.controllers.avr.items():
            p=model.gen_passports[i].exciter
            self.entries[field]=(i,'SEXS',p.EMIN,p.EMAX)

    def rates(self,state):
        m=self.model;bank=m.controllers
        if not self.entries:return {}
        voltage=m.generator_terminal_voltage(state,m.electrical(state)[0])
        rotor=state[m.gen_offset:m.rotor_end].reshape(-1,2)
        pss=bank.pss.evaluate(state,rotor[:,1],voltage)[1];bias=m.excitation_bias(state)
        rates={}
        for i,(valve,_) in bank.gov.items():
            p=m.gen_passports[i].governor
            rates[valve]=(bank.pref[i]-(rotor[i,1]-1)/p.R-state[valve])/p.T1
        for i,(f,field) in bank.avr.items():
            p=m.gen_passports[i].exciter;error=bank.vref[i]-abs(voltage[i])+pss[i]-bias[i]
            rates[field]=(p.K*(p.TATB*error+(1-p.TATB)*state[f])-state[field])/p.TE
        return rates

    def guards(self,state):
        ids=[];guards=[]
        for a,(i,group,low,high) in self.entries.items():
            if a in self.active:
                side=self.active[a]
                eps=64*np.finfo(float).eps
                initial=self.rates(state)[a]
                instant=initial>eps if side=='lower' else initial < -eps
                def release(t,x,a=a,side=side,instant=instant):
                    if instant:return 0.
                    rate=self.rates(x)[a];eps=64*np.finfo(float).eps
                    return rate-eps if side=='lower' else rate+eps
                release.terminal=True;release.direction=1 if side=='lower' else -1
                ids.append(('controller',a,'release'));guards.append(release)
            else:
                for side,limit,direction in [('lower',low,-1),('upper',high,1)]:
                    def entry(t,x,a=a,limit=limit):return x[a]-limit
                    entry.terminal=True;entry.direction=direction
                    ids.append(('controller',a,side));guards.append(entry)
        return ids,guards

    def refresh(self,time,state,event_log):
        if not self.active:return
        rates=self.rates(state);eps=64*np.finfo(float).eps
        roots={('controller',a,'release') for a,side in self.active.items()
               if (rates[a]>eps if side=='lower' else rates[a] < -eps)}
        self.apply(roots,time,state,event_log)

    def derivative(self,result):
        for a in self.active:result[a]=0.
        return result

    def apply(self,roots,time,state,event_log):
        selected=set(roots)
        if roots and self.model.settings.event_policy=='legacy':
            rates=self.rates(state)
            for a,(_,_,low,high) in self.entries.items():
                if a in self.active:continue
                eps=64*np.finfo(float).eps*max(1.,abs(low),abs(high))
                if abs(state[a]-low)<=eps and rates[a]<0:selected.add(('controller',a,'lower'))
                if abs(state[a]-high)<=eps and rates[a]>0:selected.add(('controller',a,'upper'))
        for _,a,side in sorted(selected):
            i,group,low,high=self.entries[a]
            if side=='release':self.active.pop(a,None)
            else:
                state[a]=low if side=='lower' else high
                self.active[a]=side
            event_log.append(dict(time_s=float(time),kind='controller_limit_release' if side=='release' else 'controller_limit_enter',equipment_id=self.model.generators[i].id,controller=group,bound=side))
