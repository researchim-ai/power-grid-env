"""Shared balanced AC case, polar Newton power flow and explicit limit reporting.

The case is the existing OPF model: MW/Mvar, MVA base, shunts and impedances in
pu. It is not an implicit conversion of an unbalanced canonical Network.
"""
from dataclasses import asdict
import math

import numpy as np

from power_grid_env.network_opf import (
    Branch, Bus, GeneratorSpec, OpfCase, _branch_quad, _injections,
    _state_derivatives, _ybus, audit_feasibility,
)

SCHEMA = 'power-grid-env.balanced-ac/v1'
MAX_BUSES = 2048
MAX_DENSE_BUSES = 512


def validate_case(case: OpfCase) -> None:
    if not isinstance(case.name, str) or not case.name:
        raise ValueError('case name is required')
    if not math.isfinite(case.base_mva) or case.base_mva <= 0:
        raise ValueError('positive finite base_mva required')
    if not 1 <= len(case.buses) <= MAX_BUSES:
        raise ValueError(f'balanced case requires 1..{MAX_BUSES} buses')
    ids = [b.id for b in case.buses]
    if any(type(i) is not int for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('unique integer bus IDs required')
    if sum(b.type_code == 3 for b in case.buses) != 1:
        raise ValueError('exactly one reference bus required')
    for bus in case.buses:
        values = [bus.pd, bus.qd, bus.gs, bus.bs, bus.vm, bus.va_deg, bus.vmin, bus.vmax]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('nonfinite bus parameter')
        if bus.type_code not in (1, 2, 3) or not 0 < bus.vmin <= bus.vmax or bus.vm <= 0:
            raise ValueError('unsupported bus type or voltage bounds')
    if not case.generators or len(case.generators) > 4096 or len(case.branches) > 8192:
        raise ValueError('bounded nonempty generator list and branch list required')
    id_set=set(ids)
    for gen in case.generators:
        values = [gen.qmin, gen.qmax, gen.pmin, gen.pmax, *gen.cost]
        values.extend(v for v in (gen.pg, gen.qg) if v is not None)
        if type(gen.bus) is not int or gen.bus not in id_set or not all(math.isfinite(v) for v in values):
            raise ValueError('invalid generator bus or nonfinite parameter')
        if gen.pmin > gen.pmax or gen.qmin > gen.qmax or not gen.cost or len(gen.cost) > 8:
            raise ValueError('invalid generator bounds or polynomial')
    generator_buses={g.bus for g in case.generators if type(g.bus) is int}
    for bus in case.buses:
        if bus.type_code in (2, 3) and bus.id not in generator_buses:
            raise ValueError('voltage-controlled bus has no generator')
    for branch in case.branches:
        values = [branch.r, branch.x, branch.b, branch.rate_a, branch.ratio,
                  branch.angle_deg, branch.angmin_deg, branch.angmax_deg]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('nonfinite branch parameter')
        if type(branch.fbus) is not int or type(branch.tbus) is not int or branch.fbus not in id_set or branch.tbus not in id_set or branch.fbus == branch.tbus:
            raise ValueError('invalid branch endpoints')
        if type(branch.status) is not int or branch.status not in (0, 1) or branch.ratio < 0 or branch.rate_a < 0:
            raise ValueError('invalid branch status, tap or MVA rating')
        if branch.r == branch.x == 0 or branch.angmin_deg > branch.angmax_deg:
            raise ValueError('zero branch impedance or reversed angle limits')
    if case.published_cost is not None and not math.isfinite(case.published_cost):
        raise ValueError('nonfinite reference objective')


def case_document(case):
    validate_case(case)
    return dict(schema=SCHEMA, **asdict(case))


def case_from_document(payload):
    if not isinstance(payload, dict) or payload.get('schema') != SCHEMA:
        raise ValueError('balanced AC schema required')
    fields = {'schema', 'name', 'base_mva', 'buses', 'generators', 'branches', 'published_cost'}
    if set(payload) - fields:
        raise ValueError('unknown balanced AC fields')
    try:
        case = OpfCase(
            name=payload['name'], base_mva=payload['base_mva'],
            buses=[Bus(**row) for row in payload['buses']],
            generators=[GeneratorSpec(**{**row, 'cost': tuple(row['cost'])}) for row in payload['generators']],
            branches=[Branch(**row) for row in payload['branches']],
            published_cost=payload.get('published_cost'),
        )
        validate_case(case)
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError('invalid balanced AC document: ' + str(exc)) from exc
    return case


def components(case):
    """Stable bus-ID components. A PV generator never silently becomes a slack."""
    neighbours = {b.id: set() for b in case.buses}
    for branch in case.branches:
        if branch.status:
            neighbours[branch.fbus].add(branch.tbus)
            neighbours[branch.tbus].add(branch.fbus)
    groups = []
    unseen = set(neighbours)
    while unseen:
        stack = [min(unseen)]; found = set()
        while stack:
            node = stack.pop()
            if node in found:
                continue
            found.add(node); stack.extend(neighbours[node] - found)
        unseen -= found
        groups.append(sorted(found))
    return groups


def branch_results(case, vm, theta):
    index = {b.id: i for i, b in enumerate(case.buses)}
    rows = [];voltage=np.asarray(vm)*np.exp(1j*np.asarray(theta))
    for i, branch in enumerate(case.branches):
        if branch.status:
            ff,ft,tf,tt=_branch_quad(branch);vf,vt=voltage[index[branch.fbus]],voltage[index[branch.tbus]]
            f=vf*np.conj(ff*vf+ft*vt)*case.base_mva;t=vt*np.conj(tf*vf+tt*vt)*case.base_mva
        else:f,t=0j,0j
        angle = math.degrees(theta[index[branch.fbus]] - theta[index[branch.tbus]])
        rows.append(dict(
            branch_index=i, from_bus=branch.fbus, to_bus=branch.tbus, in_service=bool(branch.status),
            p_from_mw=f.real, q_from_mvar=f.imag, p_to_mw=t.real, q_to_mvar=t.imag,
            s_from_mva=abs(f), s_to_mva=abs(t), rate_mva=branch.rate_a,
            overload=bool(branch.status and branch.rate_a > 0 and max(abs(f), abs(t)) > branch.rate_a + 1e-3),
            angle_difference_deg=angle,
            angle_violation=bool(branch.status and not branch.angmin_deg-1e-3 <= angle <= branch.angmax_deg+1e-3),
        ))
    return rows


def _allocate(total, low, high):
    """Deterministic bounded sharing; infeasibility remains visible in one unit."""
    low = np.asarray(low, dtype=float); high = np.asarray(high, dtype=float)
    if total < sum(low):
        result = low.copy(); result[0] += total-sum(low); return result
    if total > sum(high):
        result = high.copy(); result[0] += total-sum(high); return result
    width = high-low
    return low + (total-sum(low))*width/sum(width) if sum(width) else low.copy()


def solve_power_flow(case, *, tolerance_pu=1e-9, max_iterations=40, enforce_q_limits=True, linear_solver="auto"):
    """Connected single-slack PF with fixed Pg and monotone PV→PQ Q-limit switching.

    Limit violations are distinct from Newton nonconvergence. No OPF redispatch,
    load shedding, frequency model or automatic island reference is inferred.
    """
    validate_case(case)
    if not math.isfinite(tolerance_pu) or tolerance_pu <= 0 or type(max_iterations) is not int or not 1 <= max_iterations <= 200:
        raise ValueError('invalid Newton accuracy/budget')
    if type(enforce_q_limits) is not bool:
        raise ValueError('Q-limit policy must be boolean')
    if linear_solver not in {'auto','dense','sparse'}:raise ValueError('linear_solver must be auto/dense/sparse')
    sparse=linear_solver=='sparse' or (linear_solver=='auto' and len(case.buses)>128)
    if not sparse and len(case.buses)>MAX_DENSE_BUSES:raise ValueError('dense AC is limited to 512 buses; use sparse or auto')
    groups = components(case)
    if len(groups) != 1:
        slack_id = next(b.id for b in case.buses if b.type_code == 3)
        return dict(completed=False, converged=False, status='islanded', islands=[dict(
            bus_ids=g, has_reference=slack_id in g,
            generator_indices=[i for i, gen in enumerate(case.generators) if gen.bus in g],
            demand_mw=sum(b.pd for b in case.buses if b.id in g),
        ) for g in groups], voltage_collapse_claimed=False)
    n = len(case.buses); index = {b.id: i for i, b in enumerate(case.buses)}
    slack = next(i for i, b in enumerate(case.buses) if b.type_code == 3)
    nonref = [i for i in range(n) if i != slack]
    pv = {i for i, b in enumerate(case.buses) if b.type_code == 2}
    pq = {i for i, b in enumerate(case.buses) if b.type_code == 1}
    vm = np.array([b.vm for b in case.buses], dtype=float); theta = np.radians([b.va_deg for b in case.buses])
    theta -= theta[slack]
    if sparse:
        from power_grid_env.balanced_sparse import admittance,derivatives,stored_bytes
        from scipy.sparse import bmat
        from scipy.sparse.linalg import splu
        y=admittance(case,index)
    else:y = _ybus(case, index)
    maximum_jacobian_stored_values=maximum_jacobian_bytes=0
    pg = np.array([0. if g.pg is None else g.pg for g in case.generators], dtype=float)
    qg = np.array([0. if g.qg is None else g.qg for g in case.generators], dtype=float)
    units_by_bus=[[] for _ in range(n)]
    for i,gen in enumerate(case.generators):units_by_bus[index[gen.bus]].append(i)
    for gen in case.generators:
        if index[gen.bus] != slack and gen.pg is None:
            raise ValueError('non-reference generator needs an explicit Pg schedule')
    pset = -np.array([b.pd for b in case.buses], dtype=float); qset = -np.array([b.qd for b in case.buses], dtype=float)
    for i, g in enumerate(case.generators):
        pset[index[g.bus]] += pg[i]; qset[index[g.bus]] += qg[i]
    pset /= case.base_mva; qset /= case.base_mva
    switched = []; iterations = 0; residual = math.inf; converged = False
    for _active in range(len(pv)+1):
        qrows = sorted(pq)
        def mismatch(v, a):
            p, q = _injections(v, a, y)
            return np.r_[p[nonref]-pset[nonref], q[qrows]-qset[qrows]]
        converged = False
        for _ in range(max_iterations):
            mis = mismatch(vm, theta); residual = float(np.max(np.abs(mis), initial=0.))
            if not np.all(np.isfinite(mis)):
                return dict(completed=False, converged=False, status='nonfinite_residual', iterations=iterations, voltage_collapse_claimed=False)
            if residual <= tolerance_pu:
                converged = True; break
            dp,dq,dpv,dqv=derivatives(vm,theta,y) if sparse else _state_derivatives(vm,theta,y)
            if sparse:
                jac=bmat([[dp[nonref,:][:,nonref],dpv[nonref,:][:,qrows]],
                          [dq[qrows,:][:,nonref],dqv[qrows,:][:,qrows]]],format='csc')
                maximum_jacobian_stored_values=max(maximum_jacobian_stored_values,jac.nnz)
                maximum_jacobian_bytes=max(maximum_jacobian_bytes,stored_bytes(jac))
            else:
                jac=np.block([[dp[np.ix_(nonref,nonref)],dpv[np.ix_(nonref,qrows)]],
                              [dq[np.ix_(qrows,nonref)],dqv[np.ix_(qrows,qrows)]]])
                maximum_jacobian_stored_values=max(maximum_jacobian_stored_values,jac.size)
                maximum_jacobian_bytes=max(maximum_jacobian_bytes,jac.nbytes)
            try:
                step = splu(jac).solve(-mis) if sparse else np.linalg.solve(jac, -mis)
            except (np.linalg.LinAlgError,RuntimeError):
                return dict(completed=False, converged=False, status='singular_jacobian', iterations=iterations, voltage_collapse_claimed=False)
            improved = False
            for factor in (1., .5, .25, .125, .0625, .03125, .015625, .0078125):
                a = theta.copy(); v = vm.copy()
                a[nonref] += factor*step[:len(nonref)]; v[qrows] += factor*step[len(nonref):]
                if np.all(v > .02) and np.all(v < 3.) and np.max(np.abs(mismatch(v, a)), initial=0.) < residual:
                    vm, theta = v, a; improved = True; break
            iterations += 1
            if not improved:
                break
        if not converged:
            residual = float(np.max(np.abs(mismatch(vm, theta)), initial=0.))
            converged = math.isfinite(residual) and residual <= tolerance_pu
        if not converged:
            break
        _p, q = _injections(vm, theta, y)
        changed = False
        if enforce_q_limits:
            for bus in sorted(pv, key=lambda i: case.buses[i].id):
                units = units_by_bus[bus]
                needed = q[bus]*case.base_mva + case.buses[bus].qd
                low = sum(case.generators[i].qmin for i in units); high = sum(case.generators[i].qmax for i in units)
                if needed < low-1e-7 or needed > high+1e-7:
                    fixed = min(high, max(low, needed)); qset[bus] = (fixed-case.buses[bus].qd)/case.base_mva
                    pq.add(bus); switched.append(dict(bus_id=case.buses[bus].id, q_fixed_mvar=fixed))
                    changed = True
            pv -= pq
        if not changed:
            break
    p, q = _injections(vm, theta, y)
    for bus in range(n):
        units = units_by_bus[bus]
        if not units:
            continue
        if bus == slack:
            pg[units] = _allocate(p[bus]*case.base_mva+case.buses[bus].pd,
                                  [case.generators[i].pmin for i in units], [case.generators[i].pmax for i in units])
        if bus == slack or case.buses[bus].type_code == 2:
            qg[units] = _allocate(q[bus]*case.base_mva+case.buses[bus].qd,
                                  [case.generators[i].qmin for i in units], [case.generators[i].qmax for i in units])
    audit = audit_feasibility(case, vm, theta, pg, qg, tolerance=max(tolerance_pu*10, 1e-8),matrix_format='sparse' if sparse else 'dense')
    return dict(completed=converged, converged=converged, status='converged' if converged else 'newton_not_converged',
                iterations=iterations,linear_solver=dict(requested=linear_solver,used='sparse' if sparse else 'dense',ybus_stored_values=y.nnz if sparse else y.size,ybus_bytes=stored_bytes(y) if sparse else y.nbytes,maximum_jacobian_stored_values=maximum_jacobian_stored_values,maximum_jacobian_bytes=maximum_jacobian_bytes), max_mismatch_pu=residual, bus_ids=[b.id for b in case.buses],
                vm_pu=vm.tolist(), va_deg=np.degrees(theta).tolist(), pg_mw=pg.tolist(), qg_mvar=qg.tolist(),
                pv_to_pq=switched, audit=audit, branches=branch_results(case, vm, theta),
                within_limits=bool(converged and audit['feasible']), voltage_collapse_claimed=False)


def dispatch_case(case, result):
    """Transfer a separately audited OPF point into the same PF/N−1 case.

    Equipment ordering and constraints are preserved. An optimizer success flag
    alone is insufficient to transfer a dispatch.
    """
    validate_case(case)
    if not result.get('success'):
        raise ValueError('only a successful audited OPF dispatch can be transferred')
    try:
        vm = np.asarray(result['vm_pu'], dtype=float)
        theta = np.radians(result['va_deg'])
        pg = np.asarray(result['pg_mw'], dtype=float)
        qg = np.asarray(result['qg_mvar'], dtype=float)
    except (KeyError, TypeError) as exc:
        raise ValueError('incomplete OPF operating point') from exc
    if not audit_feasibility(case, vm, theta, pg, qg)['feasible']:
        raise ValueError('dispatch fails the independent feasibility audit')
    from dataclasses import replace
    buses = [replace(bus, vm=float(vm[i]), va_deg=float(np.degrees(theta[i]))) for i, bus in enumerate(case.buses)]
    generators = [replace(gen, pg=float(pg[i]), qg=float(qg[i])) for i, gen in enumerate(case.generators)]
    return replace(case, buses=buses, generators=generators)
