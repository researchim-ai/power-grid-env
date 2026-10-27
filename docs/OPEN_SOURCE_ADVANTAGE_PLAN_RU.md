# План развития: промышленный моделер и проверяемое преимущество

Актуализация следующей очереди: **27 октября 2026, `93eb7e6` + dirty workspace**.
Подробная проверка физики и новые gates:
[INDUSTRIAL_MODELING_STAGES_RU.md](INDUSTRIAL_MODELING_STAGES_RU.md),
машинная очередь — [industrial-modeling.yaml](../plans/industrial-modeling.yaml).
Разделы 1–5 и 7–9 ниже сохраняют предыдущую оценку `67f4dbf` как историю;
актуальный порядок следующих работ задан новым документом и разделом 6.

Дата предыдущей оценки: 27 октября 2026 года. Проверенный commit: `67f4dbf`.
Статус: технический план, а не заявление о превосходстве или выполнении работ.
Экономическая часть исключена. Исполнитель доработок — агент.

Базовое техническое задание: [Industrialization](AGENT_INDUSTRIALIZATION_PLAN_RU.md).
Его очередь AG-01…AG-16 сохраняется; этот документ уточняет приоритеты по новой
версии и добавляет честное сравнение с экосистемой. Не сбрасывать выполненные
задачи и не объявлять их старые дефекты всё ещё открытыми без проверки.

## 1. Главный вывод

Необходимо строить промышленное рабочее место для воспроизводимой проверки
сети и управляющих алгоритмов, используя зрелые физические backend там, где
они подходят. Направление первого преимущества: промышленные распределительные
сети, несимметрия, переключения/АВР/DER, физические ограничения и понятное
объяснение результата с полным повтором исследования.

Точность и надёжность должны быть не хуже согласованных эталонов в общей
области. Превосходство надо доказывать по конкретному workflow/метрике, а не
по количеству модулей, наличию AI или внешнему виду интерфейса.

Сейчас мы не превосходим перечисленные ниже решения по совокупности функций:
сравнительных прогонов ещё нет, scope уже, внешняя валидация неполна. Есть
хорошая основа для развития и уже выполненные исправления.

## 2. Что изменилось и что проверено

- Рабочее дерево до аудита чистое; текущая база `67f4dbf`.
- 50 pytest-тестов прошли, без skips. Ранее было 40.
- Свежий restoration suite: 10 пар, без failures/unassessed; негативные
  результаты кандидата сохранены.
- Comparator теперь отклоняет пустой эталон и NaN, сравнивает |V| обесточенного
  узла. Профильный outage ENS и bounded IPC получили regression tests.
- Появились delayed taps, повторная проверка переключений, локальные UV/OV,
  immutable attempt directories, script-content fingerprint и idempotency.
- В backlog AG-01…AG-06 отмечены done; это заметный прогресс. Полный gate каждого
  пакета всё равно требует evidence, а не только изменения поля status.
- AG-07…AG-16 пока planned. Установка вне checkout, общий API/UI, full external
  Engine QSTS и длительные resource trials ещё не квалифицированы.

Доказательства: [assessment](../reports/development-review-67f4dbf/assessment.json),
[дополнительные probes](../reports/development-review-67f4dbf/probes.json).
Полные окна SMART-DS 96/672 в этом аудите заново не выполнялись. Полевая сверка
и запуск конкурирующих платформ не проводились.

## 3. Два новых подтверждённых ограничения

### NS-01. Разная физика direct/process backend — P0

На одной сети с `LTC.delay_s=5` в t=0:
- direct Engine: tap=1.0;
- Engine с ProcessBackend.solve: tap=1.1;
- ожидаемое состояние до задержки: tap=1.0.

Причина в `simulation.py:_call_solve`: выключение мгновенного регулирования
зависит от `self.solve is solve_network`. Для обёртки/процессного метода это
условие ложно. Нужен единый backend protocol, явно передающий SolveOptions и
разделяющий snapshot equilibrium от stateful timed controls.

Gate: direct, wrapped и process modes воспроизводят одну sequence taps/limits/
events до и после checkpoint. Случай должен войти в обязательный release corpus.

### NS-02. Fingerprint не учитывает импортируемый код контроллера — P1

Изменение `helper.py`, импортируемого неизменным `controller.py`, не изменило
experiment_fingerprint. Сейчас хешируются отдельные .py токены команды;
runtime identity включает Python/ОС/NumPy/SciPy, но не весь controller artifact,
платформу, engine и реально исполненную конфигурацию.

