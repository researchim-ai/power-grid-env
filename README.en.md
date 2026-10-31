# power-grid-env

[Русский](README.md)

![Industrial substation and plant with a neural-network graph over the equipment](docs/assets/power-grid-env-banner.jpg)

An engineering testbed for distribution-grid control algorithms. The first release is a quasi-static unbalanced feeder: import, preflight, an external controller, a restoration suite, comparison with a baseline, a report, and replay.

The simulator runs locally. A cluster, Kubernetes, and control of a real network are outside this project.

A simulation run is not permission to operate a real network, a real-time guarantee, or a full EMT study.

## What is numerically checked, and what is only implemented

Statuses live in `power_grid_env/capabilities.py`.

Checked numerically on authored cases, without field recordings:

- phase-domain power flow through OpenDSSDirect / DSS-Extensions: lines, `Dyn11` and `YNd11` transformers, ZIP loads, faults, an island with no source, and Kron reduction only when the neutral is explicitly solidly grounded;
- DSS import of the 110/10/0.4 kV plant: two infeeds, LineCode, capacitor, generator, storage, PV, a load profile, and Redirect;
- the positive-sequence ladder of that plant matches phase-domain OpenDSS to better than 1e-5 pu in magnitude;
- symmetric Newton AC power flow and a local AC OPF with a branch-flow limit;
- a classical machine, two machines, a first-order field voltage, and governor droop;
- RL/RLC transients and a dt versus dt/2 step check;
- DC power flow, PTDF, and linear WLS on small cases.

Implemented and exercised by scenarios, without a `validated_for_declared_scope` claim: restoration, interlocks, Gymnasium, LLM tools, a VLM canvas, and a local AC OPF. Distance and differential protection, full EMT, and hardware-in-the-loop stay outside this release.

Requirements: [specification](AGENT_IMPLEMENTATION_SPEC_RU.md) and [architecture plan](IMPLEMENTATION_PLAN_RU.md), both in Russian. If they disagree, the specification wins. Equations and assumptions: [docs/physics](docs/physics/conventions.md).

## Install

```bash
pip install -e ".[dev]"
```

Python 3.11+ and the `dss-python` / `opendssdirect.py` wheels are required.

## Commands

```bash
power-grid import --source cases/acceptance/feeder_north/feeder.dss \
  --sidecar cases/acceptance/feeder_north/pge-import.yaml \
  --out /tmp/feeder.json --report /tmp/import.json

power-grid preflight /tmp/feeder.json --report /tmp/preflight.json

power-grid suite cases/suites/restoration_v1.yaml --out reports/acceptance

power-grid validate
power-grid accept
power-grid schemas --out /tmp/pge-schemas
power-grid pilot cases/industrial/plant --out /tmp/pilot.json
```

`validate` writes `reports/validation`. `accept` repeats the section 16–20 acceptance path: an external DSS case, rejection of an incomplete file, subprocess controllers, the suite, HTML, checksums, the viewer, replay, numerical checks, and a short Gymnasium rollout.

An external controller is a separate process speaking JSON lines (`initialize`, `reset`, `act`, `close`). Examples live in `examples/controllers`. The subprocess does not isolate the filesystem or the network of untrusted code.

## Model

Canonical JSON does not invent grounding or a vector group. DSS import requires a sidecar; otherwise a wye winding is not treated as grounded, and `r1/x1` are not turned into a phase-impedance matrix. The format choice is recorded in [ADR 0002](docs/adr/0002-import-format.md).

In the restoration task the damaged section is known to the algorithm. Fault impedance, the future load profile, and the scenario's hidden marker stay out of the observation.

## Durable studies

The shared runner returns full phase-domain PF results and executes hold-policy QSTS with a streamed engineering journal, checkpoints and verified continuation. See [runtime guide (Russian)](docs/DURABLE_STUDIES_RU.md). This does not qualify arbitrary RMS models, external feeders, or field accuracy.

```bash
python -m power_grid_env.cli run-study cases/studies/qsts_runtime.json --out /tmp/pge-study
```

Saved balanced networks can now run classical RMS through `rms/andes`: [model scope, units and limitations (Russian)](docs/CANONICAL_RMS_RU.md).

Multivoltage RMS, PV refinement and clock-5 mapping are now covered by additional regressions: [validation and scale limits (Russian)](docs/MULTIVOLTAGE_AND_SCALE_RU.md).

Canonical RMS now includes explicit TGOV1/SEXS/IEEEST passports, finite bus faults with clearance, and a killable worker: [execution, independent references and limits (Russian)](docs/REGULATED_RMS_AND_FAULTS_RU.md).

Six-state GENROU with explicit saturation modes is supported by the shared RMS adapter: [passports, independent references and examples (Russian)](docs/GENROU_RMS_RU.md).

