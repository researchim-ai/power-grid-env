# Задание агенту: довести power-grid-env до промышленной готовности

Дата: 27 октября 2026 года. Проверенная база: `336d6c8`.
Статус: технический план; перечисленные доработки ещё не выполнены.
Экономическая часть, продажи и оценка рынка в документ не входят.

Машиночитаемая очередь: [industrialization.yaml](../plans/industrialization.yaml).
Доказательства проверки: [assessment.json](../reports/industrial-readiness-20261027/assessment.json).
Предыдущие планы сохраняются как история; этот документ уточняет следующий шаг
по фактически проверенной версии, а не повторяет старые задачи как незакрытые.

## 1. Решение по направлению продукта

Довести до первой промышленно пригодной версии локальную платформу исследований
распределительных и промышленных сетей: импорт → проверка модели → режим/QSTS →
исполнение внешнего алгоритма → сравнение → доказательный отчёт → replay.
Основная область: offline-исследования, проверка переключений и восстановления,
напряжений/токов, DER/BESS и алгоритмов управления в явно поддержанном subset.

Промышленный уровень означает контролируемую область применения, корректные
модели, воспроизводимость, отказоустойчивое исполнение, проверяемые результаты
и установку без зависимости от рабочего checkout. Это не число функций.

Разделить поставки:

- **Industrial QSTS v1** — основной обязательный релиз: точные поддержанные
  модели, фазный PF, QSTS, контроллеры, external regression, API/CLI/UI,
  журналирование, установка, ограничения ресурсов и support bundle.
- **Industrial RMS** — отдельное расширение с инициализацией машин/регуляторов,
  динамическими событиями, reference trajectories и своим release gate.
- **EMT / ко-симуляция** — последующее ограниченное расширение под выбранные
  задачи. Наличие RL/RLC не переводит весь проект в полный EMT.

Базовый релиз остаётся полезен без RMS, обучения собственной модели и натурных
данных. Натурная валидация получает отдельный статус и конкретную область.
Контроль реального оборудования и HIL не включать в Industrial QSTS v1.

## 2. Актуальное состояние: что подтверждено

### Повторено в этом аудите

- До проверки рабочее дерево было чистым.
- `python3 -m pytest -q`: **40 тестов прошли, пропусков нет**. Среди них
  external smoke/distribution, derived cases, SimBench, PGLib и короткий
  SMART-DS QSTS/replay на 4 шага.
- Свежий restoration suite: **10 пар baseline/candidate**, без runner failures
  и unassessed; намеренные негативные исходы сохранены.
- **26 файлов acquisition lock** присутствуют и совпадают по SHA-256.
- Wheel `0.1.0` собрался. Это подтверждение сборки, но не полной установки продукта.
- Дополнительные негативные probes обнаружили блокеры, описанные в разделе 3.

### Изучены сохранённые результаты, но не повторялись полностью

- IEEE13/34/123 и три ENWL-фидера: conversion consistency по напряжению на
  том же DSS-Extensions engine в объявленном допуске.
- 10 derived operating points: фиксированные регуляторы, множители нагрузки
  и одно переключение IEEE123.
- SimBench: три снимка, сверка модуля V с pandapower в `1e-4 pu`; большая часть
  углов/токов/потерь не входит в такой вывод.
- PGLib: локальные допустимые решения, включая case118; сверка не доказывает
  глобальность оптимума.
- SMART-DS: сохранены 96/672-step отчёты. Их полные окна в этом аудите заново
  не запускались; короткий 4-step test выполнен в pytest.

Эти достижения реальны: не надо заново писать импорт и бенчмарк с нуля.
Но полнота метрик, качество verdict и использование production Engine требуют
усиления. ENWL сутки с исходным Shape_N не собраны; paired field measurements
в проекте не подтверждены. Авторская площадка 110/10/0.4 кВ — полезный smoke
case, а не независимая промышленная валидация.

## 3. Найденные блокеры и ограничения

### IR-01. Ложный pass при пустом сравнении и нечисловых данных — P0

