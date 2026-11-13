"""Fixed protection-screen protocol and independent OpenDSS fault checks."""
import argparse
import copy
import math
import shutil
from pathlib import Path
from power_grid_env.protection_design import screen,verify_screen,run_screen
from power_grid_env.equipment_catalog import compile_catalog
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest
from power_grid_env.evidence import package_fingerprint


def variants(plan):
    changes=[('accepted',None,None,None),('inverse-primary','primary','curve','standard_inverse'),
        ('start-pickup','primary','pickup_a',100),('grading','backup','time_multiplier',.05),
        ('slow-primary','primary','time_multiplier',.2),('missed-fault','primary','pickup_a',100000),
        ('interrupting','backup','interrupting_a',100),('slow-backup','backup','time_multiplier',2)]
    for name,stage,field,value in changes:
        p=copy.deepcopy(plan)
        if stage is not None:p[stage][field]=value
        if name=='inverse-primary':p['primary']['time_multiplier']=.01
        yield name,p


def external_faults(catalog_root,result):
    import opendssdirect
    _,_,selection,task,_=compile_catalog(read_json(Path(catalog_root)/'catalog.json'),read_json(Path(catalog_root)/'brief.json'),read_json(Path(catalog_root)/'selection.json'))
    x=next(t for t in task.sizing.transformers if t.id==selection.transformer_id)
    c=next(t for t in task.sizing.cables if t.id==selection.cable_id)
    base=task.sizing.voltage_v**2/(x.rating_kva*1000)
    rt=base*x.resistance_percent/100;xt=base*math.sqrt(x.impedance_percent**2-x.resistance_percent**2)/100
    d=opendssdirect.NewContext();rows=[]
    for fault in result['faults']:
        position=fault['position_pu'];factor=fault['source_voltage_factor'];d.Basic.ClearAll()
        commands=['set defaultbasefrequency=50',
          f'new circuit.ref basekv={task.sizing.voltage_v/1000:.17g} pu={factor:.17g} phases=3 bus1=source r1={rt:.17g} x1={xt:.17g} r0={rt:.17g} x0={xt:.17g}']
        bus='source'
        if position>0:
            bus='faultbus'
            commands.append(f'new line.c bus1=source bus2=faultbus phases=3 length={position*task.sizing.installation.length_m/1000:.17g} units=km r1={c.r_operating_ohm_km:.17g} x1={c.x_ohm_km:.17g} r0={c.r_operating_ohm_km:.17g} x0={c.x_ohm_km:.17g} c1=0 c0=0')
        commands.extend([f'new fault.f phases=3 bus1={bus} bus2={bus}.0.0.0 r=1e-8','set controlmode=off','solve mode=snapshot frequency=50'])
        for cmd in commands:d.Text.Command(cmd)
        if not d.Solution.Converged():raise ValueError('OpenDSS fault not converged')
        d.Circuit.SetActiveElement('fault.f');currents=d.CktElement.CurrentsMagAng()[::2][:3]
        if len(currents)!=3 or any(not math.isfinite(i) or i<=0 for i in currents):raise ValueError('invalid reference currents')
        error=max(abs(i-fault['current_a'])/fault['current_a'] for i in currents)
        rows.append(dict(position_pu=position,source_voltage_factor=factor,commands=commands,currents_a=currents,relative_error=error,passed=error<=2e-6))
    return dict(passed=all(r['passed'] for r in rows),rows=rows,engine=d.Basic.Version(),relative_limit=2e-6,
        scope='independent constant RMS fault solution; no independent dynamic relay action')


def run(source,settings,out):
    source=Path(source);root=Path(out);root.mkdir(parents=True,exist_ok=False)
    plan=copy.deepcopy(settings);plan['catalog_manifest_sha256']=verify(source)
    shutil.copytree(source,root/'catalog-study');rows=[]
    for name,p in variants(plan):
        r=screen(p,root/'catalog-study')
        if not r['numerically_qualified'] or r['passes_declared_screen']!=(name in ['accepted','inverse-primary']):
            raise ValueError('unexpected screen outcome: '+name)
        folder=root/name;folder.mkdir()
        (folder/'plan.json').write_bytes(encoded(p));(folder/'result.json').write_bytes(encoded(r))
        rows.append(dict(id=name,passed=r['passes_declared_screen'],checks=r['checks']))
    primary=read_json(root/'accepted'/'result.json')
    external=external_faults(source,primary)
    if not external['passed']:raise ValueError('external fault reference differs')
    (root/'external-reference.json').write_bytes(encoded(external))
    summary=dict(passed=True,code_sha256=package_fingerprint(),rows=rows,external_fault_solutions=len(external['rows']),
        producer_sha256=digest(Path(__file__).read_bytes()),closed_loop_protection_qualified=False,normative_approval=False,reward_exported=False)
    (root/'summary.json').write_bytes(encoded(summary));seal(root);return summary


def verify_matrix(root):
    root=Path(root);before=verify(root);rows=[]
    base=read_json(root/'accepted'/'plan.json')
    for name,p in variants(base):
        if read_json(root/name/'plan.json')!=p:raise ValueError('matrix settings differ')
        r=screen(p,root/'catalog-study')
        if read_json(root/name/'result.json')!=r:raise ValueError('screen matrix evidence differs')
        if r['passes_declared_screen']!=(name in ['accepted','inverse-primary']):raise ValueError('matrix decision differs')
        rows.append(dict(id=name,passed=r['passes_declared_screen'],checks=r['checks']))
    external=external_faults(root/'catalog-study',read_json(root/'accepted'/'result.json'))
    if read_json(root/'external-reference.json')!=external or not external['passed']:raise ValueError('external fault reference differs')
    expected=dict(passed=True,code_sha256=package_fingerprint(),rows=rows,external_fault_solutions=len(external['rows']),
        producer_sha256=digest(Path(__file__).read_bytes()),closed_loop_protection_qualified=False,normative_approval=False,reward_exported=False)
    if read_json(root/'summary.json')!=expected:raise ValueError('matrix summary differs')
    if verify(root)!=before:raise ValueError('matrix changed')
    return dict(verified=True,manifest_sha256=before)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--study',required=True);p.add_argument('--input',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    print(encoded(run(a.study,read_json(a.input),a.out)).decode());print(encoded(verify_matrix(a.out)).decode())
