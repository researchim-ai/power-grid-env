# Нагруженная motor-only сеть: согласованная инициализация и динамика

## Новая возможность численного ядра

В `coupled_rms` теперь можно указать `machines: []` и явный
`operating_point`, не добавляя фиктивный синхронный генератор. Рабочие
скорости, внутренние ЭДС машин и узловые напряжения решаются совместно;
подтверждаются исходный RHS, KCL, согласие статического и динамического
решения сети и локальная устойчивость подключённого электромеханического
подмножества. Введён отдельный mode `motor_only_loaded_equilibrium`.

```json
"operating_point": {
  "method": "high_speed_equilibrium",
  "minimum_speed_pu": 0.8,
  "tolerance": 1e-8,
  "thermal_state": "equilibrium"
}
```

Начальная скорость не задаётся эвристикой и не копируется из прогретой
траектории. Используется ограниченный поиск высокоскоростной ветви.
Отказ поиска не доказывает глобальное отсутствие любого физического режима.
Если подключённых двигателей нет, выдаётся явная диагностика до интеграции.

Тепловая политика обязательна для интерпретации результата:

- `equilibrium`: реплика каждого мотора начинается с `Ipu²`; при неизменной
  нагрузке нагрев стационарен. Если это уже превышает trip level, init отвергается.
- `passport`: сохраняется паспортное начальное значение; электрическая и
  механическая части стационарны, но тепловая реплика может изменяться.
  Её ненулевая производная публикуется и не называется тепловым равновесием.

`connected_motor_state_names` и eigenvalues относятся к подключённым E/speed
и cage states, а не всему гибридному процессу. Политика учитывает отключённых
членов группы и сохраняет их нулевое электрическое состояние. В отчёте
`initial_active_automation_relays` и
`network_automation_stationary_initialization` отдельно показывают состояние
сетевых clocks. Активная защита может намеренно начать отсчёт с t=0 при
согласованном физическом состоянии; это не сертификат устойчивости гибридной
логики. PT-контакторы и будущие события исключены из такого сертификата.

## Пример и запуск

Полные runnable studies лежат рядом: hold с двумя thermal policies,
провалы питания, изменение момента, выбег/повторное включение, КЗ на шине
двигателя/источника и стационарная группа четырёх double-cage моторов.
Из корня:

```bash
python -m power_grid_env.cli run-study \
  cases/motor-only-loaded/T90p0-V0p95-equilibrium-hold-network_dae-grid0.json \
  --out reports/my-loaded-hold.json --isolate --wall-timeout 60
```

Для реального сервиса используйте outer worker deadline (`--isolate`),
который покрывает поиск/сериализацию; in-process интеграционный deadline
сам по себе не покрывает всю инициализацию. Число residual evaluations
ограничено существующим budget. `fan_torque_pu` и `mechanical_load` —
взаимоисключающие паспорта; для явного нелинейного механизма удалите fan field,
а не передавайте одновременно `fan_torque_pu: 0`.

В `protected/` сохранены loaded primary/CB-failure/CT-loss и no-UV control:

```bash
python -m power_grid_env.running_fault_protection \
  --study cases/motor-only-loaded/protected/primary-uv.json \
  --requirements cases/motor-only-loaded/protected/primary-uv-requirements.json \
  --out reports/my-loaded-protected
```

Каталог должен отсутствовать. No-UV control намеренно получает exit2:
отключение сети не гарантирует прекращения подпитки КЗ ротором.

## Выполненная квалификация R06.35, 15 ноября 2026

Всего512 native launches, включая504 интегрированные траектории и8
отказов инициализации до интегрирования:

- 288 single-cage studies:4 endpoint conditions20/90°C и0.95/1.05U ×2 heat
 policies ×6 normal/programmed-fault modes ×Radau/DAE ×3 output meshes.
- 24 stationary group studies:2/4 мотора, single/double cage, оба решателя,
 три output meshes. Группы симметричны; нативная согласованность/стационарность, а не независимый
 multi-cage differential oracle.
- 8 отрицательных init checks: нет подключённого мотора, непосильная нагрузка
 в заданной ветви, минимум скорости выше найденной ветви, thermal limit;
 оба метода. Невозможность интегрирования не выдаётся за успешную инициализацию.
- 144 protected loaded studies:3 пути отключения ×4 network endpoints ×2
 heat policies ×2 solvers ×3 output meshes. Пределы150ms/0.1A/20ms сохранены;
 relay tolerance corners для этого нового loaded-профиля не заявляются.
