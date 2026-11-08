"""Fixed-seed sensor-noise audit on hash-pinned original-motulator traces."""
from pathlib import Path
import argparse
import json

import numpy as np

from benchmarks.motor_recording_acceptance import ROOT, FIXTURE_HASHES
from power_grid_env.dynamic_identification_audit import audit_identification
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import digest, encoded, read_json, seal, export_zip, extract_zip


SEEDS = (17001, 17002)
DEPLOYMENT_LIMITS = dict(current_a=.1, voltage_v=.1, flux_vs=.001, speed_rad_s=.1, torque_nm=.1, loss_w=3.)


def equivalent_restored_result(original, repeated, expected_pass):
    """Passed attempts are reused exactly; rejected attempts are rerun by design.

    Only elapsed wall time is nonphysical metadata for a rerun. Parameter values,
    trajectories, residuals, RHS work and rejection reason must still match.
    """
    if original.get('passed') is not expected_pass or repeated.get('passed') is not expected_pass:
        return False
    if expected_pass:
        return original == repeated
    return {k: v for k, v in original.items() if k != 'elapsed_s'} == {k: v for k, v in repeated.items() if k != 'elapsed_s'}


def specimen(profile='linear', seed=17001, window=0, defect=None):
    path = ROOT / 'cases/dynamic-identification' / (profile + '.json')
    if digest(path.read_bytes()) != FIXTURE_HASHES[profile]:
        raise ValueError('frozen independent observation fixture changed')
    document = read_json(path)
    # Noise makes some fits require more iterations than the clean B15 corpus.
    # This is an explicit work allowance; all physical/residual limits stay fixed.
    document['maximum_rhs_evaluations'] = 4000000
    for split_index, split in enumerate(('fitting', 'validation')):
        for index, trace in enumerate(document[split]):
            # Split-specific streams are independent of iteration/fit order.
            rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence([seed, split_index, index])))
            for field in ('current_alpha_a', 'current_beta_a'):
                values = np.asarray(trace[field]) + rng.normal(0, .001, len(trace[field]))
                if defect == 'heldout_current_bias' and split == 'validation' and field == 'current_alpha_a':
                    values += .004
                trace[field] = values.tolist()
            if not document['initial']['locked_rotor']:
                values = np.asarray(trace['speed_rad_s']) + rng.normal(0, .001, len(trace['time_s']))
                values[0] = trace['speed_rad_s'][0]  # Authored known initial condition.
                if defect == 'heldout_speed_drift' and split == 'validation':
                    values += np.linspace(0, .03, len(values))
                trace['speed_rad_s'] = values.tolist()
            trace['current_sigma_a'] = .004
            trace['speed_sigma_rad_s'] = .01
            trace['provenance'] += f'; authored Gaussian sensor noise, PCG64 SeedSequence([{seed},{split_index},{index}]), current std=.001 A, mechanical speed std=.001 rad/s, exact initial speed; defect={defect}'
    return dict(schema_version='power-grid-env.dynamic-identification-audit/v1', identification=document,
                training_speed_filter_window=window)


