# R06.38 — температурная обратная связь в RMS/DAE

Opt-in профиль `winding_thermal` добавляет физические температуры статора/ротора
в RMS single-cage двигатель. Температуры меняют Rs, Rr, Norton conductance,
токи и rotor-flux rates **внутри каждого шага**, включая неявные итерации.
По умолчанию прежние fixed-R модели не меняются; отсутствие нового поля
сохраняет прежний canonical JSON. Это отдельная численная квалификация,
не OEM точность и не широкая промышленная сертификация.

## Использование

```bash
python -m power_grid_env.cli run-study \
  cases/rms-winding/primary-uv-loaded-T90-70-feedback.json \
  --out reports/my-rms-hot-fault
```

В `coupled.motors[]`:

```json
{
  "winding_thermal": {
    "reference_c": 20.0,
    "ambient_c": 20.0,
    "alpha_per_c": 0.00393,
    "initial_temperature_c": [90.0, 70.0],
    "stator_capacity_j_per_k": 3000.0,
    "rotor_capacity_j_per_k": 2000.0,
    "stator_ambient_w_per_k": 10.0,
    "rotor_ambient_w_per_k": 8.0,
    "cross_w_per_k": 2.0,
    "maximum_c": 180.0,
    "provenance": "owner://my-study/thermal-passport"
  }
}
```

Существующие `stator_r_pu` и `rotor_r_pu` задаются при **reference_c**.
Порядок температур — статор/ротор; без explicit temperatures используется
ambient. Сопротивления: `R(T)=Rref·[1+alpha·(T−Tref)]`.
Нужны single-cage motors, `jacobian_network: direct_solve`; генераторы пока явно отвергаются. Two-cage/common-rotor-T и mixed single+two-cage
теперь проверены отдельно в [R06.40](../two-cage-winding/README.md). Физическая тепловая модель
не заменяет независимую I²-релейную реплику `thermal`: её уровень и trip_level
не являются градусами Цельсия и не калибруются по winding temperatures автоматически.

Полезные каналы: `stator_temperature_c`, `rotor_temperature_c`,
`stator_resistance_pu`, `rotor_resistance_pu`, `stator_copper_loss_w`,
`rotor_copper_loss_w`, `stator_net_heat_w`, `rotor_net_heat_w`.
Потери в3фазном own-base RMS: `Pcu=Srated·Rpu·|Ipu|²`.
Cross/cooling flows входят с противоположными знаками в двух тепловых узлах.
При открытии двигателя rotor flux/temperature не обнуляются; остаточный rotor
current продолжает давать потери и нагрев. Насыщение/iron loss здесь не включены.

## Что изменено в решателе

Сетевая невязка теперь допускает `g(x,V)=Y(x)·V−b(x)`. Её аналитическое действие:
`dg=Y·dV+Y_x·dx·V−b_x·dx`; эквивалентная инъекция для Schur —
`C·dx=(b_x−Y_x·V)·dx`. Это существенно для температуры статора.
Сохранены CSC/column actions и локальные sparse blocks; полная dense state-square
матрица для DAE/GMRES не требуется. Производные rotor resistance и тепловых
потерь включены отдельно. Matrix cache не меняет дискретный mode и корректно
пересчитывается при temperature rollback и изменении топологии.

Загруженная инициализация ищет **электрическое/механическое равновесие при
фиксированных исходных Ts/Tr**. Для DAE начальная производная температур
согласована с тепловыми ODE; она обычно не равна нулю. В отчёте есть
`winding_initial_derivative_c_per_s` и explicit frozen-temperature stability
scope. `operating_point.thermal_state=equilibrium` по-прежнему относится к
I² replica, а не к физическим Ts/Tr. В этом fixed-initial-T профиле полное thermal equilibrium не решается.
Отдельный opt-in `winding_state: equilibrium` с полным physical spectrum
теперь доступен в [R06.39](../rms-equilibrium/README.md).

