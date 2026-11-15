# R06.40 — двухклеточный RMS с R(T) и теплом обеих клеток

Профиль расширяет существующий physical two-parallel-cage двигатель: теперь
R(T), динамика, стационарный расчёт и аналитические DAE actions учитывают обе
клетки. Их токи и медные потери рассчитываются отдельно; тепло поступает в
**общий rotor thermal node**. Это не независимые температуры/hotspots клеток.

```bash
python -m power_grid_env.cli run-study \
  cases/two-cage-winding/equilibrium-primary.json \
  --out reports/my-two-cage-fault.json
```

В `coupled.motors[]` доступны прежние electrical fields:

```json
{
  "model": "double_cage",
  "rotor_r_pu": 0.015,
  "rotor_x_pu": 0.1,
  "rotor2_r_pu": 0.06,
  "rotor2_x_pu": 0.03,
  "rotor_mutual_x_pu": 0.02
}
```

Числа — authored scenario, не идентифицированный паспорт. Добавьте
`winding_thermal` по примеру JSON: Cs/Cr, cooling/cross conductance, reference/
ambient/initial temperatures, alpha, maximum_c и обязательное provenance.
Оба rotor resistances используют общие Tr и alpha; разные reference R1/R2
сохраняются. Применимость limited to single/two-cage motor-only/direct_solve.
Смешанная группа single+two-cage поддержана и проверена unit tests.

Режимы:

- `operating_point: null`: cold zero-flux start; начальные температуры задаются
  thermal passport, поэтому «cold» означает electrical initialization;
- `winding_state: passport`: loaded electrical equilibrium при заданных Ts/Tr;
  последующие thermal derivatives могут быть ненулевыми;
- `winding_state: equilibrium` вместе с `thermal_state: equilibrium`: совместное
  speed/EMF/network/Ts/Tr/I² equilibrium с watt-based heat gate и physical spectrum.

Для stationary режима действуют [ограничения R06.39](../rms-equilibrium/README.md):
connected thermal motors, passive heat removal, bounded high-speed search;
1–32 — implementation budget, не capacity qualification всех размеров.
При отсутствии I² relay replica её equilibrium policy не требуется.

## Уравнения и диагностические каналы

Прежние rotor coordinates сохранены: `E=u1+u2` и auxiliary `u1`. При common Tr
`A(T)=f(T)·Aref`, `B(T)=f(T)·Bref`; slip rotation остаётся общей для обоих
потокосцеплений. Native time stepping использует state-dependent stator Norton
stamp и analytical rotor/copper/temperature derivatives. Stationary elimination
использует полную2×2 rotor matrix, а не эквивалент одной клетки. Сохранены
CSC/column actions, real/complex Schur blocks и DAE residual products.

Новые `rotor1_resistance_pu`, `rotor2_resistance_pu`; существующие per-cage
currents/EMF/losses теперь согласованы с R(T). `rotor_copper_loss_w` — сумма
потерь обеих клеток, которая входит в rotor heat equation.
`rotor_resistance_pu` сохраняется как **alias R1**, не effective total resistance.
Cage-loss channels записываются один раз на sample, без duplicate axis entries.
Rotor fluxes и residual copper losses не обнуляются при отключении статора.

Отдельные клетки дают отдельные electrical modes; стационарный physical
spectrum включает8 states при наличии I² replica и7 без неё. Это fixed-topology
linearized certificate, не hybrid/global stability защит и контакторов.
Нет stator EMT, насыщения или произвольного continuous skin-effect law.

## Проверенный корпус

**33 authored scenarios /120 completed native runs /66 independent trajectories**:

- cold / fixed-hot / full stationary;
- rotor mutual reactance0/.02pu;
- hold / primary / breaker failure / CT loss / primary no-UV;
- Radau и DAE,2 output grids для fault cases и1 для holds;
-3 дополнительных cage-label permutation controls.

Ещё **12 expected initial rejections**,6 причин×2 engines:cooling singularity,
ceiling,excess load,replica-policy mismatch,disconnected/missing thermal passport.
Stationary holds5s проверяются без дрейфа. No-UV controls численно завершены,
но мотор остаётся подключённым: они **не получают engineering approval**.
Не переносим qualification исходного single-cage engineering package на новые
уставки/оборудование автоматически.

Эталон независимо решает две физические SI rotor-flux ODE и5-row winding/KCL
system, сам находит initial T-circuit/heat root и исполняет relay/breaker/UV events.
Native cage coefficients/init/tangents не используются; запрет проверен тестом.
Вторая reference trajectory использует меньший max step/допуски. Проверяются
terminal/cage/heat channels, event identities/times/sides, stationary power,
8-state native-vs-SI spectra и cage-loss sum.240 comparisons пересчитываются
offline verifier вместе со всеми66 references и12 native refusals.

Важные предельные проверки:

- Перестановка cage labels сохраняет terminal/thermal traces и events,
  а individual cage EMF/current/resistance/loss channels правильно меняются местами.
- Две одинаковые клетки при нулевой дополнительной mutual leakage точно
  сводятся к single cage с Rr/2 и Xr/2; transient fault+heat limit проверен.
- Reduced stationary Jacobian и augmented DAE tangent проверены finite differences,
  включая inconsistent supplied voltage и complex Schur actions.

```bash
python -m benchmarks.two_cage_winding_acceptance \
  --out reports/my-two-cage-matrix --workers 4
python -m benchmarks.two_cage_winding_verify \
  --source reports/my-two-cage-matrix \
  --out reports/my-two-cage-accepted --workers 4
python -m pytest tests/test_two_cage_winding.py -q
```

Final evidence2026-11-15:
`reports/two-cage-winding-final-qualified-*`,
`reports/two-cage-winding-accepted-*`,
`reports/two-cage-winding-installed-replay-*`,
`reports/two-cage-winding-delivery-*/delivery.json`.
**841 targeted tests прошли**,0 failures/errors/skips;4 optional ANDES checks
явно deselected. Это не весь repository suite. Installed-wheel checks:
4 новых completed,2 expected initial refusals и2 legacy RMS/phase. Host/dependencies
общие;Python-I/import guard не OS sandbox или independent host.

Также закрыт silent-loss путь RMS→Gamma: для поддерживаемого single-cage export
потеря `winding_thermal` теперь требует exact omission acknowledgement.
Two-cage export в single-cage Gamma по-прежнему явно отвергается.

Открыто: mixed synchronous generators с R(T),3+ rotor cages в RMS,separate cage
thermal nodes/material coefficients,OEM calibration,temperature-trip/selectivity
и широкие topology/uncertainty studies. Фазный full-winding multicage solver —
другой профиль; его coverage не переносится сюда. Предыдущие результаты сохраняют
старые wheel/fingerprints; выборочные legacy replays не переквалифицируют их целиком.
Hashes подтверждают integrity, не authenticity. Универсальное превосходство и
manufacturer temperature accuracy не заявляются.
