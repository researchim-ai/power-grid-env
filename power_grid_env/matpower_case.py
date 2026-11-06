"""Data-only MATPOWER v2 matrix importer for the bounded balanced AC profile."""
from dataclasses import replace
from pathlib import Path
import math
import re

from power_grid_env.balanced_ac import validate_case
from power_grid_env.network_opf import Branch, Bus, GeneratorSpec, OpfCase


def _matrix(text, name):
    match = re.search(rf'mpc\.{name}\s*=\s*\[(.*?)\];', text, flags=re.S)
    if match is None:
        raise ValueError('missing MATPOWER matrix: '+name)
    rows = []
    for line in match.group(1).splitlines():
        line = line.split('%')[0].strip()
        for row in line.split(';'):
            if row.strip():
                try:
                    rows.append([float(v) for v in row.split()])
                except ValueError as exc:
                    raise ValueError('only literal numeric MATPOWER matrices are supported') from exc
    return rows


def _integer(value):
    if not value.is_integer():
        raise ValueError('MATPOWER integer field is fractional')
    return int(value)


def load_case(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 32_000_000:
        raise ValueError('unsafe or oversized MATPOWER source')
    text = path.read_text(encoding='utf-8')
    clean = '\n'.join(line.split('%')[0] for line in text.splitlines())
    assignments = re.findall(r'mpc\.(\w+)\s*(\([^=]*?\))?\s*=', clean)
    names = [name for name, _indexed in assignments]
    known = {'version', 'baseMVA', 'bus', 'gen', 'branch', 'gencost', 'areas', 'bus_name', 'gentype', 'genfuel'}
    if any(indexed for _name, indexed in assignments) or len(names) != len(set(names)):
        raise ValueError('indexed or repeated MATPOWER assignments are unsupported')
    if set(names)-known:
        raise ValueError('unsupported MATPOWER equipment/fields: '+', '.join(sorted(set(names)-known)))
    body = re.sub(r'(?m)^\s*function[^\n]*', '', clean)
    if re.search(r'\bmpc\s*=', body):
        raise ValueError('computed MATPOWER structure assignments are unsupported')
    if not re.search(r'''mpc\.version\s*=\s*['"]2['"]\s*;''', clean):
        raise ValueError('literal MATPOWER version 2 required')
    base = re.search(r'mpc\.baseMVA\s*=\s*([-+0-9.eE]+)\s*;', clean)
    if not base:
        raise ValueError('missing literal baseMVA')
    base = float(base.group(1))
    if base <= 0:
        raise ValueError('positive baseMVA required')
    bus_rows = _matrix(clean, 'bus'); gen_rows = _matrix(clean, 'gen')
    branch_rows = _matrix(clean, 'branch'); costs = _matrix(clean, 'gencost')
    if len(gen_rows) != len(costs):
        raise ValueError('exactly one active-power polynomial per generator required')
    if any(len(r) < 13 for r in bus_rows+branch_rows) or any(len(r) < 10 for r in gen_rows):
        raise ValueError('incomplete MATPOWER bus/gen/branch row')
    buses = [Bus(_integer(r[0]), _integer(r[1]), r[2], r[3], r[4]/base, r[5]/base,
                 r[7], r[8], r[12], r[11]) for r in bus_rows]
    generators = []
    setpoints = {}
    for row, cost in zip(gen_rows, costs, strict=True):
        if _integer(row[7]) not in (0, 1):
            raise ValueError('unsupported generator service status')
        if len(cost) < 5 or _integer(cost[0]) != 2:
            raise ValueError('only polynomial MATPOWER objectives are supported')
        count = _integer(cost[3])
        if not 1 <= count <= 8 or len(cost) != 4+count:
            raise ValueError('invalid MATPOWER polynomial width')
        if row[7] == 1:
            bus_id = _integer(row[0])
            if not math.isfinite(row[5]) or row[5] <= 0:
                raise ValueError('invalid generator voltage setpoint')
            if bus_id in setpoints and abs(setpoints[bus_id]-row[5]) > 1e-9:
                raise ValueError('conflicting voltage controls on one bus')
            setpoints[bus_id] = row[5]
            generators.append(GeneratorSpec(_integer(row[0]), row[4], row[3], row[9], row[8],
                                            tuple(cost[4:]), row[1], row[2]))
    buses = [replace(b, vm=setpoints[b.id]) if b.type_code in (2, 3) and b.id in setpoints else b for b in buses]
    branches = [Branch(_integer(r[0]), _integer(r[1]), r[2], r[3], r[4], r[5], r[8], r[9],
                       _integer(r[10]), r[11], r[12]) for r in branch_rows]
    published = re.search(r'opt objective value:\s*([0-9.eE+-]+)', text)
    case = OpfCase(path.name, base, buses, generators, branches,
                   None if published is None else float(published.group(1)))
    validate_case(case)
    return case