- 48 matched primary no-UV failures: те же physics/criteria, отличается
 только контактор. Всего456 positive и56 negative outcomes,252 solver pairs.
 По192 protected/control traces рассчитаны по-портовые I²t и64 fine-pair checks.

Худшее погасание защищённого КЗ≈127.953ms при150ms deadline; после grid
isolation sampled motor-fed ток до145.02A. Это конечные номинальные уставки,
не автоматическое наследование cold-runup corner qualification R06.34.

## Независимая проверка физики

`motor_only_loaded_reference` самостоятельно находит корень механического
баланса полного T-эквивалента через bracketing/Brent, затем решает4 SI
winding/KCL equations для токов статора/ротора и напряжений двух шин.
Интегрирует физический rotor flux, speed иthermal replica через DOP853;
производные, constants, stamps и initial state production solver не вызываются.
Native EMF используется лишь для сравнения, не продвижения reference.

48 самостоятельных loaded trajectories и96 native fine-grid comparisons,
в том числе16 независимых programmed-fault trajectories, проверены вместе
с начальными eigenvalues. Fault shunts могут находиться на обеих шинах.
Динамика активных network relays/UV/thermal trips в этот oracle не входит:
guard явно отвергает их, а в этих independent cases pickup намеренно1e8A.
Защищённые144 случая используют conditional SI algebra, independent clock
replay иshared-physics two-solver agreement. Не смешивайте эти доказательства.

Максимальные ошибки: speed≈2.39e-11pu, EMF≈1.02e-9pu, current≈4.62e-9pu,
voltage≈1.22e-10pu, heat≈7.72e-11; fault current≈5.35e-8A;
eigenvalues≈3.02e-8/s. Это согласие математических реализаций, не измеренная
точность оборудования. Мотор/механизм и часть rated duties авторские;
нормативное/OEM одобрение иtraining reward не выдаются.

## Полное воспроизведение

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python -m benchmarks.motor_only_loaded_qualification \
  --protocol cases/motor-only-loaded/protocol.json \
  --out reports/my-loaded-physics --workers 8

python -m benchmarks.motor_only_loaded_acceptance \
  --protocol cases/motor-only-loaded/protocol.json \
  --source reports/my-loaded-physics --out reports/my-loaded-physics-accepted

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python -m benchmarks.motor_only_loaded_protection \
  --protocol cases/motor-only-loaded/protocol.json \
  --out reports/my-loaded-protection --workers 8

python -m benchmarks.motor_only_loaded_protected_acceptance \
  --protocol cases/motor-only-loaded/protocol.json \
  --source reports/my-loaded-protection \
  --core-source reports/my-loaded-physics \
  --core-accepted reports/my-loaded-physics-accepted \
  --out reports/my-loaded-accepted
