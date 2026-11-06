import pytest
from power_grid_env.study_artifacts import verify
from power_grid_env.advanced_studies import run_protected_artifact,run_sensitivity_artifact
from tests.test_protected_motor import short
from benchmarks.network_automation_reference import example


def test_protected_workflow_has_sealed_engineering_report(tmp_path):
    s=short();s.case.id='<script>untrusted</script>'
    r=run_protected_artifact(s.model_dump(mode='json'),tmp_path/'run')
    assert r['completed'];assert verify(tmp_path/'run')
    page=(tmp_path/'run/report.html').read_text();assert '<script>untrusted' not in page and '&lt;script&gt;' in page
    with pytest.raises(ValueError):run_protected_artifact(s.model_dump(mode='json'),tmp_path/'run')


def test_failed_study_has_no_engineering_report(tmp_path):
    s=short();s.runtime_limits.maximum_rhs_evaluations=1
    r=run_protected_artifact(s.model_dump(mode='json'),tmp_path/'run')
    assert not r['completed'] and not (tmp_path/'run/report.html').exists();verify(tmp_path/'run')


def test_cli_protected_exit_policy_and_manifest(tmp_path):
    import json,os,subprocess,sys
    source=tmp_path/'input.json';source.write_text(short().model_dump_json())
    result=subprocess.run([sys.executable,'-m','power_grid_env.cli','protected-motor','--study',str(source),'--out',str(tmp_path/'cli'),'--require-acceptable'],capture_output=True,text=True,env=os.environ.copy())
    assert result.returncode==2,result.stderr
    assert verify(tmp_path/'cli')
    assert not json.loads((tmp_path/'cli/result.json').read_text())['engineering']['engineering_acceptable']
