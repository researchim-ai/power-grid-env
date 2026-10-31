"""Online native-physics controller with independently located hybrid events.

No production controller, SI trajectory or precomputed control commands are
used. Root trials repeat the native implicit step from a full checkpoint.
Thermal replica integration uses exact convolution for a linear heat input.
"""
from __future__ import annotations
import math
import numpy as np
from scipy.optimize import brentq
from benchmarks.native_checkpoint import NativeCheckpoint

EPS=np.finfo(float).eps


def thermal_step(level,heat0,heat1,duration,tau):
    if duration<0 or tau<=0:raise ValueError('positive thermal time scale required')
    if duration==0:return float(level)
    q=duration/tau;one=-math.expm1(-q)
    ramp=q/2-q*q/6+q**3/24-q**4/120 if q<1e-4 else 1-one/q
    return float(level*(1-one)+heat0*one+(heat1-heat0)*ramp)


class NativeLoop:
    def __init__(self,spec):self.spec=spec

    def __call__(self,ctx):
        self.ctx=ctx;self.a=ctx['system'];self.settings=self.spec.coupled if self.spec.coupled else self.spec.induction
        self.motors=self.settings.motors;self.closed=np.array([m.connected for m in self.motors],dtype=bool);self.latched=np.zeros(len(self.motors),dtype=bool)
        self.controls={};self.thermal={};self.log=[];self.transitions=0;self.records=[];self.aux={};self.accepted=0;self.probes=0;self.rollbacks=0;self.maximum_g=ctx['initial_residual']
        n=self.spec.network;source=n.sources[0];line=n.lines[0]
        self.zs=complex(source.r_ohm,source.x_ohm);self.zl=complex(line.r_ohm[0][0]-line.r_ohm[0][1],line.x_ohm[0][0]-line.x_ohm[0][1])
        if abs(self.zs+self.zl)==0:raise ValueError('online native sensing requires nonzero total branch impedance')
        terminals={t.id:t.connectivity_node_id for t in n.terminals};self.source_node=terminals[source.terminal_id];self.plant_node=self.motors[0].node_id
        self.layout={m.id:(m,model,name,ctx['addresses'][m.id]) for m,model,name in ctx['layout']}
        volts=self.voltages();heating=self.heating()
        for i,m in enumerate(self.motors):
            if m.contactor:
                p=m.contactor;v=volts[p.sense_node_id]
                self.controls[i]=dict(available=bool(v>p.dropout_voltage_pu if self.closed[i] else v>=p.pickup_voltage_pu),since=0.,pending=False,opened=0.,retries=0)
            if m.thermal:
                warm=bool(self.settings.operating_point and self.settings.operating_point.thermal_state=='equilibrium')
                self.thermal[i]=heating[i] if warm else m.thermal.initial_level
                if self.thermal[i]>=m.thermal.trip_level:raise ValueError('initial native thermal latch is outside current initialization scope')
        timeline={}
        for e in self.settings.events:timeline.setdefault(e.time_s,[]).append(e)
        checkpoint=NativeCheckpoint(self.a);self.checkpoint=checkpoint;time=0.;h=ctx['step_s'];self.record(0.,'regular')
        grid=np.arange(math.ceil(self.settings.duration_s/h))*h;grid_index=1
        output=np.r_[np.arange(math.ceil(self.settings.duration_s/self.settings.step_s))*self.settings.step_s,self.settings.duration_s]
        output_index=1
        while time<self.settings.duration_s:
            while grid_index<len(grid) and grid[grid_index]<=time:grid_index+=1
            right=min([self.settings.duration_s,*[t for t in timeline if t>time],*[self.deadline(i) for i in self.controls],float(grid[grid_index]) if grid_index<len(grid) else math.inf])
            if right<=time:raise ValueError('native online deadline did not advance')
            checkpoint.capture();theta0=self.thermal.copy();heat0=self.heating();guards=self.guards(theta0)
            values0=self.guard_values(guards,theta0);theta1=self.advance(time,right,theta0,heat0);values1=self.guard_values(guards,theta1)
            crossings=[]
            for index,(kind,i,direction) in enumerate(guards):
                left,right_value=values0[index],values1[index]
                crosses=(left>0 and right_value<=0) if direction<0 else (left<0 and right_value>=0)
                if not crosses:continue
                def value(at,index=index):
                    if at==time:return values0[index]
                    if at==right:return values1[index]
                    checkpoint.restore();self.rollbacks+=1;theta=self.advance(time,at,theta0,heat0)
                    return self.guard_values(guards,theta)[index]
                root=brentq(value,time,right,xtol=5e-16,rtol=4*EPS,maxiter=80)
                crossings.append((root,kind,i,index))
            roots=[];end=right
            if crossings:
                end=min(x[0] for x in crossings)
                if abs(end-right)<=16*EPS*max(1.,abs(right)):end=right
                checkpoint.restore();self.rollbacks+=1;theta1=self.advance(time,end,theta0,heat0)
                residuals=self.guard_values(guards,theta1)
                first=min(crossings,key=lambda x:x[0]);roots=[(first[1],first[2])]
                # Compare guard residuals to the located root residual. This
                # preserves exact thermal ties without coalescing nearby levels.
                for root_time,kind,i,index in crossings:
                    if (kind,i) in roots:continue
                    if kind==first[1]:
                        tol=64*EPS*(max(abs(theta1[i]),abs(theta1[first[2]]),1.) if kind=='thermal' else 1.)
                        if abs(residuals[index]-residuals[first[3]])<=tol:roots.append((kind,i))
                    elif abs(root_time-first[0])<=8*EPS*max(1.,abs(end)):roots.append((kind,i))
            self.thermal=theta1;self.accepted+=1;self.a.TDS.custom_event=False;self.a.b_update(self.a.exist.pflow_tds)
            tolerance=16*EPS*max(1.,abs(end));scheduled=[]
            for t in list(timeline):
                if abs(t-end)<=tolerance:scheduled.extend(timeline.pop(t))
            due=any(self.deadline(i)<=end+tolerance for i in self.controls)
            event=bool(roots or scheduled or due)
            if event:
                self.record(end,'pre_event');self.handle(end,roots,scheduled);self.record(end,'post_event')
            else:
                # Retain the integration brackets around regular study samples.
                while output_index<len(output) and output[output_index]<=end+1e-14:output_index+=1
                near=output_index<len(output) and output[output_index]-end<h+1e-14
                after=output_index>0 and end-output[output_index-1]<h+1e-14
                if near or after or end==self.settings.duration_s:self.record(end,'regular')
            if end<=time:raise ValueError('native online event step did not advance')
            time=end
        metadata=dict(event_log=self.log,auxiliary_channels=self.aux,root_trial_steps=self.probes,rollback_count=self.rollbacks,checkpoint_discrete_inventory=checkpoint.discrete_inventory,controller_commands_precomputed=False,thermal_replica='Exact convolution of linearly interpolated native stator I^2 on each native step',stock_native_controller=False)
        return dict(records=self.records,maximum_accepted_algebraic_residual_pu=self.maximum_g,accepted_steps=self.accepted,metadata=metadata)

    def voltages(self):
        a=self.a;es=a.Bus.v.v[0]*np.exp(1j*a.Bus.a.v[0]);vp=a.Bus.v.v[1]*np.exp(1j*a.Bus.a.v[1]);vs=es-self.zs*(es-vp)/(self.zs+self.zl)
        return {self.source_node:float(abs(vs)),self.plant_node:float(abs(vp))}

    def heating(self):
        result={}
        for i,m in enumerate(self.motors):
            if not m.thermal:continue
            _,model,_,j=self.layout[m.id];factor=m.rated_va/self.spec.network.s_base_va
            result[i]=float((model.Id.v[j]**2+model.Iq.v[j]**2)/factor**2)
        return result

    def advance(self,left,right,theta0,heat0):
        if not right>left:raise ValueError('positive native trial step required')
        self.probes+=1
        if self.probes>2_000_000:raise ValueError('native root trial budget exceeded')
        self.a.TDS.h=right-left;self.a.dae.t[...]=right
        if not self.a.TDS.itm_step():raise ValueError('native online implicit trial failed')
        residual=float(max(abs(self.a.dae.g)))
        if not math.isfinite(residual) or residual>1e-8:raise ValueError('native online algebraic residual limit')
        self.maximum_g=max(self.maximum_g,residual);heat1=self.heating()
        return {i:thermal_step(value,heat0[i],heat1[i],right-left,self.motors[i].thermal.time_constant_s) for i,value in theta0.items()}

    def guards(self,theta):
        result=[('thermal',i,1) for i in theta if self.closed[i] and not self.latched[i]]
        result.extend(('voltage',i,-1 if c['available'] else 1) for i,c in self.controls.items());return result

    def guard_values(self,guards,theta):
        voltage=self.voltages();result=[]
        for kind,i,direction in guards:
            m=self.motors[i]
            result.append(theta[i]-m.thermal.trip_level if kind=='thermal' else voltage[m.contactor.sense_node_id]-(m.contactor.dropout_voltage_pu if self.controls[i]['available'] else m.contactor.pickup_voltage_pu))
        return result

    def deadline(self,i):
        c=self.controls[i];p=self.motors[i].contactor
        if self.latched[i]:return math.inf
        if self.closed[i] and not c['available']:return c['since']+p.dropout_delay_s
        if not self.closed[i] and c['available'] and c['pending'] and c['retries']<p.maximum_automatic_restarts:return max(c['since']+p.pickup_delay_s,c['opened']+p.minimum_off_time_s)
        return math.inf

    def emit(self,i,kind,time,**fields):
        if kind.startswith('contactor_') or fields.get('cause') in {'automatic_restart','undervoltage_timer'}:
            self.transitions+=1
            if self.transitions>self.settings.maximum_contactor_transitions:raise ValueError('native contactor transition budget exceeded')
        self.log.append(dict(time_s=float(time),kind=kind,equipment_id=self.motors[i].id,**fields))

    def observe(self,time,forced=()):
        voltage=self.voltages()
        for i,c in self.controls.items():
            p=self.motors[i].contactor;v=voltage[p.sense_node_id];threshold=p.dropout_voltage_pu if c['available'] else p.pickup_voltage_pu;eps=32*EPS*max(1.,abs(v),abs(threshold))
            if i in forced or (v<=threshold+eps if c['available'] else v>=threshold-eps):
                c['available']=not c['available'];c['since']=time
                self.emit(i,'contactor_voltage_available' if c['available'] else 'contactor_voltage_unavailable',time,outcome='hysteresis_transition')
            if self.latched[i] and c['pending']:c['pending']=False;self.emit(i,'contactor_restart_cancelled',time,outcome='thermal_latch')

    def project(self):
        for i,m in enumerate(self.motors):
            _,model,_,j=self.layout[m.id];model.closed.v[j]=float(self.closed[i])
        before=self.a.dae.x.copy();self.a.TDS.niter=0;self.a.TDS.mis=[0.];res=self.ctx['project']()
        if not np.array_equal(before,self.a.dae.x):raise ValueError('native online projection changed differential states')
        self.maximum_g=max(self.maximum_g,res);self.a.TDS.custom_event=True

    def handle(self,time,roots,scheduled):
        voltage_roots=[i for kind,i in roots if kind=='voltage']
        if voltage_roots:self.observe(time,voltage_roots)
        for kind,i in sorted(roots,key=lambda x:x[1]):
            if kind=='thermal':self.closed[i]=False;self.latched[i]=True;self.emit(i,'thermal_trip',time,outcome='latched_open')
        self.project()
        for e in scheduled:
            if e.kind=='source_voltage':self.a.Slack.v0.v[0]=self.spec.network.sources[0].ll_v/self.motors[0].rated_ll_v*e.value
            elif e.kind=='governor_step':self.a.TGOV1.pref0.v[0]+=e.value*self.spec.coupled.machines[0].rated_va/self.spec.network.s_base_va
            elif e.kind=='voltage_reference_step':self.a.SEXS.vref0.v[0]+=e.value
            elif e.kind not in {'motor_open','motor_close'}:raise ValueError('unsupported native controller event')
        self.project();manual=[e for e in scheduled if e.kind in {'motor_open','motor_close'}]
        if manual:self.observe(time)
        for e in manual:
            i=next(i for i,m in enumerate(self.motors) if m.id==e.equipment_id);c=self.controls.get(i);requested=e.kind=='motor_close'
            outcome='blocked_thermal_latch' if requested and self.latched[i] else 'blocked_undervoltage' if requested and c and not c['available'] else 'already_in_requested_state' if requested==self.closed[i] else 'applied'
            if outcome in {'applied','already_in_requested_state'}:
                self.closed[i]=requested
                if c:c['pending']=False
                if c and not requested:c['opened']=time
            self.emit(i,e.kind,time,outcome=outcome)
        self.project();self.observe(time);eps=16*EPS*max(1.,abs(time))
        actions=[(i,not self.closed[i]) for i in self.controls if self.deadline(i)<=time+eps]
        for i,closing in actions:
            c=self.controls[i];self.closed[i]=closing
            if closing:c['retries']+=1;c['pending']=False;self.emit(i,'motor_close',time,cause='automatic_restart',outcome='applied')
            else:c['opened']=time;c['pending']=c['retries']<self.motors[i].contactor.maximum_automatic_restarts;self.emit(i,'motor_open',time,cause='undervoltage_timer',outcome='applied')
        if actions:self.project();self.observe(time)

    def record(self,time,side):
        self.records.append((float(time),side,self.a.dae.x.copy(),self.a.dae.y.copy()));voltage=self.voltages();values={}
        for i,m in enumerate(self.motors):
            values[m.id+'.connected']=float(self.closed[i])
            if m.thermal:values[m.id+'.thermal_level']=self.thermal[i];values[m.id+'.thermal_latched']=float(self.latched[i])
            if i not in self.controls:continue
            c=self.controls[i];due=self.deadline(i)
            row=dict(contactor_sensed_voltage_pu=voltage[m.contactor.sense_node_id],contactor_voltage_available=c['available'],contactor_pending_restart=c['pending'],contactor_restart_count=c['retries'],contactor_timer_active=math.isfinite(due),contactor_timer_remaining_s=max(0.,due-time) if math.isfinite(due) else 0.)
            values.update({m.id+'.'+key:float(value) for key,value in row.items()})
        for key,value in values.items():self.aux.setdefault(key,[]).append(value)
