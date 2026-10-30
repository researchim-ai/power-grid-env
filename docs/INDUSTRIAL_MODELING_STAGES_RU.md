# Следующие этапы промышленного моделирования

> Актуальное состояние и следующая очередь на 30.10.2026: [новый аудит и план](INDUSTRIAL_READINESS_AND_ROADMAP_2026_10_30_RU.md). Ниже сохранены предыдущая постановка и исторические выводы.

Дата: 27 октября 2026 года. База: `93eb7e6` **и незакоммиченные изменения**,
найденные при проверке. Это план следующих работ; статусы имеющихся AG-задач
не заменяются и не обнуляются.

Очередь для агента: [industrial-modeling.yaml](../plans/industrial-modeling.yaml).
Техническая надёжность остаётся обязательной по
[промышленному плану](AGENT_INDUSTRIALIZATION_PLAN_RU.md).
Этот документ заменяет последовательность дальнейшего развития физики:
объектом приёмки становится инженерная задача с моделью и доказательствами.

## 1. Состояние проекта на момент проверки

### Что уже есть

- Фазный OpenDSS backend, каноническая модель, импорт, сценарии и event engine.
- Внешние IEEE/ENWL cases, независимая сверка части SimBench, локальный OPF.
- Process/direct контракт, checkpoints, история попыток и типизированные ошибки.
- Локальный workspace, CLI и ограниченный HTTP read interface.
- Все каналы измерителей в Gym, quality masks, подготовка инструментов агентов.
- В незакоммиченной работе: отдельные P/Q profiles, ограничение длины trace,
  incremental load update, короткий SMART-DS Engine QSTS и изменения scale probe.
- 73 теста проверены: 72 прошли в sandbox; один HTTP-тест не смог создать
  socket, затем прошёл отдельным запуском вне sandbox. Это два запуска, а не
  утверждение об одном полном зелёном прогоне в исходной среде.

Код этой рабочей версии нельзя описывать только хешем HEAD. До следующего
qualification зафиксировать dirty diff и hashes всех исполняемых источников.
Существующие правки не откатывать и не переписывать без проверки их смысла.

### Чего это пока не подтверждает

- Промышленную многомашинную RMS-модель и подключение ANDES.
- Согласованные с сетью пуск/самозапуск/опрокидывание двигателей.
- Полный метод расчёта КЗ и проверки выбора/координации защит.
- Grid-forming BESS, переходы остров/сеть и black-start в нужной точности.
- Гармоники/резонансы/тепловое состояние кабелей и трансформаторов.
- Внешний production Engine QSTS двух feeders на 7/30 суток.
- Натурную точность и преимущество над конкурентом в общем scope.

`qualify.py` сейчас записывает декларативный снимок с `released=False`, а не
вычисляет полноту прохождения всех обязательных физических gates.
HTTP workspace пока не равен полному асинхронному API управления исследованиями.

## 2. Подтверждённый новый численный блокер

В `dynamics.py` residual неявной трапеции записан как

`R(x1) = x1 - x0 - h/2 · (f(x0) + f(x1))`.

Его якобиан должен быть `I - h/2 · df/dx`. В `_numerical_jacobian` используется
плюс. Аналогичную запись надо проверить в `simulate_two_machine` и
`simulate_smib_with_controls`. Фиксированное число итераций не сопровождается
обязательным отказом при большой итоговой невязке.

На проверенном SMIB-примере при h=0.01 элемент J[0,1] получился +0.005 вместо
−0.005. На h=0.05 максимальная residual norm по трапеции составила около
`7.87e-5`; на h=0.002 — около `8.08e-13`. Это не доказывает ошибочность всех
траекторий на малом шаге, но исключает промышленную квалификацию текущего
интегратора без исправления и проверки остатка каждого шага.

Дальнейшую RMS-физику начинать после PM-00, не наслаивать новые машины на эту
численную основу. Если зрелый backend закрывает задачу, интегрировать его,
оставив собственные формулы независимыми regression cases.

## 3. Как определить целевой промышленный моделер