Gate: versioned controller package/image/code bundle и его dependencies
составляют воспроизводимый artifact. Любое их изменение отвергает stale resume.
Solver identity берётся из фактической конфигурации, не дублирующей константы.

Не переоткрывать весь AG-05/06 автоматически: добавить узкие follow-up задачи
и обновить evidence покрытия после исправления.

## 4. С кем сравниваться и чему учиться

Ниже перечислены подтверждённые документацией возможности. Неперечисленная
функция не считается отсутствующей. Возможные преимущества нашего продукта —
гипотезы до head-to-head испытания.

### OpenDSS / DSS-Extensions / AltDSS

Сильная исходная база фазных сетей и backend, который мы уже используем.
AltDSS предоставляет batch operations для сокращения Python/API overhead.
Его документация прямо предупреждает, что пример с runtime numbers не является
универсальным benchmark.

Вывод: не заявлять, что wrapper точнее собственного engine. Использовать direct
DSS как нижний baseline, оценивать накладные расходы и переиспользовать bulk API
после profiling. Успех продукта измерять полным исследованием.

Источник: https://dss-extensions.org/AltDSS-Python/examples/Batches.html

### pandapower

Зрелые модели оборудования, PF/OPF, short circuit, state estimation и graph
analysis. Это важный baseline и возможный backend для совпадающего subset.

Вывод: обычный load flow, OPF или Python API сами по себе не преимущество.
На общей модели обязаны пройти сопоставление V/I/P/Q/потерь и ограничений.
Нужно учитывать различие модели источника/трансформатора и выбранного метода.

Источник: https://www.pandapower.org/

### Power Grid Model

C++ core с Python/C интерфейсами, symmetric/asymmetric power flow, state
estimation, short circuit, batch calculations и собственным validation corpus.

Вывод: не обещать более быстрый solver без одинакового workload. Рассмотреть
опциональный backend, если mapping/neutral/control subset действительно подходит.
Сравнивать и hardware/runtime, и стоимость подготовки модели.

Источник: https://power-grid-model.readthedocs.io/en/stable/

### PyDSS и DISCO — наиболее близкие workflow-инструменты

PyDSS уже предлагает custom controls, project/scenario structure, экспорт,
отчёты, визуализацию, API и ко-симуляционные интерфейсы. DISCO добавляет
повторяемые distribution analyses, QSTS и voltage/thermal impact metrics.

Вывод: «у нас есть сценарии, UI и контроллеры» не является достаточным отличием.
Сравнивать конкретную промышленную задачу: strict import, event-level physical
execution, отказоустойчивость, provenance, разбор нарушения и replay.

Источники:
- https://nrel.sitefinity.cloud/grid/pydss
- https://natlabrockies.github.io/disco/

### Grid2Op

Имеет агентский action/observation/backend contract, правила legal/ambiguous
actions, time series, outages и запись/повтор эпизодов. Его документация описывает
оговорки replay при redispatch/storage и восстановлении внешних воздействий.

Вывод: RL API, masking и replay уже существуют в экосистеме. Проверять наше
преимущество на промышленной фазной задаче с устройствами, задержками и
engineering evidence. Сравнение имеет смысл только на совместимой постановке.

Источники:
- https://grid2op.readthedocs.io/en/stable/user/action.html
- https://grid2op.readthedocs.io/en/latest/user/chronics.html

### ANDES

Многомашинная динамика, power flow, eigenvalue analysis и опубликованная
верификация с PSS/E/TSAT. Нам пока нечем обосновать превосходство собственного
малого dynamics модуля над этой областью.

Вывод: подключать как отдельный RMS backend с proof-of-mapping и initialization
gates. SMIB остаётся полезным аналитическим эталоном.

Источник: https://docs.andes.app/en/stable/

## 5. Где возможно доказуемое преимущество

### A. Прозрачность модели до расчёта

Инженер видит original object/property → canonical mapping → backend →
допущение → влияние на доступную задачу. Missing grounding/tap/profile или
unsupported effect не теряются в потоке warnings. Diff модели показывает
изменение физического смысла, а не только JSON.

Измерение: доля корректно выявленных seeded import/model defects, false positives,
полнота параметров, число ручных преобразований, время до первого корректного run.
Улучшение надо сравнить с default workflow и отдельно с documented custom glue.

### B. Испытание реального исполнения управления

Команда проходит requested → accepted → scheduled → executed/rejected,
с физическими задержками, interlocks, local measurements, device failures,
реакцией сети и сохранением unsafe attempts. Один и тот же контракт для rule,
optimization, RL, LLM и внешнего бинарного контроллера.

