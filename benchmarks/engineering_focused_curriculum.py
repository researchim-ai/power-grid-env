"""TRAIN-only projections of replayed primitives into focused learning tasks.

One-field targets preserve actual engine labels. Coarse comparisons are explicitly
analytic lessons. No historical receipt or held-out corpus is modified.
"""
import argparse
import copy
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_primitive_admission import PrimitiveDataset
from benchmarks.engineering_primitive_curriculum import comparison, decimal_value
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_staged_data import pack
from power_grid_env.study_artifacts import read_json, seal, verify


FILES=('engineering_focused_curriculum.py','engineering_primitive_admission.py',
       'engineering_primitive_curriculum.py','engineering_staged_data.py','engineering_value_loss.py')


def sources():
    return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in FILES}


def request(kind,problem,field,reverse):
    if kind=='api_field':
        instruction=('Predict only the requested field for the given action from public before, installed_line_ids, '
          'tool_schema and tool_effect. Schema-invalid calls preserve revision and freshness. Assume successful '
          'computation for valid supported studies; this is not a convergence forecast. Return one JSON field named '+field+'.')
    elif kind=='comparison_field':
        instruction=('Compare exact decimal observed/limit with the stated maximum/minimum rule. Equality never violates. '
          'Missing observation means computable=false,relation=unknown,violation=null. Convert kA to A and kV to V '
          'by multiplying values by1000; %voltage to pu by dividing by100. Return only the requested JSON field '+field+'.')
    elif kind=='coarse_threshold':
        instruction=('Compare the simple decimal observed/limit in identical units. A maximum is violated only above; '
          'a minimum only below. Equality never violates. Return computable(boolean),relation(below/equal/above/unknown),'
          'violation(boolean or null). Missing observation means false/unknown/null. This is arithmetic, not a power study.')
    else:
        raise ValueError('unsupported focused task')
    value=dict(problem=copy.deepcopy(problem))
    if field is not None:value['requested_field']=field
    value['task']=kind
    if reverse:value=dict(reversed(list(value.items())))
    return [dict(role='system',content='ENGINEERING-FOCUSED/V1. '+instruction+' No Markdown.'),
            dict(role='user',content=json.dumps(value,separators=(',',':'),allow_nan=False))]


def derive(parent_rows):
    networks={r['base_project_id']:json.loads(r['messages'][1]['content'])['network']
              for r in parent_rows if r['skill']=='state_reading'}
    result=[]
    for parent in parent_rows:
        if parent['split']!='train':raise ValueError('focused derivation must not consume heldout labels')
        problem=json.loads(parent['messages'][1]['content'])
        tasks=[]
        if parent['skill']=='tool_effect':
            tasks=[('api_field',field,problem,{field:value}) for field,value in parent['answer'].items()]
        elif parent['skill']=='threshold':
            answer=comparison(problem)
            if sha(answer)!=sha(parent['answer']):raise ValueError('parent comparison label differs')
            tasks=[('comparison_field',field,problem,{field:value}) for field,value in answer.items()]
            unit=problem['limit']['unit']
            limit=Decimal('1') if unit=='pu' else Decimal(5+int(decimal_value(problem['limit']['value']))%40)
            step=Decimal('.1') if unit=='pu' else Decimal(1)
            relation=answer['relation']
            observed=None if relation=='unknown' else dict(value=str(limit+{'below':-step,'equal':Decimal(0),'above':step}[relation]),unit=unit)
            coarse=dict(observed=observed,limit=dict(value=str(limit),unit=unit),direction=problem['direction'],
                        network=networks[parent['base_project_id']],scope='Analytic simple-number lesson; not a new solved network.')
            tasks.append(('coarse_threshold',None,coarse,comparison(coarse)))
        for kind,field,public,answer in tasks:
            m=request(kind,public,field,parent['nuisance_variant']=='reordered_keys')
            value=dict(split='train',family_sha256=parent['family_sha256'],base_project_id=parent['base_project_id'],
                       source_row_id=parent['id'],skill=kind,field=field,nuisance_variant=parent['nuisance_variant'],
                       pair_group=sha([parent['pair_group'],kind,field]),member=parent['member'],
                       messages=m,answer=answer,input_sha256=sha(m),modality='text',
                       provenance='executed_transition_field' if kind=='api_field' else 'exact_analytic_comparison')
            value['id']=sha(value);result.append(value)
    if not result or len({r['id'] for r in result})!=len(result) or len({r['input_sha256'] for r in result})!=len(result):
        raise ValueError('nonempty unique focused inputs required')
    pairs={}
    for r in result:pairs.setdefault((r['pair_group'],r['member']),[]).append(r)
    for values in pairs.values():
        if (len(values)!=2 or len({sha(r['answer']) for r in values})!=1 or
                len({sha(json.loads(r['messages'][1]['content'])) for r in values})!=1):
            raise ValueError('focused nuisance transformation changed meaning or target')
    return result