def run(out, wheel):
    from benchmarks.extended_equipment_validation import verify_native
    native_identity = verify_native(Path(wheel))
    root = Path(out)
    if root.exists():
        raise ValueError('new noise acceptance directory required')
    root.mkdir(parents=True)
    code = package_fingerprint()
    # Entire corpus and limits are fixed before the first fit. No hold-out tuning.
    protocols = {f'{profile}-{seed}-w{window}': specimen(profile, seed, window)
                 for profile in ('linear', 'saturated', 'viscous', 'locked') for seed in SEEDS for window in (0, 9)}
    for defect in ('heldout_current_bias', 'heldout_speed_drift'):
        protocols[defect] = specimen(defect=defect)
    (root / 'protocols.json').write_bytes(encoded(dict(inputs=protocols, parameter_relative_limit=.02, deployment_limits=DEPLOYMENT_LIMITS,
                                                     observations='frozen original-motulator synthetic traces with authored noise, not field measurements')))
    rows = []
    deployments = []
    for name, protocol in protocols.items():
        result = audit_identification(protocol)
        folder = root / name
        folder.mkdir()
        (folder / 'result.json').write_bytes(encoded(result))
        expected_pass = not name.startswith('heldout_')
        # Compare to the same independent true passports used in B15/B16.
        from power_grid_env.motor_catalog import catalog_entry
        profile = name.split('-')[0]
        truth = catalog_entry('aalto-linear-fan' if profile in ('linear', 'locked') or not expected_pass else 'aalto-saturated-fan')['motor']
        if profile == 'viscous':
            truth.fan_nm_per_rad_s2 = 0.
            truth.viscous_nm_per_rad_s = 14.6 / (np.pi * 50)
        errors = {}
        if result.get('completed'):
            candidate = result['passport']
            for field in ('stator_r_ohm', 'unsaturated_h', 'inertia_kg_m2', 'fan_nm_per_rad_s2', 'viscous_nm_per_rad_s', 'saturation_beta_per_vs'):
                errors[field] = abs(candidate[field] - getattr(truth, field)) / max(abs(getattr(truth, field)), 1e-12)
            for field in ('resistance_ohm', 'leakage_h'):
                errors[field] = abs(candidate['cages'][0][field] / getattr(truth.cages[0], field) - 1)
        passed = bool(result.get('completed') and result.get('passed') == expected_pass and
                      (max(errors.values(), default=float('inf')) <= .02 if expected_pass else result.get('fit_passed') and not result.get('raw_residuals_passed')))
        if expected_pass and name.endswith(('17001-w0', '17001-w9')) and result.get('passed'):
            from power_grid_env.flux_machine import FluxMachinePassport
            from power_grid_env.motor_catalog import catalog_study
            from power_grid_env.flux_network import run_flux_network
            from benchmarks.flux_network_native import reference
            if profile == 'locked': truth.locked_rotor = True
            network, settings = catalog_study('aalto-linear-fan', disturbance=False)
            settings.duration_s = .24; settings.step_s = .002
            native_settings = settings.model_copy(deep=True); native_settings.motors = [truth]
            settings.motors = [FluxMachinePassport.model_validate(result['passport'])]
            primary = run_flux_network(network, settings)
            native = reference(network, native_settings, max_step=.0001)
            if not primary['completed'] or not native['completed'] or primary['time_s'] != list(native['time_s']):
                raise ValueError('complete aligned deployment trajectories required')
            errors_by_channel = {}; limits_by_channel = {}
            for key, values in native['channels'].items():
                label = 'loss_w' if 'copper' in key or key.endswith('.loss_w') else 'voltage_v' if 'voltage' in key else 'current_a' if 'current' in key else 'flux_vs' if 'flux' in key else 'speed_rad_s' if 'speed' in key else 'torque_nm' if 'torque' in key else None
                if label is None: raise ValueError('unregistered deployment channel')
                a = np.asarray(primary['channels'][key]); b = np.asarray(values)
                if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
                    raise ValueError('finite complete deployment channels required')
                errors_by_channel[key] = float(np.max(np.abs(a - b)))
                limits_by_channel[key] = DEPLOYMENT_LIMITS[label]
            check = dict(id=name, passed=all(errors_by_channel[k] <= limits_by_channel[k] for k in errors_by_channel), errors=errors_by_channel, limits=limits_by_channel)
            deployments.append(check)
            for label, data in [('network-primary', primary), ('network-native', native), ('deployment', check)]:
                (folder / (label + '.json')).write_bytes(encoded(data))
            passed = bool(passed and check['passed'])
        rows.append(dict(id=name, expected_pass=expected_pass, passed=passed, identification_passed=result.get('passed'),
                         fit_passed=result.get('fit_passed'), reason=result.get('reason'), parameter_relative_errors=errors))
    if code != package_fingerprint():
        raise ValueError('noise acceptance producer changed')
    from power_grid_env.campaign import CampaignCase, CampaignPlan, run_campaign, verify_campaign
    campaign = CampaignPlan(id='noise-audit', maximum_workers=2, cases=[CampaignCase(id=name, kind='dynamic_identification_audit', study=protocols[name], worker_wall_s=180.) for name in ('linear-17001-w0', 'linear-17001-w9', 'heldout_current_bias')])
    first = run_campaign(campaign.model_dump(mode='json'), root / 'campaign'); verify_campaign(root / 'campaign')
    export_zip(root / 'campaign', root / 'campaign.zip'); extract_zip(root / 'campaign.zip', root / 'imported'); verify_campaign(root / 'imported')
    restored = run_campaign(campaign.model_dump(mode='json'), root / 'restored', resume_from=root / 'imported')
    expected = [True, True, False]
    campaign_ok = first['completed'] and restored['completed'] and not first['passed'] and not restored['passed']
    for case, wanted in zip(campaign.cases, expected, strict=True):
        original = read_json(root / 'campaign/attempts' / case.id / 'result.json')
        repeat = read_json(root / 'restored/attempts' / case.id / 'result.json')
        campaign_ok = bool(campaign_ok and equivalent_restored_result(original, repeat, wanted))
    campaign_ok = bool(campaign_ok and all(row['reused'] is expected_pass for row, expected_pass in zip(restored['cases'], expected, strict=True)))
    if code != package_fingerprint(): raise ValueError('noise acceptance producer changed')
    summary = dict(schema='power-grid-env.noisy-identification-acceptance/v1', passed=all(row['passed'] for row in rows) and len(deployments) == 8 and all(row['passed'] for row in deployments) and campaign_ok,
                   producer_sha256=code, benchmark_sha256=digest(Path(__file__).read_bytes()),
                   protocols_sha256=digest((root / 'protocols.json').read_bytes()), profiles=rows, network_deployments=deployments,
                   original_reference=native_identity, wheel_sha256=digest(Path(wheel).read_bytes()), campaign_restoration_passed=campaign_ok,
                   campaign_restoration_contract='passed: exact reused result; rejected: rerun, all fields exact except elapsed_s',
                   field_validated=False, statistical_confidence_claimed=False)
    (root / 'summary.json').write_bytes(encoded(summary))
    seal(root)
    return summary