`benchmarks/external/compare.py:compare_buses` вернул pass:
1. при пустом native reference и нуле общих targets;
2. при NaN в рассчитанном напряжении;
3. при reference V=0 и actual V=1 pu: target пропущен целиком как низковольтный.

Необходимо исключать только физически неопределённый угол, сохраняя сравнение
модуля/energization. Отсутствие targets, NaN/Inf, дубликаты и неполное обязательное
покрытие не могут давать pass. Проверять также статус нашего solver, а не только
наличие строк: `_run_case` сейчас не делает его самостоятельным gate.

Доказательство: [probes.json](../reports/industrial-readiness-20261027/probes.json).

### IR-02. Недоотпуск не учитывает изменяющийся спрос — P0

`simulation.py:_interval_metrics` берёт demand из `base_p`, хотя профиль уже
изменил `load.p_w`. На обесточенной нагрузке 100 кВт с ростом до 200 кВт в t=30 с
за 60 с записано **1666.67 Wh вместо 2500 Wh**.
Повторное вычисление ENS в evaluator использует те же ошибочные demand rows,
поэтому совпадение двух итогов ещё не означает корректность метрики.

Нужно раздельно хранить demanded, connected, actually supplied и quality-valid
energy, профиль/curtailment/CLPU semantics; интегрировать по физическим интервалам.
Пониженное напряжение и полное отсутствие энергии не смешивать без определения
proxy service metric. Определение ENS должно входить в task contract.

### IR-03. Таймаут subprocess не ограничивает весь ответ — P0

`controllers.py:_request`: `select` ждёт первый байт, после чего `readline`
может ждать newline неограниченно. Контроллер, записавший только `{`, завис
дольше внешних 1.5 с при заданном timeout=0.1 с. Тестовый процесс принудительно
остановлен, остаточных процессов аудита нет.

Нужны bounded byte framing и единый monotonic deadline на запись/чтение,
drain stderr во время работы, лимит строки, проверка JSON root/схемы,
контроль handshake и process-tree cleanup. Аналогичную read/write схему
исправить у ProcessBackend: наличие select само по себе не закрывает проблему.

Доказательство: [controller_timeout.json](../reports/industrial-readiness-20261027/controller_timeout.json).

### IR-04. Полный отпечаток эксперимента отсутствует — P1

`experiment_fingerprint` содержит сценарий, параметры команды и hash сети,
но не содержимое файла контроллера/его зависимостей, версию платформы,
окружение, solver options и все thresholds. Изменение controller.py при той же
командной строке не изменило fingerprint. Resume может принять устаревший результат.
Роли пишутся в общие каталоги, SQLite хранит попытки, но их traces перезаписываются.

Нужны content-addressed inputs, immutable attempts и fingerprint полной конфигурации.
Повторная попытка не должна заменять исходный failure.

### IR-05. Установка продукта зависит от checkout — P1

Wheel импортируется из отдельного каталога, но `repo_root()` падает:
`FileNotFoundError: корень power-grid-env не найден`. Контроллер использует его
для cwd/PYTHONPATH. `cases` и внешний benchmark в wheel не входят.
Сам core solve может работать; неработоспособность относится к зависящим от
repo_root workflows. Нужны explicit project/data dirs, package resources
и отдельная стратегия доставки case/benchmark packs.

Доказательство: [packaging.json](../reports/industrial-readiness-20261027/packaging.json).

### IR-06. Объём внешней валидации уже заявленной функции неполон — P1

- Основной distribution comparator сравнивает V/angle, но не terminal I/P/Q,
  потери, нейтраль и state transitions как обязательные targets.
- SMART-DS QSTS вручную меняет нагрузки и вызывает `solve_network`, не Engine.
  Его checkpoint — network/cursor, а не scheduler/защиты/BESS/очереди основного
  продукта. Поэтому он не доказывает production QSTS/replay целиком.
- SimBench: нормальная phase-domain cross-check здесь только по |V| и для
  трёх выбранных снимков; другие величины/режимы не подтверждены автоматически.
- В pack distribution SMART-DS отмечен not_run, но общий verdict pass;
  обязательность tracks не задана достаточно строго.
