"""Sensored current-model observer retaining every Gamma rotor cage.

No plant state is read. Core-loss current separation and temperature estimation
are outside this observer: its explicit passport must have Gc=0 and no thermal.
"""
import numpy as np
from scipy.optimize import brentq, root
from power_grid_env.rotor_flux_control import RotorFluxController


class MulticageFluxController(RotorFluxController):
    def __init__(self, parameters, machine):
        if machine.core_loss_s or machine.thermal or machine.locked_rotor:
            raise ValueError('current-model observer requires zero core loss, no thermal and free shaft')
        super().__init__(parameters, machine)
        self.leakage = np.array([c.leakage_h for c in machine.cages])
        self.resistance = np.array([c.resistance_ohm for c in machine.cages])
        self.rotor_flux = np.zeros((len(machine.cages), 2))
        self.inverse_leakage = float(sum(1/self.leakage))
        self.last_residual_vs = 0.

    def reconstruct(self, rotor_flux, current):
        """Unique radial inverse of i_s = g(psi_s) + sum((psi_s-psi_r)/L)."""
        p = self.machine
        b = np.array([current.real, current.imag]) + np.sum(rotor_flux/self.leakage[:, None], axis=0)
        amplitude = float(np.linalg.norm(b))
        a = 1/p.unsaturated_h + self.inverse_leakage
        if not amplitude:
            return np.zeros(2), p.unsaturated_h, a
        upper = amplitude/a
        if not p.saturation_beta_per_vs or (p.saturation_beta_per_vs*upper)**p.saturation_exponent < 1e-14:
            radius = upper
        else:
            radius = brentq(lambda r: a*r + r*(p.saturation_beta_per_vs*r)**p.saturation_exponent/p.unsaturated_h-amplitude,
                            0., np.nextafter(upper, np.inf), xtol=1e-14, rtol=1e-14)
        lm = p.unsaturated_h/(1+(p.saturation_beta_per_vs*radius)**p.saturation_exponent)
        return b*(radius/amplitude), lm, 1/lm+self.inverse_leakage

    def derivative(self, rotor_flux, current, speed):
        stator, _, _ = self.reconstruct(rotor_flux, current)
        spin = self.machine.pole_pairs*speed*np.column_stack([-rotor_flux[:, 1], rotor_flux[:, 0]])
        return self.resistance[:, None]/self.leakage[:, None]*(stator-rotor_flux)+spin

    def derivative_jacobian(self, rotor_flux, current, speed):
        p = self.machine
        stator, _, a = self.reconstruct(rotor_flux, current)
        radius = float(np.linalg.norm(stator))
        reluctance = a*np.eye(2)
        if radius:
            direction = stator/radius
            reluctance += p.saturation_exponent*(p.saturation_beta_per_vs*radius)**p.saturation_exponent/p.unsaturated_h*np.outer(direction, direction)
        inverse = np.linalg.inv(reluctance)
        n = len(self.leakage)
        jac = np.empty((2*n, 2*n))
        spin = p.pole_pairs*speed*np.array([[0., -1.], [1., 0.]])
        for k in range(n):
            for j in range(n):
                block = self.resistance[k]/self.leakage[k]*inverse/self.leakage[j]
                if k == j:
                    block = block-self.resistance[k]/self.leakage[k]*np.eye(2)+spin
                jac[2*k:2*k+2, 2*j:2*j+2] = block
        return jac

    def observe(self, current, speed, elapsed):
        if not (np.isfinite(current) and np.isfinite(speed) and np.isfinite(elapsed)) or elapsed < 0:
            raise ValueError('finite observer inputs and nonnegative elapsed required')
        if not elapsed:
            return
        old = self.rotor_flux.copy()
        def residual(flat):
            after = flat.reshape(old.shape)
            return (after-old-elapsed*self.derivative((old+after)/2, current, speed)).ravel()
        def jacobian(flat):
            return np.eye(old.size)-elapsed/2*self.derivative_jacobian((old+flat.reshape(old.shape))/2, current, speed)
        predictor = old+elapsed*self.derivative(old, current, speed)
        solved = root(residual, predictor.ravel(), jac=jacobian, tol=1e-10)
        error = float(np.max(np.abs(residual(solved.x))))
        if not np.all(np.isfinite(solved.x)) or not np.isfinite(error) or error > 1e-10:
            raise RuntimeError('multicage flux observer midpoint failed; state not committed')
        after = solved.x.reshape(old.shape)
        _, lm, a = self.reconstruct(after, current)
        equivalent = np.sum(after/self.leakage[:, None], axis=0)/a
        self.rotor_flux = after.copy()
        self.flux = complex(*equivalent)
        self.lsigma = 1/a
        self.lm = lm*self.inverse_leakage/a
        self.last_residual_vs = error

    def orientation_frequency(self, speed, measured, flux):
        # The radial scaling is real: the angle of equivalent rotor flux is
        # exactly the angle of B=sum(psi_r/L). No one-cage slip reduction.
        current = measured*np.exp(1j*self.angle)
        b = np.sum(self.rotor_flux/self.leakage[:, None], axis=0)
        db = np.sum(self.derivative(self.rotor_flux, current, speed)/self.leakage[:, None], axis=0)
        norm = float(b@b)
        return float((b[0]*db[1]-b[1]*db[0])/norm) if norm > 1e-20 else self.machine.pole_pairs*speed

    def update(self, t, current, speed, vdc, period, enabled):
        voltage, channels = super().update(t, current, speed, vdc, period, enabled)
        channels['observer_midpoint_residual_vs'] = self.last_residual_vs
        for k, row in enumerate(self.rotor_flux, 1):
            channels[f'observer_cage_{k}_flux_alpha_vs'] = float(row[0])
            channels[f'observer_cage_{k}_flux_beta_vs'] = float(row[1])
        return voltage, channels
