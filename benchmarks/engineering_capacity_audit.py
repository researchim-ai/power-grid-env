"""Independent artifact/trace replay of the controlled depth8 arm.

Baseline reuse remains explicit; no original source/score is rewritten. This
verifies recorded decisions,not a second model execution or industrial delivery.
"""
from pathlib import Path
from benchmarks import engineering_capacity_campaign as capacity
from benchmarks.engineering_native_staged_policy import checkpoint
from benchmarks.engineering_staged_audit import replay
from benchmarks.engineering_skill_tasks import score,identity
from benchmarks.engineering_grounding import digest
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal


def run(root,anchor,plan,plan_anchor,target,out):
    if verify(root)!=anchor:raise ValueError('owner capacity result differs')
    data,p,reference,before_workflows=capacity.load_plan(plan,plan_anchor)
    r,_=checkpoint(root,anchor,plan,plan_anchor,p['target_sha256'])
    if r.get('variant')!=capacity.VARIANT or r.get('lora')!=p['lora'] or r.get('baseline_reused')!=p['baseline_reused'] or r.get('initialization_rechecked') is not True or r.get('initialization_proof_sha256')!=digest(p['initialization_proof']):
        raise ValueError('capacity initialization/config/baseline attribution differs')
    capacity.alignment_evidence(r['alignment_audit'],p)
    if r['adapter_names']!=p['initialization_proof']['expanded_adapter_names'] or r['source_sha256']!=p['source_sha256']:
        raise ValueError('capacity module/source inventory differs')
    for name,sha in p['baseline_reused']['files'].items():
        if identity(Path(root)/name)!=sha:raise ValueError('copied parent40 baseline differs')
    if r['before']!=reference['before']:raise ValueError('reused skill baseline differs')
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    t=local_target(target,p['target_sha256']);processor=local_qwen35_processor(t['local_checkpoint'],t['config_sha256'])
    rows=[];after_workflows=[]
    for c in data.cases(p['case_ids']):
        saved=read_json(Path(root)/'episodes'/(capacity.CANDIDATE+'-'+c['id']+'.json'))
        if saved['candidate']!=capacity.CANDIDATE:raise ValueError('capacity candidate attribution differs')
        rows.append(replay(c,saved,processor));after_workflows.append(saved)
    capacity._check_skills(r['after'],data,p,capacity.CANDIDATE)
    compact=lambda traces:[dict(task_id=x['task_id'],success=x['success'],calls=x['calls']) for x in traces]
    if r['workflow_results']!={'adapter40':compact(before_workflows),capacity.CANDIDATE:compact(after_workflows)} or r['after_workflow_replay']!=rows:
        raise ValueError('capacity recorded workflows/replay differ')
    expected=capacity.score_result(r['before'],r['after'],before_workflows,after_workflows)
    comparison=capacity.control_comparison(reference['score'],expected)
    gate=expected['after_skills']==15 and expected['after_workflows']==3
    if r['score']!=expected or r['control_comparison']!=comparison or r['gate_passed']!=gate:raise ValueError('capacity primary scores/comparison differ')
    result=dict(schema='power-grid-env.capacity-campaign-audit/v1',source_manifest_sha256=anchor,plan_manifest_sha256=plan_anchor,
        source_sha256=identity(__file__),rows=rows,score=expected,control_comparison=comparison,replay_passed=True,gate_passed=gate,
        baseline_reused=True,scope='Independent replay of recorded depth8 model outputs,physical judge and native caps;baseline remains reused;one train base network,no heldout/industrial claim')
    write_json(Path(out)/'summary.json',result);seal(out);return result