```

Выходные каталоги должны отсутствовать. Index запечатывает ссылки на отдельно
bounded native/reference packets; составные корни не являются единым generic
artifact. Полные owner inputs, оригинальные уравнения начального состояния,
T-root,48 независимых траекторий, eigenmodes, clocks, actuators, solver pairs
иI²t повторно пересчитываются gate-ами. Hashes — целостность, не аутентичность.

466 целевых тестов прошли без ошибок/пропусков. Четыре ANDES-dependent
checks исключены из этого прогона из-за отсутствующего optional runtime;
первый диагностический XML с unavailable external case сохранён отдельно.
Это не полный repository suite. Старые cold/transient доказательства R06.34
привязаны к прежнему wheel, а не переименованы под новый fingerprint.

Открыто: большие неоднородные loaded-группы/топологии, расширение независимых active
protection trajectories на большие топологии/reclose, широкая R(T)/electrothermal qualification, несимметрия,
DC/peak/EMT иOEM подтверждение. Поиск локален; uniqueness/global/hybrid
stability не доказана. Source-transformer R/X фиксирован, conductor temperature
здесь задаёт только постоянное сопротивление кабеля.

Локальная поставка `reports/motor-only-loaded-delivery-2026-11-15` содержит
новый wheel, предыдущий pinned wheel для регрессий, source-boundary proof
(ровно2 изменённых runtime-модуля), все48 independent reference packets,
466-test XML, исходники и owner protocols. Семь new-wheel checks включают
шесть интегрированных trajectories (численные arrays/events совпали побитово)
иодин init rejection;56 negative outcomes основной серии не превращаются
в engineering approval. Шесть old-wheel cold/gen traces совпадают по осям/
событиям ив пределахroundoff1e-12 absolute+relative; наблюдавшийся max delta
1.8e-15. Первоначальный строгий bitwise probe сохранён как diagnostic.
Host и зависимости общие; import guard/Python-I не являются OS sandbox.
Основные512 launches привязаны к sibling indexes, а не продублированы целиком.


## R06.36 — независимая гибридная динамика защит

`benchmarks/loaded_protection_winding_reference.py` самостоятельно решает SI
уравнения статора/ротора и KCL. Начальное состояние берётся из собственного
T-circuit/Brent root. DOP853 интегрирует потокосцепление, скорость, thermal
replica и часы OC. Эталон сам ищет pickup/dropout/trip roots, обслуживает
opening delay, отказ выключателя и backup; local UV и thermal latch сами
отключают двигатель. Native currents/states/event times не используются
для продвижения эталона. Запрет native RHS/init/relay/contactor helpers
проверяется отдельным тестом.

- **32 физических endpoint-сценария**, каждый с двумя настройками точности
  эталона: **64 independent trajectories**, сравнение с64 ранее сохранёнными
  fine-grid native traces Radau/DAE (128 comparisons с refinement).
  Здесь VI,4 temperature/voltage endpoints,2 heat policies,primary/CB failure/
  CT loss плюс matched primary no-UV. Все48 native no-UV failures предыдущего
  этапа сохраняются как инженерные отрицательные результаты.
- **14 дополнительных trajectories /28 новых native runs**: четыре OC curves
  (definite,SI,VI,EI) ×primary/CB failure/CT loss; повторное КЗ с exponential
  clock reset/CT restoration; thermal trip. Это выбранный endpoint, а не
  полный Cartesian envelope новых curves/thermal settings.
- Итого **78 independent trajectories /92 distinct native comparisons**.
  Event identities/outcomes/order и времена проверяются отдельно (cap200ns).
  Только совпавшие one-sided event samples сопоставляются с соответствующим
  independently found event time; regular samples остаются на абсолютной
  шкале. Через скачок не интерполируем. Ошибка события не маскируется сдвигом
  всей траектории. Для endpoint-корпуса есть собственное refinement-сравнение.
- Сохранены токи КЗ, EMF/speed/thermal/current, OC progress/active/latched/
  validity/measurement, положение аппаратов и подключение двигателя.
  Numerical caps заданы заранее:2e-6pu для speed/EMF/voltage,5e-6 для heat/
  current/progress и1mA для амперных каналов. Это caps сравнения реализаций,
  не паспортная точность оборудования.

Воспроизведение из корня репозитория (каталоги назначения должны отсутствовать):

```bash
python -m benchmarks.loaded_protection_winding_acceptance \
  --protocol cases/motor-only-loaded/protocol.json \
  --source reports/motor-only-loaded-protected-2026-11-15 \
  --out reports/my-loaded-hybrid-matrix --workers 4
python -m benchmarks.loaded_protection_winding_extensions \
  --out reports/my-loaded-hybrid-extensions
python -m pytest tests/test_loaded_protection_winding.py -q
python -m benchmarks.loaded_protection_winding_delivery \
  --out reports/my-loaded-hybrid-delivery
```

Последняя команда использует зарегистрированные final-корпуса и regression XML
по умолчанию; для своих результатов передайте `--matrix`, `--extensions`,
`--tests`. Receipt проверяет привязку fingerprints/producers/manifests и
сохраняет исходники, тестовый XML и неизменившийся R06.35 wheel. Основные
корпуса остаются отдельными sealed directories. Hashes не аутентифицируют автора.

Вне этого эталона: ATS/reclosing/automatic restart, directional/earth relays,
initial active OC или unavailable contactor, асимметрия, дополнительные машины,
трансформаторы, converters, напряжения с разными bases и island без passive
load. Для этих случаев явная диагностика, а не молчаливое упрощение. Thermal
replica — I², не температура обмотки; R(T)/electrothermal feedback ещё открыт.
Runtime/wheel на этом этапе не менялись; нового installed replay/другого host
этот этап не заявляет. Промежуточные diagnostic artifacts сохранены отдельно.


Opt-in single-cage RMS R(T), state-dependent DAE и независимая сверка защит
добавлены отдельно в [R06.38](../rms-winding/README.md). Прежние исследования
этого каталога остаются fixed-R, если `winding_thermal` не задан явно.
