"""Cold/hot passive radial fault bounds; no transient or motor contribution.

Continuous current bounds follow monotonicity of nonnegative series R and X.
Relay and I²t bounds are conservative interval bounds, not extrema searches.
"""
import itertools
import math
from pathlib import Path

from pydantic import Field, model_validator
from power_grid_env.electrical_sizing import Contract, Source
from power_grid_env.equipment_catalog import compile_catalog
from power_grid_env.protection_design import RelaySetting
from power_grid_env.study_artifacts import encoded, digest, read_json, seal, verify
from power_grid_env.evidence import package_fingerprint


class Interval(Contract):
    minimum: float
    maximum: float

    @model_validator(mode='after')
    def ordered(self):
        if self.minimum > self.maximum:
            raise ValueError('interval minimum exceeds maximum')
        return self


class FaultEnvelope(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    source: Source
    conductor_c: Interval
    voltage_factor: Interval
    position_pu: Interval
    fault_resistance_ohm: Interval
    primary: RelaySetting
    backup: RelaySetting
    grading_margin_s: float = Field(gt=0, le=2)

    @model_validator(mode='after')
    def domain(self):
        for name, low, high in [('conductor_c',20,90),('voltage_factor',.5,1.5),
                                ('position_pu',0,1),('fault_resistance_ohm',0,100)]:
            interval=getattr(self,name)
            if interval.minimum < low or interval.maximum > high:
                raise ValueError('unsupported '+name+' interval')
        return self


def relay_bounds(current_min, current_max, setting):
    # Stable expression at pickup; do not silently impose a time floor.
    def trip(current, pickup, multiplier):
        if current <= pickup:
            return None
        if setting.curve == 'definite':
            return multiplier
        k, alpha={'standard_inverse':(.14,.02),'very_inverse':(13.5,1.),
                  'extremely_inverse':(80.,2.)}[setting.curve]
        return multiplier*k/math.expm1(alpha*math.log(current/pickup))
    early=trip(current_max,setting.pickup_a*(1-setting.pickup_tolerance),
               setting.time_multiplier*(1-setting.time_tolerance))
    late=trip(current_min,setting.pickup_a*(1+setting.pickup_tolerance),
              setting.time_multiplier*(1+setting.time_tolerance))
    return dict(earliest_trip_s=early,
                latest_clear_s=None if late is None else late+setting.breaker_opening_s,
                pickup_guaranteed=late is not None)


def calculate(catalog, brief, selection, envelope):
    e=FaultEnvelope.model_validate(envelope)
    cat, _, sel, task, _=compile_catalog(catalog,brief,selection)
    cable=next(c for c in cat.cables if c.id==sel.cable_id)
    transformer=next(t for t in task.sizing.transformers if t.id==sel.transformer_id)
    if e.conductor_c.maximum > cable.maximum_conductor_c.value:
        raise ValueError('temperature exceeds cable passport operating limit')
    a=cat.assumptions
    if a.copper_alpha_per_c.value <= 0:
        raise ValueError('positive copper temperature coefficient required for bounds')
    length=task.sizing.installation.length_m/1000
    zbase=task.sizing.voltage_v**2/(transformer.rating_kva*1000)
    rt=zbase*transformer.resistance_percent/100
    xt=zbase*math.sqrt(transformer.impedance_percent**2-transformer.resistance_percent**2)/100
    xline=a.cable_reactance_ohm_km.value*length
    def point(temp,voltage,position,resistance):
        rline=cable.dc_r20_ohm_km.value*(1+a.copper_alpha_per_c.value*(temp-20))*a.ac_dc_resistance_factor.value*length
        current=voltage*task.sizing.voltage_v/(math.sqrt(3)*math.hypot(rt+position*rline+resistance,xt+position*xline))
        return dict(conductor_c=temp,voltage_factor=voltage,position_pu=position,
                    fault_resistance_ohm=resistance,cable_r_ohm=rline,current_a=current)
    corners=[point(*v) for v in itertools.product(*[(i.minimum,i.maximum) for i in
             [e.conductor_c,e.voltage_factor,e.position_pu,e.fault_resistance_ohm]])]
    lo=min(corners,key=lambda r:r['current_a']);hi=max(corners,key=lambda r:r['current_a'])
    imin,imax=lo['current_a'],hi['current_a']
    primary=relay_bounds(imin,imax,e.primary);backup=relay_bounds(imin,imax,e.backup)
    grading=None if primary['latest_clear_s'] is None or backup['earliest_trip_s'] is None else backup['earliest_trip_s']-primary['latest_clear_s']
    checks=dict(primary_pickup=primary['pickup_guaranteed'],backup_pickup=backup['pickup_guaranteed'],
                conservative_grading=grading is not None and grading>=e.grading_margin_s)
    duty={}
    for name,setting,bounds in [('primary',e.primary,primary),('backup',e.backup,backup)]:
        clear=bounds['latest_clear_s']
        i2t=None if clear is None else imax**2*clear
        duty[name]=dict(conservative_i2t_a2s=i2t)
        checks[name+'_interrupting']=imax<=setting.interrupting_a
        checks[name+'_cable_adiabatic']=clear is not None and clear<=5 and i2t<=(a.cable_k.value*cable.area_mm2.value)**2
        checks[name+'_transformer_withstand']=clear is not None and clear<=transformer.short_time_s and imax<=transformer.short_time_a
    checks['installation_interrupting']=imax<=task.sizing.installation.breaker_interrupting_a
    checks['primary_clearing_budget']=primary['latest_clear_s'] is not None and primary['latest_clear_s']<=task.sizing.installation.clearing_s
    result=dict(schema='power-grid-env.fault-temperature-envelope-result/v1',code_sha256=package_fingerprint(),
        input_sha256=digest(encoded(dict(catalog=cat.model_dump(mode='json'),brief=task.model_dump(mode='json'),selection=sel.model_dump(mode='json'),envelope=e.model_dump(mode='json')))),
        corners=corners,current_bounds_a=dict(minimum=imin,maximum=imax),
        minimum_witness=lo,maximum_witness=hi,primary=primary,backup=backup,
        conservative_grading_s=grading,duty=duty,checks=checks,
        passes_sufficient_checks=all(checks.values()),normative_approval=False,reward=None,
        transformer_resistance_temperature_corrected=False,
        scope='Continuous current bounds only for positive passive radial series impedances, copper 20..passport limit, fixed transformer R/X, balanced steady three-phase RMS faults. Relay/duty bounds are sufficient and conservative; rejection may be inconclusive. No motor contribution, transient heating, peak/DC, earth fault, dynamic coordination or design certification.')
    encoded(result)
    return result


def run(catalog,brief,selection,envelope,out):
    inputs=dict(catalog=catalog,brief=brief,selection=selection,envelope=envelope)
    result=calculate(**inputs)
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for name,value in {**inputs,'result':result}.items():
        (root/(name+'.json')).write_bytes(encoded(value))
    seal(root)
    return result


def verify_envelope(root, *, expected_inputs):
    root=Path(root);before=verify(root)
    inputs={name:read_json(root/(name+'.json')) for name in ['catalog','brief','selection','envelope']}
    if inputs!=expected_inputs:
        raise ValueError('owner inputs mismatch')
    result=calculate(**inputs)
    if result!=read_json(root/'result.json'):
        raise ValueError('recomputed envelope mismatch')
    if verify(root)!=before:
        raise ValueError('artifact changed during verification')
    return dict(verified=True,manifest_sha256=before,**result)


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['catalog','brief','selection','envelope','out']:
        p.add_argument('--'+name,required=True)
    args=vars(p.parse_args());out=args.pop('out')
    result=run(**{k:read_json(v) for k,v in args.items()},out=out)
    print(encoded(result).decode())
    if not result['passes_sufficient_checks']:raise SystemExit(2)


if __name__=='__main__':
    main()