- PGLib audit отделён от SLSQP, но использует собственные же Ybus/flow helpers.
  Это внутренняя проверка допустимости, не полностью независимый oracle.

### IR-07. Неподдержанная физика местами продолжает рассчитываться — P0 по scope

Для Dyn5 backend сохраняет warning, что сдвиг не передан в OpenDSS, и продолжает
расчёт. Этот subset при этом находится внутри общей записи phase_domain_power_flow.
Fuse импортируется как ignored_non_electrical, хотя для protection/restoration
его поведение может быть существенным. Поддержка должна зависеть от задачи.

В точном режиме unsupported transformation должен блокироваться. Explicit
approximation допустима отдельным режимом, с отдельным verdict и provenance.
Решение парсера не является подтверждением поддерживаемости backend.

### IR-08. Временные состояния и защиты ещё требуют системной проверки — P1

Из чтения кода, без заявления о проверке всех комбинаций:
- РПН/Volt-VAR корректируются на глубокой копии внутри solve; Engine не имеет
  полного контракта принятия нового persistent состояния и физических задержек.
- `_apply_event` переключает аппарат без повторной проверки всех условий
  непосредственно в момент исполнения. Между принятием и исполнением условия
  могут измениться.
- UV/OV relay signal ищется по всем питаемым узлам, а не по своему измерителю;
  directional/reclose/breaker delays требуют полного event-level corpus.
- Операции защит и control equipment должны попадать в общий execution log.
- Один source/grid-forming policy не покрывает произвольный islanded BESS режим.

### IR-09. Эксплуатационный контур и доказательства релиза неполны — P1/P2

- CI — один Python 3.12 job, без full acceptance/external pack provisioning,
  wheel install smoke, заданного runtime image и закреплённого dependency lock.
- Внешние тесты пропускаются при отсутствии cache; необязательная зависимость
  pandapower не ставится текущим CI. Зелёный CI не доказывает эти tracks.
- Native reference может создавать derived script после ошибки compile;
  нужны hashes всех преобразований и строгий запрет выдавать это за original case.
- Схемы экспортируются только для Network/Scenario, action/tool схемы поверхностны.
- Gym observation всё ещё обрезан до 8 напряжений; age считается от available_s,
  а не produced_s, то есть не является полным возрастом исходного измерения.
- Viewer — полезный self-contained HTML. Нет полного project/import/run/review
  интерфейса и версионированного внешнего study API.
- Нет доказанного resource envelope для реальных больших сетей и длительных runs.
  SMART-DS week report показывает примерно 1.75 GB peak RSS процесса, но это
  накопленный process-wide показатель; его нельзя приписать одному solve.

## 4. Целевая архитектура

1. **Domain contracts:** versioned equipment, topology, operating state,
   scenario truth, observation, action, execution event, result и evidence.
2. **Study service:** validate/import/compile/run/compare/replay/export.
   Один интерфейс используется CLI, UI, Python SDK и batch runner.
3. **Backend adapters:** declared supported subset, preprocessing ledger,
   solve diagnostics, state transitions, cleanup. OpenDSS остаётся фазным ядром.
4. **Event engine:** state changes/measurements/profiles/protection и время;
   отделён от physics adapter и политики агента.
5. **Evaluator:** read-only interval/state log; независимые проверки метрик,
   нарушения и complete/incomplete semantics.
6. **Execution manager:** process isolation, deadlines, quotas, cancellation,
   durable checkpoints, attempts и recovery.
7. **Evidence store:** immutable input fingerprints и attempt bundles;
   локальная файловая структура + SQLite index достаточно для v1.
8. **Engineering workspace:** проекты, версии моделей, run history, схема,
   графики, действия и разбор ограничений; явное разделение observer/truth.
9. **Validation corpus:** analytical, external same-engine, independent solver,
   measured и stress/performance tracks с независимыми verdicts.

Убирать монолитность importer/backend/simulation постепенно после regression tests.
Не переписывать всё ядро за один refactor и не добавлять ещё один свой решатель
вместо независимой проверки уже существующих.