Первое целевое применение: сети площадки/предприятия с несколькими уровнями
напряжения, кабельными и воздушными участками, реальной нейтралью, несколькими
вводами, трансформаторами/РПН, двигателями, PV/BESS и коммутацией.
Выбирать supported voltage/parameter ranges по corpus, не по произвольному
списку номиналов в интерфейсе.

Каждый engineering study имеет:
1. Вопрос: например, сохранится ли питание двигателей при переходе на резерв.
2. Требуемую физику: PF/QSTS/RMS/harmonics/EMT, а не один универсальный режим.
3. Equipment subset, входы, units/bases и проверенные диапазоны.
4. Reference evidence и ограничения наблюдаемости/параметров.
5. Порог, результат, diagnostics и команду replay.

Не объединять статическое КЗ, нормативный SC study и временную осциллограмму
аварии в один вид результата. QSTS интегрирует интервалы режимов, но не
подтверждает субпериодную динамику. Frequency-domain harmonics не заменяют EMT.

## 4. Переписанная последовательность моделирования

### PM-00. Численная квалификация и единый runtime — P0

Сделать:
- Исправить якобианы, проверить аналитическим/finite-difference derivative test.
- Контролировать scaled residual и update norm каждого шага; max iterations,
  numerical failure и rejected step должны быть видимыми.
- Проверить Newton/linear algebra, finite values, tolerance/bases и остановку.
- Добавить dt, dt/2, dt/4 studies и тесты чувствительности к perturbation.
- Проверить одинаковую физику direct/process/incremental, а затем checkpoint.
- Отделить отсутствие физического operating point от численной неудачи.
  Аналитический предел длинного луча не считать пределом количества узлов.

Gate: numerical Jacobian совпадает с производной residual, недосошедшийся шаг
не возвращается как успешный; на гладких тестах наблюдается ожидаемый порядок
сходимости, на discontinuities шаги разделены точными событиями.

### PM-01. Единая модель оборудования для разных видов расчёта — P1

Задать паспорт и operating state раздельно. Оборудование имеет stable IDs,
терминалы/проводники, rated limits, provenance и family-specific parameters.
Одна identity машины связывает её PF, SC, RMS и harmonic representations,
но параметры каждого режима не подставляются друг из друга без модели.

Обязательные subsets:
- Sources: положительная/нулевая последовательности, Thevenin и limits.
- ABC/ABCN линии: матрицы, нейтраль, заземление, длина и temperature convention.
- Двух-/трёхобмоточные трансформаторы: impedances на согласованных bases,
  vector group/clock, winding orientation, losses, neutral и taps.
- Loads/motors, shunts/filters, switches, генераторы, converters и BESS.
- Measurements: место, фаза, produced/delivered timestamp, quality и CT/PT.

Scope compiler проверяет `equipment × study × backend × parameter range`.
Unknown/assumed/derived параметры отражаются на результате. Отдельные switches
могут иметь идеальное топологическое либо физическое impedant representation,
и соответствие должно быть явным.

Gate: roundtrip/mapping сохраняет электрический смысл; неподдержанная группа,
нейтраль или representation блокируется либо становится explicit approximation.

### PM-02. Полная стационарная фазная сеть — P1

Довести PF до проверенного subset: mesh/radial topology, multiple sources,
asymmetry, floating/impedance/solid neutral, mutual coupling, transformer groups,
PV/PQ limits, reverse power, ZIP и управляющие устройства.

Проверять напряжения phase-ground/phase-neutral/line-line, токи на обоих
терминалах и нейтрали, P/Q и потери каждого типа элемента, source balance,
нулевую и обратную последовательности там, где применимо. Базу нейтрали и
преобразование её потенциала определять отдельно от фазного напряжения.

Корпус: analytic/MNA → external same-engine → independent matched phase model.
Cases near voltage nose допускаются только с известным oracle/conditioning.

Gate: V/I/P/Q/losses и topology/status проходят полное сопоставление, не только
напряжение фазы A. Начальные well-conditioned gates проекта: |V| 1e-5 pu,
angle 1e-4 rad после общей опоры острова, KCL 1e-8 pu. Для другого reference
precision фиксировать отдельный tier до оценки, без подгонки допуска.

