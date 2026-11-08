"""Frozen B18 failure -> knot-aligned integration -> unchanged physical gates."""
from pathlib import Path
import argparse
import json
import shutil
import time

import numpy as np

from benchmarks.calibrated_motor_acceptance import fixture
from power_grid_env.calibrated_motor_study import run_calibrated_study
from power_grid_env.dynamic_motor_identification import DynamicMotorIdentification, WorkBudget, replay, _passport, _value
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json, encoded, digest, verify, seal, export_zip, extract_zip

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / 'cases/sampled-identification/frozen-b18'
REPLAY_LIMITS = dict(current=5e-7, speed=5e-7, stator_flux=1e-8, rotor_flux=1e-8, torque=1e-6)
FROZEN_CSV = {'holdout-45.csv':'a1138803dc11f1086058bea43e337a28a287a917d5fd3e197d97f8c0a23e4ff0',
              'train-50.csv':'20a54e11423fe5fe6f6c7b864f7ebacde6f9942c855cb4e8cb342434d419b652',
              'train-35.csv':'71c8a5659ac29e1a4e63007d68a356f57b800124a15bcec0b6f4ee9d715173e3'}


def prepare(directory, profile, *, old=OLD, defect=None):
    directory = Path(directory)
    if profile == 'linear' and defect is None:
        verify(old)
        shutil.copytree(old / 'voltage_noise_10mv/input', directory)
        path = directory / 'study.json'
    else:
        path = fixture(directory, profile, defect=defect, voltage_noise_std_v=.01)
    protocol = read_json(directory / 'protocol.json')
    protocol['identification'].update(replay_method='sample_rk4', maximum_rhs_evaluations=4000000, maximum_wall_s=120.)
    (directory / 'protocol.json').write_bytes(encoded(protocol))
    return path


def work_measurements(spec):
    """Same trace/passport, sequential paired trials; Jacobian cost measured separately."""
    trace = spec.fitting[0]; names = spec.electrical_parameters
    seed = np.log([_value(spec.initial, name) for name in names])
    rows = {}
    for method in ('dop853', 'sample_rk4'):
        forward = []; jacobian = []
        for _ in range(3):
            budget = WorkBudget(spec); wall = time.perf_counter(); cpu = time.process_time()
            response = replay(spec.initial, trace, budget, conditional=True, method=method)
            forward.append(dict(rhs=budget.rhs_calls, elapsed_s=time.perf_counter()-wall, cpu_s=time.process_time()-cpu))
            budget = WorkBudget(spec); wall = time.perf_counter(); cpu = time.process_time(); columns = []
            for j in range(len(seed)):
                outputs = []
                for direction in (-1, 1):
                    point = seed.copy(); point[j] += direction * 1e-4
                    p = _passport(spec.initial, names, np.exp(point))
                    value = replay(p, trace, budget, conditional=True, method=method)['current']
                    outputs.append(np.r_[value.real, value.imag] / trace.current_sigma_a)
                columns.append((outputs[1]-outputs[0]) / .0002)
            matrix = np.asarray(columns).T
            if not np.isfinite(matrix).all(): raise ValueError('finite timing Jacobian required')
            jacobian.append(dict(rhs=budget.rhs_calls, elapsed_s=time.perf_counter()-wall, cpu_s=time.process_time()-cpu))
        rows[method] = dict(forward=forward, central_log_jacobian=jacobian, log_step=.0001, columns=len(names))
    ratios = {}
    for phase in ('forward', 'central_log_jacobian'):
        ratios[phase] = float(np.median([x['rhs'] for x in rows['dop853'][phase]]) / np.median([x['rhs'] for x in rows['sample_rk4'][phase]]))
    return dict(trials=rows, rhs_reduction_ratios=ratios, passed=all(r >= 2 for r in ratios.values()),
                comparison_scope='same frozen linear noisy-voltage trace and initial passport; CPU/wall are measured, no general speed guarantee')


