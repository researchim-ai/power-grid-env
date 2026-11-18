"""Local authored mixed GENCLS / two-cage R(T) qualification, without CI."""
import copy
from pathlib import Path
from benchmarks.rms_winding_acceptance import compare as thermal_compare


def compare(reference,report):
    result=thermal_compare(reference,report)
    if 'errors' not in result:return result
    # Absolute SI power tolerance, shared by both generator P/Q and winding heat.
    for key in result['limits']:
        if key.endswith(('_w','_var','_j')):result['limits'][key]=.003
    result['passed']=not result['missing_channels'] and result['event_identities_match'] and result['maximum_event_error_s']<=result['event_tolerance_s'] and all(v<=result['limits'][k] for k,v in result['errors'].items())
    return result


def execute(job,root):
    from benchmarks.generator_stator_reference import GeneratorStatorReference
    from power_grid_env.study_spec import StudySpec,run_study
    from power_grid_env.study_artifacts import encoded,seal
    root.mkdir(parents=True,exist_ok=False)
    refs=[GeneratorStatorReference(job['study']).run(max_step=.002),GeneratorStatorReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
    rows=[]
    for method in ['radau','network_dae']:
        for grid in [0,1]:
            s=copy.deepcopy(job['study']);s['coupled'].update(integration_method=method,step_s=.002/2**grid)
            spec=StudySpec.model_validate(s);report=run_study(spec);checks=[compare(r,report) for r in refs]
            import numpy as np
            audit=report.get('operating_point',{});joint=job['study']['coupled']['operating_point']['winding_state']=='equilibrium'
            physical_ok=report['completed'] and report['additional_validation']['ok']
            key='full_physical_spectral_abscissa_per_s' if joint else 'connected_machine_spectral_abscissa_per_s'
            for r in refs:
                physical_ok=physical_ok and abs(audit.get(key,1.)-r.initial['energy_spectral_abscissa_per_s'])<2e-6
            if joint:
                physical_ok=physical_ok and audit['maximum_stationary_heat_error_w']<1e-5 and audit['checked_dynamic_residual_per_s']<1e-8
                if not job['fault']:
                    physical_ok=physical_ok and all(np.ptp(report['channels'][k])<1e-8 for k in ['m.speed_pu','m.stator_temperature_c','m.rotor_temperature_c','g.omega'])
            # This qualification deliberately remains inside both controller limits.
            for group,parameter,signal,lo,hi in [('TGOV1','governor','valve_state_pu','VMIN','VMAX'),('SEXS','exciter','field_state_pu','EMIN','EMAX')]:
                p=job['study']['coupled']['machines'][0].get(parameter)
                if p:
                    values=report['channels']['g.'+group+'.'+signal]
                    physical_ok=physical_ok and min(values)>p[lo]+1e-4 and max(values)<p[hi]-1e-4
            child=root/(method+'-'+str(grid));child.mkdir()
            for name,value in [('study',spec.model_dump(mode='json')),('trace',report),('comparison',checks)]:
                (child/(name+'.json')).write_bytes(encoded(value))
            seal(child);rows.append(dict(method=method,grid=grid,passed=bool(physical_ok and all(c['passed'] for c in checks))))
    for name,r in zip(['reference','refined-reference'],refs):
        times=[i*.001 for i in range(401) if all(abs(i*.001-e['time_s'])>2e-7 for e in r.events)]
        (root/(name+'.json')).write_bytes(encoded(dict(initialization=r.initial,events=r.events,regular_values=[dict(time_s=t,channels=r.sample(t)) for t in times])))
    summary=dict(id=job['id'],passed=all(r['passed'] for r in rows),rows=rows)
    (root/'summary.json').write_bytes(encoded(summary));seal(root);return summary


def qualify(out):
    from benchmarks.generator_stator_cases import jobs
    from power_grid_env.study_artifacts import encoded,seal,digest
    from power_grid_env.evidence import package_fingerprint
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint()
    sources=['benchmarks/generator_stator_cases.py','benchmarks/generator_stator_reference.py','benchmarks/generator_stator_acceptance.py','benchmarks/generator_energy_cases.py','benchmarks/generator_energy_reference.py','benchmarks/generator_energy_acceptance.py','benchmarks/generator_heat_cases.py','benchmarks/generator_heat_reference.py','benchmarks/generator_heat_acceptance.py','benchmarks/generator_replica_cases.py','benchmarks/generator_replica_reference.py','benchmarks/generator_replica_acceptance.py','benchmarks/controlled_winding_cases.py','benchmarks/controlled_winding_reference.py','benchmarks/controlled_winding_acceptance.py','benchmarks/mixed_equilibrium_cases.py','benchmarks/mixed_equilibrium_reference.py','benchmarks/mixed_equilibrium_acceptance.py','benchmarks/mixed_winding_cases.py','benchmarks/mixed_winding_reference.py','benchmarks/mixed_winding_acceptance.py','benchmarks/two_cage_winding_reference.py','benchmarks/rms_winding_reference.py','benchmarks/loaded_protection_winding_acceptance.py','benchmarks/rms_winding_acceptance.py','benchmarks/motor_only_loaded_reference.py']
    hashes={p:digest(Path(p).read_bytes()) for p in sources};rows=[]
    for j in jobs():
        row=execute(j,root/j['id']);rows.append(row);print(row,flush=True)
    from benchmarks.generator_stator_cases import refusals
    from power_grid_env.study_spec import StudySpec,run_study
    negatives=[]
    for job in refusals():
        for method in ['radau','network_dae']:
            spec=copy.deepcopy(job['study']);spec['coupled']['integration_method']=method
            owner=StudySpec.model_validate(spec);trace=run_study(owner)
            if trace['completed']:raise ValueError('expected initial refusal')
            leaf=root/(job['id']+'-'+method);leaf.mkdir()
            for name,value in [('study',owner.model_dump(mode='json')),('trace',trace)]:
                (leaf/(name+'.json')).write_bytes(encoded(value))
            seal(leaf);negatives.append(dict(id=leaf.name,passed=True))
    result=dict(refusals=negatives,initial_refusals=len(negatives),passed=len(rows)==4 and all(r['passed'] for r in rows),physical_cases=4,native_runs=16,independent_trajectories=8,comparisons=32,code_sha256=fp,producers=hashes,rows=rows,scope='Authored balanced radial, one GENCLS with interior TGOV1/SEXS and two-cage R(T) motor;passport-temperature or joint P/V/load/two-node heat initialization; hold and finite three-phase fault. Electrical stator R(T) stamp,constant shaft power,H*S*omega^2,copper loss in internal electrical power and core-only extra shaft debit;no temperature trip,limiter-event qualification,PSS/GENROU, generator topology switching or OEM qualification.')
    if fp!=package_fingerprint() or hashes!={p:digest(Path(p).read_bytes()) for p in sources}:raise ValueError('producer changed')
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args();r=qualify(a.out)
    if not r['passed']:raise SystemExit(2)