def reassess_completed(source, out):
    """Correct the restoration gate without claiming new physics execution.

    Only accepts an intact completed corpus whose entire physical acceptance
    passed. Original inputs, producer, failed gate and evidence are retained.
    """
    import shutil
    from power_grid_env.study_artifacts import verify
    from power_grid_env.campaign import verify_campaign
    source, out = Path(source), Path(out)
    identity = verify(source)
    if out.exists(): raise ValueError('new reassessment directory required')
    summary = read_json(source / 'summary.json')
    if summary['producer_sha256'] != package_fingerprint(): raise ValueError('saved numerical producer differs')
    if len(summary['profiles']) != 18 or not all(row['passed'] for row in summary['profiles']): raise ValueError('physical profile gate did not pass')
    if len(summary['network_deployments']) != 8 or not all(row['passed'] for row in summary['network_deployments']): raise ValueError('network gate did not pass')
    protocols = read_json(source / 'protocols.json')
    expected = {f'{profile}-{seed}-w{window}': specimen(profile, seed, window) for profile in ('linear', 'saturated', 'viscous', 'locked') for seed in SEEDS for window in (0, 9)}
    for defect in ('heldout_current_bias', 'heldout_speed_drift'): expected[defect] = specimen(defect=defect)
    if protocols['inputs'] != expected or protocols['parameter_relative_limit'] != .02 or protocols['deployment_limits'] != DEPLOYMENT_LIMITS:
        raise ValueError('saved corpus or predeclared limits differ')
    for name in ('campaign', 'restored'): verify_campaign(source / name)
    first = read_json(source / 'campaign/summary.json'); restored = read_json(source / 'restored/summary.json')
    if not first['completed'] or not restored['completed'] or first['passed'] or restored['passed']: raise ValueError('expected a complete mixed positive/negative campaign')
    for id, wanted in [('linear-17001-w0', True), ('linear-17001-w9', True), ('heldout_current_bias', False)]:
        original = read_json(source / 'campaign/attempts' / id / 'result.json')
        repeated = read_json(source / 'restored/attempts' / id / 'result.json')
        row = next(r for r in restored['cases'] if r['id'] == id)
        if not equivalent_restored_result(original, repeated, wanted) or row['reused'] is not wanted:
            raise ValueError('campaign result or reuse contract differs')
    shutil.copytree(source, out)
    (out / 'manifest.json').unlink()
    (out / 'original-summary.json').write_bytes(encoded(summary))
    summary.pop('campaign_exact_restored_results', None)
    summary.update(passed=True, campaign_restoration_passed=True,
                   campaign_restoration_contract='passed: exact reused result; rejected: rerun, all fields exact except elapsed_s',
                   numerical_runs_reexecuted=False, reassessed_source_manifest_sha256=identity,
                   reassessment_checker_sha256=digest(Path(__file__).read_bytes()))
    # Preserve the old field as historical evidence rather than rewrite it.
    (out / 'summary.json').write_bytes(encoded(summary))
    seal(out)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--wheel')
    parser.add_argument('--reassess', help='intact completed corpus; recheck restoration gate without new numerical runs')
    args = parser.parse_args()
    if not args.reassess and not args.wheel: parser.error('--wheel required for new numerical execution')
    result = reassess_completed(args.reassess, args.out) if args.reassess else run(args.out, args.wheel)
    print(json.dumps(dict(passed=result['passed'], profiles=len(result['profiles']))))
    raise SystemExit(0 if result['passed'] else 2)
