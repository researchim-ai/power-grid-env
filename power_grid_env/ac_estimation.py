"""Analytical polar AC WLS with rank, leverage and explicit bad-data decisions.

Independent Gaussian diagonal measurement errors are an explicit assumption.
State estimation does not enforce OPF limits or prove the topology is correct.
"""
from dataclasses import dataclass
import math

import numpy as np
from scipy.optimize import least_squares
from scipy.stats import chi2

from power_grid_env.balanced_ac import validate_case, components
from power_grid_env.network_opf import _ybus, _branch_quad

KINDS = {
    'vm_pu': 'pu', 'va_rad': 'rad', 'p_injection_mw': 'MW', 'q_injection_mvar': 'Mvar',
    'p_from_mw': 'MW', 'q_from_mvar': 'Mvar', 'p_to_mw': 'MW', 'q_to_mvar': 'Mvar',
}


@dataclass(frozen=True)
class Measurement:
    id: str
    kind: str
    value: float
    sigma: float
    bus_id: int | None = None
    branch_index: int | None = None


class MeasurementModel:
    def __init__(self, case, measurements):
        validate_case(case)
        if len(components(case)) != 1:
            raise ValueError('AC estimation requires a connected single-reference case')
        self.case = case
        self.rows = list(measurements)
        if len(case.buses) > 128:
            raise ValueError('dense AC estimation profile supports at most 128 buses')
        if not 1 <= len(self.rows) <= 5_000:
            raise ValueError('measurement count must be 1..5000')
        self.index = {b.id: i for i, b in enumerate(case.buses)}
        self.slack = next(i for i, b in enumerate(case.buses) if b.type_code == 3)
        self.nonref = [i for i in range(len(case.buses)) if i != self.slack]
        self.n = len(case.buses)
        self.size = 2*self.n-1
        self.y = _ybus(case, self.index)
        ids = set()
        for row in self.rows:
            if not isinstance(row.id, str) or not row.id or row.id in ids:
                raise ValueError('unique nonempty measurement IDs required')
            ids.add(row.id)
            if row.kind not in KINDS or not math.isfinite(row.value) or not math.isfinite(row.sigma) or row.sigma <= 0:
                raise ValueError('measurement kind/value/sigma is invalid')
            if row.kind in {'vm_pu', 'va_rad', 'p_injection_mw', 'q_injection_mvar'}:
                if row.bus_id not in self.index or row.branch_index is not None:
                    raise ValueError('bus measurement needs only a known bus_id')
            elif row.bus_id is not None or type(row.branch_index) is not int or not 0 <= row.branch_index < len(case.branches):
                raise ValueError('branch measurement needs only a valid branch_index')
        self.values = np.array([r.value for r in self.rows])
        self.sigmas = np.array([r.sigma for r in self.rows])
        self.angle_rows = [i for i, r in enumerate(self.rows) if r.kind == 'va_rad']

    def state(self, vm, theta):
        vm = np.asarray(vm, dtype=float); theta = np.asarray(theta, dtype=float)
        if vm.shape != (self.n,) or theta.shape != (self.n,) or not np.all(np.isfinite(vm)) or not np.all(np.isfinite(theta)) or np.any(vm <= 0):
            raise ValueError('finite positive voltage vector and angle vector required')
        return np.r_[(theta-theta[self.slack])[self.nonref], vm]

    def unpack(self, x):
        x = np.asarray(x, dtype=float)
        if x.shape != (self.size,) or not np.all(np.isfinite(x)):
            raise ValueError('invalid estimator state')
        theta = np.zeros(self.n); theta[self.nonref] = x[:self.n-1]
        return x[self.n-1:], theta

    def evaluate(self, x):
        vm, theta = self.unpack(x)
        voltage = vm*np.exp(1j*theta)
        d_voltage = np.zeros((self.n, self.size), dtype=complex)
        for col, bus in enumerate(self.nonref):
            d_voltage[bus, col] = 1j*voltage[bus]
        d_voltage[:, self.n-1:] = np.diag(np.exp(1j*theta))
        current = self.y @ voltage
        power = voltage*np.conjugate(current)*self.case.base_mva
        dpower = (d_voltage*np.conjugate(current)[:, None]
                  + voltage[:, None]*np.conjugate(self.y @ d_voltage))*self.case.base_mva
        values = []; jac = []
        for row in self.rows:
            derivative = np.zeros(self.size)
            if row.kind == 'vm_pu':
                i = self.index[row.bus_id]; value = vm[i]; derivative[self.n-1+i] = 1.
            elif row.kind == 'va_rad':
                i = self.index[row.bus_id]; value = theta[i]
                if i != self.slack:
                    derivative[self.nonref.index(i)] = 1.
            elif row.kind in {'p_injection_mw', 'q_injection_mvar'}:
                i = self.index[row.bus_id]
                value = power[i].real if row.kind[0] == 'p' else power[i].imag
                derivative = dpower[i].real if row.kind[0] == 'p' else dpower[i].imag
            else:
                branch = self.case.branches[row.branch_index]
                if not branch.status:
                    value = 0.
                else:
                    ff, ft, tf, tt = _branch_quad(branch)
                    i = self.index[branch.fbus]; j = self.index[branch.tbus]
                    if '_to_' in row.kind:
                        i, j = j, i; ff, ft = tt, tf
                    current = ff*voltage[i]+ft*voltage[j]
                    s = voltage[i]*np.conjugate(current)*self.case.base_mva
                    ds = (d_voltage[i]*np.conjugate(current)
                          + voltage[i]*np.conjugate(ff*d_voltage[i]+ft*d_voltage[j]))*self.case.base_mva
                    value = s.real if row.kind[0] == 'p' else s.imag
                    derivative = ds.real if row.kind[0] == 'p' else ds.imag
            values.append(value); jac.append(derivative)
        values = np.array(values); jac = np.array(jac)
        if not np.all(np.isfinite(values)) or not np.all(np.isfinite(jac)):
            raise ValueError('nonfinite measurement prediction or derivative')
        return values, jac

    def residual(self, x):
        predicted, _jac = self.evaluate(x)
        residual = predicted-self.values
        residual[self.angle_rows] = (residual[self.angle_rows]+np.pi) % (2*np.pi)-np.pi
        return residual/self.sigmas

    def jacobian(self, x):
        return self.evaluate(x)[1]/self.sigmas[:, None]


