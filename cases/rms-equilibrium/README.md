# R06.39 — совместное электрическое, механическое и тепловое равновесие

Новый opt-in режим ищет одновременно скорости, ЭДС, напряжения/токи сети и
температуры двух узлов обмоток. В отличие от R06.38 с заданными исходными Ts/Tr,
здесь начальные winding heat balances действительно равны нулю. Режим работает
в общем single-cage RMS/DAE, сохраняет динамику R(T) после возмущения.

```bash
python -m power_grid_env.cli run-study \
  cases/rms-equilibrium/primary-uv.json \
  --out reports/my-stationary-fault.json
```

В существующей секции `coupled` укажите:

```json
{
  "operating_point": {
    "method": "high_speed_equilibrium",
    "minimum_speed_pu": 0.8,
    "thermal_state": "equilibrium",
    "winding_state": "equilibrium"
  }
}
```

Каждый мотор должен иметь `winding_thermal`; single-cage/motor-only,
`jacobian_network: direct_solve`, все моторы подключены. Новый solver ограничен
1–32 моторами; тесты размеров1/2/4 не являются capacity qualification всех32.
См.[R06.40](../two-cage-winding/README.md):two-cage/common-rotor-T и mixed
single+two-cage группы теперь поддержаны. Этот исходный R06.39 корпус остаётся
single-cage. Полный stationary mode отвергает генераторы и disconnected
initial motors и вырожденную матрицу теплоотвода. Нужна положительно определённая
пассивная двухузловая heat-removal matrix. Если I² replica `thermal` включена,
нужно `thermal_state: equilibrium`; без неё такое условие не требуется.

`initial_temperature_c` теперь **начальное приближение поиска**, а не
навязанная температура. Два seed20/20 и90/70°C сходятся к одному выбранному
режиму в зарегистрированной матрице. Если нужны заданные измеренные горячие
температуры с последующим нагревом/охлаждением, оставьте прежний
`winding_state: passport` — это default, который не добавляется в canonical JSON.

## Численный метод и проверяемый результат

Статор/ротор и сеть исключаются стационарно при пробных speed/Ts/Tr; решается
bounded reduced system из3n неизвестных. Применяются четыре стартовые скорости,
аналитический reduced Jacobian с производными R(T), steady motor response и
сетевого напряжения. Нулевой механический load имеет точную synchronous
boundary omega=1; температурная boundary ротора без cross flow учтена отдельно.

Тепловой поиск использует **небаланс мощности**, нормированный на rated VA.
После решения каждый узел отдельно проверяется в ваттах:
`|heat imbalance| <= max(1e-7 W,1e-10·Srated)`.
Это защищает от ложного equilibrium при огромной heat capacity, когда dT/dt
мал лишь из-за большого C. Увеличение Cs/Cr в1e9 раз проверено отдельным тестом:
стационарная точка сохраняется, меняются временные масштабы.

Принятие требует original physical ODE residual, KCL, совпадения статического
и динамического напряжения, допустимых температур и I² relay limit. Полный
physical Jacobian включает E/speed/Ts/Tr и I² replica, если она есть. Для
обычного временного запуска выбранная точка должна иметь отрицательную
spectral abscissa. Это fixed-topology linearization, **не hybrid/global stability**
защит, контакторов и будущих коммутаций. Спектр вычисляется плотным небольшим
physical block только при initialization, не подменяет sparse time-step solver.

Отчёт `operating_point` содержит:

- `mode: motor_only_electrothermal_equilibrium`;
- `motor_initial` с найденными скоростями, токами и температурами;
- `stationary_heat_balance_error_w` и `maximum_stationary_heat_error_w`;
- `checked_dynamic_residual_per_s`, `network_kcl_residual_pu`;
- `physical_eigenvalues_per_s`, `full_physical_spectral_abscissa_per_s`;
- границы local search и описание stationary/stability scope.

`no_qualified_electrothermal_equilibrium` означает, что bounded multi-start
search не нашёл допустимый режим. Это не доказательство отсутствия любого
steady state вне заявленного speed/temperature диапазона и не global uniqueness.
Недостаточное охлаждение/temperature ceiling, чрезмерный load, отсутствующий
thermal passport и неверная replica policy получают диагностику **до интеграции**.

## Независимая квалификация

**20 физических сценариев,40 seed configurations,144 completed native runs**:
четыре cable-temperature/source-voltage endpoints, primary/CB-failure/CT-loss/
no-UV faults и пятисекундные holds; Radau/DAE. Для faults две output meshes,
для holds одна. Плюс **12 initial rejections** по шести причинам/двум движкам.
В исходных RC/copper alpha есть явное owner provenance; это не OEM thermal fits.

**80 independent trajectories**: отдельные SI T-circuit/heat roots с numerical
Jacobian, затем all-flux winding/KCL/heat/relay/actuator DOP853. Ни native
initializer, ни native thermal/network/tangent helpers для продвижения эталона
не используются — запрет проверен тестом. Нативный analytic physical spectrum
сопоставлен с независимым6-state finite-difference SI spectrum. Проверяется
начальная точка, события, переходные процессы, heat power и отсутствие hold drift.

Offline verifier заново выполняет все80 independent integrations и12 native
initial refusals; пересчитывает288 comparisons, root/spectrum/seed/hold gates,
owner bindings и manifests. Дополнительно из сохранённых физических измерений
проверяет stationary motor active-power balance и отведение copper heat в ambient.
Это steady power audit; полного transient EMT energy balance из RMS не заявляем.

```bash
python -m benchmarks.rms_equilibrium_acceptance \
  --out reports/my-stationary-matrix --workers 4
python -m benchmarks.rms_equilibrium_verify \
  --source reports/my-stationary-matrix \
  --out reports/my-stationary-accepted --workers 4
python -m pytest tests/test_rms_equilibrium.py -q
```

Final evidence с датой2026-11-15: `reports/rms-equilibrium-power-qualified-*`,
`reports/rms-equilibrium-power-accepted-*`,
`reports/rms-equilibrium-power-installed-replay-*` и
`reports/rms-equilibrium-delivery-*/delivery.json`.
Интермедиатные корпуса до final watt-scaled acceptance сохраняют собственные
fingerprints и не подменяют final evidence.

**791 targeted tests прошли**,0 failures/errors/skips;4 optional ANDES cases
явно исключены из selection. Это не полный repository suite.

Новый wheel replay вне checkout проверяет4 новых completed cases,2 ожидаемых
initial rejections и2 прежних RMS/phase cases. Host/dependencies общие;
Python-I/import guard не OS sandbox или another-host qualification.
Предыдущие большие корпуса сохраняют прежние wheel/fingerprints; выбранные
legacy replays не переквалифицируют их целиком. Hashes — integrity, не authenticity.

Открыто: mixed generators,3+ cages и раздельные rotor thermal nodes,
temperature-trip/selectivity, широкая топология/неопределённости и идентификация
теплопроводностей/материалов по независимым данным. R(T)/RC остаются lumped
assumptions, температуры не являются certified hotspots. Нет универсального
превосходства над другими моделерами или OEM qualification.