## 5. Рабочие пакеты для агента

Каждый пакет заканчивается кодом, тестами, воспроизводимой командой и evidence.
Ниже AG-номера совпадают с машиночитаемым backlog. Критерии проверяются
автоматически; отсутствие внешнего ресурса фиксируется отдельно, не тормозит
независимые задачи и не превращается в pass.

### AG-01. Защитить достоверность verdict — P0

Изменить comparator и pack evaluator:
- finite checks на все входы/выходы; JSON запрещает NaN/Inf;
- ожидаемый список targets и обязательных cases; дубликаты, пропуски,
  пустой набор и invalid solver status имеют явный отрицательный исход;
- deenergized: сравнивать energization и |V|; исключать только angle;
- expected/attempted/solved/compared/failed/skipped counts в отчёте;
- mandatory track not_run/inconclusive не допускает release pass;
- UI/CLI отличают «прогон выполнен» от «кандидат принят» и отражают exit codes.

Gate: все IR-01 негативные probes отвергаются; mutation test удаляет target,
вносит NaN, меняет solver status — report/CI действительно краснеет.

### AG-02. Исправить обслуживание и энергетические метрики — P0

Задать интервалами demanded/supplied/curtailed/quality-service quantities,
source of demand, CLPU и profile interpolation. Поддержать ZIP и несимметрию
с согласованным смыслом service proxy; физически отпущенную энергию считать
по terminal flows, потерянный спрос — по отдельной модели нагрузки.
Независимый evaluator не должен доверять итоговым demand/ENS Engine без
проверки исходного профиля и временных границ.

Gate: пример IR-02 даёт 2500 Wh; переключение в середине интервала, повышение/
понижение профиля, outage, curtailment, partial-phase supply и оборванный run
имеют аналитические oracle. Reward отдельно от инженерных метрик.

### AG-03. Реальные deadlines и строгий controller protocol — P0

Typed request/response schemas, version handshake, correlation IDs и units.
Ограничение байтов, буферов, вложенности, wall-clock и stderr. Чтение до полного
frame с monotonic deadline; контроль блокировки записи. Проверять initialize/
reset ответы, JSON root type, missing fields и late replies.
ProcessBackend использует ту же проверенную transport primitive.

Gate: partial line, stdout flood, stderr flood, wrong ID/version, crash,
зависание и child process не оставляют живые процессы, не блокируют runner и
дают различимые machine outcomes. Негативные тесты ограничены внешним timeout.

### AG-04. Точная область физики и строгий compiler — P0/P1

Создать task/backend/equipment capability registry с supported/approximate/
unsupported статусом и evidence IDs, не общей строкой описания.
Model validation → semantic validation → capability check → compile ledger.
Поддержать finite/range/passivity и глобальную уникальность IDs, принадлежность
терминалов, bases, connection/grounding. Не считать свойства перенесёнными
только по распознанному классу New.

Dyn5: либо реализовать и подтвердить фазовый сдвиг, либо точный режим отклоняет.
Fuse: различить отсутствие электрического влияния в fixed power-flow study
и обязательную динамику для защиты. Любое упрощение — opt-in derived model.

Gate: каждый объект/значимое свойство исходного case имеет mapping; удаление
свойства из renderer обнаруживается тестом; unsupported нельзя скрыть warning.

### AG-05. Полный QSTS state и корректные защиты — P1

Вынести continuous/discrete operating state и acceptance backend transition:
taps/caps, DER setpoints, limit modes, SOC, CLPU, relay accumulators, shots,
measurement queues и failures. Определить event ordering и physical step limits.
Управление РПН/конденсаторами исполняется с deadband/hysteresis/delay и сохраняется.

Разделить измеряемый CT/VT terminal и trip actuator. Проверить селективность,
направление, pickup/dropout/reset, breaker opening delay, АПВ/lockout,
местные UV/OV, operation counts. В момент исполнения повторно проверять
interlocks/current limits/maintenance и регистрировать окончательный исход.

