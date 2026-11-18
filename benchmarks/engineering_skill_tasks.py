"""Train-only isolated skills, with public observations and shuffled action menus.

This is a new interface. Tiny memorization scores are not closed-loop engineering
success or heldout generalization. All derived variants retain their base family.
"""
import copy
import hashlib
import json
import random
from pathlib import Path
from benchmarks.engineering_action_menu import candidates
from benchmarks.engineering_grounding_runtime import load_records, messages_for
from benchmarks.engineering_grounding import digest
from benchmarks.engineering_rollout import strict_json
from power_grid_env.study_artifacts import read_json, verify

SCHEMAS = dict(card='power-grid-env.card-curriculum/v1',
               choice='power-grid-env.action-choice-data/v1',
               catalog='power-grid-env.catalog-intervention-corpus/v1',
               visual='power-grid-env.visual-grounding-data/v1')


def identity(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def shuffled_menu(card, seed):
    menu = copy.deepcopy(candidates(card))
    random.Random(seed).shuffle(menu)
    for i, row in enumerate(menu):
        row['choice'] = 'A' + str(i)
    return menu


def card_messages(row, presentation_seed):
    card = copy.deepcopy(row['card'])
    card.pop('tools')
    skill = row['skill']
    if skill == 'choice':
        menu = shuffled_menu(row['card'], presentation_seed)
        answer = dict(choice=next(m['choice'] for m in menu if m['action'] == row['action']))
        card['choices'] = [[m['choice'], m['action']['name'], m['action']['arguments']] for m in menu]
        instruction = ('ACTION-CHOICE/V2. Choose one operation from the public choices. '
                       'Return only JSON {"choice":"A..."}. Choice order is arbitrary. '
                       'Preserve all constraints and call budgets. A completed calculation '
                       'does not by itself make a solution feasible. Diagnose using observed '
                       'evidence; construction and repair require a feasible fresh study.')
    elif skill == 'binding':
        card['given_tool'] = row['given_tool']
        card['directive'] = row['directive']
        card['argument_schema'] = next(t['function']['parameters'] for t in row['card']['tools']
                                       if t['function']['name'] == row['given_tool'])
        instruction = ('ARGUMENT-BINDING/V2. The requested operation is already given. '
                       'Return only its JSON arguments using the public aliases and schema. '
                       'Do not choose a different operation.')
        answer = row['answer']
    elif skill == 'readiness':
        field = 'study_ready' if card['kind'] == 'diagnose' else 'solution_ready'
        instruction = ('READINESS/V2. Return only JSON with boolean field ' + field + '. '
                       'A diagnosis study is ready only when fresh and converged. '
                       'A construction/repair solution additionally needs every required '
                       'connection/load and physical constraint satisfied. Respect edit limits.')
        answer = row['answer']
    else:
        raise ValueError('unknown public card skill')
    return [dict(role='system', content=instruction),
            dict(role='user', content=json.dumps(card, ensure_ascii=False, separators=(',', ':')))], answer


def score(raw, expected):
    try:
        actual = strict_json(raw)
    except (ValueError, TypeError):
        return dict(format_valid=False, exact=False)
    # Canonical JSON equality distinguishes true/1, unlike Python object equality.
    return dict(format_valid=isinstance(actual, dict) and set(actual) == set(expected),
                exact=digest(actual) == digest(expected))


class SkillDataset:
    def __init__(self, sources):
        self.sources = copy.deepcopy(sources)
        summaries = {}
        for role, schema in SCHEMAS.items():
            item = sources[role]
            if verify(item['path']) != item['manifest_sha256']:
                raise ValueError('owner skill source differs: ' + role)
            summaries[role] = read_json(Path(item['path']) / 'summary.json')
            if summaries[role].get('schema') != schema:
                raise ValueError('skill source role differs: ' + role)
        if summaries['choice']['card_manifest_sha256'] != sources['card']['manifest_sha256'] or summaries['choice']['catalog_manifest_sha256'] != sources['catalog']['manifest_sha256']:
            raise ValueError('choice parent manifests differ')
        if len({summaries[k]['train_manifest_sha256'] for k in ('card', 'visual', 'catalog')}) != 1:
            raise ValueError('skills do not share a train lineage')
        self.rows = []
        self.duplicate_rows = 0
        self._row_ids = set()
        for raw in self._jsonl('card'):
            if raw['task'] not in ('bind_arguments', 'readiness'):
                continue
            r = dict(raw, skill='binding' if raw['task'] == 'bind_arguments' else 'readiness')
            self._append(r)
        for raw in self._jsonl('choice'):
            expected_menu = candidates(raw['card'])
            if raw['profile'] != 'action-choice/v1' or raw['candidates'] != expected_menu:
                raise ValueError('public action menu differs from schema')
            action = next((m['action'] for m in expected_menu if m['choice'] == raw['answer']['choice']), None)
            if action is None:
                raise ValueError('unknown supervised action choice')
            self._append(dict(raw, skill='choice', action=action))
        visual = sources['visual']
        for raw in load_records(visual['path'], visual['manifest_sha256']):
            for mode in ('image', 'none'):
                self._append(dict(raw, skill='perception', mode=mode,
                                  answer=raw['answer'] if mode == 'image' else {k: None for k in raw['answer']}))
        if not self.rows:
            raise ValueError('nonempty skills required')
        if len({r['id'] for r in self.rows}) != len(self.rows):
            raise ValueError('duplicate skill row identity')

    def _jsonl(self, role):
        return [json.loads(s) for s in (Path(self.sources[role]['path']) / 'records.jsonl').read_text().splitlines()]

    def _append(self, row):
        if row['split'] != 'train':
            raise ValueError('only train skills are permitted')
        public_identity = {k: v for k, v in row.items() if not k.startswith('_')}
        row['id'] = digest(public_identity)
        if row['id'] in self._row_ids:
            self.duplicate_rows += 1
            return
        self._row_ids.add(row['id'])
        self.rows.append(row)

    def messages(self, index, presentation_seed=42):
        row = self.rows[index]
        if row['skill'] != 'perception':
            return card_messages(row, presentation_seed)
        path = Path(self.sources['visual']['path']) / row['image']
        return messages_for(row, path, row['mode']), row['answer']

    def batch(self, processor, index, presentation_seed=42):
        import torch
        messages, answer = self.messages(index, presentation_seed)
        settings = dict(tokenize=True, return_dict=True, return_tensors='pt', enable_thinking=False)
        prefix = processor.apply_chat_template(messages, add_generation_prompt=True, **settings)
        full = processor.apply_chat_template(messages + [dict(role='assistant', content=json.dumps(answer, separators=(',', ':')))], add_generation_prompt=False, **settings)
        n, length = prefix['input_ids'].shape[1], full['input_ids'].shape[1]
        if not n < length <= 8192 or length - n > 128 or not torch.equal(prefix['input_ids'], full['input_ids'][:, :n]):
            raise ValueError('skill target prefix/context differs')
        for k in ('pixel_values', 'image_grid_thw'):
            if k in prefix and (k not in full or not torch.equal(prefix[k], full[k])):
                raise ValueError('skill image tensors changed while appending answer')
        full['labels'] = full['input_ids'].clone()
        full['labels'][:, :n] = -100
        raw = processor.tokenizer.decode(full['input_ids'][full['labels'] != -100], skip_special_tokens=True)
        if not score(raw, answer)['exact']:
            raise ValueError('actual skill tokens lost target semantics')
        pixels = 'pixel_values' in full
        if pixels != (self.rows[index]['skill'] == 'perception' and self.rows[index]['mode'] == 'image'):
            raise ValueError('unexpected/missing image tensors')
        return full, dict(skill=self.rows[index]['skill'], row_id=self.rows[index]['id'],
                          prompt_tokens=n, label_tokens=length - n, pixels_present=pixels)

    def pilot_indices(self):
        """Eight declared TRAIN exemplars, selected from labels, never outcomes."""
        rows = self.rows
        binding = []
        for tool in ('add_line', 'set_line_catalog'):
            binding.append(next(i for i, r in enumerate(rows) if r['skill'] == 'binding' and r['given_tool'] == tool))
        ready = [next(i for i, r in enumerate(rows) if r['skill'] == 'readiness' and r['answer'].get('solution_ready') is v and r['card']['has_fresh_measurements']) for v in (False, True)]
        choices = [i for i, r in enumerate(rows) if r['skill'] == 'choice' and r['task_id'].startswith('catalog-') and r['action']['name'] == 'set_line_catalog']
        a = choices[0]
        b = next(i for i in choices if rows[i]['base_project_id'] == rows[a]['base_project_id'] and rows[i]['action']['arguments']['catalog_id'] != rows[a]['action']['arguments']['catalog_id'])
        visual = [next(i for i, r in enumerate(rows) if r['skill'] == 'perception' and r['kind'] == 'line_rating' and r['variant'] == 'factual' and r['view'] == 'equipment_zoom' and r['mode'] == mode) for mode in ('image', 'none')]
        return binding + ready + [a, b] + visual
