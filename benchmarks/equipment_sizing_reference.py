"""Independent OpenDSS fault solution and fixed public worked examples.

References are engineering examples, not OEM passports. No dynamic motor
trajectory is compared with OpenDSS here. Run via python -m benchmarks....
"""
import argparse
import copy
import math
from pathlib import Path
import importlib.metadata

from power_grid_env.electrical_sizing import SizingTask, Selection, calculate
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json, encoded, seal, digest

EIG='https://www.electrical-installation.org/enwiki/'
SOURCES=dict(transformer=EIG+'Short-circuit_current_at_the_secondary_terminals_of_a_MV/LV_distribution_transformer',
             voltage=EIG+'Calculation_of_voltage_drop_in_steady_load_conditions',
             thermal=EIG+'Verification_of_the_withstand_capabilities_of_cables_under_short-circuit_conditions',
             fault_model='https://opendss.epri.com/Fault.html',source_model='https://opendss.epri.com/Vsource.html')


def fault_reference(task,selection):
    # Build independently from the owner catalog, not compiled/result impedances.
    import opendssdirect
    d=opendssdirect.NewContext()
    t=SizingTask.model_validate(task);p=Selection.model_validate(selection)
    c=next(c for c in t.cables if c.id==p.cable_id)
    x=next(x for x in t.transformers if x.id==p.transformer_id)
    base=t.voltage_v**2/(1000*x.rating_kva)
    impedance=complex(x.resistance_percent,math.sqrt(x.impedance_percent**2-x.resistance_percent**2))*base/100
    rows={}
    for bus in ['source','load']:
        d.Basic.ClearAll()
        commands=['set defaultbasefrequency=50',
            f'new circuit.ref basekv={t.voltage_v/1000:.17g} pu=1 phases=3 bus1=source basefreq=50 r1={impedance.real:.17g} x1={impedance.imag:.17g} r0={impedance.real:.17g} x0={impedance.imag:.17g}',
            f'new line.feeder bus1=source bus2=load phases=3 length={t.installation.length_m/1000:.17g} units=km r1={c.r_operating_ohm_km:.17g} x1={c.x_ohm_km:.17g} r0={c.r_operating_ohm_km:.17g} x0={c.x_ohm_km:.17g} c1=0 c0=0',
            f'new fault.test phases=3 bus1={bus} bus2={bus}.0.0.0 r=1e-8',
            'set controlmode=off','solve mode=snapshot frequency=50']
        for cmd in commands:d.Text.Command(cmd)
        if not d.Solution.Converged():raise ValueError('OpenDSS reference did not converge')
        d.Circuit.SetActiveElement('fault.test')
        currents=d.CktElement.CurrentsMagAng()[::2][:3]
        if len(currents)!=3 or any(not math.isfinite(v) or v<=0 for v in currents):raise ValueError('invalid OpenDSS fault current')
        rows[bus]=dict(commands=commands,currents_a=currents)
    return dict(rows=rows,engine=d.Basic.Version(),wrapper=importlib.metadata.version('opendssdirect.py'),
                scope='balanced steady three-phase short-circuit equivalent; 1e-8 ohm fault; no motor contribution or IEC correction')


def public_checks(task,selection):
    # Fixed published targets, not generated from calculate(). All unmentioned
    # fields stay synthetic and are not claimed to be manufacturer's ratings.
    t=copy.deepcopy(SizingTask.model_validate(task).model_dump(mode='json'))
    p=Selection.model_validate(selection)
    cable=next(c for c in t['cables'] if c['id']==p.cable_id)
    transformer=next(x for x in t['transformers'] if x['id']==p.transformer_id)
    checks=[]
    def check(name,value,target,tolerance,source):
        checks.append(dict(id=name,value=value,target=target,absolute_tolerance=tolerance,
                           passed=abs(value-target)<=tolerance,source=SOURCES[source]))
    t['voltage_v']=420
    for x in t['transformers']:x['secondary_v']=420
    transformer.update(rating_kva=400,impedance_percent=4,resistance_percent=1)
    check('published-transformer-kA',calculate(t,p)['values']['bus_fault_a']/1000,13.7,.05,'transformer')
    t['voltage_v']=400
    for x in t['transformers']:x['secondary_v']=400
    cable.update(area_mm2=35,r_operating_ohm_km=23.7/35,x_ohm_km=.08)
    t['installation']['length_m']=50
    for current,pf,target in [(100,.8,5.),(500,.35,13.5)]:
        t.update(demand_kva=math.sqrt(3)*400*current/1000,lagging_power_factor=pf)
        drop=calculate(t,p)['values']['cable_drop_percent']*4
        check('published-cable-drop-'+str(current)+'A',drop,target,.15,'voltage')
    cable.update(area_mm2=4,k_a_sqrt_s_mm2=143)
    check('published-4mm2-XLPE-I2t',calculate(t,p)['values']['cable_limit_i2t_a2s'],327200.,100.,'thermal')
    return checks


def run(task,selection,out):
    task=SizingTask.model_validate(task).model_dump(mode='json')
    selection=Selection.model_validate(selection).model_dump(mode='json')
    root=Path(out)
    if root.exists():raise ValueError('new reference directory required')
    code=package_fingerprint();primary=calculate(task,selection)
    reference=fault_reference(task,selection)
    checks=public_checks(task,selection)
    for bus,key in [('source','bus_fault_a'),('load','end_fault_a')]:
        error=max(abs(v-primary['values'][key])/primary['values'][key] for v in reference['rows'][bus]['currents_a'])
        checks.append(dict(id='OpenDSS-'+bus,relative_error=error,relative_limit=2e-6,passed=error<=2e-6))
    if code!=package_fingerprint():raise ValueError('producer changed')
    root.mkdir(parents=True,exist_ok=False)
    summary=dict(passed=all(c['passed'] for c in checks),checks=checks,code_sha256=code,
                 producer_sha256=digest(Path(__file__).read_bytes()),sources=SOURCES,
                 dynamic_external_validation=False,manufacturer_catalog=False)
    for name,value in [('task',task),('selection',selection),('primary',primary),('reference',reference),('summary',summary)]:
        (root/f'{name}.json').write_bytes(encoded(value))
    seal(root);return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--proposal',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    r=run(read_json(a.input),read_json(a.proposal),a.out)
    print(encoded(r).decode());raise SystemExit(0 if r['passed'] else 2)