Gate: dt/2 study, repeated events, equal-time ordering, динамика нагрузки,
контакт с задержкой, multiple sources и relay/reclose имеют reference timelines.
Checkpoint из середины выдержки сохраняет полную траекторию, не только V.

### AG-06. Неизменяемые исследования и аварийное восстановление — P1

Fingerprint включает content hashes модели/includes/profiles/controller code
и dependencies/image, schema/evaluator/threshold versions, solver options,
platform commit/dirty diff, seeds, numerical libraries/runtime.
Attempt имеет отдельный UUID-каталог; результаты не перезаписываются. SQLite
индекс транзакционен; artifact writes атомарны; manifest финализируется последним.

Resume проверяет fingerprint и целостность, восстанавливает совместимую версию
state либо отказывает с причиной. Сохранять журнал первоначальных failures.
Cancellation внутри эпизода, partial output и restart после worker/runner crash.

Gate: изменение controller.py меняет fingerprint; stale/corrupt checkpoint
отвергается; принудительное завершение в разных точках и resume эквивалентны
непрерывному прогону. Повторяющийся API-запрос с idempotency key не дублирует run.

### AG-07. Внешний benchmark через production pipeline — P1

Развить существующий harness, сохранив raw hashes и источник каждого изменения:
- эталонные Vcomplex/Icomplex/P/Q, branch/transformer losses, neutral current,
  island/source state, control states и convergence diagnostics;
- сравнение original IDs с canonical mapping, одна опора на каждый остров;
- отдельные precision tiers: analytical / same-engine / independent / measured;
- полный QSTS pack вызывает именно Engine/runner, а не ручной _apply loop;
- native reference имеет независимый extractor, без повторного нашего mapping;
- записанные controls и closed-loop controls — отдельные tracks;
- ENWL без Shape_N: сохранять missing track; новые профили только как явно
  полученный derived dataset, без утверждения восстановления оригинала.

Рекомендуемый минимум обязательного корпуса: IEEE13/34/123, три ENWL snapshots,
не менее двух разных SMART-DS feeders с сутками/неделей, SimBench snapshots,
а также авторские analytic/event cases. Число feeders — целевой coverage gate,
не текущее достижение. Отбор по заранее объявленным признакам.

Gate: все обязательные случаи завершены либо release заблокирован; NaN/пропуски
не исключаются; external QSTS проверяет SOC/taps/events/ENS/replay основного Engine.
Существующие tolerance tiers не расширять постфактум.

### AG-08. Установка и воспроизводимое окружение — P1

Закрепить dependencies и runtime image; определить поддержанную ОС/архитектуру
v1 по реально доступным CI runners. Привязка v1 только к Linux допустима,
если Windows/macOS явно не заявлены. Не имитировать непроверенную поддержку.

Удалить runtime поиск checkout. Пользовательский project dir/cwd задаётся
явно; package data читать через resources. Samples и benchmarks доставлять
отдельными versioned packs с лицензиями и hash, без скрытого network fetch.
Проверить wheel/sdist и optional dependencies с минимальной установкой.

Gate: на чистом окружении вне репозитория install → import внешнего case →
preflight → solve → subprocess controller → report → replay выполняются по
одной инструкции. Код/data/runtime versions попадают в evidence.

### AG-09. Локальный study API и инженерное рабочее место — P1/P2

Ввести стабильный service слой, поверх него Python API/CLI и локальный HTTP API:
projects, model versions, import report, validate, jobs, status/cancel/resume,
results, compare и export. Long jobs выполняются workers, не в UI process.
Схемы/API versioning, error taxonomy и machine-readable diagnostics обязательны.

Интерфейс: открыть проект → импортировать → исправить ошибки с привязкой к
original object → исходный режим → настройки study/алгоритма → запуск →
прогресс → схема/графики/нарушения → сравнение/replay. Существующий viewer
и SVG assets переиспользовать. Model editor сначала ограничить поддержанными
параметрами, с diff и повторной проверкой; geometry не менять физику молча.

Схема показывает все типы в scope, фазы/нейтраль и islands; time cursor связан
с V/I/P/Q/SoC/taps/командами. Privileged данные отделены на API/data уровне.
Локальный bind по умолчанию; сетевой доступ/аутентификация — только явным режимом.