`maximum_c` — предел применимости, **не температурная защита**. Выход за него
не клиппируется. Если invalid trial возникает в solve_ivp, возвращается
`completed:false`, `status:numerically_not_converged` и точный
`solver_message:RMS winding temperature outside passport validity`.
Внешний validity guard также может вернуть `model_validity_exceeded`.
Время такого отказа не является локализованным физическим trip time.

## Независимая проверка

**32 физических случая /128 native runs /64 independent trajectories**:

- primary/CB failure/CT loss и matched no-UV;
- cold zero-flux start и loaded electrical equilibrium;
- initial Ts/Tr20/20°C и90/70°C;
- R(T) и frozen-at-initial-R control;
- Radau/DAE, output meshes2/1ms, независимый SI winding/KCL DOP853 с2/1ms
  max step и отдельным refinement.

Frozen controls сохраняют исходные Rs/Rr, потокосцепления, токи, температуры,
скорости, схему и защитные уставки; меняется последующая R(T)-связь.
Наблюдавшаяся максимальная разница current magnitude≈0.000765–0.001321pu
в выбранных случаях. Это результат authored эксперимента, не универсальное
улучшение точности или вывод о реальном двигателе.

Эталон самостоятельно рассчитывает SI stator/rotor winding equations,
температуры, OC clocks, breaker failure/backup и UV действия. Native токи,
init/RHS, события и temperature helpers для его продвижения не используются;
запрет вызовов проверяется тестом. Проверяются последовательность/outcomes/
время событий, односторонние samples, физические каналы и тепловые потери.
Caps:200ns events,2e-6 speed/EMF/voltage/temperature,5e-6 current/clock/replica,
1mA для амперных каналов,3mW для heat-power. Regular time остаётся абсолютным;
совпавшие event samples сопоставляются по independently found roots.
Это критерии сравнения реализаций, не точность относительно OEM.

```bash
python -m benchmarks.rms_winding_acceptance \
  --out reports/my-rms-winding --workers 4
python -m benchmarks.rms_winding_verify \
  --source reports/my-rms-winding \
  --out reports/my-rms-winding-accepted --workers 4
python -m pytest tests/test_rms_winding.py -q
```

Offline verifier заново выполняет все64 независимых интегрирования и
пересчитывает owner/network/settings bindings, numerical/refinement gates,
состав файлов и frozen-control effects. Основной final корпус:
`reports/rms-winding-final-qualified-2026-11-15`; receipt:
`reports/rms-winding-final-accepted-2026-11-15`.

Итог: **769 unique targeted checks прошли**:747 non-RMS regression cases
и22 final RMS tests. Broad XML сохраняет2 первоначальных неверных assertions
о названии failure envelope; они исправлены под documented numerical-failure
контракт с проверкой exact model-validity cause, затем весь22-case класс
прошёл повторно. Production code между этими final runs не менялся; raw XML
сохранены, receipt сверяет node IDs и явную supersession.4 optional ANDES
checks исключены; это не полный repository suite.

Регрессии включают прежние motor/protection/phase workflows, DAE capacity,
real/complex Schur actions, temperature J·v/augmented residual при inconsistent V,
несколько двигателей, rollback и отказы validity. Итоги и XML привязаны в
`reports/rms-winding-delivery-2026-11-15/delivery.json`.
Промежуточные диагностические XML и предыдущие корпуса не перезаписываются.

Новый wheel проверяется вне checkout с Python-I/import guard:4 новых и4 legacy
RMS/phase replay. Host/dependencies общие; это не OS sandbox или независимый
host. Предыдущие sealed корпуса сохраняют прежние wheel/fingerprints; выбранные
legacy replays не переквалифицируют их целиком. Hashes — integrity, не authenticity.

Открыто: double/multicage RMS с раздельными thermal nodes, смешанные генераторы,
расширение стационарного electrothermal equilibrium на эти семейства, temperature-trip coordination,
широкие topology/uncertainty matrices, идентификация RC/material coefficients
по внешним данным. Значения RC, начальные температуры и shared copper alpha
здесь authored; алюминиевый ротор не подтверждён этим паспортом. Нет полного
EMT energy balance из алгебраического RMS и доказанного continuous peak.