### PM-03. Согласованный QSTS и тепловые ограничения — P1

Один event engine обслуживает P и Q profiles, source variations, devices,
timed controls, measurement clocks, SOC и demand/service energy.
Шаг управления, шаг физики и sampling не обязаны совпадать.

Добавить:
- РПН/capacitor/Volt-VAR взаимодействие с hysteresis, delays и limits;
- CLPU, reconnect/ramp и восстановление после продолжительного outage;
- независимые timestamps, переключения и все защиты в execution log;
- thermal state для согласованной lumped модели кабеля/трансформатора,
  ambient inputs и R(T), если имеются параметры;
- distinction fixed ampacity vs динамическая температура/aging. Aging только
  при заданной и проверенной модели, не по одному току без тепловой истории.

Gate: энергобаланс/метрики и thermal response сходятся при уменьшении шага;
7/30 суток основного Engine имеют bounded memory/output, полный replay и
отдельно проверенные controls. Нельзя просто удалить trace без сохранения
интегралов и доказательств существенных нарушений.

### PM-04. Отдельный инженерный расчёт коротких замыканий — P1/P2

Реализовать два явных task типа:
1. Faulted network operating point для pre-fault/network/protection studies.
2. Standard-method SC calculation, например IEC 60909, в заявленном subset.

Для второго зафиксировать edition/method, входные данные, limits применения
и расчётные коэффициенты по доступной первичной методике. Одного совпадения
с функцией другой библиотеки недостаточно для общего заявления о соответствии.

Предусмотреть 3ph, LL, SLG и LLG только после проверки каждого; R/Xfault,
source contribution, motor/generator/converter contribution и zero sequence.
Результаты: initial symmetrical currents, peak/thermal quantities только там,
где их поддерживает выбранный метод; branch contributions и breaker duty.

Gate: совпадение с аналитикой и независимым SC backend на общей постановке,
positive/negative/min/max cases и transformer-neutral cases. Отсутствующий
LLG или вклад двигателя не скрывать названием «все КЗ».

### PM-05. Селективность, отключение и промышленная автоматика — P1/P2

Сначала IDMT/definite overcurrent, earth-fault в поддержанной измерительной
модели, local UV/OV/UF и breaker/recloser/fuse subset. Затем coordination study:
fault location × source configuration × load regime × protection settings.

CT/VT location и характеристики отделить от trip actuator. Учитывать pickup,
reset, directional polarization, breaker delay, failed breaker, reclose/lockout,
interlocks, sync-check requirements и запрет опасного parallel supply.
АВР — state machine с документированными входами и последовательностью.

Дистанционная/дифференциальная защита требует согласованных измерительных
сигналов/filters и своего RMS/EMT gate. Не выводить их динамическую корректность
из одной фазорной величины тока.

Gate: selectivity map, clearing time/sequence и неотключённая критическая
нагрузка совпадают с reference; wrong trip/failed trip и измерительные отказы
проверяются как обязательные negative cases.

### PM-06. Промышленный RMS-backend и генераторы — P2

Подключить зрелый backend с допустимым equipment mapping, начиная с ANDES
proof-of-interface. Зафиксировать общую инициализацию от PF без искусственного
переходного процесса. Не расширять учебный SMIB до произвольной сети без DAE.

Первый subset: multi-machine synchronous models, AVR/exciter, governor/droop,
PSS и limits. Events: load step, fault/clearing, trip generator/line, island.
Выходы: U/I/P/Q, speed/frequency, rotor angles и controller states.

Gate: equilibrium residual в объявленной нормировке, no-disturbance trajectory,
dt convergence, сохранение state при событиях, reference waveforms независимой
реализации. Time alignment не подгоняется по validation trajectories.

### PM-07. Двигатели, преобразователи и островные режимы — P2

Двигатели: terminal equivalent и dynamic states, torque-speed characteristic,
механическая нагрузка, J/H и синхронная скорость в явных SI/pu conventions.
Проверить размерность `T_e = P_airgap / omega_sync`, потери и связи с P/Q сети.
Существующий упрощённый slip example остаётся regression, не industrial motor.