Gate: end-to-end UI test проходит на новой схеме без Python, trace и графики
совпадают с bundle, отмена и перезагрузка не теряют job, controller view не
получает privileged payload. Проверить escaping импортированных имён/ошибок.

### AG-10. Ресурсы, производительность и длительная работа — P1/P2

Определить workload contracts: размер в bus/phase nodes, equipment mix,
controls/events, физический шаг, горизонт, hardware, memory/deadline limits.
Профилировать compile/topology/solver/serialization/evaluator/IO отдельно;
публиковать cold/warm p50/p95/max и RSS worker + parent.

После measurements: persistent backend sessions, инкрементальные updates,
сокращение сериализации, streaming numeric trajectories в Parquet, bounded
buffers и worker pool независимых контекстов. Все оптимизации проходят
trajectory/numerical equivalence, включая неуспешные modes.

Цели испытаний: реалистичные 100/1 000/10 000 узлов и 7/30 суток; конкретный
верхний размер включать в scope только после прохождения. Длинный луч с большой
просадкой не является универсальным stress test большого города.
Бюджеты времени/памяти freeze до итогового evaluation после отдельного profiling.

Gate: full-step throughput и memory growth измерены на объявленном workload;
повторный запуск не накапливает память/процессы; OOM/timeout/cancel сохраняют
диагностику и не портят проект. Ускорение без потери точности подтверждено.

### AG-11. Прикладная полнота промышленного QSTS — P2

Собрать task packs: АВР/два ввода/секция; восстановление с ремонтной зоной;
РПН+конденсаторы; PV/BESS с limits и SOC; несколько источников и обратный поток;
недоступный аппарат и потеря измерения. Тепловые ограничения обмоток/вводов и
временные overload limits должны оцениваться наряду с токами линий.

Load/motor equivalent в QSTS имеет свой паспорт; пуск двигателя, частотная
устойчивость и быстрые защиты направляются в RMS/EMT task, а не вычисляются
произвольным статическим приближением без отметки.
Отдельно проверить 3-winding, neutral/zero-sequence, asymmetry и short-circuit
equipment subset. Метод SC и его условия не выдавать за соответствие стандарту
без собственного набора доказательств. Добавлять стандартный метод по задаче.

Gate: по каждому pack есть положительный, отрицательный и near-limit case,
baseline и физически проверенные последствия действий; scope автоматизирован.

### AG-12. Независимые OPF / N−1 / state estimation — P2

Существующий OPF сохранить, но проверить mapping целиком: taps/phase shift,
both-end MVA limits, angle limits, P/Q bounds, disconnected islands,
generator status и cost formats. Зафиксировать поддержанные MATPOWER fields.
Feasibility oracle по независимому backend/другой реализации проверяет все
ограничения; published objective не заменяет feasibility.

N−1 включает генераторы/трансформаторы/линии и потерю нагрузки, V/I и
unassessed cases. State estimation — observability, covariance residuals,
QR/SVD conditioning, bad-data/topology errors, time/quality. Наивный r/sigma
не обозначать полноценным largest-normalized-residual test.

Gate: infeasible задачи распознаны, constraints mutations обнаруживаются;
OPF результат независимым solve допустим; N−1 enumeration не теряет outages;
state estimator на необозримой сети не выдаёт успешную оценку.

### AG-13. RL / LLM / VLM как проверяемые клиенты — P2

Стабильные meter/phase IDs, valid/age/quality masks и переменный размер сети
вместо первых 8 значений. Age = now − produced, delivery latency отдельно.
Gymnasium checker, deterministic seeds, graph/padded observations,
terminal/truncated semantics, invalid-action handling и vector rollout.

LLM tools через те же validated schemas и execution service; quota, model
version, tool log, recorded responses. VLM scene строится из разрешённой сети
и состояния, hit-test проверяет реальные координаты. Масштаб и отсутствие DOM
утечек тестируются. Raw/shielded controllers оцениваются отдельно.