GENCLS/GENROU small-signal analysis now exposes the reduced DAE matrix, modes and state participation, checked against independent references. Dense analysis is limited to 8 machines/256 states; this is local linear stability, without industrial qualification. [Details (Russian)](docs/SMALL_SIGNAL_RU.md).

Network-coupled induction motor starts: single-cage third-order dynamics, voltage sags, contactor coast/reclose and staggered group starts. Checked against an independent SI rotor-flux solver; stator transients, saturation, VFDs and joint synchronous-machine dynamics remain outside this model. [Passports, examples and verification (Russian)](docs/INDUCTION_MOTOR_RU.md).

Added motor current-based thermal memory, continuous threshold trips, latched reclose blocking and strict SI CSV comparison for current/voltage/speed. Synthetic independent references exercise the workflow; OEM relay and field qualification remain open. [Model and examples (Russian)](docs/MOTOR_THERMAL_AND_VALIDATION_RU.md).

Added coupled loaded motor operating-point initialization, viscous/quadratic/smooth-friction shaft loads and explicit load-torque steps. Initialization checks the original ODE residual and local machine stability. [Independent references and scope (Russian)](docs/MOTOR_OPERATING_POINT_RU.md).

Added reproducible motor parameter experiments: OAT/Cartesian points, independent initialization, fixed start/recovery criteria, deadlines and hashed artifacts. Results describe declared finite samples, without interval-wide or field qualification. [Protocol and scope (Russian)](docs/MOTOR_SENSITIVITY_RU.md).

Added unified classical-generator/induction-motor dynamics with one shared nodal solve, joint initialization and motor thermal trips. Independent SI references and an ANDES generator-only limit check cover the declared subset; islanding remains a separate extension. [Scope and verification (Russian)](docs/COUPLED_GENERATOR_MOTOR_RU.md).

Optional TGOV1 governors and SEXS excitation now share the GENCLS/motor solve, with limiter recovery, reference steps and independent SI/ANDES checks. [Scope and reproducible validation (Russian)](docs/REGULATED_COUPLED_RMS_RU.md).

The shared generator/motor solver now accepts six-state GENROU with armature losses, explicit saturation and optional TGOV1/SEXS, including mixed GENCLS/GENROU layouts. [Equations, eight independent references and scope (Russian)](docs/GENROU_COUPLED_RMS_RU.md).

Added finite balanced faults in the shared dynamic network and a `fault-clearing` runner with frozen durations, recovery criteria and hashed isolated-worker artifacts. [Seven independent SI references and scope (Russian)](docs/COUPLED_FAULTS_AND_CLEARING_RU.md). No critical-clearing-time certification is claimed.

Added joint IEEEST MODE=1 and `coupled_small_signal` for local motor/generator/controller modes, including unstable equilibrium reporting and independent SI/transfer-function checks. [Equations and scope (Russian)](docs/COUPLED_PSS_AND_MODES_RU.md).

Added both-end line opening/reclosing in the shared dynamic network, including charging, branch currents/powers and atomic fault clearing. Source-reachability checks reject unsupported islands. [Examples and independent validation (Russian)](docs/COUPLED_LINE_SWITCHING_RU.md).

Added `generator_open`: zero network injection while rotor/field/controllers continue, with machine-side excitation sensing. [Electrical-opening semantics, independent validation and limits (Russian)](docs/GENERATOR_ELECTRICAL_OPEN_RU.md).

The first industrial target is motor starting/restarting. `motor-bundle` seals inputs, replay criteria, runtime identity and optional SI trace comparison; a platform hash lock and clean-venv qualification runner are provided. [Delivery workflow and acceptance gates (Russian)](docs/MOTOR_PILOT_DELIVERY_RU.md). Industrial/field qualification remains open.

[External ANDES motor cross-check, analytic Jacobian and scaling protocol (Russian)](docs/MOTOR_EXTERNAL_VALIDATION_AND_SCALE_RU.md).

[Motor coast, residual terminal voltage and reclose diagnostics (Russian)](docs/MOTOR_COAST_AND_RECLOSE_RU.md).

[Voltage contactors and automatic restart for direct-on-line motors (Russian)](docs/MOTOR_CONTACTOR_AND_AUTOMATIC_RESTART_RU.md).

[Finite restart timing and sequence studies (Russian)](docs/MOTOR_RESTART_FINITE_STUDY_RU.md).

[Restart experiment integrity checks and full replay (Russian)](docs/MOTOR_RESTART_AUDIT_AND_REPLAY_RU.md).

The optional `double_cage` motor uses a physical two-rotor inductance matrix,
including mutual rotor leakage, with mixed motor groups and per-cage current/loss
outputs. It shares the RMS network, contactors, initialization and analytic motor
Jacobian. [Scope and independent checks (Russian)](docs/DOUBLE_CAGE_MOTOR_RU.md).
This remains a numerically checked model, without field or industrial qualification.