Задачи: прямой/ступенчатый пуск в поддержанной модели, voltage sag, stall,
самозапуск группы и sequential restart с interlocks. Модели VFD и soft-starter
имеют собственную применимость; не считать их идеальным источником P без записи.

Converters/BESS: grid-following и grid-forming раздельно; current limits,
P/Q priority, ramp, droop, voltage/frequency control, SOC/efficiency, anti-islanding
и переход island/grid. Black-start, transformer energization и inrush переводить
в EMT study, если выбранная RMS-модель не описывает необходимые эффекты.

Gate: motor/network coupled trajectories и напряжения на соседних нагрузках,
restoration/restart order и power/energy balance подтверждены; остров без
поддержанного grid-forming устройства остаётся без искусственного slack.

### PM-08. Гармоники и качество электроэнергии — P2/P3

Использовать frequency-domain возможности проверенного backend. Ввести measured/
declared harmonic spectra, частотные параметры линий/трансформаторов/нагрузок,
фильтры, capacitor resonance и triplen currents в нейтрали.

Выходы: individual harmonics, THD при определённом знаменателе/окне,
impedance sweep, resonance/overload indicators и sensitivity к нагрузочному
эквиваленту. Не использовать default spectrum как измеренный спектр завода.
Не выводить transient flicker/EMT waveform из статического THD без модели.

Gate: RLC/impedance analytic references, controlled harmonic source cases,
independent frequency-domain comparison и sensitivity к известным допущениям.

### PM-09. Оценка состояния, надёжность и оптимизация — P2

State estimation: observability, phase mapping, variance/quality, residual
covariance, robust/bad-data behaviour и missing measurements. Отделить
estimated inputs от независимых targets.

N−1/reliability: line/transformer/source outages, острова, ENS/critical load,
unresolved cases; ограничения и неопределённость по сценариям. SCOPF/dispatch
вводить только после согласованной проверки исходного OPF.

OPF: full P/Q balances, оба конца ветвей, voltage/current/angle constraints,
generator/converter/thermal limits. Дискретные taps/switches не выдавать за
непрерывный минимум без постановки. Feasibility проверяется независимым oracle;
global optimum только с соответствующим доказательством.

Gate: infeasible/unobservable cases распознаны; мутации всех значимых constraints
обнаруживаются; каждое рекомендованное действие проверяется фазной сетью.

### PM-10. Связь уровней физики и натурная сверка — P2/P3

Общий asset identity связывает PF → QSTS → RMS; EMT подключать отдельным
локальным detailed subsystem после выбора необходимого явления.
Для каждого перехода описать state mapping, bases, initialization, boundary
V/I/P/Q, обмен энергией, delays/interpolation и rollback policy.

Сначала последовательное увеличение детализации одного события. Затем coupling
двух backend с step-refinement и conservation tests. Подключение HELICS или
транспорта само по себе точность не подтверждает.

Measured track: paired topology/parameters/inputs/independent outputs,
timestamp/quality и uncertainty; calibration/validation split до подбора.
При отсутствии данных — waiting_for_data только в этой ветви; агент продолжает
software/model verification. Синтетический сигнал не становится натурным.

Gate: один и тот же industrial event имеет согласованное объяснение на нужных
уровнях; расхождения количественно объяснены. Field-validated status содержит
точный объект/режим/версию, не распространяется на все модели.

### PM-11. Производительность и сравнительная квалификация — P1…P3

Начать profiling рано, но сравнивать производительность после accuracy gates.
Workload: realistic topology/equipment mix, PF/QSTS/RMS task, controls/events,
output volume, hardware/thread configuration, timestamps и horizon.

Раздельные метрики: preprocessing/compile, warm solve, full step, controller,
evaluator/IO, parent+worker memory и full study turnaround. Incremental/bulk/
worker pool допускаются только при unchanged numerical/state trajectories.

100/1 000/10 000 узлов — размеры испытаний, не готовый support statement.
Сравнивать физически корректные случаи. Удлинение линии одновременно с ростом
числа узлов меняет electrical problem; mesh, distributed loads, controls и
limits должны присутствовать в отдельных scale cases.

