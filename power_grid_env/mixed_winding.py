"""Explicit GENCLS + single/two-cage winding thermal profiles.

TGOV1/SEXS, generator I2/heat and stator R(T) are explicit profiles.
PSS/GENROU and excitation protections remain excluded. Joint initialization
is implemented separately.
"""
import math
import numpy as np


def supported(settings):
    profile=getattr(settings,'winding_generator_profile',None)
    if profile not in {'GENCLS_fixed_field','GENCLS_TGOV1_SEXS','GENCLS_TGOV1_SEXS_I2','GENCLS_TGOV1_SEXS_HEAT','GENCLS_TGOV1_SEXS_ENERGY','GENCLS_TGOV1_SEXS_STATOR_RT'}:return False
    if settings.generator_heat and profile not in {'GENCLS_TGOV1_SEXS_HEAT','GENCLS_TGOV1_SEXS_ENERGY','GENCLS_TGOV1_SEXS_STATOR_RT'}:return False
    if any(m.model!='GENCLS' or m.stabilizer is not None for m in settings.machines):return False
    if profile=='GENCLS_fixed_field' and any(m.governor is not None or m.exciter is not None for m in settings.machines):return False
    if settings.generator_thermal and profile not in {'GENCLS_TGOV1_SEXS_I2','GENCLS_TGOV1_SEXS_HEAT','GENCLS_TGOV1_SEXS_ENERGY','GENCLS_TGOV1_SEXS_STATOR_RT'}:return False
    return not any(getattr(settings,name,[]) for name in ['generator_field_thermal','overexcitation_limiters',
        'underexcitation_limiters','oel2c_limiters','esst1a_exciters','takeover_oels'])


def callback(model):
    if not supported(model.settings):raise ValueError('unsupported mixed winding generator controls')
    from power_grid_env.jacobian_columns import attach,seeds,tangent_seeds
    from power_grid_env.dae import injection_product
    from power_grid_env.rms_winding import operator
    dimension=model.generator_heat.end;core=model.generator_thermal.end
    def actions(time,state,u,ctx,voltage_directions=None):
        x=np.asarray(state)[:dimension];u=tangent_seeds(dimension,u)
        voltage,current=ctx[:2]
        dv=operator(model,x)[1].solve(injection_product(model,x,u,voltage=voltage)) if voltage_directions is None else np.asarray(voltage_directions)
        from power_grid_env.genrou_jacobian import jacobian
        # Reuse controller/machine branch derivatives with the actual variable-Y
        # voltage direction; replace the fixed-R motor rows below.
        from types import SimpleNamespace
        from power_grid_env.generator_stator import impedance,impedance_product,enabled
        gz=impedance(model,x);view=SimpleNamespace(**vars(model));view.gen_z=gz
        view.electrical=model.electrical;view.generator_internal=model.generator_internal;view.generator_currents=lambda ignored,v:np.where(model.gen_connected,(model.generator_internal(x)-v[model.gen_buses])/gz,0j)
        out=np.zeros((dimension,u.shape[1]));out[:core]=jacobian(view,x[:core],electrical=ctx,directions=u[:core],voltage_directions=dv) if enabled(model) else jacobian(model,x[:core],electrical=ctx,directions=u[:core],voltage_directions=dv)
        if enabled(model):
            ig=model.generator_currents(x,voltage);eg=model.generator_internal(x);di=-ig[:,None]*impedance_product(model,u)/gz[:,None]
            for i,m in enumerate(model.gen_passports):out[model.gen_offset+2*i+1]-=(eg[i]*np.conj(di[i])).real/(2*m.inertia_s)
            for i,(a,p) in model.generator_thermal.entries.items():out[a]+=2*(np.conj(ig[i])*di[i]).real/p.time_constant_s
        out+=model.generator_heat.actions(x,u,voltage,dv)
        out[:model.motor_state_end]=model.windings.actions(time,x,u[:model.motor_state_end],ctx,voltage_directions=dv)
        from power_grid_env.generator_energy import actions as energy_actions
        energy_actions(model,x,u,voltage,out,energy_voltage_directions=dv)
        return out
    def columns(t,x,cols,ctx):
        _,u=seeds(dimension,cols);return actions(t,x,u,ctx)
    def evaluate(t,x):return columns(t,x,np.arange(dimension),model.electrical(x))
    cb=attach(evaluate,model,columns,actions);cb.jacobian_kind='analytic_GENCLS_winding_state_dependent_Norton'+('_TGOV1_SEXS' if model.settings.winding_generator_profile=='GENCLS_TGOV1_SEXS' else '')
    return cb