Измерение: правильность sequenced reference traces, ошибочные разрешения/отказы,
селективность и coverage сценариев. Не сравнивать лишь scalar reward.

### C. Разбор нарушения и воспроизводимость

Каждое нарушение связано со временем, элементом, пределом, measured/calculated
значением, состоянием и preceding action; переход к replay и counterfactual.
Хронология не выдаётся за причинность. Минимальный counterfactual фиксирует,
что изменено и какие exogenous inputs сохранены.

Измерение: частота успешного воспроизведения, корректная локализация seeded
дефектов, completeness bundle и число действий до открытия причины. Заявление
«удобнее инженеру» требует отдельного usability study; автоматический script
пока измеряет только workflow steps, не человеческое время.

### D. Робастность алгоритма при неизвестных данных

Truth/reference отдельно от разрешённой модели; sensor delays/dropouts,
неверная фаза/топология, uncertainty параметров, partial observability.
Сценарии paired и независимы от числа вызовов policy/what-if.

Измерение: constraint violations, ENS, restored critical load, operations,
latency/timeouts и uncertainty по независимым сценариям. Все failures видимы.

### E. Скорость полного исследования

Persistent compile, bulk updates/readout, streaming results, bounded worker
pool и сохранение compiled state при допустимом backend lifecycle.

Измерение: compile/solve/IPC/metrics/IO и end-to-end p50/p95/RSS. Первая цель —
сократить собственные издержки; сравнительная цель объявляется после общего
accuracy-qualified benchmark. Не использовать разные controls/output объёмы.

## 6. Конкретная последовательность развития

Очередь переписана после проверки `93eb7e6` и незакоммиченной работы.
Полные постановки, эталоны и критерии:
[следующие этапы промышленного моделирования](INDUSTRIAL_MODELING_STAGES_RU.md).
Существующие AG-статусы не обнуляются; новый список PM описывает следующие gates.

1. **PM-00 — численная основа.** Исправить знак якобиана неявной трапеции,
   ввести контроль residual и отказ по несходимости, проверить шаг и runtime parity.
2. **PM-01/02 — оборудование и фазная сеть.** Общая identity и units/bases
   для PF/SC/RMS/PQ; нейтраль, группы трансформаторов, несколько источников,
   полная сверка V/I/P/Q/losses с независимой физически эквивалентной моделью.
3. **PM-03 — длительные режимы.** QSTS, события, регуляторы, энергия и SOC,
   тепловые состояния; production Engine 7/30 суток с bounded output и replay.
4. **PM-04/05 — КЗ, защиты и АВР.** Отдельный расчёт standard-method SC,
   вклад источников, координация и времена отключений, безопасное восстановление.
5. **PM-06/07 — промышленная динамика.** Зрелый многомашинный RMS backend,
   согласованная инициализация, AVR/governor/PSS, двигатели и преобразователи,
   самозапуск и переходы остров/сеть с собственной qualification.
6. **PM-08/09/10 — расширенная физика и анализ.** Гармоники/резонансы,
   оценка состояния, N-1/OPF feasibility, согласование PF/QSTS/RMS и локального EMT.
   Натурные данные идут отдельным track; отсутствие данных не заменять синтетикой.
7. **PM-11 — сквозная квалификация.** Profiling начать рано; сравнивать скорость
   после accuracy gates на одинаковой физике, версиях, hardware и output scope.
   Публиковать raw samples, failures и replay. `qualify` должен вычислять readiness.

Первый выпуск: **Industrial Network Studies**, затем отдельно
**Industrial Dynamic Studies** и **Advanced Power Quality / Coupled Studies**.
Для каждого опубликован точный equipment/task/backend subset. Более узкий PF/QSTS
выпуск допустим, но не означает готовности КЗ, RMS или гармоник.

Превосходство проверяется на пяти задачах: авария кабеля + АВР, самозапуск
двигателей, PV/BESS/РПН, гармонический резонанс и селективность при нескольких
вводах. Статус сравнительного превосходства сейчас **не доказано**.

## 7. Как доказать «лучше open source»

### 7.1. Правила честного сравнения

- Версии, commits, compiler/runtime, CPU/RAM/threads и configs закреплены.
- Один dataset split и одинаковая поддержанная физика/точность/выходы.
- Для unsupported свойства не подменять модель. Различать native support,
  custom adapter, declared approximation и out_of_scope. Доработки baseline
  доступны для повторения; сравнивать default и configured tracks отдельно.
- Подготовку/import/model compile, warm solver, full timestep, controller calls,
  output и postprocessing измерять отдельно и суммарно.
