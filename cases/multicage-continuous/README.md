# Continuous multicage drive profile / Многоклеточный привод

Six authored PHY-20 / R06.20 studies, executable with the existing installed
package. Each JSON uses the qualified adaptive budget 0.0125:

```bash
python -m power_grid_env.cli switching-drive run --study cases/multicage-continuous/four_rotor_trip.json --out reports/four-rotor-trip
```

The output directory must be new. These cases use a free mechanical shaft,
positive constant eddy-current conductance, resolved PWM and an explicitly
declared nominal single-cage observer. They do not silently reduce the physical
plant to that observer.

- `double_linear`: two rotor cages, linear magnetics and warm winding/electronics.
- `double_saturated_warm`: two cages with radial saturation and winding R(T).
- `triple_saturated_warm`: three cages with the same coupled thermal profile.
- `four_saturated_warm`: four cages with switching/conduction losses and heating.
- `four_recovery`: four cages, source sag, gate block at 4–6 ms and recovery.
- `four_rotor_trip`: four cages and a localized rotor thermal latch.

Recovery lasts 12 ms; the other cases last 4 ms. The trip uses an intentionally
small thermal capacity to test the event within the bounded window. A thermal
latch blocks controlled inverter gates; it does not open a feeder breaker or
remove passive diode paths. Rotor cages share one rotor thermal lump.

## Parameter provenance

The base saturated Gamma circuit comes from the published Aalto/motulator model
form referenced in each motor's provenance. Saturation coefficients, extra rotor
branches, mechanics, thermal capacities and controller settings are authored
numerical fixtures. The extra branches use R=5+k ohm and L=0.018+0.004k H;
they are not fitted skin-depth data or manufacturer passports. Positive core
conductance represents a declared eddy-current loss term, not a hysteresis map.

## Independent equations and limits

For each rotor branch k, in complex stationary coordinates:

```text
i_rk = (psi_rk - psi_s) / L_lk
i_mu = psi_s * [1 + (beta * |psi_s|)^exponent] / L_m - sum(i_rk)
i_s = (i_mu + G_c * u_s) / (1 + R_s * G_c)
dpsi_s/dt = u_s - R_s * i_s
dpsi_rk/dt = -R_rk * i_rk + j * pole_pairs * speed * psi_rk
torque = 1.5 * pole_pairs * Im(i_mu * conjugate(psi_s))
```

All rotor copper losses enter the shared rotor thermal equation. Independent
per-cage heat quadratures must be positive and sum to the total rotor heat.
Switching energy moves from DC storage into inverter thermal storage once.

`benchmarks.multicage_drive_reference` expresses these equations separately
from the runtime solver. It uses the independently assembled continuous circuit
and control equations; production imports are blocked in its workers. It is
**not an external multicage simulator**. Its single-cage reduction is separately
checked against the unmodified, SHA256-pinned motulator 0.23.1 machine RHS and
full single-cage hybrid reference.

Additional physical checks include:

- Identical parallel cages with identical initial flux reduce exactly to one
  cage with R/n and L/n. This does not hold for arbitrary unequal time constants.
- Permuting cages and their states preserves stator/network quantities, torque,
  thermal response and stored energy.
- An opposed equal-cage difference mode decays as exp(-R/L * t).
- Linear sinusoidal cases match a separately derived equivalent impedance at
  5/50/200 Hz, including synchronous speed, motoring, blocked rotor and generation.
- Instantaneous terminal currents match runtime for all 27 safe gate states,
  two–four cages and zero, energized and unequal flux inventories.

These symmetries matter for future parameter identification: terminal traces
cannot always identify a unique ordered cage passport. Training/evaluation must
accept equivalent physical responses and must not penalize cage relabeling.

## Reproduce and verify

```bash
python -m benchmarks.multicage_drive_acceptance --out reports/multicage-comparison --python /path/to/python
python -m benchmarks.multicage_drive_acceptance --verify reports/multicage-comparison
```

The independent full-circuit worker requires NumPy/SciPy, not motulator. The
native package is needed only for the separate single-cage reduction tests.
Both DOP853 and Radau are required. Their disagreement must stay below 1% of
the existing physical-unit caps; primary budgets are 0.05 and 0.0125. Heat
comparisons retain 1e-5 J, event clocks 1e-8 s. The verifier checks all cages,
source identities, solver controls, every mandatory case and raw verdicts;
subset runs explicitly report incomplete declared inventory.

Границы: 1–4 Gamma-клетки, положительный Gc и объявленные короткие случаи.
Нулевой Gc в полном circuit, charge-dynamic semiconductor physics, произвольные
многомашинные сети, точность конкретного двигателя и длительные температурные
режимы не квалифицированы этим корпусом. Новые SFT/GRPO-эпизоды и VLM-наблюдения
для него ещё требуется связать с публичным интерфейсом среды.
