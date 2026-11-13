# Coupled electrothermal drive profile / Электротепловой привод

Seven authored synthetic studies for the PHY-19 / R06.19 profile. Each JSON
is executable with the installed package, without benchmark or motulator imports:

```bash
python -m power_grid_env.cli switching-drive run --study cases/electrothermal-drive/rectifier_trip.json --out reports/rectifier-trip-run
```

The output directory must be new. Inputs use SI units, instantaneous alpha-beta
fluxes and a positive-core-conductance single-cage Gamma machine. The explicit
nominal observer differs from the saturated thermal plant by design.

- `warm_device_losses`: warm electronics, device R(T), conduction and switching losses.
- `switching_trip`: inverter latch triggered by the discrete switching-energy jump.
- `conduction_trip`: inverter latch triggered by continuous conduction heating.
- `winding_trip`: stator latch with winding heating, winding R(T) and core losses.
- `brake_trip`: active brake followed by its thermal latch.
- `rectifier_trip`: low initial DC voltage, charging and rectifier thermal latch.
- `lossy_block_recovery`: 12 ms trajectory with blocked gates at 4–6 ms and recovery.

The other trajectories last 4 ms. Small heat capacities and tight temperature
thresholds intentionally accelerate events. They are numerical fixtures, not
manufacturer-fitted modules, relay settings or measured site passports.

## Equations and protection meaning

Device channel and diode resistance is R25 × [1 + alpha × (T − 25°C)]. Winding
resistance uses its separately declared reference temperature and coefficient.
Each electronics lump follows C × dT/dt = P_loss − G × (T − T_ambient).

Switching energy uses |I|/I_ref × Vdc/V_ref and the declared temperature factor:
turn-off uses the pre-switch terminal current; turn-on uses the prospective
post-switch current at unchanged flux and pre-jump rails. The energy is
transferred from effective differential DC capacitance Cdc + Cearth/2 into the
inverter thermal lump. Rail common mode, magnetic flux and shaft speed are not
reset; switching heat must not also be counted as external energy loss.

Any thermal latch blocks the controlled inverter gates. **It does not open an
upstream breaker or disconnect the passive rectifier/body diodes.** The brake's
own latch removes its resistive branch. Latches do not reset after cooling.
Modeling a source contactor or protective breaker requires a separate topology
action; these fixtures do not imply such an action.

## Independent comparison

```bash
python -m benchmarks.electrothermal_drive_acceptance --out reports/electrothermal-comparison --python /path/to/native-environment/bin/python --wheel /path/to/motulator-0.23.1-py3-none-any.whl
python -m benchmarks.electrothermal_drive_acceptance --verify reports/electrothermal-comparison --wheel /path/to/motulator-0.23.1-py3-none-any.whl
```

The native environment must already contain the exact unmodified motulator
0.23.1 wheel pinned by SHA256 in the reference. Production imports are blocked
inside reference workers. The circuit, controls, thermal equations, hybrid roots
and switching-energy jumps are expressed separately from production; the external
machine RHS is not replaced. DOP853 and Radau must agree within 1% of the existing
physical-unit channel caps; primary trajectories use budgets 0.05 and 0.0125.
Heat comparisons use the existing extended-reference 1e-5 J cap. Event clocks
use the existing 1e-8 s cap. The verifier recomputes verdicts from raw traces and
checks input, solver controls, native sources, current producer and manifests.

Это ограниченная независимая проверка уравнений и численного решения, а не
натурная валидация. Зарядовая динамика диодов, avalanche, EMI, произвольные
многоклеточные машины, точность OEM и масштабирование больших сетей не входят
в этот профиль. Успех этих семи исследований не переносится автоматически на
другие параметры или сочетания оборудования.
