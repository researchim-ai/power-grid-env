"""Explicit fixed-field GENCLS + single/two-cage winding thermal profile.

No governors,AVR/PSS,GENROU or generator thermal/protection states are silently
ignored. Initial heat temperatures remain passports; joint mixed heat/PV
stationary initialization is a separate profile,not implemented here.
"""
import math
import numpy as np


def supported(settings):
    if getattr(settings,'winding_generator_profile',None)!='GENCLS_fixed_field':return False
    if any(m.model!='GENCLS' or m.governor is not None or m.exciter is not None or m.stabilizer is not None for m in settings.machines):return False
    return not any(getattr(settings,name,[]) for name in ['generator_thermal','generator_field_thermal','overexcitation_limiters',
        'underexcitation_limiters','oel2c_limiters','esst1a_exciters','takeover_oels'])


def callback(model):
    if not supported(model.settings):raise ValueError('unsupported mixed winding generator controls')
    from power_grid_env.jacobian_columns import attach,seeds,tangent_seeds
    from power_grid_env.dae import injection_product
    from power_grid_env.rms_winding import operator
    dimension=model.rotor_end
    def actions(time,state,u,ctx,voltage_directions=None):
        x=np.asarray(state)[:dimension];u=tangent_seeds(dimension,u)
        voltage,current=ctx[:2]
        dv=operator(model,x)[1].solve(injection_product(model,x,u,voltage=voltage)) if voltage_directions is None else np.asarray(voltage_directions)
        out=np.zeros((dimension,u.shape[1]))
        out[:model.motor_state_end]=model.windings.actions(time,x,u[:model.motor_state_end],ctx,voltage_directions=dv)
        eg=model.generator_internal(x);ig=model.generator_currents(x,voltage);wb=2*math.pi*model.network.frequency_hz
        for i,m in enumerate(model.gen_passports):
            delta=model.gen_offset+2*i;omega=delta+1
            de=1j*eg[i]*u[delta]
            di=(de-dv[model.gen_buses[i]])/model.gen_z[i] if model.gen_connected[i] else np.zeros(u.shape[1],complex)
            out[delta]=wb*u[omega]
            out[omega]=-((de*np.conj(ig[i])+eg[i]*np.conj(di)).real+m.damping_pu*u[omega])/(2*m.inertia_s)
        return out
    def columns(t,x,cols,ctx):
        _,u=seeds(dimension,cols);return actions(t,x,u,ctx)
    def evaluate(t,x):return columns(t,x,np.arange(dimension),model.electrical(x))
    cb=attach(evaluate,model,columns,actions);cb.jacobian_kind='analytic_GENCLS_winding_state_dependent_Norton'
    return cb
