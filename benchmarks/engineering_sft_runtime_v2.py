"""Exact-profile staged rollout. No repair, teacher fallback or action filtering."""
import copy
from jsonschema import Draft202012Validator

from benchmarks.engineering_sft_contract_v2 import ExactEpisode, feedback, request
from benchmarks.engineering_staged_agent import bounded_json


def decide(card, policy, previous=None):
    queries, completions = [], []
    try:
        query = dict(stage='operation', messages=request(card, 'operation', previous), maximum_completion_tokens=128)
        queries.append(query)
        raw = policy(copy.deepcopy(query))
        completions.append(raw)
        value = bounded_json(raw)
        if set(value) != {'operation'} or not isinstance(value['operation'], str):
            raise ValueError('one allowed operation required')
        name = value['operation']
        spec = next((x for x in card['tools'] if x['function']['name'] == name), None)
        if spec is None:
            raise ValueError('operation not allowed')
        schema = spec['function']['parameters']
        args = {}
        if schema.get('required') or not Draft202012Validator(schema).is_valid(args):
            query = dict(stage='arguments', messages=request(card, 'arguments', previous, name), maximum_completion_tokens=128)
            queries.append(query)
            raw = policy(copy.deepcopy(query))
            completions.append(raw)
            args = bounded_json(raw)
        if not Draft202012Validator(schema).is_valid(args):
            raise ValueError('arguments violate schema')
        return dict(action=dict(name=name, arguments=args), requests=queries, completions=completions, error=None)
    except (ValueError, TypeError, RuntimeError) as exc:
        return dict(action=None, requests=queries, completions=completions,
                    error=type(exc).__name__ + ':' + str(exc)[:500])


def run(case, policy):
    episode = ExactEpisode(case)
    events, previous = [], None
    while not episode.engine.state['finished'] and episode.engine.state['calls'] < 24:
        card, aliases = episode.observe()
        decision = decide(card, policy, previous)
        action = decision['action']
        if action is None:
            response = episode.engine.apply('__invalid_action__', {})
            previous = dict(ok=False, error=decision['error'], last_executed_operation=None,
                            result=None, revision=episode.engine.state['revision'])
        else:
            response, _ = episode.apply(action)
            previous = feedback(response, action, aliases)
        events.append(dict(card=card, decision=decision, response=response))
    if not episode.engine.state['finished']:
        episode.engine.apply('__invalid_action__', {})
    return dict(profile='exact-editor-sft/v2', task_id=case['id'], split=case['split'],
                family_sha256=case['family_sha256'], base_project_id=case['base_project_id'],
                events=events, terminal=copy.deepcopy(episode.engine.state['terminal']),
                success=episode.engine.state['terminal'].get('success') is True,
                scope='Full owner-judged episode; scripted controls are not learned agents.')


class NativePolicy:
    def __init__(self, processor, model):
        self.processor, self.model = processor, model

    def __call__(self, query):
        import torch
        inputs = self.processor.apply_chat_template(query['messages'], tokenize=True,
            add_generation_prompt=True, return_dict=True, return_tensors='pt', enable_thinking=False)
        length = inputs['input_ids'].shape[1]
        if length + query['maximum_completion_tokens'] > 8192:
            raise ValueError('exact-profile native context budget exceeded')
        self.model.eval()
        with torch.inference_mode():
            ids = self.model.generate(**inputs, do_sample=False, num_beams=1, use_cache=True,
                max_new_tokens=query['maximum_completion_tokens'], logits_to_keep=1,
                pad_token_id=self.processor.tokenizer.pad_token_id,
                eos_token_id=self.processor.tokenizer.eos_token_id)
        return self.processor.tokenizer.decode(ids[0, length:], skip_special_tokens=True)