def convergence(spec):
    rows = []
    for conditional in (True, False):
        reference = replay(spec.initial, spec.fitting[0], WorkBudget(spec), conditional=conditional, method='dop853', accuracy_factor=.001)
        responses = [replay(spec.initial, spec.fitting[0], WorkBudget(spec), conditional=conditional, method='sample_rk4', subdivisions=n) for n in (1, 2, 4)]
        errors = {key: float(np.max(np.abs(responses[0][key] - reference[key]))) for key in REPLAY_LIMITS}
        differences = [float(np.max(np.abs(responses[i]['current'] - responses[i+1]['current']))) for i in (0, 1)]
        refined = differences[1] <= differences[0] / 2 if differences[0] > 1e-8 else differences[1] <= 1e-8
        rows.append(dict(conditional=conditional, passed=all(errors[k] <= REPLAY_LIMITS[k] for k in errors) and refined,
                         dop853_tolerance_factor=.001, errors=errors, current_refinement_differences=differences,
                         integration=[r['integration'] for r in responses]))
    return rows


def run(out, wheel, old=OLD, *, include_locked=False):
    from benchmarks.extended_equipment_validation import verify_native
    from benchmarks.flux_network_native import reference
    from benchmarks.dynamic_identification_acceptance import deployment_compare
    from power_grid_env.flux_machine import FluxMachinePassport
    from power_grid_env.motor_catalog import catalog_entry, catalog_study
    from power_grid_env.flux_network import run_flux_network
    root = Path(out)
    if root.exists(): raise ValueError('new sampled acceptance output required')
    identity = verify_native(Path(wheel)); code = package_fingerprint()
    profiles=('linear','saturated','viscous','locked') if include_locked else ('linear','saturated','viscous')
    paths = {p: prepare(root / p / 'input', p, old=old) for p in profiles}
    paths['wrong_gain'] = prepare(root / 'wrong_gain/input', 'linear', defect='wrong_holdout_voltage_gain')
    source_raw = old / 'voltage_noise_10mv/input'
    raw_hashes = {p.name: digest(p.read_bytes()) for p in source_raw.glob('*.csv')}
    if raw_hashes != FROZEN_CSV or raw_hashes != {p.name: digest(p.read_bytes()) for p in (root / 'linear/input').glob('*.csv')}:
        raise ValueError('original failed raw observation bytes changed')
    (root / 'protocol.json').write_bytes(encoded(dict(source_failure_manifest_sha256=verify(old), frozen_raw_csv_sha256=raw_hashes,
        maximum_rhs_evaluations=4000000, maximum_wall_s=120., replay_limits=REPLAY_LIMITS, parameter_relative_limit=.02,
        profiles=list(profiles),inputs={name: read_json(path.parent/'protocol.json') for name,path in paths.items()},
        holdout_sigma_changed=False, input_filtered=False, observations='B18 frozen original-motulator data and authored .01 V voltage noise')))
    from power_grid_env.motor_recordings import import_recordings, load_recordings
    import_recordings(root / 'linear/input/protocol.json', root / 'timing-input')
    timing_spec = DynamicMotorIdentification.model_validate(load_recordings(root / 'timing-input'))
    original_path=old/'original-input.json' if (old/'original-input.json').exists() else old/'voltage_noise_10mv/run/recordings/study.json'
    original_input = DynamicMotorIdentification.model_validate(read_json(original_path))
    before=original_input.model_dump(mode='json');after=timing_spec.model_dump(mode='json')
    for name in ('replay_method','maximum_rhs_evaluations','maximum_wall_s'):
        before.pop(name);after.pop(name)
    if before!=after:raise ValueError('original physical protocol changed')
    work = work_measurements(timing_spec); (root / 'work.json').write_bytes(encoded(work))
    rows = []; series = []
    for profile, path in paths.items():
        result = run_calibrated_study(path, root / profile / 'run')
        if profile == 'wrong_gain':
            rows.append(dict(profile=profile, passed=result['completed'] and not result['passed'] and not (root/profile/'run/passport.json').exists(), reason=result['reason']))
            continue
        input_spec = DynamicMotorIdentification.model_validate(read_json(root/profile/'run/recordings/study.json'))
        checks = convergence(input_spec)
        (root/profile/'convergence.json').write_bytes(encoded(checks))
        truth = catalog_entry('aalto-linear-fan' if profile in ('linear','locked') else 'aalto-saturated-fan')['motor']
        if profile == 'viscous': truth.fan_nm_per_rad_s2=0.; truth.viscous_nm_per_rad_s=14.6/(np.pi*50)
        if profile == 'locked': truth.locked_rotor=True
        errors={}; deployment=dict(passed=False)
        if result['passed']:
            fitted=FluxMachinePassport.model_validate(read_json(root/profile/'run/passport.json'))
            for field in ('stator_r_ohm','unsaturated_h','saturation_beta_per_vs','inertia_kg_m2','fan_nm_per_rad_s2','viscous_nm_per_rad_s'):
                errors[field]=abs(getattr(fitted,field)-getattr(truth,field))/max(abs(getattr(truth,field)),1e-12)
            for field in ('resistance_ohm','leakage_h'):
                errors[field]=abs(getattr(fitted.cages[0],field)/getattr(truth.cages[0],field)-1)
            net,settings=catalog_study('aalto-linear-fan',disturbance=False); settings.duration_s=.24; settings.step_s=.002
            native=settings.model_copy(deep=True); native.motors=[truth]; settings.motors=[fitted]
            primary=run_flux_network(net,settings); original=reference(net,native,max_step=.0001)
            deployment=deployment_compare(primary,original)
            for name,data in [('network-primary',primary),('network-native',original),('deployment',deployment)]:
                (root/profile/(name+'.json')).write_bytes(encoded(data))
        audit=read_json(root/profile/'run/identification/study.json')
        series.append((profile,audit))
        numerical=read_json(root/profile/'run/identification/result.json')
        rows.append(dict(profile=profile,passed=bool(result['passed'] and deployment['passed'] and all(r['passed'] for r in checks) and errors and max(errors.values())<=.02),
                         parameter_relative_errors=errors,rhs_evaluations=numerical['rhs_evaluations'],evaluations=numerical['evaluations'],optimizer=numerical.get('optimizer')))
    from power_grid_env.campaign import CampaignCase,CampaignPlan,run_campaign,verify_campaign
    plan=CampaignPlan(id='sampled-identification',maximum_workers=2,cases=[CampaignCase(id=name,kind='dynamic_identification_audit',study=audit,worker_wall_s=120.) for name,audit in series[:2]])
    campaign=run_campaign(plan.model_dump(mode='json'),root/'campaign');verify_campaign(root/'campaign')
    export_zip(root/'campaign',root/'campaign.zip');extract_zip(root/'campaign.zip',root/'imported');verify_campaign(root/'imported')
    restored=run_campaign(plan.model_dump(mode='json'),root/'restored',resume_from=root/'imported')
    same=all(read_json(root/'campaign/attempts'/name/'result.json')==read_json(root/'restored/attempts'/name/'result.json') for name,_ in series[:2])
    if code!=package_fingerprint():raise ValueError('sampled producer changed')
    summary=dict(passed=bool(all(r['passed'] for r in rows) and work['passed'] and campaign['passed'] and restored['passed'] and same),
                 scope='including_unqualified_locked_transfer' if include_locked else 'linear_saturated_fan_viscous_noisy_voltage',
                 profiles=rows,work=work,producer_sha256=code,checker_sha256=digest(Path(__file__).read_bytes()),independent_reference=identity,
                 original_raw_csv_sha256=raw_hashes,exact_campaign_restoration=same,field_validated=False,statistical_confidence_claimed=False)
    (root/'summary.json').write_bytes(encoded(summary));seal(root);return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);parser.add_argument('--wheel',required=True);parser.add_argument('--source',type=Path,default=OLD)
    parser.add_argument('--include-locked',action='store_true',help='include unqualified locked-rotor .01 V identification/network loss profile; failed limits remain failures')
    args=parser.parse_args();result=run(args.out,args.wheel,args.source,include_locked=args.include_locked)
    print(json.dumps(dict(passed=result['passed'],profiles=len(result['profiles']))));raise SystemExit(0 if result['passed'] else 2)