- Исходные DSS scripts проверяются напрямую, не нашим renderer в обеих ветвях.
- На same-engine baseline не выдавать общую физику за независимую валидацию.
- Agent code generation/evaluation не получает holdout outcomes до freeze.
- Expected negative tests отличаются от failures tested controller.

### 7.2. Наборы испытаний

- **PF correctness:** существующие external cases + cases на neutral/grounding/
  limits/phase mapping; сопоставление V/I/P/Q/losses и всех ключей.
- **Stateful QSTS:** несколько feeders, 1/7/30 суток, controls, SOC и events;
  одинаковые действия для physics track, собственные policies отдельно.
- **Industrial switching:** АВР/repair/failed breaker/interlocks; reference
  execution timeline и restoration/service metrics.
- **Data defects:** отдельный frozen pack из ошибочных units, grounding,
  missing profiles, clocks, CT/PT/phase mapping, unknown properties.
- **Runtime:** crash, incomplete frames, timeout, cancel, corrupted checkpoint,
  stale controller dependency, restart и exhausted resource budget.
- **Agent robustness:** unknown fault section, delayed/lost measurements,
  model mismatch, closed-world allowed tools и одинаковые what-if budgets.

Начальный corpus: не менее 12 базовых модельных конфигураций и 100 scripted
scenario runs как coverage-цель, а не 100 независимых сетей. Выделить dev и
held-out families до настройки; увеличивать объём после оценки результатов.
Клоны одного case и зависимые timestep не являются независимыми наблюдениями.

### 7.3. Показатели и условия заявления

- Accuracy: max/p95/RMSE и worst locations; residuals и completeness.
- Reliability: правильные исходы по заданным oracle, no hangs/orphans,
  deterministic replay и отсутствие corrupt reuse.
- Usability proxy: число обязательных ручных преобразований/команд и покрытие
  диагностики. Человеческое время не выводить из числа script steps.
- Performance: cold/warm и full-workflow wall time p50/p95, peak RSS,
  throughput при одинаковой точности и полезных outputs.
- Controller quality: constraints, ENS, critical service, operations, latency,
  coverage и uncertainty по независимым сценариям.

Превосходство по оси засчитывается, если baseline выбран заранее, обе стороны
проходят correctness/common-scope gate и разница подтверждается повторениями.
Для шумных latency результатов — paired runs и intervals, с сохранением всех
raw samples. Не складывать несовместимые величины в произвольный «общий балл».

Стартовые инженерные targets:
1. 100% обязательных targets/required cases имеют результат; unknown не pass.
2. Все заранее заданные malformed/runtime cases завершаются ожидаемо и в budget.
3. Replay и direct/process parity проходят объявленные допуски.
4. Сокращение собственного overhead относительно frozen нашего baseline;
   начальная цель 2× для выбранного long-QSTS workload, только после profiling.
5. Сравнительное преимущество хотя бы по одной A–E оси при отсутствии
   недопустимого ухудшения физической точности. Если нет — честно no_advantage_yet.

Это цели будущих испытаний. Ни одно число не является текущим результатом.

## 8. Ближайшая очередь PR

1. NS-01: backend options/state contract и direct/wrapper/process parity tests.
2. NS-02: controller bundle/dependency identity, actual runtime/solver fingerprint.
3. AG-08: install вне checkout и versioned project/data resources.
4. AG-07: full metrics, Engine-based QSTS, mandatory external CI.
5. AG-11: два законченных промышленных task packs с independent oracles.
6. AG-09: study API + UI import/run/violation/replay.
7. Comparison harness: direct DSS/PyDSS, затем общая модель pandapower/PGM.
8. AG-10: profiling, incremental/bulk/streaming и повторный comparative run.

Не начинать с GPU, переписывания solver, массы новых форматов и полного EMT.
Это не устраняет текущие parity/install/coverage ограничения.

## 9. Инструкция агенту

> Выполни развитие по этому документу поверх текущего AG backlog. Начни с
> NS-01/NS-02 и фиксируй реальные gates. Переиспользуй зрелые backend и
> существующие tests/assets. Доведи M0–M4 до работающего Industrial QSTS
> workflow, затем проведи M5 на закреплённых open-source baselines и улучши
> выявленные bottlenecks в M6. Не заявляй превосходство по документации:
> измеряй его по общему scope, точности, надёжности и полному workflow.
> Неполные/failed/unsupported cases сохраняй. Field data, missing dependencies
> и неподдержанную физику не выдумывай; продолжай независимые задачи.
> Обновляй evidence и scope после каждого изменения. Финал — установленный
> продукт, qualification bundle, comparative report и команды воспроизведения.
