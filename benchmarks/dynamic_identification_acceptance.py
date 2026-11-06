"""Synthetic original-RHS observations -> identification -> free/network replay."""
from pathlib import Path
import argparse
import numpy as np
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint
from power_grid_env.dynamic_motor_identification import identify_dynamic
from power_grid_env.flux_machine import FluxMachinePassport
from power_grid_env.campaign import CampaignPlan,CampaignCase,run_campaign,verify_campaign
from power_grid_env.study_artifacts import read_json,export_zip,extract_zip
from benchmarks.dynamic_motor_data import example

NAMES=('linear','saturated','viscous','locked')
LIMITS=dict(current_a=.01,voltage_v=.01,flux_vs=.0001,speed_rad_s=.025,torque_nm=.02,loss_w=.5)


def deployment_compare(primary,native):
    if not primary.get('completed') or not native.get('completed'):raise ValueError('complete deployment studies required')
    if primary['time_s']!=list(native['time_s']) or sorted(native['channels'])!=native['channel_inventory']:raise ValueError('deployment axes or channel inventory differ')
    errors={};limits={}
    for key,values in native['channels'].items():
        wanted=np.asarray(values);actual=np.asarray(primary['channels'][key])
        if actual.shape!=wanted.shape or not np.isfinite(actual).all() or not np.isfinite(wanted).all():raise ValueError('invalid deployment channel')
        label='loss_w' if 'copper' in key or key.endswith('.loss_w') else 'voltage_v' if 'voltage' in key else 'current_a' if 'current' in key else 'flux_vs' if 'flux' in key else 'speed_rad_s' if 'speed' in key else 'torque_nm' if 'torque' in key else None
        if label is None:raise ValueError('unregistered deployment output: '+key)
        errors[key]=float(max(abs(actual-wanted)));limits[key]=LIMITS[label]
    return dict(passed=all(errors[k]<=limits[k] for k in errors),errors=errors,limits=limits)


def run(out,wheel):
    from benchmarks.extended_equipment_validation import verify_native
    from benchmarks.flux_network_native import reference
    from power_grid_env.motor_catalog import catalog_study
    from power_grid_env.flux_network import run_flux_network
    out=Path(out)
    if out.exists():raise ValueError('fresh dynamic identification acceptance required')
    code=package_fingerprint();sources={p.name:sha256_file(p) for p in Path(__file__).parent.glob('*.py')};identity=verify_native(Path(wheel))
    specimens={name:example(name) for name in NAMES}
    write_json(out/'protocol.json',dict(code_sha256=code,producer_sources_sha256=sources,original_native_sources=identity,source_wheel_sha256=sha256_file(Path(wheel)),inputs={k:s.model_dump(mode='json') for k,(s,p) in specimens.items()},truth={k:p.model_dump(mode='json') for k,(s,p) in specimens.items()},parameter_relative_tolerance=5e-5,deployment_limits=LIMITS,observations='synthetic original motulator flux/mechanical RHS, authored excitation and mechanisms; not measured hardware'))
    rows=[];cases=[]
    for name,(spec,truth) in specimens.items():
        result=identify_dynamic(spec);write_json(out/name/'identification.json',result)
        passed=result.get('passed',False);parameter_errors={}
        if passed:
            fitted=FluxMachinePassport.model_validate(result['passport'])
            for label,actual,expected in [('stator_r_ohm',fitted.stator_r_ohm,truth.stator_r_ohm),('unsaturated_h',fitted.unsaturated_h,truth.unsaturated_h),('rotor_r_ohm',fitted.cages[0].resistance_ohm,truth.cages[0].resistance_ohm),('rotor_leakage_h',fitted.cages[0].leakage_h,truth.cages[0].leakage_h),('saturation_beta_per_vs',fitted.saturation_beta_per_vs,truth.saturation_beta_per_vs),('inertia_kg_m2',fitted.inertia_kg_m2,truth.inertia_kg_m2),('fan_nm_per_rad_s2',fitted.fan_nm_per_rad_s2,truth.fan_nm_per_rad_s2),('viscous_nm_per_rad_s',fitted.viscous_nm_per_rad_s,truth.viscous_nm_per_rad_s)]:
                parameter_errors[label]=abs(actual-expected)/abs(expected) if expected else abs(actual)
            network,settings=catalog_study('aalto-linear-fan',disturbance=False);settings.duration_s=.24;settings.step_s=.002
            native_settings=settings.model_copy(deep=True);native_settings.motors=[truth];settings.motors=[fitted]
            primary=run_flux_network(network,settings);native=reference(network,native_settings,max_step=.0001)
            check=deployment_compare(primary,native)
            for label,data in [('network-primary',primary),('network-native',native),('deployment-comparison',check)]:write_json(out/name/(label+'.json'),data)
            passed=bool(check['passed'] and max(parameter_errors.values())<=5e-5)
        row=dict(profile=name,passed=passed,parameter_relative_errors=parameter_errors);rows.append(row);print(row,flush=True)
        cases.append(CampaignCase(id=name,kind='dynamic_motor_identification',study=spec.model_dump(mode='json'),worker_wall_s=180.))
    campaign=CampaignPlan(id='dynamic-identification',cases=cases,maximum_workers=2,maximum_wall_s=600.)
    series=run_campaign(campaign.model_dump(mode='json'),out/'campaign');verify_campaign(out/'campaign')
    export_zip(out/'campaign',out/'identification.zip');extract_zip(out/'identification.zip',out/'imported');verify_campaign(out/'imported')
    restored=run_campaign(campaign.model_dump(mode='json'),out/'restored',resume_from=out/'imported')
    same=all(read_json(out/'campaign/attempts'/c.id/'result.json')==read_json(out/'restored/attempts'/c.id/'result.json') for c in cases)
    unchanged=code==package_fingerprint() and sources=={p.name:sha256_file(p) for p in Path(__file__).parent.glob('*.py')}
    result=dict(passed=bool(all(r['passed'] for r in rows) and series['passed'] and restored['passed'] and same and unchanged),profiles=rows,original_traces=12,held_out_traces=4,independent_network_deployments=4,campaign_passed=series['passed'],exact_restored_results=same,producer_unchanged=unchanged,code_sha256=code,field_validated=False,statistical_confidence_claimed=False,industrial_release_qualified=False,artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*')) if p.is_file()})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);p.add_argument('--wheel',required=True,type=Path);a=p.parse_args();raise SystemExit(0 if run(a.out,a.wheel)['passed'] else 1)
