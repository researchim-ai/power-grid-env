# PHY-21 / R06.21: dynamic network qualification

This inventory separates four coverage tracks. It does not assert that every
feature can be combined at the largest size. Parameters are authored synthetic
fixtures, not identified site passports.

## RMS/DAE scale

- `mesh_64_4_1`: 64 buses, 4 motors, 1 GENROU/TGOV1/SEXS generator.
- `mesh_256_8_2`: 256 buses, 8 motors, 2 generators.
- `mesh_512_64_16`: 512 buses, 64 double-cage motors, 16 generators.
- `mesh_2000_128_32`: 2000 buses, 128 double-cage motors, 32 generators.

Every study lasts **0.6 s**, with a group motor connection at 0.05 s, source
voltage 0.7 pu at 0.2 s, recovery at 0.3 s, a second 0.8 pu disturbance at
0.42 s and recovery at 0.48 s. The total ratings remain fixed across sizes.
These are transient windows, not complete acceleration/thermal endurance runs.
Both Radau and network DAE use analytic derivatives, CSC and direct network
solves; each is run twice in fresh processes. The reference independently
initializes and integrates physical SI winding/MNA equations and checks every
node, motor and generator. Existing 2e-5 pu/state, 100 W/var, 1e-6 s event and
1e-8 pu balance caps are retained. Sampling aligns independently located
controller boundaries only within the existing timing contract; the numerical
reference uses primary sampling clocks, not primary physical trajectories.

## Islands, failure cascades and transfer

- `islands_matched` and `islands_mismatch`: 45 s; meshed grid becomes three live
  islands, undergoes mechanical disturbances, reconnection attempts and another
  separation/reconnection cycle. The mismatch case includes rejected unsafe
  closing attempts. Independent SI phasor derivatives determine synchronism.
- `automation_breaker_failure`: overcurrent, failed line breaker and timed
  upstream backup action.
- `automation_standby_transfer`: break-before-make transfer, including a blocked
  attempt to parallel the incoming source after reserve connection.
- `automation_failed_primary_transfer`: failure to open the primary breaker
  prevents reserve closure.

Both integrators run twice. Each case also runs with reversed equipment,
passport and command lists to test order independence against the same physical
reference. The island cases use two classical generators and disconnected motor
states; induction starts while islanded are not part of this profile. The short
protection/transfer cases last 0.6 s. This is balanced RMS protection, not arc,
instantaneous-current or communication-latency qualification.

## Unbalanced phase RLC

`phase_12`, `phase_48` and `phase_96` contain 12/48/96 independent RLC branches
under three unequal stiff phase sources. There are 3 additional prescribed
source nodes and 24/96/192 physical electrical states. Every load resistance
changes at 6, 12 and 16 ms; the horizon is 20 ms. Exact independent 4x4 matrix
exponentials propagate each branch's current, capacitor voltage and sinusoidal
forcing. Checks retain both sides of every change, all current/voltage channels,
energy balance and KCL. This is not a general meshed or mutually coupled phase
network capacity claim.

The phase schema now accepts up to 512 commands; the 96-branch case needs 288.
The initial 256-command rejection is retained as negative evidence. Existing
sample/step/output budgets and rejection of ambiguous same-clock commands remain
in force. This changes input capacity, not the electrical equations or tolerances.

## Run individual cases

```bash
python -m power_grid_env.cli run-study cases/network-dynamic/islands_mismatch.json --out reports/islands-mismatch.json
python -m power_grid_env.cli phase-study run --study cases/network-dynamic/phase_48.json --out reports/phase48-run
```

RMS JSON files are ordinary `StudySpec` inputs; phase files are `PhaseStudy`
inputs. The installed numerical package can run them without importing the
benchmark modules. Use a new output directory for each artifact-producing run.

## Reproduce the whole matrix

```bash
python -m benchmarks.network_dynamic_qualification --out reports/network-dynamic
python -m benchmarks.network_dynamic_qualification --verify reports/network-dynamic
```

The full inventory has 12 cases, 47 primary runs (including five order-reversal
runs) and 21 independently calculated reference runs. `--case` selects a subset,
which explicitly reports incomplete declared inventory. Failures and timeouts
remain failures; an incomplete trace cannot pass. Verification recomputes
numerical checks, repeats, resource summaries and producer/input identities.
The manifest checks integrity, not authenticated external provenance.

Each measured solve uses a fresh process, one BLAS thread and one available CPU.
`wall_s`/`cpu_s` cover case construction and solving, excluding process bootstrap,
global imports and trace serialization. Linux **current-exec VmHWM** includes
imports, construction, solving and trace serialization; it is not incremental
solver memory. Lifetime `ru_maxrss` is recorded separately. Medians use two
repeats; host load and CPU frequency are not controlled. Recorded times are not
a superiority ranking against other solvers. Verifying a transferred sealed
source bundle uses `python -B -I` to avoid creating bytecode inside the manifest.

Далее остаются длительный пуск на больших схемах, произвольные комбинации
регуляторов/защит/островных двигателей, расширенная несимметричная динамика и
подбор физического оборудования. Успех этой матрицы не закрывает эти задачи.