Gate: repeatable performance+correctness report, mandatory scenarios без hidden
skips, failure/recovery tests, clean install и evidence-backed release status.
Показатели qualify вычисляются из результатов, а не вручную заданных boolean.

## 5. Три поставки вместо одного бесконечного «всё готово»

### Industrial Network Studies

Обязательны PM-00…05 и квалификация PM-11 в точном PF/QSTS/SC/protection subset.
Поставить инженерные результаты: режимы, V/I/P/Q/losses, КЗ, координация защит,
АВР/restoration, thermal limits, replay и явная применимость.
Если standard-method SC пока не готов, выпускать более узкий PF/QSTS profile;
не отмечать весь PM-04 done из-за простого faulted solve.

### Industrial Dynamic Studies

Добавляет PM-06…07: генераторы, промышленная нагрузка, моторный пуск/самозапуск,
grid-forming и island transitions. Собственная динамическая qualification
не наследуется от успешного PF или от наличия сторонней библиотеки.

### Advanced Power Quality / Coupled Studies

PM-08…10 для выбранных задач; каждый имеет самостоятельную приёмку. Полный
EMT, real-time/HIL и любая дополнительная методика — отдельный scope.

## 6. Как стать лучше других симуляторов

Не обещать превосходство над всеми по всей физике. Выбрать сравнительные
industrial tasks, в которых нужны одновременно фазная сеть, исполнение
устройств, измерения, replay и проверка алгоритма.

### Конкретные задачи для соревнования

1. Переход на резерв при повреждении кабеля с несимметричной нагрузкой:
   правильный trip/order, critical service, ограничения и запрет опасной связи.
2. Самозапуск группы двигателей после провала: restart order, minimum voltage,
   stall detection, recovery и корректность защит. Требует Dynamic release.
3. PV/BESS/РПН/конденсаторы: напряжение, потери, количество операций,
   SOC/current limits и реакция на ошибку измерения/модели.
4. Резонанс capacitor/filter: spectrum/sweep, neutral currents и перегрузки.
   Требует Power Quality release.
5. Fault coordination при нескольких вводах: вклад источников, селективность,
   время отключения и сохранение неповреждённых секций.

### С кем и в какой области сравнивать

- OpenDSS/PyDSS: общий phase-domain/PF/QSTS/control subset. На том же engine
  можно доказать лучшую организацию/overhead, но не независимую точность ядра.
- pandapower: общий PF/OPF/SC subset; standard-method SC сравнивается с той же
  методикой и входами, не с произвольным faulted operating point.
- Power Grid Model: matched symmetric/asymmetric network study; различия
  transformer/source/neutral моделей отражаются, а не прячутся в допуск.
- ANDES: matched RMS machines/controllers/events и одинаковая инициализация.
- Grid2Op: только совместимая control task, с одинаковой информацией/actions.

При расширении benchmark зафиксировать версии/лицензии/configs. Default workflow
и дополненный custom glue baseline публиковать раздельно. Не считать неизвестную
по документации функцию отсутствующей у конкурента.

### Условия доказанного преимущества

- Accuracy/common physics и полнота targets должны пройти до сравнения скорости.
- Все inputs, transformations, parameters и numerical tolerances опубликованы
  либо входят в воспроизводимый разрешённый bundle.
- Ошибки, unsupported и inconclusive случаи не исключаются из знаменателя.
- Сравниваются p50/p95/full-run wall time, RSS, число корректно обнаруженных
  модельных дефектов, воспроизводимость событий и инженерные последствия.
- Контроллеры сравниваются с rule и optimization baselines на held-out families;
  ENS и violations важнее scalar reward.
- Утверждение имеет вид «лучше X по Y на Z при таких ограничениях».

Первый желаемый выигрыш: более полная и воспроизводимая проверка industrial
event/control workflow при той же физической точности. Второй: меньшая стоимость
полного вычислительного workflow после persistent/incremental оптимизации.
Это технические targets, не результаты уже выполненного comparison.

## 7. Первые задания агенту