def estimate_state(case, measurements, *, max_evaluations=200, reject_bad_data=False,
                   normalized_threshold=3., significance=.01, max_removals=5,
                   initial_vm=None, initial_theta=None):
    if type(max_evaluations) is not int or not 1 <= max_evaluations <= 2000:
        raise ValueError('invalid estimator evaluation budget')
    if type(max_removals) is not int or not 0 <= max_removals <= 100 or type(reject_bad_data) is not bool:
        raise ValueError('invalid bad-data removal budget/policy')
    if not math.isfinite(normalized_threshold) or normalized_threshold <= 0 or not 0 < significance < 1:
        raise ValueError('invalid bad-data thresholds')
    original = list(measurements); model = MeasurementModel(case, original)
    x = model.state([b.vm for b in case.buses] if initial_vm is None else initial_vm,
                    np.radians([b.va_deg for b in case.buses]) if initial_theta is None else initial_theta)
    kept = list(range(len(original))); removed = []; history = []
    for attempt in range(max_removals+1):
        model = MeasurementModel(case, [original[i] for i in kept])
        lower = np.r_[np.full(model.n-1, -2*np.pi), np.full(model.n, .05)]
        upper = np.r_[np.full(model.n-1, 2*np.pi), np.full(model.n, 2.)]
        if np.any(x <= lower) or np.any(x >= upper):
            raise ValueError('initial state outside estimator numerical bounds')
        fit = least_squares(model.residual, x, jac=model.jacobian, bounds=(lower, upper),
                            method='trf', x_scale='jac', max_nfev=max_evaluations,
                            ftol=1e-11, xtol=1e-11, gtol=1e-10)
        x = fit.x; residual = model.residual(x); jac = model.jacobian(x)
        u, singular, vt = np.linalg.svd(jac, full_matrices=False)
        tolerance = singular[0]*max(jac.shape)*np.finfo(float).eps if len(singular) else 0.
        rank = int(np.sum(singular > tolerance))
        condition = float(singular[0]/singular[-1]) if len(singular) >= model.size and singular[-1] > 0 else None
        if condition is not None and not math.isfinite(condition):
            condition = None
        observable = rank == model.size and condition is not None and condition <= 1e12
        if not observable:
            return dict(completed=False, accepted=False, status='unobservable_or_ill_conditioned', observable=False,
                        rank=rank, states=model.size, condition_number=condition, removed_measurement_ids=removed,
                        estimate=None, history=history)
        leverage = np.sum(u[:, :model.size]**2, axis=1)
        variance = np.maximum(0., 1.-leverage)
        critical = variance < 1e-10
        normalized = [None if critical[i] else float(residual[i]/math.sqrt(variance[i])) for i in range(len(kept))]
        dof = len(kept)-model.size; objective = float(residual @ residual)
        p_value = float(chi2.sf(objective, dof)) if dof > 0 else None
        gate = bool(p_value is not None and p_value >= significance)
        history.append(dict(kept_measurement_ids=[original[i].id for i in kept], objective=objective,
                            degrees_of_freedom=dof, chi_square_p_value=p_value, bad_data_test_passed=gate))
        suspect = max((i for i, v in enumerate(normalized) if v is not None),
                      key=lambda i: abs(normalized[i]), default=None)
        removal_status = 'not_requested' if not reject_bad_data else 'not_needed'
        if reject_bad_data and not gate and suspect is not None and abs(normalized[suspect]) > normalized_threshold:
            if attempt == max_removals:
                removal_status = 'removal_budget_exhausted'
            else:
                candidate = kept[:suspect]+kept[suspect+1:]
                if len(candidate) < model.size:
                    removal_status = 'removal_would_destroy_observability'
                else:
                    trial = MeasurementModel(case, [original[i] for i in candidate])
                    if np.linalg.matrix_rank(trial.jacobian(x)) < model.size:
                        removal_status = 'removal_would_destroy_observability'
                    else:
                        removed.append(original[kept[suspect]].id); kept = candidate
                        continue
        vm, theta = model.unpack(x)
        covariance = (vt.T*(1./singular**2)) @ vt
        if not np.all(np.isfinite(covariance)):
            return dict(completed=False, accepted=False, status='nonfinite_covariance', observable=False)
        wrap_boundary = any(abs(abs(residual[i]*model.sigmas[i])-np.pi) < 1e-7 for i in model.angle_rows)
        completed = bool(fit.success and np.all(np.isfinite(x)) and not wrap_boundary)
        status = 'angle_wrap_boundary' if wrap_boundary else ('completed' if completed else 'iteration_budget_or_stagnation')
        return dict(completed=completed, accepted=bool(completed and gate), status=status, observable=True,
                    rank=rank, states=model.size, condition_number=condition, evaluations=int(fit.nfev),
                    bus_ids=[b.id for b in case.buses], vm_pu=vm.tolist(), va_deg=np.degrees(theta).tolist(),
                    objective=objective, degrees_of_freedom=dof, chi_square_p_value=p_value,
                    bad_data_test_available=dof > 0, bad_data_test_passed=gate,
                    normalized_residuals={original[k].id: v for k, v in zip(kept, normalized)},
                    critical_measurement_ids=[original[k].id for i, k in enumerate(kept) if critical[i]],
                    removed_measurement_ids=removed, removal_status=removal_status,
                    covariance=covariance.tolist(), covariance_state_order=['angle_rad:'+str(case.buses[i].id) for i in model.nonref]+['vm_pu:'+str(b.id) for b in case.buses],
                    measurements=[dict(id=original[k].id, unit=KINDS[original[k].kind], residual=float(-residual[i]*model.sigmas[i])) for i, k in enumerate(kept)],
                    history=history, scope='local AC WLS, fixed topology, diagonal Gaussian measurement covariance; no topology-error identification')
    raise RuntimeError('unreachable estimation state')