Проверка untrusted controllers: отдельный root/files/env/network policy,
resource quotas, hidden files недоступны. Для trusted режима явная отметка,
не заявление о защищённом holdout. Доверенный user всегда контролирует свой host.

Gate: одна и та же задача запускается rule/RL/LLM adapter без правки engine;
утечки и ограничения проверены; обучение сравнивается с эвристическим и
optimization baseline на независимых families. Массовое обучение не gate v1.

### AG-14. Натурная сверка как отдельная ветвь — P2 / external data dependency

Исполнить measured-track часть [внешнего плана](AGENT_EXTERNAL_BENCHMARK_PLAN_RU.md):
data sufficiency → channel mapping → uncertainty → calibration-only fitting →
frozen validation blocks → report. Получить topology/parameters/inputs/independent
targets/time/quality. Подготавливать файлы запроса и импорт автоматически.

Если paired данных нет, агент завершает software/external-model tracks,
публикует gap manifest и оставляет field track waiting_for_data. Не скачивать
случайный профиль и не генерировать «натурные измерения» для закрытия gate.
Запрос внешнему владельцу и использование частных данных — только при доступе,
который действительно предоставлен. Обращение к человеку не требуется для
всех остальных задач и не является универсальным стоп-фактором.

Gate field_validated: независимые validation measurements в заявленной области,
предварительно фиксированные thresholds и uncertainty, воспроизводимый отчёт.
Software-ready и field-validated статусы не объединять в один boolean.

### AG-15. RMS backend — отдельный релиз P2/P3

Подключить mature RMS backend, например ANDES, после proof-of-mapping. Первое
подмножество: стационарная инициализация → несколько машин/AVR/governor/PSS →
disturbance/clearing → ограничения/защиты → двигатели в подтверждённой модели.
Существующие SMIB/two-machine примеры оставить analytic regression.

Gate: DAE initialization residuals, безвозмущённая стационарная траектория,
dt/dt2/dt4 convergence, independent trajectories и documented valid range.
Динамический status не наследуется из стационарного solve.

### AG-16. Автоматическая приёмка и выпуск — P1, финальный gate v1

Разделить быстрые deterministic unit/property tests, numerical, scenario,
external, runtime faults, install и long/performance suites. В fast PR tests
допустим skip необязательного cache; release job должен получить pinned data
и отклонить любой skip обязательного track. Проверять optional dependency matrix.

Release command формирует один immutable qualification bundle: git/diff,
package/lock/image/data hashes, capability snapshot и evidence links, все
кейсы/counts, tolerances, timings, failed/inconclusive и known limitations.
Release README RU/EN и API docs генерируются/проверяются по реальному CLI;
исправить ссылки на отсутствующие документы требований.

Gate: чистая установка, полный обязательный корпус и lifecycle fault tests,
checksum verification, recorded replay, поддержанная ОС и workload;
нет открытых P0 в release scope и нет необъяснённых regression.
Негативные tests считаются успешными при ожидаемом отказе, но не исчезают
из отчёта о тестировании пользовательских контроллеров.

## 6. Порядок и зависимости

Критический путь: AG-01 → AG-02/03/04 → AG-05 → AG-06 → AG-07 →
AG-08/09/10/11 → AG-16. Реальные зависимости указаны в YAML; стрелки выше
показывают смысловую последовательность, не запрет независимой работы.

В одном агентском сеансе делать небольшие завершённые изменения. Параллельные
исполнители, если их использование разрешено, получают непересекающиеся файлы;
правки model/simulation/schema сначала согласовать через контракт. Число
агентов не является критерием завершения.

AG-12 и AG-13 идут после надёжных contracts/runtime и не должны задерживать
исправление P0. AG-14 можно готовить с самого начала, ждать данных только
внутри этой ветви. AG-15 и EMT не блокируют Industrial QSTS v1.

## 7. Правила автономной работы агента

1. Перед каждой задачей перечитать актуальный код/задачи и baseline; не
   исправлять уже закрытое по старой версии отчёта.
