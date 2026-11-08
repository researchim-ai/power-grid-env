"""LLM SFT examples and group-relative physical rewards; no model execution.

The simulator/evaluator owns private state. Exported prompts and demonstrations
contain only approved public inputs, actions, and resulting observations.
"""
from pathlib import Path
from typing import Annotated,Literal
import copy
import json
import math

import numpy as np
from pydantic import BaseModel,ConfigDict,Field

from power_grid_env.llm_tasks import LLMTask,LLMGenerationPlan,generate_task,compact_public_network
from power_grid_env.simulation import Engine,agent_model
from power_grid_env.heuristic import choose_action
from power_grid_env.evaluator import evaluate_trace
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,digest,read_json,seal,verify


class HoldAction(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    kind:Literal['hold']


class SwitchAction(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    kind:Literal['switch']
    equipment_id:str=Field(min_length=1,max_length=128)
    command:Literal['open','close']


Action=Annotated[HoldAction|SwitchAction,Field(discriminator='kind')]


class LLMPlan(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    actions:list[Action]=Field(max_length=8)


class ToolRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    name:Literal['observe','simulate_plan','act','finish']
    arguments:dict


class AgentContractError(ValueError):pass


SYSTEM='You control an offline electric-grid simulation. Return only a JSON object matching the response schema. Use only the public network, observations and declared limits. Plans run one action every 10 s; unused steps are hold. Do not claim rewards or fabricate tool observations. Respect interlocks and line ratings. This profile is quasi-static and has no undisclosed future events.'
STEP_SYSTEM='You control an offline electric-grid simulation. Return exactly one JSON action: {"kind":"hold"} or {"kind":"switch","equipment_id":"...","command":"open"|"close"}. Use the provided network and observation; respect interlocks and ratings. The response will be physically executed before the next observation.'


def strict_json(text,*,max_bytes=32768):
    if not isinstance(text,str) or len(text.encode('utf-8'))>max_bytes:raise ValueError('JSON text exceeds its byte budget')
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError('duplicate JSON key')
            result[k]=v
        return result
    def reject(value):raise ValueError('nonfinite JSON constant')
    def number(value):
        result=float(value)
        if not math.isfinite(result):raise ValueError('nonfinite JSON number')
        return result
    return json.loads(text,object_pairs_hook=pairs,parse_constant=reject,parse_float=number)


def decode_plan(completion):
    return LLMPlan.model_validate(strict_json(completion))


def initial_context(task,engine=None):
    task=LLMTask.model_validate(task)
    engine=engine or Engine(task.network,task.scenario)
    observation=engine.reset(seed=task.scenario.seed)
    public=agent_model(task.network,task.scenario)
    payload=dict(task_id=task.id,network=compact_public_network(public),observation=observation,
                 objective='Maximize safely served energy over 80 s, restore all loads when feasible, minimize actual switch operations.',
                 horizon_s=80,control_interval_s=10,what_if_budget=2,response_schema=LLMPlan.model_json_schema())
    messages=[dict(role='system',content=SYSTEM),dict(role='user',content=encoded(payload).decode())]
    return engine,public,messages,payload


def _invalid(reason):
    return dict(status='invalid_completion',reward=-1.,feasible=False,restored=False,reason=reason)


def _grade(task,engine):
    evaluation=evaluate_trace(engine.trace,task.scenario.horizon_s,'completed')
    if evaluation['solve_failure_times_s'] or evaluation['incomplete'] or not evaluation['energy_log_matches_report']:
        return dict(status='simulation_failure',reward=None,feasible=False,restored=False,reason='unqualified_or_incomplete_physical_result',evaluation=evaluation)
    # Outages are the restoration objective, not a prohibition against isolation.
    # Energized out-of-band voltages, overloads and rejected commands are hard gates.
    hard=[v for row in engine.trace for v in row.get('violations',[]) if v['kind']!='outage']
    unsafe=bool(hard or evaluation['interlock_violations'] or evaluation['rejected_actions'])
    demand=sum(sum(load.p_w.values()) for load in task.network.loads)*80/3600
    ens=evaluation['energy_not_served_recomputed_wh']
    fraction=min(1.,max(0.,1-ens/demand))
    value=-1. if unsafe else max(0.,min(1.,.75*fraction+.25*float(evaluation['restored'])-.001*evaluation['operations']))
    return dict(status='unsafe' if unsafe else 'ok',reward=value,feasible=not unsafe,restored=bool(evaluation['restored']),
                served_energy_fraction=fraction,hard_violation_count=len(hard),evaluation=evaluation,
                controller_self_report_used=False)


class LLMEpisode:
    """A trusted-host API. Text/JSON tools cannot expose its Python engine object.

    This is a data boundary, not an OS sandbox for arbitrary Python agents.
    """
    def __init__(self,task):
        self._task=LLMTask.model_validate(task)
        self._engine,self._public,self.prompt,self.context=initial_context(self._task)
        self._steps=0;self._calls=0;self._what_if=0;self._finished=False
        self.public_steps=[]

    def call(self,name,arguments):
        if self._finished:raise AgentContractError('episode already finished')
        self._calls+=1
        if self._calls>24:raise AgentContractError('episode tool-call budget exceeded')
        if not isinstance(arguments,dict):raise AgentContractError('tool arguments must be objects')
        if name=='observe':
            if arguments:raise AgentContractError('observe takes no arguments')
            return copy.deepcopy(self._engine.observation())
        if name=='simulate_plan':
            if set(arguments)!={'actions'}:raise AgentContractError('simulate_plan takes actions only')
            try:plan=LLMPlan.model_validate(arguments)
            except ValueError:raise AgentContractError('invalid what-if action schema') from None
            if any(a.kind=='switch' and a.equipment_id not in {sw.id for sw in self._public.switches} for a in plan.actions):raise AgentContractError('unknown public switch')
            if self._what_if>=2:raise AgentContractError('what-if budget exceeded')
            self._what_if+=1
            return self._engine.what_if([a.model_dump() for a in plan.actions],model=self._public)
        if name!='act':raise AgentContractError('unknown tool')
        return self._act(arguments)

    def _act(self,arguments):
        try:plan=LLMPlan.model_validate(dict(actions=[arguments]))
        except ValueError:raise AgentContractError('invalid action schema') from None
        if self._engine.terminated or self._engine.truncated:raise AgentContractError('action submitted after terminal observation')
        if self._steps>=8:raise AgentContractError('physical step budget exceeded')
        action=plan.actions[0].model_dump()
        if action['kind']=='switch' and action['equipment_id'] not in {sw.id for sw in self._public.switches}:
            raise AgentContractError('unknown public switch')
        before=copy.deepcopy(self._engine.observation())
        record=self._engine.step_control(action,origin='llm')
        self._steps+=1
        response=dict(observation=copy.deepcopy(record['observation']),accepted=bool(record['acceptance'].get('accepted')),
                      acceptance_reason=record['acceptance'].get('reason'),solve_status=record['solve_status'],
                      terminated=bool(self._engine.terminated),truncated=bool(self._engine.truncated))
        self.public_steps.append(dict(observation=before,action=action,response=response))
        return response

    def finish(self):
        if not self._finished:
            while not self._engine.terminated and not self._engine.truncated and self._steps<8:
                self._act(dict(kind='hold'))
            self._finished=True
        return _grade(self._task,self._engine)


def score_plan(task,completion):
    task=LLMTask.model_validate(task)
    try:plan=decode_plan(completion)
    except (ValueError,TypeError,RecursionError):return _invalid('invalid_plan_json')
    if any(a.kind=='switch' and a.equipment_id not in {sw.id for sw in task.network.switches} for a in plan.actions):
        return _invalid('unknown_public_switch')
    try:
        episode=LLMEpisode(task)
        for i,action in enumerate(plan.actions):
            if episode._engine.terminated or episode._engine.truncated:
                if any(a.kind!='hold' for a in plan.actions[i:]):return _invalid('non_hold_suffix_after_termination')
                break
            episode.call('act',action.model_dump())
        return episode.finish()
    except (ValueError,ArithmeticError,RuntimeError,OSError) as exc:
        # Never return raw exception text/private task fields to a model.
        return dict(status='simulation_failure',reward=None,feasible=False,restored=False,reason=type(exc).__name__)


def score_group(task,completions):
    if not isinstance(completions,list) or not 2<=len(completions)<=32:raise ValueError('one prompt group requires two to 32 completions')
    results=[score_plan(task,text) for text in completions]
    if any(row['reward'] is None for row in results):
        return dict(status='discard_simulation_failure',results=results,rewards=None,advantages=None,informative=False)
    values=np.array([row['reward'] for row in results],dtype=float)
    mean=float(values.mean());std=float(values.std(ddof=0))
    advantages=np.zeros(len(values)) if std<=1e-8 else (values-mean)/std
    return dict(status='ok',results=results,rewards=values.tolist(),advantages=advantages.tolist(),mean=mean,
                population_std=std,informative=std>1e-8,normalization='population_std; diagnostic only, trainer receives raw rewards')


def rollout_tools(task,generate):
    """Interactive rollout: generate(messages) returns one strict JSON tool request.

    The callback sees a deep-copied public conversation only. This runner can
    supply trajectories to an external multi-turn policy optimizer.
    """
    task=LLMTask.model_validate(task);messages=[]
    try:
        episode=LLMEpisode(task);context=copy.deepcopy(episode.context)
        context['response_schema']=ToolRequest.model_json_schema();context['tool_call_budget']=24
        messages=[dict(role='system',content='Return one JSON tool request with name and arguments. Allowed: observe {}, simulate_plan {actions:[...]}, act {kind,...}, finish {}. Tool results are authoritative; do not invent them. Maximum 24 calls and two what-if calls. Finish is physically evaluated through the full task horizon.'),dict(role='user',content=encoded(context).decode())]
        for _ in range(24):
            try:
                raw=generate(copy.deepcopy(messages));request=ToolRequest.model_validate(strict_json(raw))
            except (ValueError,TypeError,RecursionError):return dict(score=_invalid('invalid_tool_request'),messages=messages)
            messages.append(dict(role='assistant',content=raw))
            if request.name=='finish':
                if request.arguments:return dict(score=_invalid('finish_takes_no_arguments'),messages=messages)
                return dict(score=episode.finish(),messages=messages)
            try:response=episode.call(request.name,request.arguments)
            except AgentContractError:return dict(score=_invalid('tool_contract_violation'),messages=messages)
            messages.append(dict(role='user',content=encoded(dict(tool_result=request.name,result=response)).decode()))
        return dict(score=_invalid('tool_call_budget_exceeded'),messages=messages)
    except (ValueError,ArithmeticError,RuntimeError,OSError) as exc:
        return dict(score=dict(status='simulation_failure',reward=None,feasible=False,restored=False,reason=type(exc).__name__),messages=messages)


def teacher_episode(task,policy):
    episode=LLMEpisode(task);actions=[]
    while not episode._engine.terminated and not episode._engine.truncated and len(actions)<8:
        observation=episode.call('observe',{})
        proposed=dict(kind='hold') if policy=='hold' else choose_action(observation,episode._public.model_dump(mode='json'),respect_rating=policy=='heuristic')
        action={k:v for k,v in proposed.items() if k in {'kind','equipment_id','command'}}
        episode.call('act',action);actions.append(action)
    result=episode.finish()
    return dict(plan=dict(actions=actions),score=result,public_steps=episode.public_steps,private_trace=episode._engine.trace)


def sft_records(task,episode,prompt):
    whole=dict(task_id=task.id,topology_group=task.topology_group,prompt=prompt,
               completion=[dict(role='assistant',content=encoded(episode['plan']).decode())])
    context=copy.deepcopy(prompt);context[0]['content']=STEP_SYSTEM
    payload=json.loads(context[1]['content'])
    from pydantic import TypeAdapter
    payload['response_schema']=TypeAdapter(Action).json_schema()
    context[1]['content']=encoded(payload).decode()
    rows=[]
    for index,step in enumerate(episode['public_steps'][:len(episode['plan']['actions'])]):
        completion=dict(role='assistant',content=encoded(step['action']).decode())
        rows.append(dict(task_id=task.id,step=index,topology_group=task.topology_group,prompt=copy.deepcopy(context),completion=[completion]))
        context.append(completion)
        context.append(dict(role='user',content=encoded(dict(tool_result='act',**step['response'])).decode()))
    return whole,rows


def tool_sft_records(task,episode,prompt):
    context=copy.deepcopy(prompt)
    context[0]['content']='Return one JSON tool request: act with a single action in arguments, or finish with {}. Wait for actual tool results before choosing another action.'
    payload=json.loads(context[1]['content']);payload['response_schema']=ToolRequest.model_json_schema()
    context[1]['content']=encoded(payload).decode();rows=[]
    for index,step in enumerate(episode['public_steps'][:len(episode['plan']['actions'])]):
        completion=dict(role='assistant',content=encoded(dict(name='act',arguments=step['action'])).decode())
        rows.append(dict(task_id=task.id,step=index,topology_group=task.topology_group,prompt=copy.deepcopy(context),completion=[completion]))
        context.append(completion);context.append(dict(role='user',content=encoded(dict(tool_result='act',result=step['response'])).decode()))
    rows.append(dict(task_id=task.id,step=len(episode['plan']['actions']),topology_group=task.topology_group,prompt=context,completion=[dict(role='assistant',content=encoded(dict(name='finish',arguments={})).decode())]))
    return rows


def _write_lines(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=b''.join(encoded(row)+b'\n' for row in rows)
    if len(raw)>64000000:raise ValueError('JSONL shard byte budget exceeded')
    path.write_bytes(raw)


def _read_lines(path):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>64000000:raise ValueError('unsafe JSONL file')
    return [strict_json(line,max_bytes=2000000) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def generate_corpus(document,out):
    plan=LLMGenerationPlan.model_validate(document);out=Path(out)
    if out.exists():raise ValueError('new corpus directory required')
    code=package_fingerprint();out.mkdir(parents=True);(out/'private/tasks').mkdir(parents=True)
    (out/'generation.json').write_bytes(encoded(plan.model_dump(mode='json')))
    records={split:dict(prompts=[],plans=[],steps=[],tools=[]) for split in ('train','dev','test')};index=[];byte_budget=0
    for family in plan.families:
        for variant in range(plan.variants_per_family):
            task=generate_task(plan,family,variant)
            _,_,prompt,_=initial_context(task)
            candidates={name:teacher_episode(task,name) for name in ('heuristic','aggressive','hold')}
            if any(row['score']['reward'] is None for row in candidates.values()):raise ValueError('unqualified physical case; corpus generation aborted')
            feasible=[(name,row) for name,row in candidates.items() if row['score']['feasible']]
            if not feasible:raise ValueError('no verified feasible demonstration')
            name,selected=max(feasible,key=lambda row:row[1]['score']['reward'])
            # A supplied plan is independently replayed from reset, not accepted
            # solely because its teacher reported success.
            if score_plan(task,encoded(selected['plan']).decode())!=selected['score']:
                raise ValueError('demonstration replay differs')
            whole,steps=sft_records(task,selected,prompt)
            public=dict(task_id=task.id,topology_group=task.topology_group,prompt=prompt)
            tools=tool_sft_records(task,selected,prompt)
            byte_budget+=sum(len(encoded(row)) for row in [task.model_dump(mode='json'),public,whole,*steps,*tools,selected])
            if byte_budget>220000000:raise ValueError('corpus byte budget exceeded; use smaller shards')
            records[task.split]['prompts'].append(public);records[task.split]['plans'].append(whole);records[task.split]['steps'].extend(steps)
            records[task.split]['tools'].extend(tools)
            (out/'private/tasks'/(task.id+'.json')).write_bytes(encoded(task.model_dump(mode='json')))
            path=out/'private/episodes'/task.id;path.mkdir(parents=True)
            (path/'teacher.json').write_bytes(encoded(dict(policy=name,optimality_claimed=False,plan=selected['plan'],score=selected['score'],public_steps=selected['public_steps'],candidate_scores={k:v['score'] for k,v in candidates.items()})))
            (path/'physical-trace.json').write_bytes(encoded(selected['private_trace']))
            index.append(dict(task_id=task.id,split=task.split,topology_group=task.topology_group))
    for split,kinds in records.items():
        for kind,rows in kinds.items():_write_lines(out/'public'/split/(kind+'.jsonl'),rows)
    summary=dict(schema='power-grid-env.llm-corpus/v1',producer_sha256=code,tasks=index,
                 counts={split:{kind:len(rows) for kind,rows in kinds.items()} for split,kinds in records.items()},
                 training_scope='synthetic quasi-static restoration; known fault; constant loads; instantaneous switches',
                 teacher='best feasible of three public-observation policies; not a global optimum',
                 private_directory_is_not_training_data=True,model_training_performed=False)
    if package_fingerprint()!=code:raise ValueError('corpus producer changed')
    (out/'corpus.json').write_bytes(encoded(summary));seal(out)
    return summary


def verify_corpus(root,*,replay_demonstrations=True):
    root=Path(root);identity=verify(root);summary=read_json(root/'corpus.json')
    if summary.get('schema')!='power-grid-env.llm-corpus/v1' or summary.get('producer_sha256')!=package_fingerprint():
        raise ValueError('corpus schema/producer mismatch; requalification required')
    plan=LLMGenerationPlan.model_validate(read_json(root/'generation.json'))
    expected=[generate_task(plan,f,i) for f in plan.families for i in range(plan.variants_per_family)]
    index=[dict(task_id=t.id,split=t.split,topology_group=t.topology_group) for t in expected]
    if summary['tasks']!=index:raise ValueError('task index does not match deterministic generation')
    by_split={split:dict(prompts=[],plans=[],steps=[],tools=[]) for split in ('train','dev','test')}
    for task in expected:
        if read_json(root/'private/tasks'/(task.id+'.json'))!=task.model_dump(mode='json'):raise ValueError('private task differs from declared generation')
        teacher=read_json(root/'private/episodes'/task.id/'teacher.json')
        if replay_demonstrations and score_plan(task,encoded(teacher['plan']).decode())!=teacher['score']:
            raise ValueError('physical demonstration is inconsistent')
        if not teacher['score']['feasible'] or teacher.get('optimality_claimed') is not False:raise ValueError('invalid teacher qualification')
        _,_,prompt,_=initial_context(task)
        replayed=LLMEpisode(task)
        for action in teacher['plan']['actions']:
            replayed.call('act',action)
        replayed.finish()
        if replayed.public_steps!=teacher['public_steps']:raise ValueError('teacher transcript has fabricated public observations')
        if replayed.finish()!=teacher['score']:raise ValueError('teacher score differs from replay')
        if read_json(root/'private/episodes'/task.id/'physical-trace.json')!=replayed._engine.trace:
            raise ValueError('private physical trace differs from replay')
        candidates={name:teacher_episode(task,name) for name in ('heuristic','aggressive','hold')}
        feasible={name:row for name,row in candidates.items() if row['score']['feasible'] and row['score']['reward'] is not None}
        selected=max(feasible,key=lambda name:feasible[name]['score']['reward'])
        if (teacher.get('candidate_scores')!={name:row['score'] for name,row in candidates.items()}
                or teacher.get('policy')!=selected or teacher['plan']!=candidates[selected]['plan']):
            raise ValueError('teacher policy selection is inconsistent')
        whole,steps=sft_records(task,teacher,prompt)
        by_split[task.split]['prompts'].append(dict(task_id=task.id,topology_group=task.topology_group,prompt=prompt))
        by_split[task.split]['plans'].append(whole);by_split[task.split]['steps'].extend(steps)
        by_split[task.split]['tools'].extend(tool_sft_records(task,teacher,prompt))
    counts={split:{kind:len(rows) for kind,rows in kinds.items()} for split,kinds in by_split.items()}
    if summary['counts']!=counts:raise ValueError('corpus counts differ')
    for split,kinds in by_split.items():
        for kind,rows in kinds.items():
            if _read_lines(root/'public'/split/(kind+'.jsonl'))!=rows:raise ValueError('public prompt/SFT export differs or leaks private data')
    return dict(verified=True,manifest_sha256=identity,tasks=len(expected),counts=counts)


def export_public_dataset(root,out,*,split,kind):
    if split not in {'train','dev','test'} or kind not in {'prompts','plans','steps','tools'}:raise ValueError('known split and export kind required')
    root=Path(root);checked=verify_corpus(root);out=Path(out)
    if out.exists():raise ValueError('new public export directory required')
    out.mkdir(parents=True)
    rows=_read_lines(root/'public'/split/(kind+'.jsonl'))
    _write_lines(out/'data.jsonl',rows)
    (out/'dataset.json').write_bytes(encoded(dict(schema='power-grid-env.llm-public-export/v1',split=split,kind=kind,rows=len(rows),
        source_manifest_sha256=checked['manifest_sha256'],contains_private_tasks=False,format='conversational prompt-completion' if kind!='prompts' else 'conversational prompts')))
    seal(out);return dict(rows=len(rows),split=split,kind=kind,private_files_exported=False)


class CorpusVerifier:
    """Verified private cache kept outside the model's dataset columns."""
    def __init__(self,root,*,allowed_splits=('train',)):
        self.root=Path(root);self.identity=verify_corpus(self.root)['manifest_sha256']
        self.code=package_fingerprint();self._tasks={}
        if not set(allowed_splits)<= {'train','dev','test'} or not allowed_splits:raise ValueError('known nonempty split permissions required')
        summary=read_json(self.root/'corpus.json')
        for row in summary['tasks']:
            if row['split'] in allowed_splits:self._tasks[row['task_id']]=LLMTask.model_validate(read_json(self.root/'private/tasks'/(row['task_id']+'.json')))

    def score(self,task_id,completion):
        if package_fingerprint()!=self.code:raise ValueError('physical verifier producer changed')
        if task_id not in self._tasks:raise ValueError('unknown task or forbidden split')
        return score_plan(self._tasks[task_id],completion)

    def group(self,task_id,completions):
        if package_fingerprint()!=self.code:raise ValueError('physical verifier producer changed')
        if task_id not in self._tasks:raise ValueError('unknown task or forbidden split')
        return score_group(self._tasks[task_id],completions)

    def trl_reward(self,completions,task_id,prompts=None,**kwargs):
        if len(completions)!=len(task_id):raise ValueError('completion/task columns must align')
        rewards=[]
        for completion,identifier in zip(completions,task_id,strict=True):
            if isinstance(completion,list):
                if len(completion)!=1 or not isinstance(completion[0],dict) or completion[0].get('role')!='assistant' or not isinstance(completion[0].get('content'),str):
                    rewards.append(-1.);continue
                completion=completion[0]['content']
            result=self.score(identifier,completion)
            if result['reward'] is None:raise RuntimeError('physical solver failure; discard/replay rollout batch instead of training on a false reward')
            rewards.append(result['reward'])
        # GRPOTrainer computes its own group-relative advantages. Returning our
        # diagnostic advantages here would incorrectly normalize rewards twice.
        return rewards


def evaluate_predictions(root,predictions,*,split='test'):
    """Complete held-out plan evaluation, never a subset of successful tasks."""
    if split not in {'dev','test'}:raise ValueError('held-out dev or test split required')
    verifier=CorpusVerifier(root,allowed_splits=(split,))
    if not isinstance(predictions,list):raise ValueError('prediction rows required')
    indexed={}
    for row in predictions:
        if not isinstance(row,dict) or set(row)!={'task_id','completion'} or not isinstance(row['completion'],str):
            raise ValueError('each prediction requires task_id and completion only')
        identifier=row['task_id']
        if not isinstance(identifier,str) or identifier in indexed:raise ValueError('unique task IDs required')
        indexed[identifier]=row['completion']
    if set(indexed)!=set(verifier._tasks):raise ValueError('predictions must cover every task in exactly one held-out split')
    results=[dict(task_id=key,**verifier.score(key,indexed[key])) for key in sorted(indexed)]
    failures=sum(r['reward'] is None for r in results)
    qualified=not failures
    return dict(schema='power-grid-env.llm-evaluation/v1',split=split,tasks=len(results),
                source_manifest_sha256=verifier.identity,producer_sha256=verifier.code,
                status='ok' if qualified else 'unqualified_simulation_failure',simulation_failures=failures,
                mean_reward=float(np.mean([r['reward'] for r in results])) if qualified else None,
                feasible_fraction=sum(r['feasible'] for r in results)/len(results),
                restored_fraction=sum(r['restored'] for r in results)/len(results),results=results,
                trained_model_quality_claimed=False)
