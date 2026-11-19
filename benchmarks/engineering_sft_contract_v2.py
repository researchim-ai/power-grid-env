"""Exact public observation and staged requests for the next SFT profile.

Text only. Frozen v1 scenes and qualified training receipts stay unchanged.
The owner judge is not changed and its acceptance rules are not relaxed.
"""
import copy
import json

from benchmarks.engineering_replacement_challenges import ReplacementEpisode


TOOL_EFFECTS = {
    'validate': 'Compile the current draft; does not calculate feasibility or refresh measurements. Repeating it makes no design progress.',
    'simulate': 'Calculate the current revision; consumes one simulation. May run before validate. A converged result can violate limits.',
    'submit': 'Finish and judge the design. Requires fresh converged measurements, matching required equipment and all declared constraints. Validate is not a judge prerequisite.',
    'inspect_project': 'Read the actual current project; does not change revision or refresh solved measurements.',
    'inspect_equipment': 'Read one installed item; does not change revision or refresh solved measurements.',
    'set_line_catalog': 'Replace an installed line passport. Increments revision, invalidates measurements and counts equipment changes.',
    'remove_line': 'Remove an installed line. Increments revision, invalidates measurements and counts changed routes.',
    'add_line': 'Install a required route using a catalogue passport. Increments revision and invalidates measurements.',
    'add_load': 'Install a required load. Increments revision and invalidates measurements.',
}


def alias_record(record, aliases):
    """Translate only declared equipment references, never arbitrary text/numbers."""
    value = copy.deepcopy(record)
    mapping = {k: v for group in aliases.values() for k, v in group.items()}
    for key in ('id', 'node_id', 'from_node', 'to_node', 'source_node'):
        if key in value:
            value[key] = mapping.get(value[key], value[key])
    return value


class ExactEpisode(ReplacementEpisode):
    def observe(self):
        card, aliases = super().observe()
        project = self.engine.state['project']
        # These values are already public through inspect_project/public_task.
        req = self.engine.public_task()['requirements']
        scene = card['scene']
        scene.update(schema='power-grid-env.exact-public-scene/v2',
                     nominal_ll_v=project['nominal_ll_v'],
                     source_parameters={k: project[k] for k in ('source_r_ohm', 'source_x_ohm')},
                     scope='Exact public passports and requirements; solved values appear only in fresh study.')
        scene['lines'] = sorted((alias_record(x, aliases) for x in project['lines']), key=lambda x: x['id'])
        scene['routes'] = [dict(alias_record(r, aliases), installed_line=old['installed_line'])
                           for r, old in zip(sorted(req['routes'], key=lambda x: x['id']), scene['routes'])]
        actual = {x['id']: x for x in project['loads']}
        scene['loads'] = [dict(id=aliases['loads'][r['id']],
                               required=alias_record(r, aliases),
                               installed=alias_record(actual[r['id']], aliases) if r['id'] in actual else None)
                          for r in sorted(req['loads'], key=lambda x: x['id'])]
        required_ids = {r['id'] for r in req['loads']}
        scene['extra_installed_loads'] = [alias_record(x, aliases) for x in project['loads'] if x['id'] not in required_ids]
        scene['catalog'] = [dict(id=name, **passport) for name, passport in sorted(req['catalog'].items())]
        card.update(profile='exact-editor-sft/v2',
                    units=dict(nominal_ll_v='V line-to-line', length_m='m', r_ohm_km='ohm/km',
                               x_ohm_km='ohm/km', rating_a='A', p_kw='kW', q_kvar='kvar',
                               voltage_pu_min='pu', loading_pct='percent'),
                    tool_effects={t['function']['name']: TOOL_EFFECTS[t['function']['name']] for t in card['tools']})
        return card, aliases


