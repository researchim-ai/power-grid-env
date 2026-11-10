"""Before/after physics replay retaining the previous full Norton formulation.

The dense implementation is preserved for regression, not used by runtime.
This compares implementations of the same midpoint equations, not OEM data.
"""
import argparse
import hashlib
from pathlib import Path
import numpy as np
from power_grid_env.flux_machine import FluxMachinePassport
from power_grid_env.switching_magnetics import SwitchingMagnetics
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from benchmarks.pwm_motor_comparison import native,protocol,waveform,quantities,energy,NAMES,LEVELS,CHANNELS


def dense_native(spec,step):
    p=spec['motor'];port=SwitchingMagnetics(FluxMachinePassport.model_validate(p));flux=np.zeros(4);speed=p['initial_speed_rad_s'];work=loss=0.;initial=energy(p,flux,speed);peak_residual=0.;count=0
    times=np.arange(round(spec['duration_s']/spec['sample_s'])+1)*spec['sample_s'];rows=[quantities(p,flux,speed).tolist()];sample=1
    for left,right,voltage in waveform(spec):
        t=left
        while t<right-1e-14:
            h=min(step,right-t,times[sample]-t if sample<len(times) else right-t);wmid=speed;guess=flux.copy()
            for _ in range(24):
                affine=port.affine(flux,h,wmid,guess);delta=affine['offset_delta']+affine['gain_flux']@np.array([voltage.real,voltage.imag]);mid=flux+delta
                q=port.stage(flux,mid,h,[voltage.real,voltage.imag],delta)
                load=p['fan_nm_per_rad_s2']*wmid*abs(wmid)+p['viscous_nm_per_rad_s']*wmid
                next_mid=speed+h/2/p['inertia_kg_m2']*(q['torque_nm']-load)
                if abs(next_mid-wmid)<1e-11 and q['constitutive_residual_v']<1e-9:break
                wmid=next_mid;guess=mid
            else:raise RuntimeError('native PWM motor midpoint did not converge')
            flux+=2*delta;speed=2*next_mid-speed
            work+=h*1.5*(voltage.real*q['currents'][0]+voltage.imag*q['currents'][1]);loss+=h*(q['copper_w']+load*wmid)
            peak_residual=max(peak_residual,abs(work-loss-(energy(p,flux,speed)-initial)));count+=1;t+=h
            if sample<len(times) and abs(t-times[sample])<1e-13:rows.append(quantities(p,flux,speed).tolist());sample+=1
    if len(rows)!=len(times):raise ValueError('native sample inventory differs')
    return dict(row_channels=CHANNELS,time_s=times.tolist(),rows=rows,steps=count,source_j=work,heat_and_load_j=loss,maximum_energy_residual_j=peak_residual)


def producer():
    files=[Path(__file__),Path(__file__).with_name('pwm_motor_comparison.py')]
    return dict(package=package_fingerprint(),benchmarks={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def run(out,replay=False):
    out=Path(out);before=producer()
    if replay:
        manifest=verify(out)
        if read_json(out/'producer.json')!=before:raise ValueError('producer differs')
    else:out.mkdir(parents=True,exist_ok=False);(out/'producer.json').write_bytes(encoded(before))
    def store(name,value):
        if replay:
            if encoded(read_json(out/name))!=encoded(value):raise ValueError('numerical replay differs: '+name)
        else:(out/name).write_bytes(encoded(value))
    rows=[]
    for name in NAMES:
        spec=protocol(name);spec['reference']=dict(kind='previous dense production Norton formulation');store(name+'-protocol.json',spec)
        errors={};energy_errors={}
        for level,step in LEVELS:
            a=native(spec,step);b=dense_native(spec,step)
            if a['time_s']!=b['time_s'] or a['row_channels']!=b['row_channels'] or a['steps']!=b['steps']:raise ValueError('prescribed-voltage inventory differs')
            error=dict(zip(a['row_channels'],np.max(abs(np.array(a['rows'])-np.array(b['rows'])),axis=0).tolist()));energy_error=float(max(abs(a[k]-b[k]) for k in ['source_j','heat_and_load_j']))
            if not all(np.isfinite(v) for v in error.values()) or not np.isfinite(energy_error):raise ValueError('nonfinite comparison')
            errors[level]=error;energy_errors[level]=energy_error;store(name+'-'+level+'-fast.json',a);store(name+'-'+level+'-dense.json',b)
        row=dict(case=name,channel_differences=errors,maximum_integral_difference_j=energy_errors,passed=all(max(v.values())<1e-8 for v in errors.values()) and max(energy_errors.values())<1e-8);rows.append(row);print(encoded(row).decode(),flush=True)
    summary=dict(schema='power-grid-env.prescribed-voltage-equivalence/v1',passed=all(r['passed'] for r in rows),rows=rows,fast_trajectories=12,dense_trajectories=12,scope='same prescribed-voltage midpoint/discrete-gradient trajectories; full Norton vs exact rotor elimination; no network performance claim')
    store('summary.json',summary)
    if producer()!=before:raise ValueError('producer changed during run')
    identity=verify(out) if replay else seal(out)
    if replay and manifest!=identity:raise ValueError('artifact changed during replay')
    print(encoded(dict(passed=summary['passed'],replayed=replay,manifest_sha256=identity)).decode(),flush=True)
    if not summary['passed']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();g=parser.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');a=parser.parse_args();run(a.verify or a.out,bool(a.verify))
