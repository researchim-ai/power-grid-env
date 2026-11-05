"""Explicit eight-machine SI plant extension with motor I² relay semantics.

Only schema values are used. MNA, motor windings, roots and ODEs come from the
independent SI reference; production stamps, states and rates are never read.
PSS/ESST/OEL FMUs are attached by the owning native master, not dropped here.
"""
import numpy as np
from benchmarks.generator_excitation_si import ExcitationCircuit

PROFILE='eight_machine_v1'


class EightMachineCircuit(ExcitationCircuit):
    def __init__(self,spec,*,maximum_duration_s=6.,current_constraints=None):
        super().__init__(spec,maximum_duration_s=maximum_duration_s,current_constraints=current_constraints,maximum_nodes=512,sparse_network=True,maximum_generators=8)

    def set_context(self,cfg):
        changed=any(not np.array_equal(cfg.get(k),self.cfg.get(k)) for k in ['connected','generator_connected'])
        self.cfg={k:v.copy() if isinstance(v,np.ndarray) else v for k,v in cfg.items()};self.cache_x=None
        if changed:self.rebuild()
        for i,(_,g,_) in enumerate(self.ports):
            g.pref=self.cfg['generator_pref'][i];g.vref=self.cfg['generator_vref'][i]
            g.field_enabled=bool(self.cfg['field_enabled'][i])

    def extra_guards(self,state=None):
        guards=super().extra_guards(state)
        for i,a in self.thermal.items():
            if self.cfg['latched'][i]:continue
            p=self.s.motors[i].thermal
            def crossing(t,x,a=a,p=p):return x[a]-p.trip_level
            crossing.terminal=True;crossing.direction=1;guards.append((('motor_thermal',i),crossing))
        return guards

    def extra_apply(self,roots,t,x,cfg):
        roots=set(roots);selected={key[1] for key in roots if key[0]=='motor_thermal'}
        _,st,_,_=self.electrical(x)
        if selected:
            for i,a in self.thermal.items():
                p=self.s.motors[i].thermal;heat=(abs(st[i])/self.p[i]['ibase'])**2
                eps=32*np.finfo(float).eps*max(abs(x[a]),p.trip_level,np.finfo(float).tiny)
                if not cfg['latched'][i] and cfg['connected'][i] and x[a]>=p.trip_level-eps and heat>x[a]:selected.add(i)
        logs=super().extra_apply({key for key in roots if key[0]!='motor_thermal'},t,x,cfg)
        for i in sorted(selected):
            if cfg['latched'][i]:continue
            p=self.s.motors[i].thermal;cfg['latched'][i]=True;cfg['connected'][i]=False
            logs.append(dict(time_s=float(t),kind='thermal_trip',equipment_id=self.s.motors[i].id,level=float(x[self.thermal[i]]),threshold=p.trip_level,outcome='latched_open'))
        return logs

    def extra_signals(self,x):
        out=super().extra_signals(x)
        for i,a in self.thermal.items():
            gid=self.s.motors[i].id;out[gid+'.thermal_level']=float(x[a]);out[gid+'.thermal_latched']=float(self.cfg['latched'][i])
        return out

    def motor_command(self,event,cfg):
        i=next(i for i,m in enumerate(self.s.motors) if m.id==event.equipment_id)
        if event.kind=='motor_close' and cfg['latched'][i]:return 'blocked_thermal_latch'
        requested=event.kind=='motor_close'
        if bool(cfg['connected'][i])==requested:return 'already_in_requested_state'
        cfg['connected'][i]=requested;return 'applied'

    def complete_motor_signals(self,i,flux,rot):
        p=self.p[i]
        if len(p['rr'])!=2:return {}
        parts=1j*self.wb*p['lm']*np.linalg.solve(p['L'],np.ones(2))*flux/p['vbase']
        out={}
        for j in range(2):
            out.update({f'cage{j+1}_e_real_pu':float(parts[j].real),f'cage{j+1}_e_imag_pu':float(parts[j].imag),f'rotor{j+1}_current_pu':float(abs(rot[p['rotor']][j])/p['ibase']),f'rotor{j+1}_copper_loss_w':float(3*p['rr'][j]*abs(rot[p['rotor']][j])**2)})
        return out


def extra_required(spec):
    from power_grid_env.coupled_pss import STATE_NAMES
    required=set()
    for m in spec.coupled.motors:
        required.update(m.id+'.'+k for k in ['speed_pu','e_real_pu','e_imag_pu','current_pu','bus_voltage_pu','p_w','q_var','te_pu','connected'])
        if m.thermal:required.update(m.id+'.'+k for k in ['thermal_level','thermal_latched'])
        if m.model=='double_cage':required.update(m.id+'.'+k for k in ['cage1_e_real_pu','cage1_e_imag_pu','cage2_e_real_pu','cage2_e_imag_pu','rotor1_current_pu','rotor2_current_pu','rotor1_copper_loss_w','rotor2_copper_loss_w'])
    for p in spec.coupled.generator_thermal:required.update(p.generator_id+'.'+k for k in ['stator_thermal_level','stator_thermal_latched'])
    for p in spec.coupled.generator_field_thermal:required.update(p.generator_id+'.'+k for k in ['field_thermal_level','field_thermal_latched','field_current_pu','field_voltage_enabled'])
    for p in spec.coupled.underexcitation_limiters:required.update(p.generator_id+'.UEL.'+k for k in ['p_pu','q_pu','voltage_pu','circle_error_pu2','output_pu','raw_lag_state_pu','feedback_pu','feedback_memory_pu'])
    for m in spec.coupled.machines:
        if m.stabilizer:required.update(m.generator_id+'.IEEEST.'+k for k in [*STATE_NAMES,'sig','raw_pu','limited_pu','output_pu','voltage_gate'])
    return required
