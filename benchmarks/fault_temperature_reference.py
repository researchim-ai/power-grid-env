"""Independent finite-shunt nodal checks of the cold/hot radial envelope."""
import argparse
import itertools
import math
from pathlib import Path

import numpy as np
from power_grid_env.fault_temperature_envelope import calculate, run, verify_envelope
from power_grid_env.protection import iec_operate_time
from power_grid_env.study_artifacts import read_json, encoded, seal, digest


def qualify(catalog,brief,selection,envelope,out):
    inputs=dict(catalog=catalog,brief=brief,selection=selection,envelope=envelope)
    result=calculate(**inputs)
    cable=next(c for c in catalog['cables'] if c['id']==selection['cable_id'])
    transformer=next(t for t in catalog['transformers'] if t['id']==selection['transformer_id'])
    a=catalog['assumptions'];u=brief['voltage_v'];length=brief['installation']['length_m']/1000
    # Direct passport SI conversions, no producer compiler or stamping calls.
    rating=transformer['rating_kva']['value']*1000
    rs=transformer['load_loss_w']['value']*u*u/(rating*rating)
    zs=transformer['impedance_percent']['value']/100*u*u/rating
    source=complex(rs,math.sqrt(zs*zs-rs*rs))
    bounds=result['current_bounds_a'];rows=[];relay_checks=0
    def values(name):
        v=envelope[name];return np.linspace(v['minimum'],v['maximum'],7)
    for temperature,voltage,position,rf in itertools.product(*[values(k) for k in ['conductor_c','voltage_factor','position_pu','fault_resistance_ohm']]):
        r=cable['dc_r20_ohm_km']['value']*(1+a['copper_alpha_per_c']['value']*(temperature-20))*a['ac_dc_resistance_factor']['value']*length
        line=position*complex(r,a['cable_reactance_ohm_km']['value']*length)
        emf=voltage*u/math.sqrt(3)
        # Exact bolted/shunt and zero-line limits avoid artificial tiny impedances.
        if line==0:
            v=emf/(1+source/rf) if rf else 0
            current=abs((emf-v)/source)
        elif rf==0:
            v=emf/(1+source/line)
            current=abs(v/line)
        else:
            y=np.array([[1/source+1/line,-1/line],[-1/line,1/line+1/rf]],complex)
            v=np.linalg.solve(y,np.array([emf/source,0]))
            current=abs(v[1]/rf)
        contained=bounds['minimum']-1e-7<=current<=bounds['maximum']+1e-7
        relay_ok=True
        for name in ['primary','backup']:
            s=envelope[name];b=result[name]
            for pf,tf in itertools.product([1-s['pickup_tolerance'],1,1+s['pickup_tolerance']], [1-s['time_tolerance'],1,1+s['time_tolerance']]):
                t=iec_operate_time(current,s['pickup_a']*pf,s['curve'],s['time_multiplier']*tf)
                if b['earliest_trip_s'] is not None:relay_ok &= t>=b['earliest_trip_s']-1e-9
                if b['latest_clear_s'] is not None:
                    relay_ok &= t+s['breaker_opening_s']<=b['latest_clear_s']+1e-9
                    relay_ok &= current**2*(t+s['breaker_opening_s'])<=result['duty'][name]['conservative_i2t_a2s']+1e-3
                relay_checks+=1
        rows.append(dict(conductor_c=float(temperature),voltage_factor=float(voltage),position_pu=float(position),fault_resistance_ohm=float(rf),current_a=float(current),contained=bool(contained),relay_bounds_contain=bool(relay_ok)))
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    run(**inputs,out=root/'envelope')
    verify_envelope(root/'envelope',expected_inputs=inputs)
    summary=dict(passed=all(r['contained'] and r['relay_bounds_contain'] for r in rows),
                 nodal_points=len(rows),relay_tolerance_checks=relay_checks,
                 design_passes_sufficient_checks=result['passes_sufficient_checks'],
                 producer_sha256=digest(Path(__file__).read_bytes()),
                 scope='Independent nodal algebra and existing relay formula checks; same host; finite sampling verifies implementation, monotonicity supplies analytic current bounds. No independent dynamic simulation.')
    (root/'rows.json').write_bytes(encoded(rows));(root/'summary.json').write_bytes(encoded(summary));seal(root)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['catalog','brief','selection','envelope','out']:p.add_argument('--'+key,required=True)
    args=vars(p.parse_args());out=args.pop('out')
    result=qualify(**{k:read_json(v) for k,v in args.items()},out=out)
    print(result)
    if not result['passed']:raise SystemExit(2)

if __name__=='__main__':main()