1. Зафиксировать текущую dirty baseline; не перезаписывать существующие правки.
2. PM-00: Jacobian/residual/convergence tests и исправление численного блока.
3. PM-01/02: backend-independent equipment/task contracts и coverage ledger.
4. PM-03: полноразмерный production Engine QSTS с потоковым хранением evidence.
5. PM-04/05: один законченный SC+protection+ATS industrial study pack.
6. PM-11: baseline сравнение этой задачи, затем profiling/ускорение.
7. PM-06: RMS adapter и первая multi-machine reference trajectory.
8. PM-07: motor/network restart и inverter island pack.
9. PM-08…10: PQ, расширенный анализ и coupling по отдельным gates.

API/UI/documentation поддерживают эту очередь, но не засчитываются вместо
физической готовности. Пользователь должен видеть исходные параметры,
размерность, reference, ограничения и результаты каждого study.

## 8. Правила для автономного агента

Для каждого PM-пакета: equation/model note → parameter schema → implementation/
adapter → independent oracle → negative/boundary cases → qualification report.
Функция с success=True не является gate. Недосошедшаяся динамика не становится
физическим collapse; missing field data не заполняются выдуманными измерениями.

Собственный solver писать только при доказанном пробеле готового backend,
с отделённой задачей и независимым эталоном. Отсутствующий доступ к стандарту,
модели или данным блокирует только соответствующий status/claim.
Не понижать пороги по результату selected policy/case. Новые аппроксимации
создают новую версию модели и отдельный результат.

Финальный release считается готовым по конкретному profile из раздела 5,
с точной capability matrix и evidence. Общее заявление «все промышленные сети»
не является критерием и не заменяет перечень проверенных задач.

## 9. Первичные источники для выбора backend и методик

- [OpenDSS IndMach012](https://opendss.epri.com/IndMach012.html): существует
  network-connected induction machine representation для PF/dynamics;
  сначала оценить его применимость, не писать очередной isolated motor example.
- [OpenDSS harmonics](https://opendss.epri.com/HarmonicFlowAnalysis.html):
  frequency-domain harmonics, spectra и чувствительность resonance к load model.
- [pandapower short circuit](https://pandapower.readthedocs.io/en/latest/shortcircuit/run.html):
  отдельный метод equivalent voltage source и параметры SC study.
- [ANDES](https://docs.andes.app/en/stable/): PF, TDS, eigenvalue analysis и
  опубликованная верификация; интеграция требует собственного model mapping.
- [Power Grid Model](https://power-grid-model.readthedocs.io/en/stable/):
  symmetrical/asymmetrical PF, SE, SC и validation corpus для общего subset.

Наличие этих возможностей в upstream не означает, что они уже поддержаны
нашим adapter. В план включены integration и qualification, а не только import.

Следующий реализованный этап: [малосигнальная устойчивость GENCLS/GENROU](SMALL_SIGNAL_RU.md), с независимой сверкой спектра и явными ограничениями локальной линеаризации.

Начат IN-07: [динамический пуск и самозапуск двигателей](INDUCTION_MOTOR_RU.md) с вентиляторной нагрузкой, контакторами и независимым SI-эталоном. Совместная GENCLS/motor динамика добавлена ниже; GENROU и converter-модели остаются открытыми.

Добавлена [нагруженная рабочая точка двигателя](MOTOR_OPERATING_POINT_RU.md), проверка её локальной устойчивости и явные механические нагрузки/скачки. Квалификация параметров реального оборудования остаётся открытой.

Добавлен [runner конечных параметрических экспериментов](MOTOR_SENSITIVITY_RU.md) с отдельными критериями и неполным результатом при отказе любой точки. Обоснование диапазонов/корреляций и натурная проверка P1/P2 остаются открытыми.

Реализован первый [совместный профиль генератора и двигателя](COUPLED_GENERATOR_MOTOR_RU.md): GENCLS без регуляторов, единая сеть, шестисценарный независимый SI-эталон. Следующим этапом добавлены [TGOV1/SEXS в совместном GENCLS/motor профиле](REGULATED_COUPLED_RMS_RU.md): согласованная инициализация, ограничители, уставки и независимые SI/ANDES проверки. Совместные GENROU/PSS, острова и натурная квалификация остаются открытыми.
