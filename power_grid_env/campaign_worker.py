"""One campaign case, one process group, no nested isolated peers."""
from pathlib import Path
import resource,sys
from power_grid_env.study_artifacts import read_json
from power_grid_env.report import write_json
from power_grid_env.evidence import package_fingerprint


def main():
    from power_grid_env.campaign import CampaignCase,_identity
    out=Path(sys.argv[1]);raw=read_json(out/'case.json')
    memory=raw.get('memory_limit_mb',4096)
    if type(memory) is not int or not 256<=memory<=8192:raise ValueError('invalid worker address-space budget')
    limit=memory*1024*1024;resource.setrlimit(resource.RLIMIT_AS,(limit,limit))
    from power_grid_env.study_artifacts import MAX_FILE
    resource.setrlimit(resource.RLIMIT_FSIZE,(MAX_FILE,MAX_FILE))
    case=CampaignCase.model_validate(raw);code=package_fingerprint()
    write_json(out/'study.json',case.study)
    if case.protocol is not None:write_json(out/'protocol.json',case.protocol)
    if case.kind=='protected_motor':
        from power_grid_env.protected_motor import ProtectedMotorStudy,run_protected_motor
        from power_grid_env.advanced_studies import protected_html
        study=ProtectedMotorStudy.model_validate(case.study);result=run_protected_motor(study)
        if result.get('completed'):(out/'report.html').write_text(protected_html(study,result),encoding='utf-8')
    elif case.kind=='dynamic_motor_identification':
        from power_grid_env.dynamic_motor_identification import DynamicMotorIdentification,identify_dynamic
        from power_grid_env.advanced_studies import dynamic_identification_html
        spec=DynamicMotorIdentification.model_validate(case.study);result=identify_dynamic(spec)
        (out/'report.html').write_text(dynamic_identification_html(spec,result),encoding='utf-8')
        if result.get('passed'):write_json(out/'passport.json',result['passport'])
    elif case.kind=='sensitivity':
        from power_grid_env.study_spec import StudySpec
        from power_grid_env.study_sensitivity import SensitivityRequest,differentiate_study
        result=differentiate_study(StudySpec.model_validate(case.study),SensitivityRequest.model_validate(case.protocol))
    else:
        from power_grid_env.study_spec import StudySpec,run_study
        result=run_study(StudySpec.model_validate(case.study))
    if package_fingerprint()!=code:raise ValueError('worker producer changed')
    write_json(out/'result.json',result)
    write_json(out/'worker.json',dict(code_sha256=code,case_sha256=_identity(case),maximum_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,address_space_limit_mb=case.memory_limit_mb,isolated_process=True))
    return 0

if __name__=='__main__':raise SystemExit(main())