2. Сначала regression/oracle для конкретного дефекта, затем минимальный fix,
   затем соседние tests. Большой refactor не заменяет доказательство исправления.
3. Для каждой задачи сохранить: input state, changed files, commands, результаты,
   limits и критерий готовности. Status done только после выполнения gate.
4. Не менять thresholds и не исключать failed cases ради pass. Если ошибка
   reference/input подтверждена, создать новую версию с provenance и сохранить старую.
5. Планировщик задач продолжает доступные независимые пакеты. Отсутствие
   закрытого backend или измерений не повод остановить software-hardening.
6. Промышленный capability — точная комбинация задачи, оборудования, размера,
   диапазона параметров и версии, связанная с evidence. Временный workaround
   не переводит модуль в validated.
7. По умолчанию работать локально, без Kubernetes, облачного развёртывания,
   телеметрии наружу и управления первичным оборудованием.
8. Документы и backlog не являются реализацией. Итог агента должен содержать
   работающие файлы, тестовые артефакты и список незакрытых ограничений.

## 8. Условия готовности Industrial QSTS v1

- AG-01…AG-11 и AG-16 завершены в точном release subset; нет известных P0.
- Fresh install вне checkout запускает весь workflow; проекты переносимы.
- Mandatory analytical/external/QSTS/runtime/install packs без неучтённых skips.
- Физические модели и unsupported режимы проверяются до запуска.
- Метрики профиля/снабжения/энергии проверены независимыми oracle.
- Контроллер не может повесить runner; cancellation/recovery воспроизводимы.
- Data/code/config/version identity фиксирована, stale resume отклоняется.
- UI/API дают тот же результат, что CLI, и позволяют разобрать любое нарушение.
- Resource envelope измерен, declared supported workload проходит budgets.
- Есть документация, upgrade/migration path, diagnostics и known limitations.
- Field validation статус указан отдельно; её отсутствие не скрывается и
  не подменяется same-engine сравнением.

## 9. Что отложить до этих gates

Полный EMT, HIL/real-time, одновременно все CIM/CGMES/RAW импорты, массовое
обучение нейросетей, GPU переписывание ядра, распределённый кластер и
неограниченная multi-user платформа. Добавлять их только как отдельную
проверяемую область поверх уже надёжного study workflow.

## 10. Готовый запускной prompt агенту

> Доведи Industrial QSTS v1 по docs/AGENT_INDUSTRIALIZATION_PLAN_RU.md и
> plans/industrialization.yaml. Сверь текущий checkout с аудитом 336d6c8,
> затем последовательно закрывай доступные задачи, начиная с AG-01–AG-04.
> Для каждого дефекта сначала добавь независимый regression/oracle, затем
> реализуй исправление, выполни gate и сохрани доказательства. Не останавливайся
> после очередного документа или зелёного unit test: доведи установку вне
> checkout, production Engine QSTS, внешние packs, runtime fault handling,
> воспроизводимость, study API/UI и release qualification. Переиспользуй
> существующие модели, assets и бенчмарки. Не понижай допуски и не скрывай
> unsupported/failed cases. Обновляй status задач только по результатам.
> Если недоступны натурные данные, оформи gap и продолжай остальные задачи;
> измерения и независимый эталон не выдумывай. RMS/EMT, cloud и экономика
> не входят в обязательную поставку. Финальный отчёт: выполненные gates,
> точная область применимости, commands для clean install/run/replay,
> qualification bundle и все оставшиеся ограничения.

## 11. Первичные документы для будущих интеграций

- [OpenDSSDirect contexts](https://dss-extensions.org/OpenDSSDirect.py/opendssdirect.html):
  отдельные contexts и ответственность пользователя за thread management.
- [ANDES TDS](https://docs.andes.app/en/v1.10.0_a/tutorials/04-time-domain.html):
  динамика начинается с сошедшегося power flow и согласованной инициализации.
- [pandapower state estimation](https://pandapower.readthedocs.io/en/v3.5.2/estimation.html):
  оценка состояния и bad-data procedures имеют собственные ограничения;
  подключение библиотеки не снимает обязанность проверить task-specific corpus.
