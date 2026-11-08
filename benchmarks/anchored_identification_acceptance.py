"""Unchanged locked-rotor observations, additional declared DC data, fixed gates."""
from pathlib import Path
import argparse
import copy
import json

import numpy as np

from power_grid_env.stator_resistance_anchor import run_anchored_identification
from power_grid_env.dynamic_identification_audit import run_audit_artifact
from power_grid_env.dynamic_motor_identification import DynamicMotorIdentification, WorkBudget, replay
from power_grid_env.flux_machine import FluxMachinePassport
from power_grid_env.motor_catalog import catalog_entry, catalog_study
from power_grid_env.flux_network import run_flux_network
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,digest,verify,seal,export_zip,extract_zip

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'cases/anchored-identification'


def protocol():
    verify(SOURCE/'frozen-unanchored')
    return read_json(SOURCE/'locked-dc-anchor.json')


def run(out,wheel):
    from benchmarks.extended_equipment_validation import verify_native
    from benchmarks.flux_network_native import reference
    from benchmarks.dynamic_identification_acceptance import deployment_compare
    root=Path(out)
    if root.exists():raise ValueError('new anchored acceptance output required')
    native_identity=verify_native(Path(wheel));code=package_fingerprint();request=protocol()
    wrong=copy.deepcopy(request)
    for point in wrong['stator_resistance_measurement']['points']:point['voltage_v']*=1.2
    inconsistent=copy.deepcopy(request)
    inconsistent['stator_resistance_measurement']['points'][0]['voltage_v']+=.1
    # All new data and limits fixed before observing a fit or a held-out result.
    root.mkdir(parents=True)
    (root/'protocol.json').write_bytes(encoded(dict(anchored=request,wrong_dc=wrong,inconsistent_dc=inconsistent,
        original_audit_sha256=digest(encoded(request['audit'])),parameter_relative_limit=.02,
        original_loss_limit_w=.5,same_passport_loss_error_limit_w=.005,
        forward_current_limit_a=5e-7,additional_data='authored ideal settled four-wire DC points, not measured hardware')))
    if request['audit']!=read_json(SOURCE/'frozen-unanchored/audit.json'):
        raise ValueError('original dynamic protocol or observations changed')
    baseline=run_audit_artifact(request['audit'],root/'unanchored')
    original=read_json(SOURCE/'frozen-unanchored/original-deployment.json')
    if original['passed']:raise ValueError('expected retained unqualified baseline')
    truth=catalog_entry('aalto-linear-fan')['motor'];truth.locked_rotor=True
    net,settings=catalog_study('aalto-linear-fan',disturbance=False);settings.duration_s=.24;settings.step_s=.002
    true_settings=settings.model_copy(deep=True);true_settings.motors=[truth]
    truth_network=reference(net,true_settings,max_step=.0001)
    (root/'network-truth-native.json').write_bytes(encoded(truth_network))

    def deployment(passport,name):
        study=settings.model_copy(deep=True);study.motors=[passport]
        primary=run_flux_network(net,study)
        result=deployment_compare(primary,truth_network)
        (root/(name+'-network.json')).write_bytes(encoded(primary))
        (root/(name+'-comparison.json')).write_bytes(encoded(result))
        return result,primary,study

    candidate=FluxMachinePassport.model_validate(baseline['passport'])
    baseline_check,baseline_network,baseline_settings=deployment(candidate,'unanchored')
    same_reference=reference(net,baseline_settings,max_step=.0001)
    same=deployment_compare(baseline_network,same_reference)
    (root/'same-passport-native.json').write_bytes(encoded(same_reference))
    (root/'same-passport-comparison.json').write_bytes(encoded(same))
    loss_keys=('m.stator_copper_w','m.rotor_copper_w')
    numerical_loss=max(same['errors'][key] for key in loss_keys)
    spec=DynamicMotorIdentification.model_validate(request['audit']['identification'])
    first=replay(candidate,spec.fitting[0],WorkBudget(spec),method='sample_rk4')
    second=replay(candidate,spec.fitting[0],WorkBudget(spec),method='dop853',accuracy_factor=.001)
    forward_error=float(np.max(np.abs(first['current']-second['current'])))
    diagnosis=bool(baseline['passed'] and not baseline_check['passed'] and same['passed'] and numerical_loss<=.005 and forward_error<=5e-7)
    anchored=run_anchored_identification(request,root/'anchored')
    comparisons=[];parameters=[]
    for name in ('nominal','lower','upper'):
        result=read_json(root/'anchored'/name/'result.json')
        p=FluxMachinePassport.model_validate(result['passport'])
        check,_,_=deployment(p,'anchored-'+name)
        errors=dict(stator_r_ohm=abs(p.stator_r_ohm/truth.stator_r_ohm-1),
                    unsaturated_h=abs(p.unsaturated_h/truth.unsaturated_h-1),
                    rotor_r_ohm=abs(p.cages[0].resistance_ohm/truth.cages[0].resistance_ohm-1),
                    rotor_leakage_h=abs(p.cages[0].leakage_h/truth.cages[0].leakage_h-1))
        comparisons.append(dict(id=name,passed=check['passed'],loss_errors_w={key:check['errors'][key] for key in loss_keys}))
        parameters.append(dict(id=name,errors=errors,passed=max(errors.values())<=.02))
    wrong_result=run_anchored_identification(wrong,root/'wrong-dc')
    wrong_ok=wrong_result['completed'] and not wrong_result['passed'] and wrong_result['reason']=='anchor_bracket_raw_replay_rejected' and not (root/'wrong-dc/passport.json').exists()
    try:run_anchored_identification(inconsistent,root/'inconsistent-dc')
    except ValueError:inconsistent_ok=not (root/'inconsistent-dc').exists()
    else:inconsistent_ok=False
    verify(root/'anchored');export_zip(root/'anchored',root/'anchor.zip');extract_zip(root/'anchor.zip',root/'imported');verify(root/'imported')
    transport=read_json(root/'imported/passport.json')==read_json(root/'anchored/passport.json') if anchored['passed'] else False
    from power_grid_env.campaign import CampaignCase,CampaignPlan,run_campaign,verify_campaign
    case=CampaignCase(id='dc-anchor',kind='anchored_dynamic_identification',study=request,worker_wall_s=360.)
    plan=CampaignPlan(id='dc-reference',cases=[case],maximum_wall_s=600.)
    series=run_campaign(plan.model_dump(mode='json'),root/'campaign');verify_campaign(root/'campaign')
    restored=run_campaign(plan.model_dump(mode='json'),root/'restored',resume_from=root/'campaign')
    exact=read_json(root/'campaign/attempts/dc-anchor/result.json')==read_json(root/'restored/attempts/dc-anchor/result.json')
    if code!=package_fingerprint():raise ValueError('anchor producer changed')
    summary=dict(passed=bool(diagnosis and anchored['passed'] and all(c['passed'] for c in comparisons) and all(p['passed'] for p in parameters) and wrong_ok and inconsistent_ok and transport and series['passed'] and restored['passed'] and exact),
        producer_sha256=code,checker_sha256=digest(Path(__file__).read_bytes()),
        baseline_raw_passed=baseline['passed'],baseline_network_passed=baseline_check['passed'],
        baseline_loss_errors_w={key:baseline_check['errors'][key] for key in loss_keys},same_passport_numerical_loss_error_w=numerical_loss,
        forward_current_error_a=forward_error,anchored_passed=anchored['passed'],anchor=anchored['anchor'],anchored_network_comparisons=comparisons,
        parameters=parameters,wrong_dc_rejected=wrong_ok,inconsistent_dc_rejected=inconsistent_ok,transport_exact=transport,campaign_restoration_exact=exact,
        additional_independent_dc_data_required=True,old_unanchored_profile_qualified=False,continuous_interval_verified=False,
        independent_reference=native_identity,wheel_sha256=digest(Path(wheel).read_bytes()),field_validated=False,statistical_confidence_claimed=False)
    (root/'summary.json').write_bytes(encoded(summary));seal(root);return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);parser.add_argument('--wheel',required=True)
    args=parser.parse_args();r=run(args.out,args.wheel)
    print(json.dumps(dict(passed=r['passed'],baseline_loss_errors_w=r['baseline_loss_errors_w'],anchored=r['anchored_network_comparisons'])));raise SystemExit(0 if r['passed'] else 2)