def qualify(parent,parent_anchor,target,target_sha,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh focused release required')
    data=PrimitiveDataset(parent,parent_anchor,target_sha)
    rows=derive(data.rows)
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4)
    spec=local_target(target,target_sha)
    processor=local_qwen35_processor(spec['local_checkpoint'],spec['config_sha256'])
    checks,failures=[],[]
    for i,r in enumerate(rows):
        try:
            _,meta=pack(processor,r)
            checks.append(dict(index=i,row_id=r['id'],**meta))
        except (ValueError,RuntimeError) as exc:failures.append(dict(index=i,error=str(exc)[:500]))
    root.mkdir(parents=True)
    (root/'records.jsonl').write_text(''.join(json.dumps(r,allow_nan=False)+'\n' for r in rows))
    write_json(root/'checks.json',checks)
    result=dict(schema='power-grid-env.focused-native-release/v1',parent_admission=str(Path(parent).resolve()),
                parent_manifest_sha256=parent_anchor,target_sha256=target_sha,records=len(rows),
                skills=dict(Counter(r['skill'] for r in rows)),base_networks=len({r['base_project_id'] for r in rows}),
                passed=not failures,failures=failures,checked_targets=len(checks),maximum_prompt_tokens=max(c['prompt_tokens'] for c in checks),
                source_sha256=sources(),optimizer_run=False,weights_loaded=False,
                scope='TRAIN-only focused fields and coarse analytic comparisons derived from admitted replayed primitives; no new heldout or policy quality.')
    write_json(root/'summary.json',result);seal(root)
    return result


class FocusedDataset:
    def __init__(self,root,anchor,target_sha):
        root=Path(root)
        if verify(root)!=anchor:raise ValueError('focused release manifest differs')
        report=read_json(root/'summary.json')
        if (report.get('schema')!='power-grid-env.focused-native-release/v1' or report.get('passed') is not True or
                report.get('failures') or report.get('target_sha256')!=target_sha or report.get('source_sha256')!=sources()):
            raise ValueError('passing matching focused release required')
        self.parent=PrimitiveDataset(report['parent_admission'],report['parent_manifest_sha256'],target_sha)
        self.rows=derive(self.parent.rows)
        stored=[json.loads(line) for line in (root/'records.jsonl').read_text().splitlines()]
        if sha(self.rows)!=sha(stored) or report['records']!=len(self.rows):raise ValueError('focused labels do not reconstruct')
        checks=read_json(root/'checks.json')
        if (len(checks)!=len(self.rows) or report['checked_targets']!=len(self.rows) or
                [(c['index'],c['row_id']) for c in checks]!=[(i,r['id']) for i,r in enumerate(self.rows)]):
            raise ValueError('complete native inventory required')
        self.plan=report

    def example(self,index):
        if type(index) is not int or not 0<=index<len(self.rows):raise ValueError('bounded focused train index required')
        if self.rows[index]['split']!='train':raise ValueError('heldout focused row forbidden')
        return copy.deepcopy(self.rows[index])

    def batch(self,processor,index):return pack(processor,self.example(index))

    def selection(self):
        original,nuisance=self.parent.pilot_indices()
        selected=[]
        requests=[(original[3],'api_field','fresh_after'),(original[3],'api_field','simulations_spent'),
                  (original[5],'api_field','fresh_after'),(original[8],'comparison_field','relation'),
                  (original[8],'comparison_field','violation'),(original[9],'comparison_field','violation')]
        requests += [(i,'coarse_threshold',None) for i in original[6:]]
        for parent_index,kind,field in requests:
            pid=self.parent.rows[parent_index]['id']
            selected.append(next(i for i,r in enumerate(self.rows) if r['source_row_id']==pid and r['skill']==kind and r['field']==field))
        transfer=[]
        for index in selected:
            r=self.rows[index]
            transfer.append(next(i for i,v in enumerate(self.rows) if v['pair_group']==r['pair_group'] and v['member']==r['member'] and v['nuisance_variant']=='reordered_keys'))
        if len(set(selected+transfer))!=20:raise ValueError('ten focused originals and ten distinct key-order probes required')
        return selected,transfer,original,nuisance


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('parent-admission','manifest-sha256','target','target-sha256','out'):p.add_argument('--'+key,required=True)
    a=p.parse_args()
    print(json.dumps(qualify(a.parent_admission,a.manifest_sha256,a.target,a.target_sha256,a.out)))