def structure_assessment(card):
    """Public-only equivalent of the bounded route/load passport judge."""
    scene = card['scene']
    loads_ok = not scene['extra_installed_loads'] and all(x['installed'] is not None and
                   all(x['installed'].get(k) == v for k, v in x['required'].items()) for x in scene['loads'])
    lines_ok = len(scene['lines']) == len(scene['routes'])
    by_id = {x['id']: x for x in scene['lines']}
    for route in scene['routes']:
        line = by_id.get(route['installed_line'])
        lines_ok = lines_ok and line is not None
        if line is None:
            continue
        lines_ok = lines_ok and line['length_m'] == route['length_m'] and any(
            all(line.get(k) == v for k, v in c.items() if k != 'id') for c in scene['catalog'])
    return dict(required_loads_match=loads_ok, required_routes_and_passports_match=bool(lines_ok),
                structure_matches=bool(loads_ok and lines_ok))


def request(card, task, feedback=None, operation=None):
    """No teacher directive or desired argument value enters a request."""
    instructions = {
        'operation': 'Choose the next useful operation for this public goal. Return {"operation":"allowed name"}. Several successful plans may exist.',
        'arguments': 'Select arguments for given_operation from public equipment, requirements and measurements. Return only the arguments object.',
        'state': 'Return revision, validated, has_fresh_measurements, calls_remaining and simulations_remaining as JSON with their exact public values.',
        'structure': 'Compare installed equipment with required loads, routes and exact catalogue passports. Return required_loads_match, required_routes_and_passports_match and structure_matches booleans. This is not electrical feasibility.',
        'measurement': 'Return study_ready, measurement_feasible, overloaded_branch_count, undervoltage_node_count, deenergized_node_count, equipment_budget_ok. Without a fresh converged study the four measurement fields are null. Use the declared limits strictly; budgets are independent.',
    }
    if task not in instructions:
        raise ValueError('unknown SFT task')
    public = copy.deepcopy(card)
    if feedback is not None:
        public['previous_tool_response'] = copy.deepcopy(feedback)
    if task == 'arguments':
        spec = next((x for x in card['tools'] if x['function']['name'] == operation), None)
        if spec is None:
            raise ValueError('given operation is not allowed')
        public['given_operation'] = operation
        public['argument_schema'] = spec['function']['parameters']
    return [dict(role='system', content='ENGINEERING-SFT/V2. ' + instructions[task] + ' No Markdown.'),
            dict(role='user', content=json.dumps(public, separators=(',', ':'), allow_nan=False))]


def feedback(reply, action, aliases):
    """Keep public tool results, including inspection details, without hidden task."""
    result = copy.deepcopy(reply.get('result'))
    if action['name'] == 'inspect_equipment' and isinstance(result, dict):
        result = alias_record(result, aliases)
    elif action['name'] == 'inspect_project' and isinstance(result, dict):
        for key in ('nodes', 'lines', 'loads'):
            result[key] = [alias_record(x, aliases) for x in result[key]]
        result['source_node'] = aliases['nodes'][result['source_node']]
        for key in ('fault',):
            if isinstance(result.get(key), dict):
                result[key] = alias_record(result[key], aliases)
    elif action['name'] == 'simulate' and isinstance(result, dict):
        # Declared balanced-profile projection, not silent tokenizer truncation.
        # All feasibility quantities and line currents survive; phase-duplicate
        # detail remains in the trace's original public tool response.
        result = dict(view='balanced-study-summary/v2', converged=result.get('converged'),
                      nodes=[dict(id=aliases['nodes'][x['id']], energized=x['energized'],
                                  voltage_pu_min=min(p['voltage_pu'] for p in x['phases'].values()))
                             for x in result['nodes']],
                      branches=[dict(id=aliases['lines'][x['id']], loading_pct=x['loading_pct'],
                                     current_a=x['from_terminal']['current_a'] if x['from_terminal'] else None)
                                for x in result['branches']])
    return dict(ok=reply.get('ok') is True, error=reply.get('error'),
                last_executed_operation=action['name'], result=result,
                revision=reply.get('observation', {}).get('revision'))
