# Текущий статус и очередь развития

Обновлено: **18 ноября 2026 года**.

## R06.86 — named operations, VLM abstention и физические контрастные пары

**Новый finite40-step pilot завершён: strict4/12→10/12; общий gate12/12
НЕ пройден.**10 TRAIN exemplars +2 новых question presentations из TRAIN
распределения, не family-heldout score. Preferred operations1/6→4/6,
perception/insufficiency3/6→6/6. Train subset3/10→8/10, новые вопросы1/2→2/2.
Сохранилось чтение factual/counterfactual216.12/253.12; выбранные missing/blank
контроли теперь дают null, включая поля from_node/to_node нового вопроса.
Это узкая проверка, не универсальная VLM/abstention квалификация.

Рецепт fresh base, rank16/two text blocks, frozen vision/base, AdamW1e-4,
40 updates. JSON-value tokens получают weight4 против1 у остальных assistant
токенов; prefix/image masked. Exact token-offset roundtrip и causal alignment
проверены до updates. Введены отдельные syntax/payload CE и payload accuracy.
Vision encoder18 forwards, finite gradients, frozen base и exact adapter
roundtrip прошли. Новая цель и weighted objective менялись вместе: преимущество
именно веса4 не установлено, с прежним menu5/10 score напрямую не сравнивать.

Остались simulate→validate и set_line_catalog→submit при перегрузке. Physical
judge не пропускает unsafe submit. Preferred-action exact score не перечисляет
все допустимые планы. После failed gate нет auto-extra-epochs/GRPO. Предыдущий
128-step job выполняется отдельно с прежними fingerprints и recipe. Все128
updates завершены; standalone8-case evaluation запущен, полного score ещё нет.

**Собран двухэтапный API**: имя операции → самостоятельные required arguments.
На второй стадии только public schema/goal/state; нет teacher directive или
заранее выбранного оборудования/каталога. Previous public ok/error/operation
передаётся дальше. Optional empty args сериализует adapter, invalid decisions
расходуют реальные calls. PUBLIC control32/32 проверил полный v2 physical
workflow. Learned native staged-policy loader/полный model workflow ещё открыты;
веб Agent Lab автоматически не переключается на эти experimental checkpoints.

**Новые данные полностью допущены native processor:**
- 848 staged targets:192 operation,32 conditional arguments,624 measurement
 assessments. Полные cards/responses/terminals replayed; failed actions не
 становятся positive SFT. Measurement labels независимо сверены с physical judge.
- 128 matched-stage physics targets:32 пары feasible/overloaded, одинаковые
 goal/revision/budgets/feedback/freshness.64 public-control successes и128
 independent before/after checks. Это64 observations на16 исходных сетях,
 не64 независимых объекта. Labels/variant metadata в prompt не передаются.

Все976 новых targets прошли prefix/actual-label/payload-mask checks. Они пока
не использованы для optimizer updates, gold eligibility закрыта. Measurement
assessment — частичная проверка наблюдаемых режимов; она не сертифицирует
структуру/точное равенство округлённых паспортов/промышленную применимость.

Проверки R06.86: **525 targeted Python tests**, **60 JS tests**, static build
и976/976 native admissions прошли. Core и frozen v3 dependencies неизменны.
Receipt: `reports/engineering-decision-physics-checks-2026-11-18/summary.json`.

Canonical evidence:
- `reports/engineering-decision-payload-plan-admitted-2026-11-18/summary.json`
- `reports/engineering-decision-payload-pilot-2026-11-18/summary.json`
- `reports/engineering-staged-episode-controls-2026-11-18/summary.json`
- `reports/engineering-staged-teaching-admission-2026-11-18/summary.json`
- `reports/engineering-matched-stage-physics-2026-11-18/summary.json`

Дальше: отдельный recipe на physics contrasts/measurement reasoning/conditional
arguments, native staged-policy inference qualification и полный learned v2
workflow. Затем family-heldout и более широкая physics/VLM очередь. Подробности
и hashes — LEARNING_PIPELINE R06.86.

## R06.85 — выполнен skill pilot; найден и закрыт обход equipment budget

**Fresh-base32-step skill pilot завершён: strict generated answers1/10→5/10,
целевой gate10/10 НЕ пройден.** Это8 TRAIN exemplars +2 menu-presentation probes,
не10 сетей и не полный workflow. Binding0/2→2/2, readiness0/2→2/2,
choice0/4→0/4, perception1/2→1/2.32/32 used-target native admissions прошли,
vision encoder5 forwards, finite gradients, frozen base и exact roundtrip
подтверждены. Auto-extra-epochs/GRPO не запускались. Original128-step phase
остаётся отдельной, её recipe/source fingerprints не изменены.

Диагностическое содержание4/10→5/10: base уже понимал обе readiness labels,
но добавлял Markdown; один binding был верен внутри wrapper. Остальные strict
улучшения — формат, не доказательство новых инженерных навыков. Без изображения
модель всё ещё отвечает100 вместо null. Все choice failures означают validate
при уже validated/fresh overloaded state. Read-only operation-name probe без
меню также0/2 у base и adapter: add_line вместо set_line_catalog. Это posthoc
train-state diagnostic; единственная причина не установлена.

**Обнаружен acceptance bypass в catalog-intervention/v1:** remove_line→add_line
ВСЕХ линий сохранял catalog edit counter0; adversarial control32/32 success.
Исправлен НОВЫМ equipment profile **catalog-replacement/v2**: публичный лимит
maximum_changed_routes=1 и счётчик затронутых маршрутов, общий для set/remove/add.
Легитимный remove/add одного маршрута разрешён, rejected edits не учитываются.
Новые controls: expert32/32, rebuild-all0/32 при physics_success=True.
Исторические source/receipts сохранены, прежний каталог с именем final теперь
historical v1 candidate, не current gold. Сравнивать v1/v2 scores напрямую нельзя.

Выпущены416 v2 teaching targets:192 positive expert choice и224 readiness,
включая32 hard negatives «физика допустима, operational budget нарушен».
Все cards/responses/terminals заново replayed. Failed-controller действия не
входят в positive SFT. **832/832** native text/visual-hybrid batches прошли
prefix/pixel/actual-label checks, max prompt3098, failures0. Это admission
нового оборудования/encoding, не улучшение learned policy. Gold gate закрыт.

Общий skill loader:6296 source rows после удаления48 точных source duplicates;
3978 unique visible inputs/targets,2318 повторных visible pairs, deterministic
label conflicts0. Proposed256-target sampler даёт64 на skill и выбирает
family/base/stratum/unique pair; сам не запускает optimizer. Lineage visual
frames восстанавливается из owner scene metadata.64/64 PUBLIC algorithmic
choice-episode controls (старый профиль ×2 seeds) проверили runner; exact
historical source сохранён перед добавлением episode factory.

Проверки: **479 targeted Python tests**, **60 JS tests**, native admission832/832
и static build прошли. Core и dependencies активного v3 job неизменны.
Receipt: `reports/engineering-skills-checks-2026-11-18/summary.json`.

Canonical R06.85 receipts:
- `reports/engineering-skill-pilot-plan-2026-11-18/summary.json`
- `reports/engineering-skill-pilot-2026-11-18/summary.json`
- `reports/engineering-skill-error-diagnosis-2026-11-18/summary.json`
- `reports/engineering-operation-probe-2026-11-18/summary.json`
- `reports/engineering-visible-skill-audit-2026-11-18/summary.json`
- `reports/engineering-replacement-challenge-v2-2026-11-18/summary.json`
- `reports/engineering-replacement-teaching-v2-2026-11-18/summary.json`
- `reports/engineering-replacement-native-admission-v2-2026-11-18/summary.json`

Следом: отдельный operation-selection/insufficiency recipe, value-token metrics,
затем learned v2 workflow с полным failure denominator. Full tiny gate,
heldout-family transfer, topology perception и industrial physics curriculum
остаются открытыми. Подробная очередь — LEARNING_PIPELINE R06.85.

## R06.84 — аудит обучающего сигнала, новая программа обучения и проверка VLM

**Качество инженерного агента пока не подтверждено.** Завершённый strict24k
TRAIN-прогон65-step adapter дал **1/8** (diagnose1/3, assemble0/3, repair0/2),
0 принятых правок против17 у base; base0/8. Это те же8 train-сетей/семейств,
не generalization. Нулевые protocol errors не означают заполненных аргументов.
Mixed128 phase R06.83 продолжается с прежними source fingerprints и recipe;
новые данные ниже в неё не подмешиваются. Старые записи «adapter pending» ниже
описывают прошлый момент.65-state admission завершён отдельно:50 prefixes,
42 successful corrections,364/364 targets допущены; это не learned recovery.

**Что реально нашли в данных:**
- 4292 targets происходят из112 задач на16 исходных сетях/8 семействах;
  2464 вызова без аргументов (57.4%), медиана prompt4301, максимум16905 tokens.
- 208 повторов input/action и64 состояния с несколькими допустимыми действиями.
  Последние не являются ошибочными метками: планы проверены физическим replay.
  Все3784 argument fields сохранены; гипотеза потери аргументов при serialization
  проверкой actual labels не подтвердилась.
- На всех16 исходных исправных train-сетях замена ВСЕХ линий на copper-1 и,
  отдельно, на copper-2 проходит физические ограничения. Старый набор слишком
  слабо учит выбирать оборудование по режиму. Нужны случаи, где выбор меняет
  допустимость, и отдельные operational constraints.
- Старые изображения не показывали ID требуемых маршрутов и нагрузок; полный
  JSON рядом позволял обходить визуальное наблюдение. Image admission сам по
  себе не означал, что vision encoder участвовал в optimizer step.

**Реализовано и выполнено:**
- Новая публичная сцена с короткими N/L/R/D handles, маршрутами, обязательными
  нагрузками, паспортами/единицами и каталогами. Stale observations запрещены.
  Scene и equipment zoom; контрфактическая пара меняет ровно одно видимое число.
- 1852 visual readout records,64 factual scenes,538 images; не1852 независимых
  сетей. Все5556 image/text/missing-source processor batches прошли pixel,
  prefix и actual-label checks; без источника positive label — null.
- editor-card/v1: текущая публичная схема, цель, revision, budgets и свежий
  результат расчёта;2448 targets из224 успешных teacher trajectories:
  960 action,528 conditional argument binding,960 readiness. Есть64 состояния,
  где расчёт свежий, но решение ещё не готово.4896 text/visual-hybrid batches
  прошли native processor. Это НОВЫЙ интерфейс, не улучшение весов само по себе.
- 32 paired catalog-selection задачи на16 train-сетях: перегрузка при формально
  допустимом паспорте, разрешена одна замена. Эксперт32/32, «заменить всё»0/32;
  PF независимо проверен NumPy reference до/после. Safe catalog name меняется
  между парами, порядок enum одинаковый: найденная order-подсказка устранена.
  Каталоги синтетические, не OEM;32/32 — контроль учителя, не результат Qwen.
- action-choice/v1:1152 decision records, до59 публичных вариантов. Ошибочные
  каталоги/объекты и преждевременный submit остаются доступны. Schema validity
  обеспечивает adapter, а не интеллект модели. Есть resolver; полноценный
  constrained decoder и standalone learned-policy результат ещё не выполнены.
- Настоящий image-conditioned optimizer smoke:2 updates, vision encoder вызван
  4 раза, finite gradients, frozen base/vision и exact adapter roundtrip прошли.
  Это техническая проверка, не доказательство улучшения VLM/агента.

**VLM проверена на6 dev-сетях/3 heldout families:** factual+changed-rating,
12 ответов на candidate. По strict JSON обе пары прошли у base4/6, adapter65
0/6. При ОТДЕЛЬНОМ диагностическом разборе одной Markdown fence оба правильно
прочли числа6/6 пар. Исходный operational score не изменён. Значит здесь видна
ошибка формата, а не потеря чтения изображения. В train missing-image/blank
контроле base выдумывает100 вместо null. Это новая задача на abstention.
Этот dev-набор теперь является development regression; для финального вывода
нужен нетронутый test. Проверено чтение номинала, не вся топология/инженерный план.

Canonical receipts (полные hashes — manifests и learning roadmap):
- `reports/engineering-supervision-research-2026-11-18/summary.json`
- `reports/engineering-catalog-shortcut-audit-2026-11-18/summary.json`
- `reports/engineering-visual-grounding-release-2026-11-18/summary.json`
- `reports/engineering-grounding-native-admission-2026-11-18/summary.json`
- `reports/engineering-card-curriculum-release-2026-11-18/summary.json`
- `reports/engineering-card-native-admission-2026-11-18/summary.json`
- `reports/engineering-catalog-selection-final-2026-11-18/summary.json`
- `reports/engineering-action-choice-curriculum-2026-11-18/summary.json`
- `reports/engineering-grounding-heldout-2026-11-18/summary.json`
- `reports/engineering-grounding-failure-diagnosis-2026-11-18/summary.json`
- `reports/engineering-native-visual-optimizer-2026-11-18/summary.json`

Следующая очередь и критерии — LEARNING_PIPELINE R06.84. Нельзя масштабировать
сырые trajectories/GRPO до tiny standalone gate. Остаются здоровые/невозможные
случаи, несколько дефектов, неопределённость, более широкие физика/семейства,
visual topology tasks и оценка переноса; текущий учебный мир — balanced400V Z-load.
Нельзя переносить его результаты на motor/AC/protection/EMT автоматически.

Проверки R06.84: **438 Python tests**, включая29 новых, и **60 JS tests**
прошли; static build выполнен, YAML валиден, diff whitespace clean. Все1852
record→image hashes проверены, core fingerprint и dependencies активной128-step
phase неизменны. Это targeted regression, не полный required-suite проекта.
Receipt: `reports/engineering-data-research-checks-2026-11-18/summary.json`.

## R06.83 — actual-student корпус допущен, mixed SFT v3 и проверяемое продолжение

Завершён полный strict24k TRAIN base-прогон Qwen3.5-0.8B: **0/8**, все задачи
в знаменателе,0 protocol errors,0 policy failures,17 принятых правок,3 submit.
Это восемь зафиксированных сетей/семейств, не heldout/generalization score.
Receipt `reports/engineering-tiny-base-2026-11-18/summary.json`, manifest
`015c250b78c3b3ebd769e6868d5c04e948eb1d500fa2647eb365c355bacac52a`.
Оценка65-step adapter и его paired report ещё выполняются; final score не заявлен.

**LP-06 data admission фактически выполнен для base-visited TRAIN states:**
117 visited prefixes,83 successful public-expert corrections,28 failed branches,
6 terminal/budget-exhausted states. Всё сохранено. Получено686 expert targets,
660 уникальных public context/action pairs; **686/686** прошли настоящий Qwen
processor (prefix/masks/budgets), failures0, max prompt14995. Это производные
восьми сетей, не83 независимых объекта. Source candidate — BASE model, adapter
manifest null; эти данные не называются on-policy состояниями65-step adapter.

Текущие source evidence:
- `reports/engineering-student-admission-2026-11-18/summary.json`
- full data campaign manifest `0deb10e7a0cf21c0bcb4814a4076f9ae5ff16e1cff56271ea5bee1d8a84adfa5`
- corrections manifest `7cf4f234ae1e1c9cd9a0591ff191ffd1f90fed0fedd34cf6eb23eff8e3fa7f52`
- preflight manifest `176ebb32901423bdc3ba63c85b8b232eb132b4ae680208f7bb5c12a5ac1d9a5d`
-128-step mixture plan manifest `9521dfb6348b1122054707450476bbb3f232ab3b8874a8de5b6fafe6322b2fac`
- planned source steps:64 procedural +64 base-student corrections, replacement.

Реализован **trainer v3** (`benchmarks/engineering_trainer_v3.py`):
проверяет обе data admissions, пересчитывает frozen mixture schedule/inventory,
проверяет фактическую семантику labels перед каждым обновлением. Поддерживает
явную новую фазу от completed v2 adapter (те же LoRA modules, fresh AdamW) и
строгое same-phase resume (тот же plan/identity, adapters/AdamW/CPU RNG/cursor).
Новая фаза и resume различаются; continuation не сбрасывает optimizer на chunks.
Frozen base, finite gradients, semantic targets и exact adapter roundtrip обязательны.
При сбое сохраняется sealed incomplete failure, а не пригодный policy checkpoint.

Добавлены `engineering_v3_policy` и `engineering_v3_eval`: только completed
owner checkpoint текущих source/target/contract identities; public-message-only
native inference, одинаковый strict24k profile, полный failure denominator.
Partial, missing weights, source drift или другой профиль отклоняются.
Это experimental inference, не deployment/industrial qualification.

Native lifecycle pilot запущен: одна двухшаговая phase с одним target каждого
источника и минимум одним parameterized call; seed выбирается только по labels,
без model outcomes. Проверяются uninterrupted против chunk/resume AdamW и LoRA,
точное warm-start состояние65-step adapter, frozen base/roundtrip и загрузка
через новый native proxy. **Нативный pilot завершён успешно**: LoRA и AdamW совпали побитно,
warm start/frozen base/roundtrip/proxy loading прошли; exact conditional
call не совпал с выбранным expert target;
двухшаговая численная проверка не считается learned recovery или full-policy score.
Receipt `reports/engineering-v3-native-pilot-2026-11-18/summary.json`, manifest
`db825b5336ecbda5035be86fae55d6ce7c2a5f05184c1b06eb3f38c4606382f3`.

Подготовлена и поставлена в исполнение условная **одна128-step фаза**:
`reports/engineering-v3-mixture-phase-2026-11-18/state.json`.
Gate native pilot + full data admission ПРОЙДЕН; начата новая128-step phase,
сохраняет checkpoint каждые8 шагов, восстанавливает тот же optimizer при продолжении,
затем выполняет standalone8-case evaluation и physical replay comparison с65-step
adapter. При gate rejection не обучается. Никакого автоматического recipe search,
новых эпох, GRPO или heldout tuning.128 steps — экспериментальная гипотеза,
не доказательство достаточности. CPU wall times при параллельных jobs не являются
сопоставимым resource benchmark.

Для следующей итерации отдельно ожидается полный65-step model rollout:
`reports/engineering-student65-admission-2026-11-18/state.json`.
Его source version, corpus/preflight и возможный plan будут отдельными; frozen
128-step phase использует явно закреплённый base-state corpus. Новые состояния
не подставляются в уже начавшийся recipe.

Проверки блока:409 Python-тестов,60 JS-тестов и сборка сайта прошли. Receipt:
`reports/engineering-v3-implementation-checks-2026-11-18/summary.json`.
Core fingerprint сохранён: `b82b66bfa07e737d627caa6d12844b70be19655fc41e3896cbe64503a332a84f`.

LP-02 full tiny-workflow gate и LP-06 learned recovery improvement остаются
открытыми. Контроль8/8 — public deterministic expert; модель не признана
самостоятельным инженером. Все exports provisional, eligible_for_gold_sft:false.
GitHub/CI, публикация, dev/test training и внешние model APIs не использовались.

## R06.82 — эпоха65 завершена, строгий допуск student-state корпуса

Нативное обучение Qwen3.5-0.8B на frozen tiny suite **ЗАВЕРШЕНО:65/65 optimizer
steps**. Проверены точный полный schedule, неизменность base weights и exact
adapter roundtrip. Rank16/alpha32, два language blocks,868864 параметра,
strict24k profile. Checkpoint manifest:
`45a69856b4effca6dc08e33722c771849f6b0ed35709c989d0b90ff526cdae67`.
Это завершённая первая train-эпоха, **не** прохождение closed-loop gate.

Base8-case evaluation и queued adapter/paired campaign ещё выполняются.
В завершённых промежуточных trace видны лишние inspections, повторные simulate,
неправильный тип location в диагнозе и незавершённая сборка. Частичный набор
НЕ объявляется итоговой accuracy8-case suite и не допускается в обучение.
Финальный success rate модели нужно читать только из complete sealed report.

Реализован следующий слой LP-06:
- `StudentCorrectionDataset` требует owner train/suite/model-evaluation/corpus
  manifests, повторяет весь actual-student rollout и каждую correction branch.
  Проверяет teacher revision, полный inventory failed/successful attempts,
  attribution student/expert и все counters. Нельзя удалить failure, подменить
  измерение, превратить student call в positive label или импортировать heldout.
- `engineering_student_preflight` проверяет ВСЕ expert targets настоящим pinned
  processor: prefix/causal masks/context. Пустой корпус не проходит; каждый
  overflow/failure сохраняется без truncation/drop. Считает unique public
  message/action targets, отдельно от числа зависимых trajectories.
- `AdmittedTextMixture` допускает только одинаковые strict24k corpora с полными
  passed preflights. Recipe предлагает примерно50/50 procedural/corrections,
  баланс kind/family/action, выбор episode и expert action; replacement и
  повторения явно записаны. Это proposed128-step plan, НЕ новый optimizer run
  и НЕ доказательство оптимальности такого смешивания.
- `engineering_student_campaign` ждёт complete sealed TRAIN model evaluation,
  экспортирует все visited-prefix attempts, выполняет native admission и только
  при полном успехе пишет owner-pinned mixture plan. При отказе сохраняет
  preflight_rejected; при сбое — incomplete failure. Не запускает SFT/GRPO,
  не использует dev/test и не отправляет данные внешним сервисам.

Evidence / локальные jobs:
- `reports/engineering-tiny-epoch1-2026-11-18/summary.json` — completed65
- `reports/engineering-tiny-epoch1-2026-11-18/checkpoint/summary.json`
- `reports/engineering-tiny-base-2026-11-18/` — оценка до seal не завершена
- `reports/engineering-tiny-campaign-2026-11-18/state.json` — paired campaign
- `reports/engineering-student-admission-2026-11-18/state.json` — student-data job
- `reports/engineering-student-admission-tests-2026-11-18.xml` — новые проверки

Проверки:360 уникальных Python-тестов,60 JS-тестов и сборка сайта прошли. Receipt:
`reports/engineering-student-implementation-checks-2026-11-18/summary.json`.
Posthoc train repair probe65-step adapter **завершился неудачей**:
`reports/engineering-tiny-epoch1-repair-probe-2026-11-18/summary.json`.
Семь wire-valid calls, но3 schema-invalid calls с пустыми обязательными
аргументами;0 из5 required slots заполнены,0 принятых правок,submit отклонён.
Physical replay подтверждён. Это один выбранный после base failure TRAIN-кейс,
не итоговая accuracy8-case suite и не оценка генерализации.
Новый `engineering_action_quality` разделяет wire/schema/tool/physical success;
receipt: `reports/engineering-epoch1-action-quality-2026-11-18/summary.json`.

Проверена отдельная гипотеза о потере аргументов шаблоном: **4292/4292 actual
supervised completions** декодированы и совпали с исходными действиями целиком.
Сохранены3784 argument fields /27372 value tokens; ни одно поле не потеряно.
Receipt: `reports/engineering-target-semantics-2026-11-18/summary.json`.
Это подтверждает семантику labels данного профиля, не умение модели их выдавать.
Debug microgate **ПРОЙДЕН**: fresh-base, восемь повторов одного TRAIN
set_line_catalog target1818; затем greedy generation из публичного teacher
prefix точно воспроизвела function и оба аргумента (equipment_id/catalog_id).
Путь `reports/engineering-argument-microgate-2026-11-18/summary.json`.
Это отдельный adapter, не продолжение/замена65-step checkpoint. Conditional-call
memorization не считается полным repair workflow или8/8 gate. Подтверждена
узкая learnability одного вызова, а не правильность следующего broad recipe.

Actual current-Qwen correction corpus и его native admission на момент записи
ещё НЕ подтверждены. До complete receipts нельзя считать LP-06 выполненным,
заявлять learned recovery, новую heldout accuracy или преимущество модели.
Дальше: закончить standalone paired evaluation; после измерения — выбрать
следующий recipe с admitted correction data. LP-02 tiny8/8 всё ещё OPEN.

## R06.81 — фиксированный tiny gate, standalone evaluator и student-state corrections

Реализован следующий блок LP-02/05/09: frozen tiny suite, полный train schedule,
standalone процедурные эпизоды и парная проверка с повторным исполнением физики.
Восемь clean TRAIN-задач выбраны ДО обучения и model scores: восемь базовых сетей,
восемь семейств, сборка3 / диагностика3 / исправление2. Все65 expert targets
входят ровно один раз в первую эпоху; порядок shuffle(seed42). Предельный prompt
10433 токена, суммарно278746 prompt tokens. Это debug memorization suite,
не независимая heldout оценка и не полный новый training corpus.

Публичный deterministic control решил **8/8**,36 правок и8 submit; переходы
повторно исполнены, итог подтверждён. Он не считается нейросетью и не открывает
neural tiny gate. Evaluator даёт модели только public messages, не вызывает
expert во время оценки, использует общий strict24k contract и свежий engine
для каждой задачи. Неверные calls расходуют budget; ошибки процесса и context
не исключают задачу. Сохраняются full denominator, protocol errors, edits,
submit, stop reason, breakdown по kind/family и число базовых сетей.

Paired auditor проверяет owner manifests, совпадение suite/source/contract,
base/adapter identities, инвентарь всех episodes, заново исполняет действия,
сверяет публичные ответы, terminal и пересчитывает метрики. Изменённые ответы,
приписанный success, пропущенные episodes и подменённые counters отклоняются.
Это проверка целостности и воспроизведения, не OS sandbox/внешняя аттестация.

**Нативный CPU-прогон первой эпохи65 шагов и base8-case rollout ЗАПУЩЕНЫ;
завершение и их success rate на момент записи ещё НЕ подтверждены.**
Trainer v2/source/data/preflight остаются закреплены; recipe ранг16,alpha32,
два language blocks, learning rate1e-4, frozen vision/head. Понижение loss
на разных targets не трактуется как завершение задач. Старый guarded dev0/7
сохранён как исторический результат; нового heldout результата здесь нет.

Local campaign ожидает sealed train/base, проверяет точный полный schedule,
автоматически оценивает adapter на тех же8 задачах и строит paired replay.
Ожидание конечное (3 часа до sealed входов); сбой записывается как incomplete,
не successful qualification. Дополнительные эпохи/GRPO/dev/test не запускаются
неявно. Проверять итог нужно в `summary.json` либо `failure.json` кампании.

Evidence / текущие локальные jobs:
- suite: `reports/engineering-tiny-suite-2026-11-18/summary.json`
- suite manifest `bcca8b715b7af3722de910e530f5a0ee3099dc3359a40228ca08629cb3080f29`
- control: `reports/engineering-tiny-public-control-2026-11-18/summary.json`
- training: `reports/engineering-tiny-epoch1-2026-11-18/recipe.json`
- started recipe SHA `4feb207861d10dfe43d044846f989056bfccd195dadebcab3cf401c238131f51`
- base: `reports/engineering-tiny-base-2026-11-18/` (до seal — незавершённый job)
- campaign: `reports/engineering-tiny-campaign-2026-11-18/state.json`
- будущие complete/paired receipts находятся внутри campaign root.

LP-06 получил отдельный train-only collector `engineering_student_recovery`.
Он принимает только полностью audited native-model TRAIN-rollout, повторно
исполняет каждый реально посещённый prefix, продолжает публичным expert в
ОСТАВШЕМСЯ исходном budget. Student/invalid actions — только контекст. Terminal
и exhausted состояния не превращаются в положительные labels; unsuccessful
continuations остаются в attempts и получают0 expert targets. Эксперт заново
инспектирует после ошибок, не получает private truth. Это реализованный
collector; **фактический корпус текущего Qwen rollout ещё НЕ экспортирован**,
нет нового admitted student dataset, SFT/DAgger update или learned recovery.

Проверки нового блока:25 дополнительных Python-тестов; вместе с прежними302
прошли327 уникальных тестов,60 JS-тестов и сборка сайта. Implementation receipt:
`reports/engineering-learning-implementation-checks-2026-11-18/summary.json`. LP-02 gate пока OPEN/RUNNING; следующий переход
разрешает измеренное завершение8/8, затем matched dev с полным знаменателем.
Если gate не пройден — анализ rollouts и изменение recipe, а не увеличение
числа синтетических примеров ради объёма. GitHub/CI не используются.

## R06.80 — разнообразные данные, независимый физический допуск и trainer v2

Выполнен новый synthetic train-пилот: **196/196 сценариев**, 28 базовых сетей,
14 канонических топологических семейств. Разбиение до генерации производных:
train — 8 семейств / 16 сетей / 112 задач; dev и test — по 3 / 6 / 42.
Семейства не пересекаются друг с другом и с тремя прежними топологиями.
Переименование оборудования и порядок массивов не меняют family identity.
Семь путей на train-задачу дают **784 траектории и 4 292 экспертных targets**;
это НЕ 784 независимые сети. Варьируются схема, длины, нагрузки, источник,
каталоги, идентификаторы, порядок исправлений и ошибочные начальные действия.

Допуск требует свежего физического решения, публичного expert replay и
независимого NumPy nodal-reference без production solver imports. Проверяются
напряжения, токи, loading и доставленные источником P/Q, наблюдаемость дефекта,
здоровое исправление, stale/nonconverged submit и неправильный диагноз.
Неудачный производный случай карантинирует всю базовую группу без resampling.
Область допуска: синтетические симметричные радиальные 400 V constant-Z сети,
сборка, диагностика и исправление трёх видов дефекта. Не OEM/натурный эталон,
не допуск машин, преобразователей или произвольных видов физики.

Реальный pinned Qwen processor проверил **4 292/4 292 targets**: prefix,
assistant-only causal labels и бюджеты. Первоначальный 16k профиль отверг
16 targets; они сохранены, не обрезаны и не исключены. Текущий общий профиль:
`qwen-native-strict-v1`, `compact-pf-v1`, context 24576, action 256,
24 действия, repeat guard 2, thinking false; max prompt 16905 токенов.
Strict-v1 явно запрещает prose до/после call: предыдущий native-v1 probe с
пояснением сохранён как protocol_error. Новый base smoke выдал один корректный
inspect_project; это не завершённая задача и не quality score.

Экспортированы **784 VLM-пары / 128 уникальных PNG**. Только публичная схема,
паспорта и требования после inspect_project; endpoints связывают ветви с
паспортами. Нет hidden diagnosis или будущих результатов. Все 784 пары
повторно отрендерены и проверены настоящим image processor: pixels/grid,
prefix/masks/budgets; max prompt 12037. Это static paired candidate data,
а не доказательство visual grounding или обученная VLM-политика.

Trainer v2: сбалансированное семплирование kind/family/path, language LoRA
rank16/alpha32 в двух последних mixed-attention блоках, 868864 параметра.
На native Qwen выполнены два отладочных шага, второй после восстановления
AdamW/RNG. Базовые веса неизменны, adapter roundtrip exact. Слои vision/head
заморожены. Это text-action обучение; VLM optimization ещё не выполнена.
**LP-02 closed-loop tiny-overfit не запускался; качество новой политики не
измерено. Два шага и loss не заменяют инженерный success rate.**

Текущие sealed evidence (исторические пакеты сохранены):
- `reports/engineering-data-release-2026-11-18/summary.json`
- train manifest `5e0b5ef8b96904e01e143674ad42ed83e510da64e0faf8c087e50b3fb79c8485`
- evaluation manifest `38e258f2b4fac6c0379e9dc564be4b248a8c18370e9c68c543c08fa44667e97e`
- `reports/engineering-data-strict24k-preflight-2026-11-18/summary.json`
- `reports/engineering-visual-strict-release-2026-11-18/summary.json`
- `reports/engineering-visual-strict-preflight-2026-11-18/summary.json`
- `reports/engineering-trainer-v2-strict-resumed-2026-11-18/summary.json`
- `reports/engineering-strict-base-wire-2026-11-18/summary.json`

Проверки: **302 целевых Python-теста и 60 JS-тестов** прошли. Core fingerprint сохранён:
`b82b66bfa07e737d627caa6d12844b70be19655fc41e3896cbe64503a332a84f`.
Далее — tiny train gate на 8–16 траекториях, самостоятельный matched dev,
здоровые/невыполнимые задачи, реальные student states, VLM ablations и GRPO
после допуска. Все новые данные provisional, eligible_for_gold_sft:false.
GitHub/CI и публикация не выполнялись.

## R06.79 — первичные конкурирующие работы, protocol contract и recovery foundation

В [LEARNING_PIPELINE.md](LEARNING_PIPELINE.md) добавлена сверка PowerAgentBench-SS,
GridMind, RestoreBench, DAgger for LLM agents, Agent Lightning и AgentGym-RL.
Методы benchmark/orchestration отделены от обучения; результаты чужих4B/8B
моделей не переносятся на наш0.8B. GitHub-код/CI не использовались. RL без SFT
возможен в других работах; наш warm-start — выбранный путь, не универсальный закон.

Реализована основа LP-01: frozen RunContract, JSON/native Qwen rendering,
строгий bounded native parser, роль tool и реальные call IDs, thinking/budgets/
view/guard identity. Из exposed mask убран недопустимый policy export. Native
parser не принимает JSON как native success. Скалярное подмножество нынешних
ID/enum/string arguments; nested/delimiter-bearing arguments не квалифицированы.
Полная EpisodeSpec/RenderSpec и heldout protocol selection ещё открыты.

Начата LP-04: публичный RecoveryPolicy продолжает после ошибок/чужих действий,
при расхождении повторно читает проект. Шесть scripted путей по7 TRAIN-задачам:
42/42 expert-replays с свежим PF/submit,189 expert targets,35 perturbation-actions
только в контексте. Это НЕ42 независимых сети и НЕ настоящий student DAgger.
Dev/test collector отклоняет. Corpus и reusable RecoveryDataset проверяют
полный replay/source attribution и не обучают injected mistakes положительно.

Текущие sealed evidence:
- `reports/engineering-recovery-final-train-2026-11-18/summary.json`
- corpus manifest `ed394f64cb6a921163a57568f9b9994c38996f087a88279aa4a22e8399746cf6`
- `reports/engineering-recovery-aligned-protocol-2026-11-18/summary.json`
- `reports/engineering-native-json-aligned-probe-2026-11-18/summary.json`

Реальный pinned processor проверил189 targets В КАЖДОМ формате, prefix/mask/
budgets, всего378 expert batches. Никакого нового optimizer run здесь не было;
корпус provisional, eligible_for_gold_sft:false. Старый trainer v1 не принимает
его автоматически: нужен trainer recipe v2 и LP-02 closed-loop tiny-overfit.

Однокейсовый TRAIN-probe base weights:4 действия,compact view,guard2,256 tokens
на действие. JSON:simulate→validate→simulate→simulate; native:inspect_project
→inspect_equipment(line1)→inspect_equipment(line2)→validate. Оба success=false.
Это wiring/поведенческий probe, не heldout-победа native и не новый dev score.
Старый guarded dev0/7 и отсутствие подтверждённого инженерного навыка остаются.

Проверки блока:257 целевых Python-тестов,60 JS-тестов,сборка сайта и
проверка целостности трёх текущих evidence-комплектов прошли. Core fingerprint
сохранён; новый SFT/GRPO update не выполнялся.

Далее: LP-02 tiny closed-loop gate и расширенная language-LoRA в trainer v2;
LP-03 разнообразные topology/parameter train families; LP-06 реальные student
prefixes вместо scripted perturbations. VLM/GRPO квалификация ещё открыта.

## R06.78 — согласованный план обучения v2 (запланирован, не реализован)

Полная спецификация: [LEARNING_PIPELINE.md](LEARNING_PIPELINE.md).
Этапы LP-01–LP-10 добавлены в machine-readable `agent-designer.yaml`.
Текущее ML quality gate остаётся failed; нового обучения в этом шаге нет.

Первый приоритет: единый protocol/view/run contract, native Qwen tool ablation
и проверка tiny closed-loop обучаемости. Нынешний raw-trained last-block rank4
CPU epoch не является квалифицированным промышленным SFT-рецептом; завершённый
paired score относится только к compact/guarded-профилю. Эти факторы нужно
разделить, а не объявлять одну причину0/7.

Далее: grouped procedural splits до prefixes/images, public recovery teacher,
SFT v2 с корректным masking и более широкой language-LoRA, train-only student
state aggregation, multimodal perception/tool-use, затем stateful GRPO и
замороженный final ID/OOD test. Объёмы и гиперпараметры в плане — начальные
предложения для пилотов; критерии приёмки и ограничения указаны по этапам.
Семь прежних dev остаются regression smoke, не широкой оценкой генерализации.

План опирается на локальные исходники/evidence и первичные документы Qwen,
TRL SFT/GRPO и DAgger. TRL main docs не заменяют проверку pinned runtime;
GitHub/CI, автообновления и отправка данных внешним LLM не включены.

## R06.77 — измеренная guarded dev-оценка: workflow gate НЕ пройден

Реально выполнено парное сравнение Qwen3.5-0.8B base и rank4 epoch-adapter:
семь одинаковых dev-задач (сборка1, диагностика3, исправление3), максимум12
действий,64 новых токена,16 384 контекста,greedy. Отдельный профиль
`compact-pf-v1`, loop guard2: одинаковые распознанные действия с точно одинаковым
публичным payload на той же revision завершают эпизод отдельной причиной.
Это НЕ оценка неограниченной 12-ходовой способности моделей.

Результат: base0/7, adapter0/7, improved0, regressed0. У каждого кандидата
14 действий simulate,0 правок,0 submit,7 остановок repeated_public_observation.
Структурный формат действий допустим, context failures0. Одна эпоха из38 шагов
не улучшила измеренный workflow в этом профиле. ML quality gate закрыт;
инженерная самостоятельность и готовность к промышленному применению не заявлены.

Текущие sealed evidence:
- `reports/engineering-paired-guarded-dev-2026-11-18/summary.json`
- paired manifest `77bf659fa75aa1d6a743f9e13d5cf9451c27b908744957690ee20386bbf2e362`
- `reports/engineering-guarded-audit-2026-11-18/summary.json`

Первоначальный raw-прогон остановлен из-за стоимости повторных запросов;
`reports/engineering-paired-dev-2026-11-18/interruption.json` честно фиксирует
неполное покрытие. Он запечатан как partial evidence, не полный paired score;
невычисленные случаи не объявляются ошибками модели. Один завершённый raw
случай сборки содержит12 simulate и неуспех (около8 минут на CPU).

Добавлен отдельный детерминированный публичный инженер: получает только ТЗ,
каталог, проект и результаты разрешённых инструментов, не читает скрытый bug.
В таком же compact/guarded dev-профиле он прошёл7/7 с реальным PF и submit:
`reports/engineering-public-control-final-dev-2026-11-18/summary.json`.
Это контроль разрешимости текущих задач, не нейросеть и не независимая
валидация физики. Проверки owner-политики на всех21 задачах — regression
среды; нейросетевой test-прогон не выполнялся.

Версионный compact view сохраняет точные значения критериев, P/Q/I/потерь,
паспорта, warnings и None для обесточенных терминалов. Опущены layout,
дубли U/V, фазные углы, opaque islands и load-law residuals; профиль не годится
для произвольной защиты/phasor-аналитики. Совпадающие полные PF-результаты
получают ссылки/hash, различные наблюдения сохраняются. На38 train-префиксах
native tokenizer измерил130 138→93 274 токена (-28.3%), максимум8822→5753.
`reports/engineering-view-preflight-2026-11-18/summary.json`.
Это измерение размера контекста, не изолированная оценка runtime speedup.

Аудит проверяет manifest/child anchors, split, paired IDs, budgets, rewards,
считает отсутствие submit/правок, повторы и ошибки. Он не меняет score и
не делает dev/test данными SFT. Физический grader остаётся прежним.
198 целевых Python-тестов и60 JS-тестов прошли; core fingerprint не изменился.

Следующий блок (в таком порядке):
1. Публичные train recovery-траектории после первого simulate/ошибки инструмента,
   несколько корректных порядков действий и task-kind coverage. Согласовать
   training view с версионным evaluation view;38 expert-prefixes недостаточны
   как доказательство способности действовать из собственных состояний.
2. Procedural train-семейства/паспортные параметры с новым owner manifest и
   каноническим исключением dev/test-топологий. Не копировать dev IDs/ответы.
3. Проверить grounding и завершение train-workflows перед новым SFT-прогоном;
   повторить заранее закреплённый dev-профиль без скрытого изменения guard.
4. Отдельно исследовать raw/native-tool протокол как ablation. Нельзя объяснять
   текущий ноль одной причиной без такой проверки. Unrestricted raw paired
   остаётся незавершённым, а не заменяется guarded-цифрами.
5. GRPO допускается после появления успешных train-rollouts/полезной вариации
   награды; guard и process score не заменяют финальную физическую приёмку.

## R06.76 — детерминированный action-SFT epoch, AdamW resume и paired runner

Добавлен `benchmarks.engineering_train`: полный перемешанный train schedule,
masked loss, rank4 adapters, конечные градиенты и контроль неизменности базы.
Промежуточный chunk сохраняет adapter, AdamW moments/step и CPU RNG через
safetensors, без pickle. Возобновление требует owner manifest, совпадения
кода/модели/корпуса/плана, курсора и истории порядка примеров.

Первый native chunk из двух шагов выполнен и запечатан:
`reports/engineering-epoch-head-2026-11-18/summary.json`.
Полный native epoch завершён продолжением этого checkpoint: 38/38 шагов,
каждый из 38 train-примеров использован ровно один раз. Базовые веса не
изменились, adapter roundtrip дал точное совпадение logits.
`reports/engineering-epoch-complete-2026-11-18/summary.json`.
Это одна эпоха на семи authored train-задачах, не доказательство инженерной
генерализации.
На тестовой модели непрерывный и разделённый pipeline совпали точно по весам,
optimizer tensors, RNG и истории loss. Это не измерение эквивалентности двух
полных native Qwen-прогонов на разных host/версиях библиотек.

`benchmarks.engineering_compare` готовит парные base/adapter dev-прогоны:
семь одинаковых случаев, default 12 действий / 64 новых токена / 16 384 контекста,
greedy generation. Двенадцать действий оставляют место для inspect/validate
поверх минимальной сборки. По каждой задаче фиксируются успех/неуспех,
improved/regressed и stop reason. Непройденные случаи не исключаются.
Готовый epoch-adapter загружен в реальный worker и выполнил один dev-prefix
вызов: допустимое действие simulate. Это smoke загрузки/генерации, не результат
полного эпизода. `reports/engineering-epoch-inference-2026-11-18/summary.json`.
Полный native paired-прогон на новом адаптере пока не выполнен: улучшение
инженерных решений не заявляется. Test не использовался.

После сбоя candidate-процесса новый эпизод перезапускает его, не перенося поздний
ответ. Переполнение контекста отдельно учитывается как context failure.
Незаконченный epoch-checkpoint допускается для resume, но не как законченный
адаптер paired-сравнения. Корпус по-прежнему provisional, без VLM/GRPO qualification.

Проверки нового блока: 165 целевых Python-тестов, включая полный split/resume
pipeline на тестовой модели и парный comparator с одинаковыми бюджетами.
60 JS-тестов, сборка сайта и проверка целостности native-артефактов также прошли.
Следующая проверка качества — реальный paired dev-прогон, затем расширение
train-топологий и параметров; test должен оставаться закрытым при настройке.

## R06.75 — replay-checked SFT loader, маска loss и LoRA qualification

`benchmarks.engineering_sft.ActionDataset` принимает закреплённый manifest,
проверяет модель, ревизию среды, полный train-инвентарь и формат каждого примера.
Действия повторно исполняются owner-средой: история и результаты расчёта должны
совпасть с реальным публичным replay. Dev/test, подменённые результаты,
неполные траектории и произвольные маски отклоняются.

Для каждого батча заново выполняется native processor: точный prefix,
числа токенов, attention и область assistant-completion проверяются до forward.
Вход получает labels=-100, ответ — настоящие token IDs. Batch-size=1,
без padding/усечения/неявного объединения примеров. Causal selected-logits
loss сравнен с полной shifted CE и проверен по градиентам на prompt-позициях.

Реальный Qwen3.5-0.8B проверил все 38 записей: 130 138 masked prompt-токенов,
545 supervised токенов. Выполнен один шаг LoRA rank4 на примере 0:
train loss 0.193981 → 0.169758, ненулевые конечные градиенты, базовые веса
не изменились. Сохранённый адаптер восстановлен с точным совпадением logits.
Это проверка работоспособности тракта, не обучение инженерному решению задач.

Текущее свидетельство с закреплёнными core/loader/моделью/корпусом/планом:
`reports/engineering-sft-pinned-qualification-2026-11-18/summary.json`.
Ранний `engineering-sft-qualification-2026-11-18` сохранён как предыдущий probe;
его не следует использовать вместо текущего pinned evidence.

Worker принимает owner-pinned `--adapter` + `--adapter-manifest-sha256`,
проверяет receipt, модули, формы и конечность весов перед генерацией.
Адаптер не меняет исходный checkpoint. Корпус остаётся provisional,
`eligible_for_gold_sft:false`; один train-step не доказывает dev-улучшение.
Реальная генерация после загрузки адаптера проверена отдельно:
`reports/engineering-adapter-inference-2026-11-18/summary.json`.
На одном train-префиксе получено допустимое действие simulate; это smoke,
не решение эпизода и не свидетельство улучшения. 155 целевых Python-тестов,
60 JS-тестов, сборка сайта и проверка manifest прошли.

Далее: полноценный train schedule, восстановление optimizer/порядка примеров,
сопоставимые base/adapter dev-прогоны с достаточным числом действий, расширение
train-топологий и только затем замороженный test. VLM/GRPO ещё открыты.

## R06.74 — локальный Qwen tool-loop и train action-supervision

Добавлен owner-run цикл `benchmarks.engineering_rollout`: кандидат получает
только публичные сообщения, возвращает одно строгое JSON-действие. Закрытый
проект, проверяющий и награда не передаются модели. Это разделение данных и
процессов, не OS sandbox. Локальный Qwen worker проверяет закреплённые файлы
модели и работает без сетевой загрузки или изменения весов.

Ошибки формата расходуют бюджет, исключения/тайм-ауты не исключают задачу
из знаменателя. После тайм-аута candidate-процесс прекращается: поздний ответ
нельзя принять за ответ на следующий запрос. Контекст не обрезается скрыто;
переполнение является явным неуспехом. Отчёты имеют раздельные split, бюджеты,
пины исходников и manifest. Привилегированный teacher не является candidate.

Реальный processor Qwen3.5-0.8B проверил 38 train-префиксов семи задач:
максимальный вход 8 836 токенов (`reports/engineering-context-local-2026-11-17`).
Экспортированы 38 предварительных train action-supervision записей: нативно
проверены точное токенное продолжение assistant, непустые метки и полный
контекст; максимум 8 833 токена с компактными JSON-ответами.
`reports/engineering-action-supervision-2026-11-17/summary.json`.
Эти результаты относятся к teacher-траекториям, не произвольным 32-ходовым
историям. `eligible_for_gold_sft:false`, оптимизатор не запускался.

Измеренный Qwen base dev-probe: 7/7 случаев, 2 действия на случай,
14/14 структурно допустимых действий, 0 завершённых задач. Это smoke-проверка
контракта, не полный benchmark: двух действий недостаточно для сборки проекта.
Веса не дообучались, test не запускался. Запечатанный результат:
`reports/engineering-qwen-dev-probe-2026-11-17/summary.json`.
После прогона транспорт дополнительно защищён deadline на запись в stdin
нечитающего процесса; изменённый транспорт проверяется отдельными тестами,
не выдаётся за новую измеренную модельную оценку.

Проверки блока: 134 целевых Python-теста, 60 JS-тестов, сборка сайта и
проверка целостности четырёх комплектов свидетельств прошли.

Следующие ворота: loader и фактическая проверка loss mask, полный learned-policy
прогон с закреплённым бюджетом, затем SFT и повторная dev-оценка. Test нельзя
использовать для настройки. Визуальные tool-наблюдения и GRPO ещё не
квалифицированы; browser Agent Lab пока не запускает Qwen автоматически.

## R06.73 — инженерные tool-use эпизоды и Agent Lab

Реализованы 21 ограниченное задание: сборка, диагностика и исправление
пассивной PF-сети. Три различные топологии разделены на train/dev/test
(по 7 задач); публичный HTTP API выдаёт только train. Неисправности:
сопротивление кабеля, допустимый ток, отсутствующее соединение.

Состояние принадлежит серверу, требования неизменяемы. JSON-схемы инструментов,
32 вызова / 4 расчёта, маска инструментов, непрозрачные ID, терминальная
проверка и обязательный свежий физический расчёт. Правка отменяет измерения.
Agent Lab показывает ТЗ, журнал, публичные измерения и результат проверки;
перезагрузка страницы восстанавливает эпизод из RAM сервера без расхода бюджета.

Привилегированный детерминированный teacher прошёл 21/21 сценарий. Это проверка
среды, не результат обученного агента. Только train-трассы экспортированы;
`eligible_for_gold_sft:false`. Нового обучения Qwen не было. Корпус пока не
прошёл проверку токенизации/визуальных входов для SFT или GRPO.

Запечатанное свидетельство: `reports/engineering-agent-final-2026-11-17/summary.json`.
Код эпизодов SHA256: `a082382e997eaf534527e0c22d87d3de039fd75c0e23909701c323da3999f332`.
Промежуточные прогоны заменены этим свидетельством, не являются gold.
Проверки: 95 целевых Python-тестов, 60 JS-тестов и agent HTTP integration прошли.

17 семейств закреплены в `agent-designer.yaml`; исполнены только три ограниченных
семейства. Следующая очередь: адаптер model/tool-loop, проверка контекста,
изолированный heldout-прогон, затем AC/PV и RMS-задачи. Наличие компонента
физики не означает квалификацию нового эпизода.

## R06.72 / VIS-01 — AC PF,PQ/I/ZIP и статические PQ/PV генераторы

Добавлен профиль ac_power_flow с отдельной совместимостью оборудования.
V5 проекта мигрирует v1–v4,старые нагрузки получают явную модель Z и
параметры;ничего не отбрасывается. Паспорта PQ/I/ZIP содержат отдельные
P/Q-веса и окно закона. До8 статических генераторов PQ/PV:задания P/Q
или P/U,пределы Q,ограничение заданий по введённым kVA. Native StudySpec
сохраняет настоящие объекты генераторов и моделей нагрузки.

OpenDSS-выход не принимается только по convergence:проверяется окно
закона нагрузки,аналитическая P/Q(U) по каждой фазе,воспроизведение
команд генератора и пределы Q. Для генератора окно0,9–1,1pu фиксировано
по поддержанному native-поведению;вне окна возможный fallback отклоняется.
PV-задание U и фактическое U показаны отдельно;нативная near-Q-limit
диагностика отделена от фактического достижения границы. Нельзя считать
удержанным напряжение,если Q закончилась. Изолированный PQ/PV не становится
формирующим источником и не получает фиктивные P/Q/U.

В редакторе/статическом viewer — символы,паспорта и инспекторы генераторов,
модели нагрузок,фактические P/Q/I,проверка закона нагрузки и таблица
оборудования. Пример150kW генерации сPQ/ZIP нагрузками даёт около28kW
экспорта во внешнюю сеть в проверенном authored режиме. В браузере
проверены сборка,расчёт,таблица и переключение PV сQmin/max±2kvar.

Проверки:57 JS,62 Python,19 HTTP. Независимые формулы Z/I/P/ZIP(U),
разные активные/реактивные ZIP-веса,баланс сгенерированной/потреблённой P
и потерями,обратный поток,PV-удержание и Q-limit,потеря внешнего источника,
запрет native fallback и несовместимых профилей. Старые RMS/PF/fault
проверки сохранены. Производственное ядро и нейронные веса не менялись.

Границы:стационарный PF;нет динамики ротора/AVR или generator fault
contribution. Генераторы/PQ/I/ZIP в прежних RMS/пассивном КЗ не подменяются
эквивалентами,а отклоняются. Новые ручные записи не включены в gold-корпус;
semantic gate Qwen и closed-loop GRPO остаются открыты. Следующая очередь:
явный вклад машин в КЗ с независимым эталоном,затем подключение coupled-RMS
машин/регуляторов и сетевой автоматики к общему GUI.

## R06.71 / VIS-01 — пассивные PF/3ф КЗ,аппараты и статический инспектор

Закрыт ограниченный сквозной блок редактора:профили passive_power_flow и
passive_3ph_fault на существующем OpenDSS backend,два редактируемых примера,
паспорта/положения выключателей и разъединителей,фазный инспектор steady.html.
V4 проекта мигрирует v1/v2/v3. Смена профиля не удаляет оборудование;
моторы/временные команды в пассивных режимах явно отклоняются,нет их
молчаливого исключения или выдуманного PQ/Z-эквивалента.

PF допускает замкнутые сети и обесточенные острова. Статические аппараты
имеют явный конечный R+jX. При проверке обнаружено ограничение существующего
ядра:замкнутые Switch сливаются топологией даже при representation=series,
отдельного тока/импеданса такой объект в OpenDSS не даёт. Ядро в этом блоке
не менялось. Мост редактора явно понижает замкнутый статический аппарат в
Line с теми же R/X/ID,разомкнутый в разрыв;паспорт/положение сохранены в
assumptions экспортируемого native StudySpec и в проекте. Экспортированный
снимок реально воспроизведён через штатный run_study_document с тем же током.
Это не квалификация общей динамической модели сетевых выключателей.

КЗ ограничено пассивным трёхфазным конечным Z на заданной шине,без вклада
моторов и IEC60909 поправок/пиков/несимметрии. Повреждение на обесточенном
участке отклоняется. Показаны фазные токи КЗ и отдельно ток через аппарат;
сопоставление с отключающей способностью — один RMS-case,не селективность,
не автоматический trip и не worst-case qualification. Разъединитель не
получает breaker-approval. Обесточенность отделена от численной несходимости,
неизвестные терминальные каналы показаны как «—»,а не фальшивый измеренный0.

Проверки:53 JS,47 Python,15 HTTP. Независимые SI-формулы подтверждают
U/угол в последовательной Z-сети,ток3ф КЗ и mesh-схему с параллельной ветвью;
проверены I²R аппарата,открытый остров,запрет КЗ на мёртвом узле,явный отказ
моторов/events,местные базы трансформатора и native export replay.
Браузер:PF5/5 живых узлов;после размыкания q1 —2/5 и3 обесточенных;
3ф КЗ на n2 сR=.001Ω —7244,91A в каждой фазе в данном authored примере.
PF/КЗ результат содержит core SHA,bridge SHA,native-study SHA и backend version.
OpenDSS0.9.4/dss-python0.15.7,производственное ядро b82… не менялось.

Открыто:широкий PF-каталог PQ/ZIP/генераторов,несимметричные/нормативные КЗ,
вклад машин в КЗ,координация/отказы/АВР,динамические сетевые коммутации и
острова в RMS/DAE;далее coupled RMS генераторов/регуляторов и DER/storage.
Эти GUI-профили не покрывают все возможности Python-компонентов. Новые
ручные PF/КЗ не включены в gold-корпус;Qwen не обучалась,semantic gate и
closed-loop physics-scored GRPO остаются открыты.

## R06.70 / VIS-01 — реальные трансформаторы и многовольтный ручной RMS

Добавлены два явных профиля редактора:НН motor-start и многовольтный RMS.
Формат проекта v3 с миграцией v1/v2;у каждой шины своё Uном,источник и моторы
получают соответствующую базу. Кабель не может соединять разные напряжения.
Смена профиля сохраняет состав сети,несовместимость блокирует запуск.

Ручные двухобмоточные трансформаторы:до8 на дерево16 шин,Yy0/Dyn11/Dyn1/Dd0,
R%/X% отдельно,фиксированные ответвления0,8–1,2pu,номинальная мощность.
Используются существующие модели/матрицы run_induction,не отдельная UI-физика.
Компилятор явно задаёт соединения и нейтраль по группе,нулевые потери стали
и ток намагничивания. Есть редактируемый пример10/0,4kV с2 моторами.

Grid Studio сохраняет локальные базы и показывает трансформатор,токи обеих
сторон,отдающую/приёмную P,Q,потери меди и загрузку по max(|S1|,|S2|)/Sном.
Узлы показывают линейное U и угол. Осциллограммы привязаны к выбранному
оборудованию:токи/P/Q сторон трансформатора,потери/загрузка,локальное U/V/угол,
нагрузочные и исходные каналы. Агентский режим обрезает массивы и время по
текущему префиксу. Native StudySpec экспортируется через
отдельный локальный API:это полный проект с будущими командами,не наблюдение
для агента. Past-only экспорт отдельно сохраняет публичные паспорта обмоток
и прошлые измерения,без будущих событий и внутренней тепловой реплики.

Проверки:43 JS,31 Python,9 HTTP. Проверены4 группы,аналитическое отношение
токов25 для10/0,4kV,баланс P с потерями,30° сдвиг Dyn11,эффект обеих отпаек,
обратное питание,запрет разноуровневого кабеля и несовместимого профиля,
миграция и roundtrip MV-наблюдения. В браузере трансформатор удалён из примера
и собран заново через две шины;задана первичная отпайка1,025 и выполнен
четырёхсекундный расчёт,инспектор показывает7,07A HV/181,22A LV и315W потерь
на наблюдавшемся отсчёте. Это проверка интерфейса и ограниченного преобразования
данных,не новая независимая квалификация всех MV-схем.

Границы:симметричная положительная последовательность100–35000V,один источник,
одно дерево,фиксированные ответвления. Нет намагничивания/потерь стали/броска,
динамического РПН,сетевых коммутаций/защит,островов,синхронных машин/DER в
редакторе. Следующий блок:подключение совместимых видов расчёта PF/КЗ и
сетевых аппаратов;затем coupled RMS/генераторы/регуляторы. Масштабирование,
полный охват оборудования и closed-loop physics-scored GRPO остаются открыты.
Новые MV-ручные прогоны не стали gold SFT. Веса Qwen и закрытые корпуса не менялись;
semantic gate модели остаётся непройденным. Производственное ядро неизменно.

## R06.69 / VIS-01 — инженерный каталог,расширенный профиль и задачи агента

Добавлены16 карточек оборудования с поиском,группами и явной совместимостью
с текущим решателем;покрыты equipment-классы Network и двигатели/тепловая защита.
Есть дерево проекта,независимая прокрутка панелей,закреплённый запуск,
редактор64 команд источника/контакторов/момента. Расчётный профиль реально
расширен constant-Z нагрузками,двухклеточными двигателями и I²-защитой.
V2 проекта с явной миграцией v1;старые записи и sealed корпуса не изменены.

Проверки:37 JS,22 Python (включая прошлые3 exporter checks),7 HTTP.
Добавлены физические проверки U² нагрузки и баланса P с потерями,
двухклеточного разгона,точной стороны теплового отключения/выбега,
провала/восстановления и запрета некорректных команд. API проверяет реальную
комбинацию новых моделей и past-only экспорт без теплового oracle.
Исправлен in-memory экспорт наблюдения без исходного manifest (не добавлять
undefined SHA). Браузером собраны3 мотора+Z-нагрузка,включены две клетки и
тепловая защита,выполнен провал/восстановление питания с открытием результата.

**Полный охват оборудования в ручном UI ещё открыт.** Карточки трансформаторов,
сетевых выключателей,генераторов,DER,накопителей,шунтов,заземления,измерителей,
КЗ и гармоник показывают существующие модели и недостающий UI-профиль;
неподдерживаемые элементы не имитируются.

Следующие интерфейсные блоки:
1. Общий паспорт проекта и адаптеры видов расчёта:multivoltage PF/КЗ/motor-start/
   coupled RMS;переключение профиля с отчётом совместимости,без молчаливых пропусков.
2. Реальные трансформаторы/обмотки,нагрузочные модели и сетевые аппараты;
   затем генераторы/регуляторы и автоматика в совместимом RMS/DAE профиле.
3. DER/storage,фазные,гармонические/EMT профили по отдельным границам применимости.
4. Измеренное масштабирование и единый отчёт/сравнение сценариев.

**Обучение агента:**636 SFT views/159 префиксов/21 трасса пока prepared;
последний adapter9/48 dev,semantic gate открыт. UI не означает новой обученной
способности. Курс задач:чтение схемы/показаний и обоснованный отказ → диагностика
с выбором инструментов → изменение схемы/пусков и проверка обязательных режимов →
closed-loop GRPO. Сначала semantic-field loss/seen-case overfit,затем более
разнообразный независимо проверенный корпус и frozen family-disjoint dev/test.
Только публичные паспорта и текущая история поступают модели;oracle/будущие
события/эталонный verdict используются отдельно для проверки. Ручные одиночные
прогоны не импортируются в gold-корпус автоматически. Нового обучения не было.

## R06.68 / VIS-01 — ручной конструктор с локальным RMS-расчётом

В `web/editor.html` реализован сквозной процесс: добавление/соединение шин,
линии и двигатели, параметры SI/pu, перенос шин, удаление с Undo/Redo,
автосохранение, JSON-проект, Python-валидация и запуск SciPy с открытием
результата в Grid Studio. В браузере собраны4 шины/3 линии/3 двигателя,
задан пуск M3 в0,5s, проверены диагностика изоляции и pre_event состояние.
31 JS-проверка (12 новых),14 Python-проверок (11 новых),7 HTTP-проверок проходят.

Ограничения: один эквивалентный источник, радиальная симметричная50Hz сеть
100–1000V, до16 шин/16 одно-клеточных двигателей, вентиляторная нагрузка,
до10s, без тепловой модели, защит и иных семейств оборудования в редакторе.
API только локальный; одиночный расчёт не является независимой квалификацией.
Доступные Python-компоненты за пределами этого профиля ещё не представлены
в ручном интерфейсе. Статическая сборка требует локального сервера для расчёта.
Старые sealed reports не изменялись, веса Qwen не менялись.

## R06.67 / VIS-01 — локальный Grid Studio

Добавлен `web/grid.html`: интерактивная однолинейная схема с фактическими
P/Q, U/I, скоростями, контактами и синхронными осциллограммами. Две радиальные
конфигурации,12 записей R06.66; данные сверены с закреплённым source manifest.
Потоки восстановлены из комплексных напряжений и параметров линий.
Воспроизведение различает стороны коммутации; PNG/SVG и прошлый JSON-префикс
экспортируются. Режим агента скрывает будущие события, вердикт и тепловую реплику.
19 JS-проверок и3 Python-проверки экспортера проходят; браузером проверены
схема, инспектор, стороны события, агентский режим и PNG-экспорт.

Границы: сохранённые малые радиальные RMS-кейсы; нет live backend, редактора,
кольцевого/GIS-вида и измеренного large-graph rendering. Импорт JSON проверяет
структуру, но не удостоверяет физику. Следующая очередь визуализации:
связь с новыми расчётами → редактор и покрытие оборудования → кольцевые схемы
и многоуровневое напряжение → измеренное масштабирование Canvas/WebGL.
Прежний semantic gate Qwen остаётся открытым; обучение в этом блоке не выполнялось.

## R06.66 / DES-03 + VIS-01 — новая физика трёх двигателей, SI-сверка и корпус v2

Закрыт следующий ограниченный блок моделирования и подготовки данных.
Модель Qwen3.5-0.8B **не переобучалась**, её прежний semantic gate остаётся
непройденным. Новые проверки объясняют, почему нельзя просто считать
успешным прежний низкий общий SFT loss.

- **12 seen-train generation probes:**первые below/mixed/running observations
  выбраны по train labels до inference, затем одни и те же3 IDs проверены
  в4 modalities. Assistant messages удалены из model inputs, public blocks и
  PNG сохранены. Image/text/combined по1/3, schematic0/3 — всего3/12.
  Ошибка есть и на знакомых примерах; одно отличие motor count/topology
  не объясняет измеренный collapse. Это небольшой диагностический subset,
  не полная train accuracy и не новый dev/test результат.
- **4 native likelihood/prefix/cache controls** в историческом installed
  runtime обученного adapter3c4…:first-assistant-token full/prompt logits
  и2 cached/uncached generated tokens совпали в пределах2,8e-5 против
  заранее заданного1e-3 FP32 bound. Полная equivalence всех decoding steps
  этим не доказана. На3 text probes format NLL0,015–0,016, semantic
  class/flag NLL0,130–0,359. На refusal true flag NLL1,201 против format0,026;
  общий teacher loss0,104 скрывает неверный смысловой бит. Никаких updates,
  reward repair или test inputs в этих controls не было.
- **Два новых train P3 проекта:**3 single-cage motors на прежних3 nodes,
  два мотора совместно на n2. Это новое wiring с новым спросом150kVA на n2,
  а не переименование четырёхузловых dev/test проектов. Схемы star/chain;
  каждая включает simultaneous,staged,restart и motor_open/close отдельно
  для m,m2,m3 в3,0/3,2с. **36 новых grid runs**:2×6×3. Все shaft/algebra/
  I²t/thermal и three-grid consensus checks пройдены без изменения caps.
  Star accepted,chain rejected (в том числе по upstream ampacity);
  инженерный отрицательный verdict сохранён.
- Все **6 running openings** произошли при скорости>=0,9pu; post-open
  current=0 при сохранённой скорости/потокосцеплении, затем повторное
  подключение. Независимые SI nodal equations и6 новых passive OpenDSS
  three-phase fault solutions согласуются с компиляцией сети.
- **Новая независимая multi-bus SI rotor-flux реализация** решает напряжения
  узлов, stator/rotor currents, интегрирует flux/speed и passive current-squared
  thermal replica через DOP853. Она не вызывает production stamps,constants,
  RHS или integrator.12 continuous trajectories сверены со всеми36 grids,
  8 channels на каждый мотор. Максимум ошибок:speed3,87e-10pu,
  current6,12e-9pu,voltage8,99e-11pu,EMF3,08e-9pu,thermal1,86e-10;
  P0,000514W,Q0,000442var. Дополнительно проверены steady full-T circuit,
  exact open fan coast/flux/heat и node-order invariance. Scope — эти authored
  balanced3-node3-motor fan cases,constant-Z loads,one source,prescribed
  switching,nontripping thermal observers. Не general thermal/R(T),OEM,
  saturation,unbalanced или arbitrary-grid qualification.
- **36 installed physics replays** повторили axes/events/channels побитово
  на том же host с исходным BLAS environment. Первая попытка принудительно
  задала другие thread settings и была отвергнута строгим comparator;
  caps не ослаблялись. Нет cross-BLAS или independent-host guarantee.
  Replay разбит на6 отдельно sealed sibling batches в прежних byte budgets.
- **Corpus/export v2:**точный ограниченный inventory физических producers
  и реальный producer каждого frame/record. Сохранены старый5ec… и новый3c4…,
  они не переименованы в текущий exporter b82…. V1 поддерживается и не может
  молча разрешить mixed revisions. Missing/unknown/duplicate/unused producers
  отвергаются; public response schema enrichment сохраняет lineage.
  Producer metadata не передаётся в model inputs.
- **Prepared, not trained:**96 новых3-motor prefixes добавлены к63 прежним:
  **159 prefixes /636 SFT views**,48 прежних dev prompts. На train есть
  63 двухмоторных и96 трёхмоторных prefixes в одной P3 family;
  на measured view112 running,273 below-running,29 disconnected labels;
  schematic159 refusal targets. Это21 study trace, не159 независимых сетей.
  Все прежние rows/targets/frame/PNG bytes и public dev conversations
  сохранены; test не экспортировался. Source/installed прошли по **684 packs**
  (636 masks+48 prompts,max2978 tokens), весь corpus/raw/schema exports
  пересобран installed runtime побитово. Rotating-OFF waveform просмотрен.
- **425 targeted tests**,25 новых; **160 tests** повторены в native stack.
  Новый wheel меняет только3 data/provenance modules; solver и neural
  training/generation modules прежние. Native physics и neural diagnostics
  выполнены producer3c4…, dataset/replay/new validation — b82b66bfa07e737d627caa6d12844b70be19655fc41e3896cbe64503a332a84f.
  Исторические receipts и snapshots не переписывались.

Recipe и ограничения:`cases/design-multimodal/THREE_MOTOR.md`.
Owner physics/data pins:`three-motor-data-profile.json`;
prepared Qwen profile:`qwen35-08b-three-motor-profile.json`.
Поставка:`reports/three-motor-delivery-2026-11-17/delivery.json`.
GitHub/CI и git add/commit/push не выполнялись.

**Следующий большой блок:**изменить и проверить learning objective, а не
просто повторить длинную epoch. Добавить semantic/field loss metrics и
явно закреплённое weighting/class-refusal sampling; проверить weighted-token
normalization, partial batch и optimizer/RNG resume. Сначала bounded
seen-case overfit pilot с generation controls и numeric/class-prior baselines,
затем полная mixed recipe на подготовленных636 views и новый frozen-protocol
dev comparison. Semantic-token accuracy сама по себе не заменяет joint
field/decision correctness. Test остаётся закрытым. Image grounding
counterfactuals, engineering tool use/GRPO, DES-02 catalog и широкие
DES-03/04 combinations остаются в очереди.

## R06.65 / LEARN-01 — mixed SFT и resume завершены; semantic quality gate не пройден

Реализован и проверен `qwen35_mixed_training`: четыре train modalities,
dedicated-generator shuffle, assistant-token-weighted gradient accumulation,
клипирование один раз на effective batch, bounded AdamW и update-boundary
checkpoints. Variable-size изображения обрабатываются нативно по одному;
prompt/image/padding tokens не участвуют в loss. Это новое обучение от исходной
Qwen3.5-0.8B, не продолжение прежнего image-only адаптера.

- **Реальная полная epoch:**144/144 train views,36/36 optimizer updates,
  по36 views каждой модальности;seed42,rank4,alpha8,lr0.0002,accumulation4,
  CPU FP32/four threads.100352 adapter parameters, vision/backbone frozen.
  Все36 sealed checkpoints проверены; frozen hash до/после совпадает.
- Adapter, named AdamW moments и CPU Torch RNG сохраняются в safetensors;
  owner anchors, plan/versions, schedule hash, cursor, token denominators
  и полная microbatch history — в JSON. Нет pickle/torch.load и перезаписи
  checkpoints. Crash внутри группы требует повторить её от последнего update;
  partial gradients не сериализуются. Resume принимает только тот же runtime,
  target, recipe и data. Native fresh installed process продолжил update1→2:
  adapter/AdamW/RNG tensors, файлы, history и manifest **побитово совпали**.
  Это same-host один update, не arbitrary-host guarantee.
- **Полные48+48 dev generations** на одинаковых новых schema prompts/PNG,
  greedy96 tokens:base **0/48**, adapter **9/48 (18,75%)**;
  image/text/combined по **3/12**, schematic **0/12**.
  У адаптера48/48 generic JSON decisions и48/48 public response-contract
  validity, без budget exhaustion; base17/48 valid и7 exhaustions.
  Валидная форма ответа не подтверждает верность показаний или отказа.
- **Почти постоянные ответы:**46/48 совпадают с «все двигатели работают»;
  всего2 уникальных strings. Post-hoc controls, получающие только static IDs
  из public schema без наблюдений:all-running **9/48**, all-below-running
  **15/48**, all-disconnected0/48. Следовательно, текущий recipe **не прошёл
  содержательный quality gate** и не превзошёл даже class-prior control.
  Numeric public-message policy даёт text/combined/schematic по12/12,
  image0/12 (OCR отсутствует). Эти controls не являются neural compute-budget
  comparison или trained policies; rewards не менялись, failures не удалялись.
- Диагностика различает generic JSON, static-ID response contract и реальные
  states:правильно12/36 required motor fields в image,13/36 в text,
  12/36 в combined. Все12 schematic answers ошибочно утверждают наличие
  состояний. Общий joint reward остаётся исходным0/1; field matches его
  не заменяют. С прежними raw-prompt12/48 этот run напрямую не сравнивается:
  prompt protocol, training views и effective batches отличаются.
- **4 predetermined installed generation replays** (первый dev record каждой
  модальности) повторили raw text, tokens и scores, включая ошибки.
  Reference/test imports blocked, PGE imports только из installed runtime.
  Это subset на том же host, не full48 или independent-host replay.
- Train-only coverage исходного recipe:36 prefix observations из9 stored
  study traces,2 motors;39 running и33 below-running labels на measured
  view, **нет disconnected**;36 schematic refusal targets. Dev имеет3 motors,
  поэтому motor count и topology covary. Это установленный data gap,
  но причина collapse не доказана без train-generation и modality controls.
- **Отдельное подготовленное расширение, ещё не обучено:**train-only cutoffs
  **0,0 / 0,2 / 0,399 с** из тех же ранее проверенных трасс. Добавлены27
  prefix observations;всего63×4 = **252 SFT views**,48 прежних dev prompts.
  Это те же9 study traces, не27 независимых физических случаев. На measured
  view теперь9 disconnected,78 below-running,39 running;63 refusals.
  Все моторы по-прежнему2; disconnected — m2 перед первым включением,
  не вращающийся двигатель после размыкания. Все старые rows/targets,
  frame/PNG bytes, все48 public dev conversations иpixels сохранены.
  Test не экспортировался. Source и installed прошли **300 native packs**
  каждый (252 masks+48 prompts); installed пересобрал весь corpus/raw/schema
  exports **побитово**. Срез0,399 выбран без округления времени в0,400 на PNG.
  Физика заново не интегрировалась, новые независимые trajectories не заявлены.
- **400 уникальных targeted regressions,60 новых**; native-stack subset
  **135 уникальных tests**. Проверены token normalization, short final group,
  exact continuation, recipe/cursor/tensor tampering, complete evaluator,
  train-only early augmentation, неизменность held-out bytes и public-only
  class-prior controls. Повторные XML executions не удваивают число тестов.
- Runtime fingerprint`3c4c6e217ac5979f49e3fe0eb947d6d3f2923dede281ae48a58efeea24c26e16`.
  Wheel добавляет только1 runtime module; прежние solver/generator/trainer/
  grader modules побитово прежние. Поставка:
  `reports/qwen35-mixed-delivery-2026-11-17/delivery.json`.
  Recipe:`cases/design-multimodal/QWEN35_MIXED.md`;
  current profile:`qwen35-08b-mixed-profile.json`;
  prepared extension:`qwen35-08b-early-profile.json` (`prepared_not_trained`).
  GitHub/CI и git add/commit/push не выполнялись; reports/venv игнорируются.

**Следующая очередь по глобальному плану:**

1. До нового длинного обучения — фиксированные train-generation controls без
   assistant labels на входе; проверки numeric reading, refusal и modality
   ablations. Отличить failure обучения/декодирования от OOD generalization;
   не объяснять collapse одним предположением о motor count.
2. DES-03/VIS-01:новые **physics-qualified train cases с3 motors** в train
   topology family (например,3 motors на прежних2 load sites), без переноса
   dev/test проектов. Дополнить early252 вращающимся motor_open/close,
   разными отключаемыми IDs и threshold-boundary cases. Для нового wiring
   нужны независимые nodal/OpenDSS checks, три numerical grids, physical audits
   и installed replay; простое редактирование labels не допускается.
3. LEARN-01:после этих controls обучать новую отдельно закреплённую recipe,
   сопоставлять с numeric и class-prior controls; constrained decoding —
   отдельный protocol. Далее public tool-use и physics-scored actions/GRPO.
   Test открыть только после freeze recipe и правил оценки.

VIS-01/LEARN-01 остаются **in_progress**, EVAL-01 — **open**.
Обучающий тракт и его воспроизводимость завершены в этом profile;
качество инженерного агента, широкая физика и преимущество над моделерами
этим экспериментом не подтверждены.

## R06.64 / LEARN-01 + VIS-01 — полная оценка адаптера и отдельный schema curriculum

Выполнен следующий gate: исторический36-step Qwen3.5-0.8B adapter теперь
проверен на всех12 dev observations в4 модальностях. Prompts, PNG, thresholds,
CPU float32/four threads, greedy decode и96-token budget совпадают с R06.63.
Test и новые physics integrations не использовались.

- `qwen35_adapter_evaluation`: owner manifest training artifact + explicit
  historical producer pin; default требует current producer. До загрузки
  сверяются model/config/shards, original train export, corpus lineage, реальные
  training IDs/steps и finite rows. Native load дополнительно проверяет frozen
  backbone hash, adapter modules/count и tensor keys/shapes/dtype/finite values.
  Исторический Python не исполняется. Training producer1af… и evaluator96e…
  записываются отдельно. Базовые параметры и adapter values после inference
  не изменились.
- **48/48 raw dev generations** завершены. Строгая точность:
  image **2/12**, text **5/12**, combined **5/12**, schematic **0/12**;
  всего **12/48 (25%)**, против **0/48** у исходной модели. JSON/schema-valid
  ответы **48/48** против0/48; output-budget exhaustions **0** против39.
  Это наблюдаемая разница одного небольшого authored dev profile, не test
  generalization, не инженер-проектировщик и не superiority над моделерами.
- Paired verifier заново вычисляет все rewards и знаменатели, проверяет полные
  paired IDs, target/data/decode identity, prompt tokens, image counts и budgets.
  Missing/duplicate/foreign rows, forged rewards/validity, hidden supervision,
  изменённые prompts/protocols и выбор успешных примеров отвергаются.
  Cached baseline имеет явные owner manifest/producer pins; его generator и
  trainer modules побитово совпадают с прежним wheel. Unit test дополнительно
  сверяет реальные kwargs и metadata обеих generation paths.
- Диагностика: неверные indication classes в10 image answers,6 text и7 combined;
  один text answer пропускает motor. Все12 schematic answers нарушают требуемую
  abstention, перечисляя состояния без текущих показаний. Ошибки не исправлялись
  ни parser repair, ни удалением Markdown, ни изменением reward.
- Добавлен **public numeric arithmetic control**: политика получает только
  messages с публичным instrument JSON, без private targets/frames/metadata.
  text/combined **12/12** каждый; без numeric text она abstains:
  schematic12/12, image0/12. OCR не реализован, поэтому это не visual baseline
  и не neural compute-budget comparison. Обученный адаптер всё ещё хуже простого
  детерминированного чтения числовых наблюдений.
- **4 predetermined installed-wheel generation replays** — первый dev record
  каждой модальности — повторили text/tokens/scores в точности. Reference/test
  imports заблокированы, все PGE imports из выбранного installed runtime.
  Это same-host subset, не full48 replay или independent host.
- `design_response_schema`: отдельный curriculum с явной JSON Schema, static
  motor IDs, allowed classes и conditional empty motors при недостатке данных.
  Существующие blocks, PNG bytes и assistant answer strings не меняются;
  measured numeric state, labels/future information в public additions не
  попадают. Статические IDs добавлены в текст и image mode, что явно меняет
  prompt protocol. Его результаты нельзя смешивать с raw benchmark.
- **8 экспортов**:36 train observations×4 views = **144 SFT records**,
  12 dev×4 = **48 unlabeled prompts**. Это варианты прежних observations,
  не новые независимые physical cases. Test enrichment и повторное stacking
  схем запрещены. **Новые training steps =0**: этот curriculum подготовлен,
  но ещё не обучен. Его accuracy не измерена.
- Все **192 native processor packs** прошли в source и installed runtime:
  144 assistant-only masks,48 dev без labels; максимум2978 tokens. Installed
  rebuild повторил весь curriculum **побитово**. Weight loading/forward/training
  в этих processor checks отсутствуют.
- **340 targeted regressions** без failures/errors/skips:336 main +4
  непересекающихся numeric-policy tests; **55 новых**. Subset **90 tests**
  повторён в native model stack. Новый wheel добавляет только2 runtime modules;
  прежние solver/generator/trainer modules не менялись. Математика симулятора
  и прежняя квалификация физики не расширены этим learning gate.
- Recipe и команды: `cases/design-multimodal/QWEN35_08B_EVAL.md`,
  owner profile `qwen35-08b-adapter-eval-profile.json`. Evidence:
  `reports/qwen35-08b-adapter-{dev,paired-dev,installed-generation}-2026-11-16`,
  `reports/qwen35-08b-public-numeric-baseline-2026-11-16`,
  `reports/qwen35-08b-response-curriculum-2026-11-16` и source/installed processor
  receipts. Поставка: `reports/qwen35-08b-adapter-delivery-2026-11-16/delivery.json`.
  GitHub/CI и git add/commit/push не выполнялись; generated artifacts игнорируются.

**Глобальный сдвиг:** owner-anchored adapter evaluator и raw base-versus-adapter
comparison завершены в этом bounded diagnostic profile. VIS-01/LEARN-01 остаются
**in_progress**, EVAL-01 — **open**: engineering workflow, independent designer
assessment, test и closed-loop physics-scored GRPO ещё не квалифицированы.
Измеренный выигрыш над raw base не компенсирует провал abstention и ошибки чисел.

**Следующий большой блок:** reproducible mixed-modality trainer для schema
curriculum (текущий trainer имеет64-step cap и single-example updates), explicit
ordering/batching/accumulation, checkpoint/optimizer/RNG resume; затем **новые
base и adapter dev runs на одном schema protocol**. Отдельный constrained-decoding
control — без ослабления rewards. До открытия test зафиксировать recipe. Для
более сильной generalization добавить independently qualified train physical
cases с согласованным motor-count coverage, сохранив dev/test family boundaries;
не переносить старые held-out проекты в train. Затем multimodal tool-use и
physics-scored проектные действия. DES-02/03/04 продолжаются независимо.

Предыдущий R06.63 ниже исторический: его adapter-evaluation gate закрыт в R06.64,
но полного обучения schema curriculum и test results по-прежнему нет.

## R06.63 / LEARN-01 — Qwen3.5-0.8B, реальное адаптерное SFT и полный base-dev baseline

По выбору владельца текущая модель — **Qwen/Qwen3.5-0.8B**. Прежний 4B
checkpoint и его processor receipts остаются историческими. Новый локальный
checkpoint уже имелся на диске; скачивание, GitHub/CI и публикация не нужны.

- Новый owner-pinned target: `requirements/qwen35-08b-local-target.json`.
  Проверяются config, tokenizer/template, processor files, safetensors index и
  все shards до загрузки весов. Metadata SHA:
  `e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262`.
  Один BF16 source shard1,746,942,600 bytes; локальная origin revision не
  аутентифицирована. Native model loading: local-only, no remote code,
  safetensors only. Исходные файлы модели не менялись.
- Отдельный профиль `qwen35-training` на Transformers5.22.1/Torch2.7.0/
  safetensors0.8.0. Нативному processor окружению недоставало simulator dependencies;
  создан project-local venv с read-only reuse двух существующих Python3.12
  installations. Внешние окружения не изменялись. GPU driver недоступен;
  измеренный профиль — **CPU float32,4 threads**, с reference gated-delta kernels.
  Это не GPU/BF16 или изолированный performance benchmark.
- `qwen35_training`: строгий bounded plan, train-only anchored export и
  **100 352 параметра rank4/alpha8 LoRA** на семи projections последнего
  decoder block. Vision encoder и прочие базовые веса заморожены. Изображения
  проходят настоящий native vision forward; backward обновляет выбранные
  decoder adapters. Никаких synthetic/random weights или surrogate logits.
  Loss считается по causal assistant positions, включая EOS; prompt/image/
  padding labels маскированы. Tests сверяют loss и gradients с full shifted CE.
- Реально выполнены **2 qualification steps** и отдельный **полный проход по
  36 train-image observations /36 optimizer steps**. Seed42,lr0.0002,
  gradient clip1.0, без shuffle и dev-based selection. Все losses, gradients и
  updated tensors конечны; базовые параметры сохранили исходный hash и не
  получили gradients. Один проход — не подтверждение качества на новых схемах.
- Оба adapter checkpoints записаны в safetensors, уничтожены в памяти и
  восстановлены: native logits/loss совпадают побитово. Дополнительно **4 fresh
  Python-I replays**: source/installed для двухшагового и36-step checkpoints.
  Tests/reference imports заблокированы, все PGE modules берутся из выбранного
  runtime. Для каждой пары совпали **полные manifest hashes**, не только loss.
  Это same-host воспроизведение, не независимый host.
- **Настоящий base-dev benchmark:48 generations** — все12 dev observations
  ×image/text/combined/schematic. Исходная модель без адаптеров, greedy decoding,
  одинаковые96 output tokens, без labels в model input. Итог **0/12 в каждой
  модальности,0/48 суммарно**, ни одного schema-valid decision; **39/48**
  исчерпали token budget. Markdown fences, неверные структуры/классы и
  незавершённые ответы остаются в знаменателе и получают0. Это отрицательный
  baseline текущего prompt/decoder контракта; результат не изолирует visual
  perception и не означает оценку обученного адаптера. Train имеет2 motors,
  dev3 motors: изменение topology и motor count также совмещено.
- Отдельный host-only prediction audit повторяет все rewards и классифицирует
  JSON/schema/indication failures. Удаление Markdown fences проверяется только
  диагностически, не меняет официальные scores. Частичный generation run не
  может выдать успешный sealed evaluation. Test не участвовал в training,
  model generation или выборе hyperparameters; прежние processor-only checks
  test prompts не являются model evaluation.
- **285 targeted tests** без failures/errors/skips, включая **39 новых**.
  Отдельно **52 tests** повторены в native Qwen stack (это subset, не ещё52
  уникальных теста). Installed 0.8B processor повторил69 packs:60 image inputs,
  39 SFT masks и30 held-out prompts. Wheel содержит только2 новых runtime
  modules; существующие solver/data modules побитово не изменились относительно
  R06.62. Физические интегрирования этого выпуска не выполнялись; используются
  ранее owner-pinned45 three-grid runs и их observations.
- Recipe/API/commands: `cases/design-multimodal/QWEN35_08B.md` и3 JSON plans.
  Runtime: `qwen35_training.py`, `qwen35_evaluation.py`; benchmark launcher,
  fresh-process adapter replay, prediction audit и delivery builder.
  Evidence: `reports/qwen35-08b-{adapter-training,train-epoch,base-dev}-2026-11-16`,
  source/installed replay pairs, installed processor и prediction audit.
  Поставка: `reports/qwen35-08b-delivery-2026-11-16/delivery.json`.
  Adapter binaries, reports и local venv игнорируются git. Коммитов нет.

**Глобальный сдвиг:** первая реальная model-weight forward/backward и train-only
SFT стадия LEARN-01 выполнена в этом CPU adapter profile. LEARN-01 и VIS-01
остаются **in_progress**, EVAL-01 — **open**. Trained-adapter dev/test accuracy,
full-model tuning, optimizer/RNG resume, multi-example collation, долгие runs
за пределами64-step cap, GPU qualification и closed-loop physics-scored GRPO
ещё не выполнены. Superiority и способность быть инженером-проектировщиком
не доказаны.

**Следующий порядок:** owner-anchored adapter evaluator → полное base-versus-
adapter сравнение на тех же12 dev×4 modalities и96-token budget → исправление
форматного curriculum/явной response schema или отдельный constrained-decoding
baseline без ослабления rewards → зафиксировать recipe до открытия test →
multimodal tool-use и physics-scored действия. DES-02/03/04 продолжаются по
прежним физическим/каталожным ограничениям; обучение не закрывает их автоматически.

Предыдущие записи ниже исторические; их будущая forward/backward стадия и
выбор4B superseded настоящим R06.63. Test и результат обученной0.8B не заявлены.

## R06.62 / VIS-01 + LEARN-01 — наблюдения, мультимодальный корпус и загрузчик Qwen

Добавлен воспроизводимый путь от квалифицированных расчётов к входам VLM:
однолинейная схема, паспорт выбранного оборудования и осциллограммы только до
момента наблюдения. Это развитие слоя данных и обучения; новые интегрирования
или расширение квалифицированной физики в этом выпуске не заявляются.

- **60 наблюдений /62 PNG**, 5 проектов ×3 сценария ×4 момента:
  0.4/1.5/1.6/4 s. На разрывах берётся post-event сторона; экстремумы и стороны
  событий сохраняются при прореживании графика. Статические паспорта не содержат
  команд и состояния, будущие отсчёты и итоговые verdict/reward не экспортируются.
  PNG показывают U/I/скорость и текущие дискретные показания; единицы и
  происхождение параметров явно обозначены.
- Три **точно непересекающиеся топологические семьи**, train/dev/test36/12/12.
  Канонизация учитывает изоморфизм: трёхузловые star/chain не разнесены между
  split. Двигатели и каталог общие; независимость equipment families не заявлена.
  Четыре режима image/text/combined/schematic дают **12 переносимых экспортов**.
  SFT разрешён только для train; dev/test содержат только prompts. В режиме
  schematic без приборов правильный ответ — недостаточно наблюдений.
- Строгая диагностическая цель — чтение отображённых connection/speed и
  порога0.900 pu при округлении0.001 pu. `at_running_speed` не означает наличие
  питания, исправность или допустимость проекта: выбегающий мотор тоже может
  иметь такую скорость. Проверенные host-owned цели кешируются; пакетный callback
  наград отвергает дубли JSON-ключей, дополнительный текст, NaN и изменение
  схемы ответа. Это diagnostic0/1 reward, не закрытая GRPO-петля управления.
- Исторические источники R06.61 привязаны к двум owner manifest hashes и
  отдельному physics producer SHA. Повторно проверены **45 сохранённых grid runs**,
  compilation, критерии и итоговые решения. Новый optional explicit revision pin
  позволяет такой аудит; default current-only checks не ослаблены. Physics SHA
  и exporter/auditor SHA разделены; исходные sealed artifacts не перезаписываются.
- `VLMExportDataset` проверяет anchors, безопасные локальные PNG, модальность,
  split и роли, затем использует **нативный Qwen3VLProcessor**. Проверяются
  prompt/full prefix, image tokens, finite visual tensors и assistant-only loss
  mask. Prompt/image/padding tokens маскированы; silent truncation запрещено.
  Один пример упаковывается в batch; trainer и multi-example collator не добавлены.
- Реальный локальный **Qwen3.5-4B processor**: **69 проверок**, включая все60
  image observations, **39 SFT masks и30 held-out prompts**; максимум2757 tokens,
  3 изображения. Transformers5.22.1/Torch2.7.0/Pillow10.4.0/tokenizers0.23.3.
  Config закреплён owner SHA; файлы процессора сверены до/после. Веса не открывались,
  forward/backward, обучение и измерение качества модели не выполнялись.
  Новый `qwen35-processor` профиль отделён от прежнего Transformers4.x
  `llm-training`; совместная установка этих extras не поддерживается.
- **246 targeted tests**, включая **40 новых**, без failures/errors/skips.
  Проверены event sides, порог/округление, future poisoning, пересоздание PNG,
  изоморфные splits, утечки ответов, tampering и потеря image/mask/prefix.
  Проверки экспорта: **240 positive +480 negative** на каждом runtime.
  Same-host Python-I installed-wheel replay с заблокированными reference imports
  повторил корпус и все12 экспортов **побитово**. Это не независимый host.
- Примеры/API: `cases/design-multimodal/README.md`; source anchors и protocol
  находятся рядом. Runtime: `design_frame`, `design_multimodal`,
  `design_multimodal_render`, `vlm_batches`. Отдельные acceptance/native processor/
  delivery scripts фиксируют воспроизведение. Evidence:
  `reports/design-multimodal-qualified-current-2026-11-16`,
  `reports/design-multimodal-exports-{current,installed-current}-2026-11-16`,
  `reports/design-multimodal-native-processor-2026-11-16`.
  Поставка: `reports/design-multimodal-delivery-2026-11-16/delivery.json`.
  Родительские corpus/replay directories приватны: агенту выдаётся только
  отдельный export, без targets, frame history и host receipts.

**Глобальный сдвиг:** VIS-01 остаётся **in_progress**, изображения паспортов и
осциллограмм реализованы. LEARN-01 теперь **in_progress**: есть data/processor/
loss-mask/reward подготовка, но его критерий обучения ещё не выполнен.
Следующий блок: реальный forward/backward на Qwen, затем base-model ablations
image/text/combined/schematic, SFT только на train и закреплённая оценка на
held-out. EVAL-01 остаётся open. Затем — multimodal tool rollouts и physics-scored
проектные действия; параллельно нужны более широкие физика, каталоги и topology
families из DES-02/03/04. Превосходство над другими моделерами не установлено.
GitHub/CI и публикация не использовались, git add/commit/push не выполнялись.

Предыдущий статус R06.61 ниже описывает границы того выпуска; его открытые
паспортные/осциллографические наблюдения реализованы в R06.62 в указанном профиле.

## R06.61 / DES-02 → DES-03 + VIS-01 — построение сети, выбор оборудования и публичная однолинейная схема

Вместо изменения импеданса единственного готового фидера теперь доступно
построение реальной radial LV сети из разрешённых owner corridors. Кандидат
выбирает transformer ID и corridor/cable ID каждой ветви. Компилятор создаёт
новые line/terminal objects, проверяет spanning tree всех заданных площадок,
подключает паспортные параметры и запускает каждый обязательный сценарий.
Фиксированные load sites, motor passports, спрос, критерии, возмущения и
каталог кандидат изменять не может.

- `NetworkDesignTask` / `NetworkProposal`: exact demand coverage каждого узла,
  nameplate/auxiliary envelope, один источник и уровень LV, 50 Hz, разрешённые
  трассы, установка по точной catalog row. Unknown IDs, disconnected/cyclic
  proposals, dangling references, parameter/criteria edits, ID collisions и
  неподдержанные dynamic scopes отвергаются до создания output. Порядок
  выбранных ветвей нормализуется; все три динамические сетки preflighted.
- `derive_catalog_passports` — единая data-only derivation для прежнего
  single-feeder compiler и новой сборки: R20→R(T), AC/DC assumption,
  loss-derived transformer R/X, field-level source lineage. Новых OEM фактов
  не выдумывается; unverified transformer withstand, generic X и AC/DC factor
  сохраняют своё происхождение и ограничения.
- Статический экран агрегирует спрос по downstream tree, проверяет ampacity,
  transformer loading, nominal fixed-current projected drop **с трансформатором**,
  passive source-only fault вдоль полного пути, upstream cable I2t, supplied
  interrupting/short-time limits. Это не constant-P load flow/IEC60909,
  не motor fault contribution/inrush/peak duty и не защита/selectivity.
- Затем **все** обязательные simultaneous start / staged start / restart
  проходят 3-grid `MotorEngineeringPlan`. Static rejection не пропускает
  dynamics. Incomplete/unqualified traces получают reward=null, а не
  qualified rejection/acceptance. Отдельный verifier восстанавливает compilation,
  owner/proposal anchors, physical audits, grid consensus и итоговый verdict.
- **5 проектных вариантов /45 dynamic grid runs**, 4 s, 2/1/0.5 ms,
  **3/4 nodes и2/3 motors**. Short star/chain: 3 accepted designs; long route:
  2 rejected designs. Независимые SI nodal equations сверяют voltage/current
  screening; **17 OpenDSS fault solutions** — каждый узел. Также проверены
  фактически выданные source/line impedances и endpoints всех dynamic cases.
  Отдельный consumer повторил references, audits и все 5 public views.
- Новый `network_design_view`: deterministic single-line SVG и public JSON
  выбранных соединений, cable IDs/lengths и motor/load sites. Нет verdict,
  reward, event schedule или future trajectories. Labels escaped; длинные
  строки сокращаются с полным SVG title. Все **5 схем** rasterized через
  системные librsvg/Cairo; визуально проверены branching/chain views, устранено
  перекрытие node labels проводами. Это первая часть VIS-01, не image-grounding
  ablation, паспортные изображения, VLM dataset или обученная модель.
- **157 targeted tests passed**, включая **32 новых**, без failures/errors/skips.
  Прежний six-family feeder corpus пересобран на текущем runtime: **108 grid
  runs**, 12 options и qualified infeasible cases. Все corpus regressions
  выполнены с явным PGE_DESIGN_CORPUS, не пропущены. Проверены полный CLI run /
  verify, owner tampering, canonical ordering, invalid inputs, cancelled/null
  reward, static-negative с обязательными dynamics и public observation boundary.
- Same-host Python-I installed replay: **54 studies** (45 новых +9 прежних),
  битовое совпадение axes/events/common channels. Из-за объёма длительных
  осциллограмм 9 six-record batches запечатаны отдельно как sibling artifacts;
  composite receipt ссылается на их hashes. Лимит 256 MB на artifact не ослаблен,
  receipt не выдаётся за автономный архив всех данных.
- CLI: `compile-network-design`, `network-design`, `verify-network-design`,
  `network-design-view`. Примеры: `cases/network-construction/README.md`,
  2 owner tasks и4 proposals. Runtime: новые `network_design.py`,
  `network_design_view.py`; изменены `equipment_catalog.py`, `cli.py`.
  Physics/integrator kernels относительно R06.60 не менялись.
  Evidence: `reports/network-construction-{qualified,accepted}-release-2026-11-16`,
  `reports/network-construction-legacy-corpus-release-2026-11-16`,
  `reports/network-construction-installed-replay-2026-11-16` и sibling batches,
  `reports/network-construction-visual-qa-2026-11-16`.
  Поставка: `reports/network-construction-delivery-2026-11-16/delivery.json`.

**Глобальный сдвиг:** DES-03 и VIS-01 теперь **in_progress**, вместо open.
DES-02 остаётся in_progress: дополнительные verified cable/transformer facts,
другие installations, withstand и catalog compatibility ещё нужны.
DES-03 далее: секции, несколько трансформаторов/уровней, управляемое расширение
площадок и совместная защита; текущий профиль — fixed sites + tree rewiring.
VIS-01 далее: осциллограммы/паспортные наблюдения, visual grounding и реальные
multimodal episodes. LEARN-01/EVAL-01 не закрыты: actual Qwen SFT/GRPO,
held-out independent assessment и преимущество над другими моделерами не
доказаны. API bounds2–8 sites не являются доказательством точности/масштаба
вне измеренной матрицы3/4 nodes. GitHub/CI не используются; коммитов нет.

## R06.60 / DES-04 + подготовка LEARN-01 — явный thermal reset и диагностические tool episodes

Реализован opt-in `GeneratorThermalProtection.reset_level` и команда
`generator_thermal_reset` по ID генератора. Нужны явный порог ниже trip,
настроенный actuator, физически отключённый генератор и I2 level <= reset.
Connected/too-hot requests блокируются; повторный допустимый reset — no-op.
Сбрасывается только stator-I2 latch: replica level, физические температуры,
operator inhibit и отдельная field-thermal защита сохраняются. Автоматического
сброса по охлаждению нет. Паспорта без reset_level сериализуются как раньше.

- Тепловое открытие теперь фиксирует `opened_at` настроенного generator breaker:
  последующий reset не обходит minimum open time. DAE mode token включает
  stator/field protection latches независимо от положения выключателя.
  Дифференциальные уравнения машин/нагрева не менялись, состояния при reset
  непрерывны; повторное включение сохраняет весь синхроконтроль.
- **20 авторских случаев / 80 native runs / 40 независимых SI траекторий /
  160 сравнений**: восемь сценариев, два temperature sets, четыре перестановки.
  Radau/DAE, 2/1 ms, horizon 0.3 s. Проверены early/connected reset blocks,
  ручной и ATS retrip, minimum-open block, command-before-delayed-close,
  operator inhibit и duplicate reset. Повторный trip — ожидаемое следствие
  неустранённого нагруженного режима; reset не является устранением причины.
- Подтверждены **144 thermal trips, 64 applied /16 blocked /8 no-op resets,
  144 applied /48 blocked closes, 104 forward ATS starts**. Consumer заново
  интегрировал все references и пересчитал comparison, физическую непрерывность,
  latch/cooling memory, 1024 clock segments и 16 полных permutation pairs.
  Max clock error **7.78e-16**; native energy **0.004152 J** при gate 0.1 J;
  reference **3.46e-9 J** при gate 1e-4 J. Это numerical consistency.
- **520 targeted tests passed**, без failures/errors/skips: 319 automation/
  workflow/diagnostic, 149 физических и 52 stator/field thermal regression.
  **54 новых теста**. Старый unit fixture явно задаёт legacy event policy;
  отдельно проверено, что coincident policy применяет только переданный набор
  roots. Same-host Python-I replay **96 исследований** (80 новых +16 прежних)
  побитово сохраняет event/axis/channels. CLI manual_retrip выполнен.
- Добавлен `power_grid_env.recovery_curriculum`: strict read-only tools для
  текущего connected/latched/I2 indication и owner reset policy, typed decision,
  owner-manifest-anchored 0/1 diagnostic scoring. Кандидат не может подменять
  наблюдение/threshold через ответ. `submit_decision` не исполняет физическое
  действие. Наблюдения извлекаются **до команды**, без будущих событий/ответа.
- Из qualified + независимо accepted evidence собраны **18 scripted tool-use
  episodes**, SFT JSONL и prompts JSONL без labels. Все записи одной topology
  family остаются **train-only**, включая hot parameters. Проверены tool-message
  replay, правильные/неправильные/malformed ответы и обязательный owner anchor.
  Полный pipeline повторён Python-I из установленного wheel с блокировкой
  benchmark imports; corpus и оба экспорта совпадают побитово.
  Это подготовка диагностического curriculum: **нет model training, VLM images,
  topology-held-out evaluation или closed-loop GRPO/design reward**.
- Runtime относительно R06.59: `generator_thermal.py`, `network_automation.py`,
  `coupled_rms.py`, `dae.py`; новый `recovery_curriculum.py`. Примеры и команды:
  `cases/thermal-recovery/README.md`, восемь JSON. Evidence:
  `reports/thermal-recovery-{qualified,accepted,installed-replay}-2026-11-16`,
  `reports/thermal-recovery-learning{,-installed}-2026-11-16`.
  Поставка: `reports/thermal-recovery-delivery-2026-11-16/delivery.json`.

**Глобальная очередь после этого этапа:** расширять DES-02 catalog и DES-03
construction/redesign, параллельно VIS-01 на проверенных физических профилях.
В DES-04 остаются primary close-failure fallback, several live islands,
public equipment/thermal anchors и joint standby heat initialization.
Для LEARN-01 далее нужны разнообразные family-disjoint задачи, визуальные
наблюдения, tool-use rollout с полноценным физическим verdict, actual Qwen SFT/
GRPO и независимый EVAL-01. Малый diagnostic seed не закрывает эти этапы.

DES-04 **in_progress**, LEARN-01 **open с выполненной подготовкой**. Текущий
профиль — warm-standby radial GENCLS/two-cage R(T), заданные температуры,
permissive numerical synchrocheck и live/dead threshold 0.5 pu, не рабочие
настройки оборудования. Нет field-thermal reset/OEM/arbitrary root ties/
multi-island qualification или независимого host. GitHub/CI не используются;
коммитов нет.

## R06.59 / DES-04 — начальная недоступность PT и тепловая блокировка повторного подключения

Добавлены `initial_primary_measurement_available` и
`initial_reserve_measurement_available` в паспорт transfer scheme. При false
доступность и observed-voltage channels отражают отказ уже в initial sample,
даже без measurement commands. Physical source/network voltage и начальные
состояния не меняются. Значения true по умолчанию не сериализуются: старые
паспорта, размерности и channel inventories сохраняются. Существующие команды
restored включают измерение; reset АВР не восстанавливает отсутствующий PT.

- **20 авторских случаев / 80 native runs / 40 независимых SI траекторий /
  160 сравнений**: восемь сценариев, два prescribed temperature sets и четыре
  перестановки; Radau/DAE на 2/1 ms, horizon 0.3 s. Покрыты initial primary /
  reserve / both PT loss, restoration и явный retry, отсутствие восстановления,
  ручное и ATS-повторное включение после stator-I2 trip.
- Тепловая блокировка была реализована ранее; новый этап независимо проверяет
  её совместную работу с warm-standby ATS. После открытия ток равен нулю,
  I2 replica остывает по аналитическому exp(-t/tau), но latch остаётся даже
  ниже trip. Ручные close и ATS reset не обходят блокировку. Это отдельная
  dimensionless I2 replica, не физическая температура обмотки и не разрешение
  включения после охлаждения. Field-thermal reconnection сюда не переносится.
- Начальное отсутствие primary PT не фабрикует undervoltage detection;
  отсутствие reserve PT блокирует до запроса close. Возврат PT допускает
  новое измерение, но сохраняет напряженческие/частотные ограничения.
  В `both_restore` passport case успешно включает резерв и возвращается;
  hotter case блокируется по voltage difference. При retry после восстановления
  reserve PT включение блокируется по frequency difference. Оба отказа
  сохранены как ожидаемые результаты, параметры не подгонялись под closing.
- Native aggregate: **24 thermal trips, 48 thermal close blocks, 112 forward
  ATS starts, 16 PT transfer blocks, 16 completed returns**. Consumer повторяет
  references, владельцев параметров/схем, clock integrals, physical continuity,
  topology grid/event sides, I2 cooling, copper/heat identity, energy integrals,
  initial spectra и 16 полных permutation pairs. Пересчитаны **864 clock
  segments** и 80 native energy integrals: max clock error **9.99e-16**,
  native energy **0.01495 J** при gate 0.1 J, reference **1.60e-8 J** при
  gate 1e-4 J. Это численная согласованность. Отдельно проверены altered
  validity/latch/level/reason и различимость initial-mask владельцев.
- **415 targeted tests passed**: 266 automation/DAE/workflow, включая **31 новый**,
  и 149 физических регрессий. Дополнительная проверка owner comparison
  учитывает выбранный engine. Same-host Python-I installed replay — **96
  проверок** (80 новых +16 prior automatic-return success/cancel-reset),
  побитовое совпадение событий, осей и каналов. CLI thermal_retry выполнен.
- Единственное изменение runtime относительно R06.58 — `network_automation.py`:
  схема начальных PT flags и их применение в initialization/diagnostics.
  Уравнения машин и тепловой защиты не менялись. Примеры:
  `cases/standby-interlocks/README.md`, восемь JSON. Evidence:
  `reports/standby-interlocks-qualified-final-2026-11-16`,
  `reports/standby-interlocks-accepted-final-2026-11-16`,
  `reports/standby-interlocks-installed-replay-2026-11-16`.
  Поставка: `reports/standby-interlocks-delivery-2026-11-16/delivery.json`.

**Далее:** явно квалифицированный cooldown/reset workflow с отдельным
разрешением тепловой защиты; primary close-failure fallback; несколько живых
островов; public equipment/thermal anchors; joint standby heat initialization.
DES-04 остаётся **in_progress**. Не заявлены OEM/field accuracy, arbitrary root
ordering, global hybrid stability, several-live-island/field-thermal return
или независимый host. GitHub/CI не используются; коммитов нет.

## R06.58 / DES-04 — автоматический возврат питания и отмена отложенных действий

Добавлен opt-in `TransferScheme.return_policy`: непрерывная выдержка
восстановленного primary supply, гистерезис, открытие резерва, dead time и
отложенное включение primary. Контроллер работает в Radau и network DAE;
clock входит в состояния, дискретные режимы — в DAE mode token. Старые
профили без policy сохраняют сериализацию, размерность и каналы.

- Потеря напряжения или supply PT в ходе recovery **обнуляет** выдержку.
  Обе supply-validity и manual inhibit проверяются перед переключением и
  заново при фактическом исполнении. Сохраняются minimum open time,
  синхроконтроль и break-before-make. Приоритет одновременных health-loss /
  dwell-expiry консервативен и отдельно проверен.
- Если primary пропадает после открытия резерва или между запросом и
  исполнением primary close, forward ATS повторно вооружается при valid PT
  и отсутствии operator inhibit. В тестах он восстанавливает резерв, но
  последующий возврат блокируется по углу: controller reset не разрешает
  физически недопустимое включение. Отказ открытия резерва также блокирует
  запрос primary close.
- `return_cancel` удаляет pending actions своего цикла; `return_reset`
  удаляет их и требует новую полную выдержку. Generation защищает от stale
  actions; отмена не отменяет уже выполненное открытие, не снимает manual
  inhibit и не стирает чужие protection/ATS actions. Проверена namespace
  ownership (`ats` / `ats:child`) и отмена после close request. Исправлен
  конфликт dedup очереди: совпавшая ручная команда получает собственное
  владение и переживает отмену автоматического возврата. Cancel относится
  к циклу: последующий успешный forward ATS снова вооружает return policy.
- **22 авторских случая / 88 native runs / 44 независимые траектории /
  176 сравнений**: девять сценариев, passport/hot temperatures и четыре
  перестановки; Radau/DAE, 2/1 ms, horizon 0.3 s. Подтверждены 128 return
  starts, 56 completions, 48 blocks, 16 fallback armings, 40 удалённых
  actions, 16 сбросов recovery и 104 forward transfers во всех native runs.
- Независимый SI DOP853 reference имеет собственную очередь/автоматику;
  native return helpers не использует. Постоянная recovery clock имеет
  независимый exact expiry, сверяемый с интегрированной clock, без подгонки
  под native event times. Consumer повторил **1240 clock segments**,
  88 energy integrals, initial spectra, сохранённую topology grid/event
  sides, continuity и 16 permutation pairs. Максимальная clock error
  **9.99e-16**, native energy **0.03124 J** при gate 0.1 J, reference
  **3.82e-8 J** при gate 1e-4 J. Это численная согласованность.
- **384 targeted tests passed** без failures/errors/skips: 235 automation/
  DAE/workflow (в том числе **41 новый**) и 149 физических регрессий.
  Same-host Python-I installed replay: **108 проверок** (88 новых +20
  прежних); полные event/axis/channel traces совпадают побитово.
  CLI success example выполнен. Evidence и wheel закреплены в
  `reports/automatic-return-delivery-final-2026-11-16/delivery.json`;
  исходные пакеты — `reports/automatic-return-{qualified,accepted,installed-replay}-final-2026-11-16`.
- Новый runtime `automatic_return.py`; изменены `network_automation.py`,
  `coupled_rms.py`, `dae.py`. Примеры и воспроизведение:
  `cases/automatic-return/README.md`, девять JSON. Каналы
  `ats.return.{progress,active,mode,generation}` документированы.

**Далее:** initial measurement-fault masks; generator reconnection с thermal
latch; самостоятельный профиль primary close-failure fallback; несколько
живых островов; public equipment/thermal anchors и joint standby heat
initialization. DES-04 остаётся **in_progress**. Новый профиль — одна
двухузловая radial-схема, один warm spinning/excited GENCLS и two-cage motor,
заданные температуры, fixed-frequency source amplitude steps. Настройки
20 ms dwell/10 ms dead time и permissive sync windows — численные тесты,
не эксплуатационные рекомендации. Не заявлены OEM/global hybrid stability,
произвольные root ties или независимый host. GitHub/CI не используются;
изменения не коммитились.

## R06.57 / DES-04 — supply-PT validity, reserve CT и контролируемый обратный перевод

В native ATS добавлена доступность измерений primary/reserve supply и четыре
явные команды lost/restored по ID схемы. Invalid primary PT приостанавливает
детектирование с непрерывным reset timing, а не означает восстановление
физического напряжения. Обе supply-validity проверяются до запроса transfer
close и заново при исполнении задержанного включения. Режимы включены в DAE
mode token и аналитическую reset-row. Дополнительные measurement diagnostics
появляются только у профилей с новыми командами; legacy channel sets сохранены.

- **18 авторских сценариев / 72 native runs / 36 независимых траекторий /
  144 сравнения**: семь сценариев, passport/hot temperatures и четыре
  перестановки списков. Radau/DAE, 2/1 ms, horizon 0.3 s. Проверены **96 ATS
  starts, 24 early blocks, 32 reserve closes, 64 blocked closes, 24 successful
  grid returns, 8 reserve-CT trips и 40 supply-PT losses**.
- Отдельно проверены: primary PT loss в detection; reserve PT loss до request
  и между request/actuation; reset/retry после отказа; CT loss во время ATS
  с pickup/trip только после восстановления; guarded manual return;
  повторный цикл, заблокированный сохранённым manual inhibit. Retry сохраняет
  синхроконтроль и в тестовом случае блокируется по углу. Reset не даёт
  разрешения на параллель источников и не снимает операторскую изоляцию.
- Исправлен реальный deadline-ordering defect в общем integration loop:
  computed deadline 0.07499999999999998 s мог исполниться раньше команды
  на 0.075 s в Radau, тогда как DAE обрабатывал команду первой. В пределах
  64 eps вычисленный срок привязывается к заданной границе, сохраняя приоритет
  command-before-queue. Независимый reference order теперь совпадает у обоих
  engines. Различимые команды 0.074/0.076 s не объединяются. Python-I probe
  старого wheel воспроизводит расхождение; новый проходит оба режима.
- Исправлен initial audit отключённого генератора: internal-voltage target
  маркируется `standby_internal_voltage_target`; requested internal voltage
  и solved bus voltage показаны раздельно. Инициализационные уравнения не
  менялись. Audit проверяется consumer во всех 72 native trajectories.
- Consumer повторил references/comparisons, **448 clock segments**, break-
  before-make на всей сохранённой сетке и event sides, physical continuity,
  availability/counter/inhibit states, начальные spectral checks,
  16 permutation pairs и 72 energy integrals. Max clock error **1.16e-14**;
  native energy **0.01551 J** при gate 0.1 J; reference **1.62e-8 J** при gate
  1e-4 J; synchronism frequency error <4.4e-11 Hz. Это numerical consistency.
- **401 targeted tests passed**, без failures/errors/skips: 252 automation/
  DAE/workflow и 149 физических регрессий, включая 39 новых. Проверены invalid
  PT reset action, DAE mode invalidation, unknown measurement targets, old
  profiles и исправленные scheduled/deadline order cases. Same-host Python-I
  installed replay: **92 проверки** (72 новых + 20 прежних); event/axis/channel
  values побитово совпадают. CLI retransfer example выполнен.
- Runtime boundary относительно R06.56: `network_automation.py`,
  `coupled_rms.py`, `automation_jacobian.py`, `dae.py`,
  `coupled_initialization.py`, `induction_network.py`. Примеры:
  `cases/transfer-lifecycle/README.md`, семь JSON. Evidence:
  `reports/transfer-lifecycle-qualified-fixed-2026-11-16`,
  `reports/transfer-lifecycle-{accepted,installed-replay}-2026-11-16`.
  Поставка: `reports/transfer-lifecycle-delivery-2026-11-16/delivery.json`.

**Далее:** autonomous retransfer policy, initial measurement-fault masks,
явная отмена pending transfer actions, generator reconnection с thermal latch,
multi-island и public equipment/thermal anchors. Возврат в этом корпусе —
заданная агентом/manual последовательность, не автоматический контроллер
возврата. Reset проверен после завершённого/заблокированного действия,
не является интерфейсом отмены in-flight command. Температуры заданы,
joint standby heat initialization не квалифицирована; sync windows тестовые.
Нет OEM/global hybrid stability/arbitrary-root-tie qualification; installed
проверки на том же host, не независимая аутентифицированная поставка.
GitHub/CI не используются; изменения не коммитились.

## R06.56 / DES-04 — CT/PT continuity и АВР с изначально изолированным генератором

Расширен независимый эталон двух связанных с общей физикой workflows: потеря/
восстановление CT/PT при работе защит и АПВ; отдельный source-to-generator ATS
с первоначально разомкнутым резервным генератором. **Runtime и wheel R06.55
не изменены**; новые уравнения/reference, сценарии и consumer проверяют уже
существующие возможности ядра в этих сочетаниях.

- **22 авторских сценария / 88 native runs / 44 независимые траектории /
  176 сравнений**: четыре measurement cases в двух температурных политиках,
  пять ATS cases с двумя наборами заданных температур и четыре перестановки
  команд/оборудования. Radau/DAE, шаги 2/1 ms. CT/PT horizon 0.5 s, ATS 0.3 s.
- Подтверждены **40 measurement losses, 32 restorations, 56 relay trips,
  40 transfer starts, 16 early transfer blocks, 16 reserve closes и 8 reserve
  close blocks по синхроконтролю**. При invalid measurement сохраняется
  непрерывность clock; после dropout неполная выдержка экспоненциально
  сбрасывается. При restore учитывается оставшаяся память. Invalid PT zero
  не подставляется в физическую сеть и не вызывает ложного UV trip.
- CT отсутствует также во время АПВ: close сбрасывает clock/latch, но новое
  измерение и retrip возникают только после восстановления CT. При CT,
  не восстановленном до конца исследования, отключения не происходит.
- Standby reference независимо решает loaded grid-fed motor root и задаёт
  генератору нулевой терминальный ток, internal-voltage amplitude/phase,
  механическую мощность для покрытия core loss и физические температуры.
  Isolated spinning/excited generator не получает ложный I²-latch; реплика
  остывает. После source collapse проверяются detection, primary opening,
  break-before-make, reserve voltage и synchronism. Primary failure, low reserve
  и frequency mismatch не допускают перевод на резерв. Short dip отменяет
  запуск АВР до actuator request.
- Consumer повторил references/comparisons, **780 clock segments**, память
  измерительного таймера и ATS reset, непрерывность физических состояний,
  break-before-make, attempt/availability/latch channels, начальные спектральные
  проверки, 16 permutation pairs и 88 интегралов энергии. Max clock error
  **1.98e-11**, native energy **0.01478 J** при gate 0.1 J, independent
  **2.85e-6 J** при gate 1e-4 J, frequency decision error <3.32e-9 Hz.
- **418 targeted tests passed**, без failures/errors/skips: 247 по automation
  workflows плюс 171 физическая регрессия, в том числе 50 новых тестов.
  Python-I replay того же установленного wheel: **108 проверок** в четырёх
  ограниченных пакетах (88 новых + 20 прежних protected/limiter/I² cases).
  CLI ATS-transfer example выполнен. Эталон не использует native automation,
  stator и initialization helpers.
- Примеры: `cases/measurement-transfer/README.md`, девять JSON. Evidence:
  `reports/measurement-transfer-{qualified,accepted,installed-replay}-2026-11-16`.
  Поставка: `reports/measurement-transfer-delivery-2026-11-16/delivery.json`.

**Ограничения/дальше:** ATS использует только passport temperatures: motor
90/70 C и generator 80 C либо hot 110/90 C и 100 C. Это spinning/excited warm
standby, не engine start. Joint heat/PV standby initialization не квалифицирована;
начальный stability check относится к connected motor subset. Нужны CT/PT
failures во время самого ATS, retries/retransfer, generator reconnection with
thermal latch, несколько живых островов, более явные standby voltage-target
labels в initial audit и публичные machine/thermal anchors. Синхроокно успешного
ATS намеренно permissive и не является эксплуатационной уставкой. Numerical
consistency не доказывает OEM accuracy/global hybrid stability. Installed
проверки на том же host, не внешняя аутентифицированная поставка.
GitHub/CI не используются; изменения не коммитились.

## R06.55 / DES-04 — активные OC/UV, резервирование отказа и АПВ в общем R(T)-острове

Связаны активные защиты и предыдущая topology reference: generator-port OC
с четырьмя кривыми, downstream UV, отказ feeder breaker с отключением источника
резервным аппаратом, одно АПВ с синхроконтролем, повторное отключение и запреты
включения. R(T), физический нагрев, энергия, TGOV1/SEXS и диагностическая
I²-реплика продолжают рассчитываться. Генератор остаётся подключённым к острову.

- **18 авторских сценариев / 72 native runs / 36 независимых траекторий /
  144 сравнения**: восемь сценариев, две температурные политики и две
  перестановки very-inverse command/relay/breaker lists. Radau/DAE, шаги 2/1 ms;
  интегрирование 2e-11/2e-12, refined reference 5e-12/5e-13. Допуски сравнения
  сохранены; точность интегрирования выбрана под эти допуски.
- Подтверждены **112 relay trips, 104 успешных primary openings, 8 primary
  failures, 8 backup openings, 40 успешных АПВ и 16 blocked recloses**.
  После успешного АПВ clock/latch сбрасываются, новое измерение повторно взводит
  защиту и возникает retrip. Лимит одного shot исключает бесконечные включения.
  Manual inhibit запрещает close до механической попытки; synchronism block
  учитывает попытку. Ручное «уже открыто» не добавляет opening attempt.
- Независимый consumer повторил все references/comparisons, **1184 clock
  segments**, exponential reset, latched hold и reclose reset/rearm, начальные
  спектры, непрерывность физических состояний, topology/counter/inhibit channels,
  синхроконтроль и 72 интеграла энергии. Max clock error **4.42e-11**;
  native energy **0.03086 J** при gate 0.1 J; independent **8.64e-6 J** при
  gate 1e-4 J; frequency decision error <3.32e-9 Hz. Проверены 8 permutation pairs.
- Исправлен `protection.py`: inverse denominator вычисляется через
  `expm1(alpha*log1p((I-pickup)/pickup))`. Прежний power-minus-one терял точность
  непосредственно над pickup, вплоть до деления на ноль у standard inverse.
  Python-I probes двух установленных колёс сравнивают три кривые и три масштаба
  pickup с 80-digit Decimal. Старое колесо не прошло 7 из 9 проверок, новое
  прошло все 9. Pickup semantics/уравнения кривых сохранены. Это единственный
  runtime-файл, изменённый относительно R06.54.
- **553 targeted tests passed**, без failures/errors/skips: 296 по автоматике,
  защитам и workflows плюс 257 физических регрессий; в том числе 45 новых тестов.
  Same-host Python-I installed replay — **92 проверки**: 72 новые и 20 прежних
  (8 source-island и 12 unified limiter/I²). Полные event/axis/channel проверки
  выполняются существующим изолированным consumer в пакетах по 32 записи.
  CLI failure-backup example выполнен.
- Убрано только дублирование reference waveforms в comparison JSON: полные
  references сохраняются отдельно, errors/caps/outcomes остаются в каждом
  сравнении, consumer пересчитывает полный результат. Это позволило сохранить
  все 18 сценариев в прежнем artifact budget; глобальные byte/file limits
  не расширялись. Существующие запечатанные пакеты не перезаписывались.
- Примеры: `cases/protected-topology/README.md`, восемь JSON. Evidence:
  `reports/protected-topology-qualified-compact-2026-11-16`,
  `reports/protected-topology-accepted-2026-11-16`,
  `reports/protected-topology-installed-replay-2026-11-16`.
  Поставка: `reports/protected-topology-delivery-2026-11-16/delivery.json`.

**Далее:** АВР и CT loss/restoration в этой общей физике; generator reconnection
с thermal latch, несколько живых островов, multi-shot coordination и публичные
machine/thermal parameter anchors. Квалифицирована одна авторская радиальная
схема, не arbitrary topology/root ties, unbalanced faults или OEM-параметры.
Синхроокна и relay settings тестовые; I²-порог поднят для выделения topology
workflow. Численные метрики не доказывают точность реального оборудования.
Installed проверки на том же host, не внешняя аутентифицированная поставка.
GitHub/CI не используются; изменения не коммитились.

## R06.54 / DES-04 — островной режим, восстановление соединения и R(T)-производные защит

Расширен независимый SI-reference: отключение источника/линии, питание двигателя
от генератора в острове, одновременное отделение пустого upstream-участка,
поэтапное восстановление и разрешённое/заблокированное включение. Общая модель
сохраняет R(T), физический нагрев, энергию, TGOV1/SEXS и диагностическую I²-реплику.

- **10 авторских сценариев / 40 native runs / 20 независимых траекторий /
  80 сравнений**: четыре сценария, две политики начальной температуры и две
  перестановки списков команд/реле/выключателей. Radau/DAE, шаги 2 и 1 ms.
  Подтверждены **56 открытий, 48 успешных включений, 8 блокировок по частоте,
  16 подач питания на обесточенный участок и 8 пар перестановок**.
- Reference решает собственную комплексную SI-систему `A(x)q=b(x)` и её
  производную `A qdot=bdot-Adot q`. Частота измеряется по фазе напряжения
  с учётом потокосцеплений, поля и температурных сопротивлений, не подменяется
  скоростью генератора. Пустая изолированная координата фиксируется в нуле без
  добавочного шунта; её частота не определена. Проверяются исходы синхроконтроля
  и все численные поля решения. Максимальное расхождение частоты <2.72e-9 Hz.
- Consumer повторил все references/comparisons, начальные спектры, непрерывность
  физических состояний при коммутациях, topology/current/counter/inhibit channels,
  copper electrical=heat identity и 40 интегралов энергии. Max native energy
  error **0.01490 J** при gate 0.1 J; independent **2.65e-6 J** при gate 1e-4 J.
  Уточнение времени событий <5.78e-12 s. Это численные метрики, не equipment accuracy.
- Исправлен `automation_jacobian.py`: производные inverse-time измерения
  генератора используют фактическое `Z(T)` и член `-I dZ/Z`, включая заданное
  промежуточное напряжение DAE. Три inverse-кривые проверены конечными разностями
  для температурных/смешанных направлений, согласованных/несогласованных
  алгебраических напряжений и complex Schur products. Проверено электрическое
  отключение генератора. Только этот runtime-файл отличается от R06.53.
- Отдельный Python-I probe старого и нового установленных колёс воспроизвёл
  ошибку температурной производной **0.0138365 A/K → 6.31e-11 A/K**. Она не
  объявляется точностью реального измерителя. Активные inverse-trip trajectories
  в острове этим отдельным tangent-тестом не квалифицируются.
- **371 targeted test passed**, без failures/errors/skips: 153 по топологии,
  автоматике и производным плюс 218 физических регрессий. В том числе 42 новых
  теста. Изолированный installed replay **84 проверки**: 40 новых и 44 предыдущих;
  события и каналы побитово совпали. CLI dual-cycle example выполнен.
- Примеры: `cases/topology-stator/README.md`, четыре JSON. Evidence:
  `reports/topology-stator-{qualified,accepted,installed-replay}-2026-11-16`.
  Поставка: `reports/topology-stator-delivery-2026-11-16/delivery.json`.

**Следующая очередь:** совместить активные OC/UV, inverse-time trip trajectories,
резервирование отказа, АВР и автоматическое повторное включение с новой topology
reference; затем несколько живых островов и публичные machine/thermal anchors.
Текущий корпус использует одну радиальную двухузловую схему и заданные
напряжение/частоту внешнего источника. Окна синхроконтроля намеренно тестовые,
не эксплуатационные уставки. I²-порог поднят для отделения этой квалификации от
активных защит. Не закрыты arbitrary simultaneous roots, unbalanced faults,
PSS/GENROU, OEM/global-hybrid qualification. Installed checks выполнены на том
же host, не являются независимой внешней/аутентифицированной поставкой.
GitHub/CI не используются; изменения не коммитились.

## R06.53 / DES-04 — сетевое OC-реле, задержка выключателя и конкуренция с I²

Связаны в одном независимом расчёте токовое реле генератора, задержанное
отключение, отказ выключателя, I²-защита, электрическое R(T), физический нагрев,
баланс энергии и ограничители TGOV1/SEXS. Источник и линия остаются включёнными.

- Исправлен дефект `network_automation.py`: таймер активного definite-time реле
  повторно проверял pickup внутри непрерывного режима. Округление в найденном
  корне могло дать 149.99999999999997 A вместо 150 A и сорвать интегрирование.
  Кроме того, таймер ошибочно замирал между pickup и dropout. Теперь доступное
  активное реле считает выдержку до отпускания; потеря измерения останавливает
  счёт, неактивное реле сбрасывает прогресс, защёлкнутое не считает заново.
  Статическая функция IEC и inverse-time кривые не изменены.
- Старое установленное колесо R06.52 воспроизвело **4 численных отказа**: Radau
  и DAE на шагах 1 и 0.5 ms. Исправление проверено на тех же режимах. Граница
  изменения runtime относительно R06.52 — только `network_automation.py`.
- **8 авторских сценариев / 32 native runs / 16 независимых траекторий /
  64 сравнения**: паспортные и равновесные температуры, штатное отключение,
  тепловое отключение раньше команды выключателя, отказ выключателя, две
  перестановки списков команд/реле/аппаратов. Проверены 24 I²-отключения,
  8 подтверждений отказа, 8 штатных открытий, 16 команд «уже отключено» без
  увеличения счётчика попыток и 8 точных пар перестановок.
- Независимый consumer повторяет все траектории и сравнения, проверяет ток
  pickup, обе выдержки, причину защёлкивания, непрерывность состояний,
  аналитическое остывание I² после отключения, начальные спектры и энергию.
  Максимальная ошибка интеграла: native **0.000563 J** при gate 0.1 J;
  reference **2.92e-6 J** при gate 1e-4 J. Уточнение времени событий <8e-12 s.
  Это численные проверки, не точность реального оборудования.
- Изолированный Python-I replay установленного колеса: **44 проверки**
  (32 новых + 12 предыдущих unified-сценариев), все события и каналы побитово
  совпали. Checkout/reference imports запрещены. Проверка выполнена на том же
  host; независимая внешняя поставка и аутентификация не заявляются.
- **585 тестов прошли без failures/errors/skips**: 418 по физике, DAE и общему
  сценарию (включая 27 новых) и 167 по автоматике, реле, производным и прежним
  независимым защитным профилям. Это выбранные регрессии, не весь repository
  suite; в первой группе 4 optional ANDES cases deselected. CLI-пример выполнен.
- Примеры: `cases/network-stator/README.md`, три JSON-сценария. Evidence:
  `reports/network-stator-qualified-fixed-2026-11-16`,
  `reports/network-stator-accepted-2026-11-16`,
  `reports/network-stator-installed-replay-2026-11-16`.
  Поставка: `reports/network-stator-delivery-2026-11-16/delivery.json`.

**Следующий крупный блок:** в этом же общем reference проверить открытие
источника/линии, потерю опорного источника и восстановление соединения; затем
добавить UV, резервирование отказа, АВР и синхронизируемое повторное включение.
Для inverse-time измерения генератора с R(T) отдельно нужны производные
фактического температурного импеданса. Публичные якоря параметров машин/нагрева
остаются открыты. Текущий корпус не квалифицирует эти сочетания, произвольные
совпадения корней, OEM-параметры или глобальную гибридную устойчивость.
GitHub/CI не используются; изменения не коммитились.

## R06.52 / DES-04 — unified limiter +I² trip cascade и connection cache fix

Исправлен реальный RMS-winding operator cache defect: cache key теперь содержит
motor/generator connection flags; generator admittance deltas применяются и для
fixed-R generators. Immediate electrical query после opening дообычнойfactorize
не возвращает connected matrix. Reproducer показывалmax complex-voltage error
≈0.104pu;послеfix immediate иfresh factorization совпадают доroundoff. Проверены
motor opening иgenerator opening вfixed-R/stator-R(T) winding profiles.

- Independent unified hybrid engine связывает electrical stator R(T),physical
  generator/motor heat,shaft/kinetic/thermal budget,TGOV1/SEXS limits иlatched
  generator I² trip. Приopening меняется terminal measurement иповторно
  проверяютсяfree rates held controllers. ПриSEXS TATB1 возникает immediate
  trip→release cascade;приTATB.1 release delayed иlower field можетоставаться
  held доhorizon. State/context histories иcausal event order сохранены.
- **6 authored cases /24 native runs /12 independent trajectories /48 comparisons**:
  specified/equilibrium temperatures,двеlead ratios,двеcommand permutations,
  Radau/DAE/two output grids. Проверены24 generator trips,184 limiter events,
  16 immediate cascades,98 held reference segments и8 permutation pairs.
  Native trip/controller/stator helpers запрещены вreference test.
- Consumer повторил12 references/48 comparisons,12 full и12 frozen initial
  spectra,posttrip I² cooling,copper=heat identity и24 integral energy checks.
  Max native energy error≈3.70e-4J при0.1J sampling gate;independent≈1.02e-6J
  при1e-4J gate. Event refinement≤9.79e-11s,power≤1.19e-5W/var. Numerical metrics
  неявляютсяequipment accuracy. Energy helper теперь включает2-point short
  segments trapezoid rule,неdiscard;50us synthetic linear-power case проверен.
- **391 targeted tests,0 failures/errors/skips**,12 новых unified/cache/gate tests.
  4 optional ANDES cases deselected,не весьrepo suite. Same-host Python-I
  installed replay131 checks:12 новых trajectories и119 legacy cases. CLI example
  выполнен. Runtime boundary кR06.49:только`power_grid_env/rms_winding.py`.
- Примеры:`cases/hybrid-stator/README.md`; immediate/delayed/specified JSON.
  Evidence:`reports/hybrid-stator-{qualified,accepted,installed-replay}-2026-11-16`.
  Поставка:`reports/hybrid-stator-delivery-2026-11-16/delivery.json`.

**Далее:** network OC/UV protection иsource/line topology changes вобщемreference,
затемsynchrochecked reclose/islands иpublic machine/thermal anchors. Текущийcorpus
безnetwork OC/UV,faults,line/source switching,reclose,PSS иarbitrary root ties;
external source connected. Parameters authored/assumed. Cache fix касается
RMS-winding motor/generator stamps,не universal topology transaction API.
БезGitHub/CI,безOEM/global hybrid stability certificate.

## R06.51 / DES-04 — stator R(T)/heat/energy + TGOV1/SEXS limits

Независимо квалифицированы upper/lower limiter entry,hold иrelease в electrical
stator R(T) профиле с physical heat,shaft/kinetic/thermal energy иmotor R(T).
**Runtime/wheel R06.49 не изменены**: расширен independent combined reference,
добавлены corpus,consumer gates,examples иtargeted tests.

- Independent SI stator/winding/PV/heat equations связаны с отдельным root-localized
  limiter engine. Active valve/field rate удерживается0,остальные machine/heat
  states продолжают динамику. Reference/held contexts сохранены по segments;
  historical иone-sided samples не меняются от последующих commands.
- **8 authored cases /32 native runs /16 independent trajectories /64 comparisons**:
  TGOV1/SEXS/both ×passport/equilibrium +2 reversed command-list cases;
  Radau/DAE/two output grids. Проверены192 native limiter events,88 held reference
  segments и8 command permutation pairs. Event identity включает controller,
  bound иcommand value/outcome. Native limiter/stator/energy helpers запрещены
  вreference test.
- Consumer повторил16 trajectories/64 comparisons,16 full и16 frozen initial
  spectra,copper electrical=heat identity и32 integral energy balances.
  Max native saved-grid energy error≈1.433e-4J при0.1J gate;independent dense
  error≈1.23e-6J при1e-4J gate. Event refinement≤1.22e-11s,power≤2.25e-5W/var.
  Это numerical refinement metrics,не equipment accuracy. Shared energy gate
  выделен в `benchmarks/energy_integral_gate.py` иhash-bound вconsumer/delivery.
- **186 targeted tests,0 failures/errors/skips**,13 новых combined limiter tests.
  Проверены clipped/held rows,actual stator current/heat/energy products,
  inconsistent-V DAE residual иcomplex Schur,история references,wrong-bound
  rejection иточная command-order invariance. R06.49 broad348-test evidence
  остаётся в прежней поставке и не объявляется заново выполненной.
- Same-host Python-I installed replay119 checks:6 новых limiter trajectories
  и113 прежних. CLI example выполнен;GitHub/CI не используются.
- Примеры:`cases/stator-limiter/README.md`; JSON governor/exciter/both.
  Evidence:`reports/stator-limiter-{qualified,accepted,installed-replay}-2026-11-16`.
  Поставка:`reports/stator-limiter-delivery-2026-11-16/delivery.json`.

**Далее:** общий hybrid reference для limiter +I² trip/network protection,
затемsource/line switching,synchrochecked reclose иpublic machine/thermal anchors.
Текущий corpus безfaults,generator trip,active OC/UV,islands/reclose илиPSS;
external source connected. Parameters assumed/authored. Не доказывает arbitrary
simultaneous-root handling,OEM correctness илиglobal hybrid stability.

## R06.50 / DES-04 — combined stator R(T)/heat/I² trip/energy qualification

Независимо квалифицировано одно permanent generator opening для совместного
профиля electrical stator R(T),physical generator heat,energy closure,I² replica
и motor R(T). **Runtime/wheel R06.49 не изменены**: добавлены independent combined
hybrid reference,corpus,consumer/delivery gates,examples и targeted tests.

- Independent SI stator/network branch при trip удаляет одновременно admittance
  и injection. Copper current/heat/power исчезают; physical temperature,rotor,
  controller и I² states непрерывны. Поле остаётся возбуждённым,поэтому core heat
  не обнуляется. Без SEXS температура после trip сходится к Tambient+Pcore/G
  с pole−G/C;I² replica отдельно затухает к0 с pole−1/tau.
- **6 authored cases /24 native runs /12 independent trajectories /48 comparisons**:
  TGOV1/SEXS/both ×passport/equilibrium,trip до/во время finite fault,
  Radau/DAE/two output grids. Native current/thermal/network/energy helpers
  запрещены independent-reference test. Нет active OC/UV,limiter crossings,
  external-source loss,islands/reclose или other topology commands.
- Offline consumer повторил12 trajectories/48 comparisons,12 full initial
  spectra и12 frozen-temperature spectra,252 posttrip replica cooling points,
  threshold metadata и continuity;проверил electrical copper=heat copper.
  **24 native integral energy checks**:max error≈0.001193J при0.1J sampling gate;
  independent dense integral≤2.75e-9J при1e-4J gate. Event refinement≤4.69e-14s.
  Эти numerical metrics не являются точностью физического оборудования.
- **173 unique targeted tests /189 executions,0 failures/errors/skips**,18 новых
  combined tests. Проверены closed/open DAE actions с I² и heat state,cross
  derivatives и отсутствие temperature-dependent ghost stamp после opening.
  Предыдущая348-test evidence остаётся в неизменённой R06.49 поставке;здесь она
  не объявляется повторно выполненной. Same-host Python-I replay113 checks:
  12 новых combined trajectories и101 прежняя. CLI example выполнен.
- Примеры:`cases/stator-trip/README.md`,before-fault/during-fault/fixed-field JSON.
  Evidence:`reports/stator-trip-qualified-2026-11-16`,
  `reports/stator-trip-accepted-final-2026-11-16`,
  `reports/stator-trip-installed-replay-2026-11-16`.
  Поставка:`reports/stator-trip-delivery-2026-11-16/delivery.json`.

**Далее:** combined stator R(T)/limiter crossings/network protection,
source/line topology changes иsynchrochecked reclose;public thermal/machine anchors.
Текущий corpus balanced radial с одним GENCLS иtwo-cage motor,constant-Z load,
external source connected иauthored parameters. Не закрывает arbitrary topology,
negative-sequence/EMT/field-hotspot physics,OEM calibration или global hybrid
stability. Без GitHub/CI,всё воспроизведение локальное.

## R06.49 / DES-04 — electrical generator stator R(T) + energy partition

Добавлен `GENCLS_TGOV1_SEXS_STATOR_RT`: generator Norton admittance и injection
используют physical phase Rref*(1+alpha*(T−Tref)) с machine-base conversion.
Сопротивление меняет voltage drop,network solution,current и внутреннюю EMF.
Старые observer/lumped-loss profiles сохраняют свои semantics.

- Dynamic operator cache учитывает generator temperature и topology; augmented
  DAE injection product содержит Y_T*(E−V),а current tangent содержит−I*dZ/Z.
  Core controller/machine derivatives используют actual Z; добавлены недостающие
  temperature terms rotor/I² rows и heat gradients. Проверены J·v, inconsistent-V
  residual,complex Schur,hot operator perturbation и electrical copper identity.
- Joint initializer расширяет reduced unknowns generator temperatures и решает
  motor speed/Ts/Tr,generator complex EMF/Tg вместе с P/V и heat balance.
  Analytic reduced Jacobian проверен finite differences. Full ODE/KCL,heat watts,
  Q limits и physical spectrum gates сохранены. Zero-loss ambient boundary
  фиксируется точно. Passport mode требует specified generator temperature;
  solved generator temperature требует joint equilibrium.
- Electrical identity Pinternal−Pterminal=3R(T)|I_A|² согласована с heat input.
  Дополнительная shaft debit содержит **только core loss**,не copper loss второй
  раз. Constant-shaft-power H*S*omega² rotor/thermal budget сохранён; field/EMT
  energy storage и thermal hotspots не представлены.
- **4 authored scenarios /16 native runs /8 independent SI trajectories /
  32 comparisons**,hold/fault,two initial policies,Radau/DAE/two output grids.
  Independent SI winding/KCL/stator/PV/heat root и собственные loss/energy laws.
  Consumer повторил8 references/6 native refusals,8 spectra и16 integral energy
  checks;max native error≈3.281e-4J при0.1J gate,reference≈1.48e-9J при1e-4J gate.
  Проверено равенство electrical copper loss и copper heat;не только total budget.
- **348 unique targeted tests,0 failures/errors/skips**,13 новых stator tests.
  4 optional ANDES cases deselected,не весь repo suite. Same-host Python-I
  installed replay101 checks:4 новых positive,6 refusals и91 legacy cases.
  CLI example выполнен. Никаких GitHub/CI действий.
- Примеры:`cases/generator-stator/README.md`;новые channels `g.stator.*`.
  Evidence:`reports/generator-stator-{qualified,accepted,installed-replay}-2026-11-16`.
  Поставка:`reports/generator-stator-delivery-2026-11-16/delivery.json`.

**Далее:** combined stator R(T)/heat/protection/trip/topology qualification,
public machine/thermal parameter anchors и broader independent networks.
Текущий corpus:один GENCLS,один two-cage motor,balanced radial network,constant-Z
background,finite balanced faults,controller interiors и external source.
Нет qualified stator-R(T) limiter crossings,islands/reclose,negative-sequence
heating,saturation,rotor/field hotspots,OEM calibration или arbitrary topology
certificate. Все новые passports assumed/authored,не измеренные OEM данные.

## R06.48 / DES-04 — energy-consistent lumped-loss GENCLS

Добавлен отдельный `GENCLS_TGOV1_SEXS_ENERGY`: copper/core heat нагружает вал,
а rotor equation использует constant shaft power и Ekin=H*S*omega²,а не
nominal-speed acceleration approximation. Требуется один heat node на generator
и положительная speed. Старый HEAT observer сохраняет one-way semantics.

**Граница модели:** electrical port всё ещё pure transient reactance GENCLS.
Copper heat — сосредоточенная нагрузка на вал,не electrical resistance stamp;
отдельное resistive voltage drop не моделируется. Замкнут заявленный budget
shaft/terminal/kinetic/thermal/signed damping exchange,не field-resolved machine.
Damping — signed external power exchange,не heat loss. Поле/shaft torsion/EMT
energy storage этим профилем не представлены.

- Initialization включает heat losses в shaft input/TGOV1 reference ровно один
  раз. Для authored hold:20kW terminal+883.70W heat→20.8837kW shaft,omega1.
  Energy dynamics и loss/speed-denominator derivatives входят в analytic DAE;
  проверены off-nominal speed,inconsistent-V residual,J·v,complex Schur.
- **4 authored scenarios /16 native runs /8 independent SI trajectories /
  32 comparisons**,hold/fault,passport/equilibrium,Radau/DAE/two output grids.
  Independent SI reference отдельно считает kinetic/thermal derivatives,
  shaft/loss initialization и physical spectrum; native energy helpers запрещены
  в reference test. Дополнительно проверены lossless stationary limit,nonpositive
  speed refusal и отсутствие double loss debit.
- Offline consumer повторил8 references/8 initial refusals и8 full spectra.
  Independent dense quadrature сравнивает energy change с integral power;
  native Simpson quadrature использует сохранённые one-sided power samples.
  **16 native integral checks**:max error≈3.247e-4J при declared gate0.1J;
  independent max error≈1.78e-9J при gate1e-4J. Это numerical integration metrics,
  не физическая точность. Energy channels comparison gate0.003J,power0.003W/var.
- **335 unique targeted tests,0 failures/errors/skips**,11 новых energy tests.
  4 optional ANDES cases deselected,не весь repo suite. Same-host Python-I
  installed replay91 checks:4 новых positive,8 refusals,79 legacy cases.
  CLI example выполнен; GitHub/CI не используются.
- Примеры:`cases/generator-energy/README.md`;channels `g.energy.*`,`g.heat.*`.
  Evidence:`reports/generator-energy-{qualified,accepted,installed-replay}-2026-11-16`.
  Поставка:`reports/generator-energy-delivery-2026-11-16/delivery.json`.

**Далее:** electrical stator R(T) stamp с voltage drop и правильным loss partition
(при появлении electrical copper loss убрать его повторную shaft debit);
combined energy/heat/trip/topology qualification и public parameter anchors.
Нет OEM calibration,field/negative-sequence heat,limiter crossings,islands/reclose
или arbitrary topology certificate. Новые parameters assumed/authored.

## R06.47 / DES-04 — dimensioned generator temperature observer

Добавлен opt-in `GENCLS_TGOV1_SEXS_HEAT` и `generator_heat`: отдельное состояние
температуры°C, C[J/K],G[W/K],phase Rref[ohm],alpha и authored provenance.
Heat input:3R(T)|I_A|² +Pcore_ref|Vterminal_pu|²;heat output:G(T−Tambient).
Это **one-way thermal observer**,не полная electrothermal machine:
observed losses не вычитаются из GENCLS electrical/shaft power и electrical
stator resistance stamp не меняется. Ограничение явно присутствует в result
metadata и документации; I² replica остаётся отдельной защитной моделью.

- Joint motor heat/PV initializer находит generator equilibrium аналитически,
  требует stable positive heat-removal denominator, проверяет temperature range,
  original derivatives и residual в ваттах. Specified mode допускает
  нестационарную initial temperature. Capacity меняет pole,не equilibrium.
- Analytic variable-Y actions включают current/terminal-voltage/temperature
  derivatives;проверены eliminated и inconsistent-V DAE residual,J·v,complex Schur.
  Full stationary spectrum содержит15 states. Frozen-temperature passport
  spectrum сверяется отдельно,с исключением всех heat states.
- **4 authored scenarios /16 native runs /8 independent SI trajectories /
  32 comparisons**,Radau/DAE/two grids,hold/finite fault,две initial policies.
  Offline consumer повторил8 references/8 native refusals и8 full spectra;
  power refinement≤4.77e-7W/var. Refusals:wrong flag,wrong stationary policy,
  nonpositive heat stability denominator и equilibrium above validity range.
- **324 unique targeted tests /337 executions,0 failures/errors/skips**;
  13 новых heat tests. Отдельный600s constant-input test совпал с closed-form
  exponential и integrated heat energy. Это не600s network qualification.
  Same-host Python-I installed replay79 checks:4 новых positive,8 refusals,
  67 legacy cases. 4 optional ANDES cases deselected;не весь repo suite.
- Примеры: `cases/generator-heat/README.md`. Evidence:
  `reports/generator-heat-final-qualified-2026-11-16`,
  `reports/generator-heat-accepted-2026-11-16`,
  `reports/generator-heat-installed-replay-2026-11-16`.
  Поставка:`reports/generator-heat-delivery-2026-11-16/delivery.json`.
  Предварительная qualification с ошибочным passport/full-spectrum comparison
  gate сохранена как failed;используется только final-qualified corpus.

**Далее:** electrical/shaft loss feedback и согласованный полный machine energy
balance;combined heat/trip/topology profiles;публичные thermal parameter anchors.
Не закрыты multi-node stator/rotor/coolant heat,hotspots,temperature trips,
frequency-dependent iron loss,OEM calibration и arbitrary topology. Все новые
thermal параметры assumed/authored. Без GitHub/CI и без заявлений о превосходстве.

## R06.46 / DES-04 — independent generator trip и post-opening trajectory

Закрыта независимая численная проверка одного permanent generator opening
по stator I² replica в общей модели GENCLS/TGOV1/SEXS/motor R(T). **Runtime и
wheel R06.45 не изменены**; добавлены independent hybrid reference, corpus,
verification/delivery gates, runnable cases и targeted tests.

- Independent SI circuit после trip удаляет generator Norton admittance и
  injection. Generator current/Pe=0; rotor/controllers продолжают динамику.
  SEXS после открытия использует isolated machine terminal EMF,а не grid bus.
  Flux/speed/temperature/replica states непрерывны; network voltages/currents
  допускают algebraic jump. Replica после trip точно затухает с pole−1/tau.
- **6 authored cases /24 native runs /12 independent trajectories /48 comparisons**:
  TGOV1/SEXS/both ×specified/equilibrium replica,Radau/DAE/two grids.
  Specified case открывает генератор до fault≈0.00713s; equilibrium case во время
  finite fault≈0.10373s. R=.5 governor droop задан в owner recipe,не tuned на ходу.
- Offline consumer повторил12 trajectories и48 comparisons,включая one-sided
  event samples; проверены24 generator trips и252 posttrip cooling points.
  Event refinement≤3.91e-14s, power refinement≤5.37e-7W/var. Эти величины
  характеризуют numerical refinement,не точность физического оборудования.
  Fixed comparison gates2e-7s и0.003W/var сохранены.
- **147 targeted tests,0 failures/errors/skips**,16 новых trip tests. Native
  thermal/controller/current/topology helpers запрещены independent-reference
  test. Source remains connected;нет islands/reclose/OC/UV или limiter crossings.
- Same-host Python-I installed replay67 checks:12 новых generator-trip runs
  и55 прежних. CLI example выполнен. Старые295-test evidence остаются в R06.45;
  они здесь не заявляются повторно выполненными.
- Примеры: `cases/generator-trip/README.md`; JSON для обеих initialization policies.
  Evidence: `reports/generator-trip-{qualified,accepted,installed-replay}-2026-11-15`.
  Поставка: `reports/generator-trip-delivery-2026-11-15/delivery.json`.

**Далее:** физическая generator heat model: dimensioned losses,capacity,heat
removal,temperature и происхождение parameters;затем wider fault/topology-coupled
limiter profiles. I² replica по-прежнему не температура в°C. Нельзя обобщать
этот corpus на arbitrary topology changes,reclose,islands,OEM qualification
или global hybrid stability. Без GitHub/CI,всё воспроизведение локальное.

## R06.45 / DES-04 — generator stator I² replica в общей RMS/DAE модели

Новый opt-in `GENCLS_TGOV1_SEXS_I2` разрешает существующую stator-I² protection
replica генератора одновременно с motor winding R(T) и TGOV1/SEXS. Старые flags
продолжают запрещать generator_thermal. Это **безразмерное защитное состояние,
не температура генератора в°C**; generator copper/iron losses, heat transfer,
hotspots и generator R(T) на этом этапе не реализованы.

- Analytic mixed callback расширен до generator_thermal.end и использует
  существующие exact current-squared derivatives с variable-Y voltage response.
  Проверены J·v и inconsistent-V residual. Joint motor heat/PV initialization
  инициализирует generator replica из actual current², проверяет её initial trip
  threshold и все physical derivatives; specified replica policy явно отвергается
  в stationary mode. Полный physical spectrum дляboth controllers содержит15 states.
- **4 authored cases /16 native runs /8 independent trajectories /32 comparisons**:
  passport/equilibrium,hold/fault,Radau/DAE/two grids. Independent SI reference
  отдельно интегрирует I² filter и находит initial current². Corpus остаётся ниже
  trip threshold; offline consumer повторил8 references/6 initial refusals,
  32 comparisons и8 full physical spectra. Power refinement≤4.78e-7W/var.
- Дополнительные native tests проверили одно latched electrical trip, generator
  current extinction и latched flag в обеих engines. **Trip timing и post-trip
  waveform ещё не квалифицированы независимым hybrid reference**.
- Targeted regression:295 tests, включая11 новых replica tests. Same-host Python-I
  installed replay55 checks:4 новых positive,6 refusals,2 trip и43 legacy cases.
  Число тестов является целевой регрессией, не всем repo suite; ANDES cases excluded.
- Примеры: `cases/generator-replica/README.md`; positive и native-trip JSON рядом.
  Evidence: `reports/generator-replica-{qualified,accepted,installed-replay}-2026-11-15`.
  Поставка: `reports/generator-replica-delivery-2026-11-15/delivery.json`.

**Далее:** independent generator-trip/topology reference; физическая generator
heat model с отдельными loss/heat parameters и происхождением; fault/topology
coupled limit profiles. Статорная I² replica не закрывает задачу физического
нагрева генератора и не подтверждает OEM thermal capability. Без GitHub/CI.

## R06.44 / DES-04 — независимая hybrid qualification ограничителей

Закрыт bounded pulse профиль entry/hold/release для TGOV1/SEXS в связке с
двухклеточным motor R(T). **Физическое ядро и wheel R06.43 не изменены**:
на этом этапе добавлены независимый hybrid reference, численная квалификация,
примеры, тесты и воспроизводимая локальная поставка.

- Independent DOP853 локализует entry/release roots, фиксирует active bound,
  удерживает derivative0 и снимает ограничение по reversal свободной derivative.
  Immediate command-induced release и delayed release без команды проверены.
  Для combined case SEXS lower release≈0.4524s. Reference хранит отдельные
  references/held modes в каждом segment context; historical и one-sided samples
  не меняются от последующих команд. Native limiter/controller helpers запрещены
  test monkeypatch; native event times не используются при интегрировании.
- **8 authored cases /32 native runs /16 independent trajectories /64 comparisons**:
  TGOV1/SEXS/both ×passport/equilibrium +2 reversed command-list cases;
  Radau/DAE/two output grids. Native и reference проходят обе границы каждого
  активного controller. Проверено **192 native limiter events**; event identity
  включает controller,bound,command value/outcome,а не только kind/equipment.
- Offline consumer повторил16 references/64 comparisons,88 held segments,
  все сохранённые pre/post samples и8 command permutation pairs. Event refinement
  difference≤6.57e-12s, power refinement≤4.85e-5W/var; fixed gates2e-7s и0.003W/var.
  Это numerical qualification заданных pulse sequences,не arbitrary/global proof.
- **142 targeted tests,0 failures/errors/skips**,включая13 новых limiter tests.
  Предыдущая broad879-test evidence остаётся в неизменённой R06.43 поставке;
  здесь она не объявляется заново выполненной. Same-host Python-I replay43 checks,
  включая6 новых limiter trajectories и37 прежних. CLI example также выполнен.
- Команды/примеры: `cases/limiter-winding/README.md`.
  Evidence: `reports/limiter-winding-{qualified,accepted,installed-replay}-2026-11-15`.
  Поставка: `reports/limiter-winding-delivery-2026-11-15/delivery.json`.

**Далее:** generator thermal coupling; затем fault/topology-coupled controller
limit profiles и совместная автоматика с R(T). Не закрыты OC/UV interactions,
переключения/islands/reclose, arbitrary coincident roots, PSS/GENROU и OEM/global
hybrid stability. Новые случаи без КЗ и с inactive OC; initial controller states
строго внутри bounds. Без GitHub/CI и без заявлений о превосходстве над всеми.

## R06.43 / DES-04 — TGOV1/SEXS в связке с motor R(T)

Новый opt-in `winding_generator_profile: GENCLS_TGOV1_SEXS` поддерживает
GENCLS с TGOV1,SEXS или обоими. Старый fixed-field flag продолжает отвергать
регуляторы. PSS/GENROU/generator thermal protections по-прежнему не допускаются.
SEXS меняет generator EMF и network injection; TGOV1 меняет mechanical input.
Motor R(T), токи, rotor swing и controller states связаны общей сетью.

- Переиспользованы существующие analytic controller/GENCLS branch derivatives
  с реальным variable-Y voltage response; motor rows заменяются physical winding
  actions. Проверены eliminated и inconsistent-V J·v/residual, complex Schur,
  clip/held rows и обе temperature policies.
- Совместная P/V/load/heat initialization теперь инициализирует controller
  states/references, проверяет original derivatives и full physical spectrum:
  12 states с одним controller,14 с двумя. Для passport-temperature mode спектр
  остаётся frozen-temperature electromechanical/controller subset.
- **12 authored radial cases /48 completed native runs /24 independent
  trajectories /96 comparisons**: TGOV1/SEXS/both,passport/equilibrium,hold/fault,
  Radau/DAE/two output grids. Independent SI controller/winding/KCL/swing equations
  и собственные root solves; запрет native controller/heat-init helpers в test.
- Offline consumer повторил24 trajectories и6 initial refusals, пересчитал96
  comparisons,24 full physical spectra и12 stationary holds. Reference refinement
  power difference≤4.36e-7W/var при absolute gate0.003W/var.
- **6 initial refusals:** governor initial upper limit, exciter initial upper
  limit и controls под fixed-field flag, по2 engines. Qualified positive traces
  держатся минимум1e-4pu внутри controller bounds; initial boundary также запрещён.
- **879 unique targeted tests /879 executions,0 failures/errors/skips**;
  4 optional ANDES cases deselected; это не весь repo suite.
- Same-host Python-I installed replay:37 checks, включая6 новых controller fault
  runs,6 новых refusals и25 прежних. Примеры/команды:
  `cases/controlled-winding/README.md`. Evidence:
  `reports/controlled-winding-{qualified,accepted,installed-replay}-2026-11-15`.
  Поставка: `reports/controlled-winding-delivery-2026-11-15/delivery.json`.

**Далее:** independent hybrid limiter entry/release trajectories; generator
thermal coupling; затем совместная автоматика/смена топологии с R(T).
Локальные derivative tests clipped/held rows **не квалифицируют limiter events**.
Нет нового OEM certificate, islands/reclose/PSS/GENROU или произвольной topology
qualification. Все параметры assumed/authored; OC pickups держат relays inactive.
GitHub/CI не используются, поставка и воспроизведение локальные.

## R06.42 / DES-04 — совместное P/V/load/heat равновесие с GENCLS

Закрыт следующий ограниченный профиль: `winding_state: equilibrium` теперь
совместим с `winding_generator_profile: GENCLS_fixed_field`. Совместно ищутся
motor speed/Ts/Tr и complex generator EMF; network voltages и steady cage states
исключаются аналитически. После root генератор получает постоянные field/PM.
**Тепловые состояния здесь принадлежат двигателю, нагрева генератора пока нет.**

- Analytic reduced Jacobian учитывает temperature-dependent motor admittance,
  обе rotor cages, generator injection, terminal P/V constraints и heat balance.
  Приёмка проверяет original physical ODE/KCL, heat residual в ваттах, Q limits,
  relay replica limits и full physical spectrum. В authored two-cage case10 states,
  включая Ts/Tr и generator rotor; α≈−0.00933358/s. Это local fixed-topology
  stability, не hybrid/global stability и не доказательство уникальности root.
- **8 authored cases /32 completed native runs /16 independent trajectories /
  64 comparisons**, обе engines/output grids; mutual0/.02 и два temperature seeds.
  Independent SI P/V/load/heat root, stator/two-rotor KCL и swing equations.
  Offline consumer повторил16 trajectories/8 native refusals, сверил все10
  eigenvalues для32 runs и отсутствие дрейфа в16 hold runs. Refinement power
  difference≤3.00e-6W/var при абсолютном comparison gate0.003W/var.
- **8 initial refusals**: cooling singularity, temperature ceiling с huge heat
  capacities, generator Q limit и нестационарная initial relay policy, по2 engines.
  Температуры seeds не навязываются stationary state; изменение теплоёмкостей
  сохраняет root и изменяет thermal spectrum. Native helpers запрещены в
  independent-root test. Отдельно проверены5s hold и mixed single/double motor group.
- **863 unique targeted tests /874 executions,0 failures/errors/skips**;
  4 optional ANDES cases deselected. Это целевая регрессия, не весь репозиторий.
  Same-host Python-I installed replay:25 checks. Старый refusal joint equilibrium
  намеренно заменён новым положительным профилем; прочие выбранные legacy traces
  воспроизведены. Публикация/GitHub/CI не используются.
- Примеры и команды: `cases/mixed-equilibrium/README.md`.
  Evidence: `reports/mixed-equilibrium-qualified-2026-11-15`,
  `reports/mixed-equilibrium-accepted-final-2026-11-15`,
  `reports/mixed-equilibrium-installed-replay-2026-11-15`.
  Поставка: `reports/mixed-equilibrium-delivery-2026-11-15/delivery.json`.

**Далее:** отдельные проверенные профили AVR/governor и generator thermal coupling;
затем совместная автоматика/смена топологии с R(T) и независимые новые схемы.
GENROU/AVR/PSS, generator heat, islands/reclose и industrial/OEM qualification
не закрыты. Benchmark остаётся authored radial balanced network с одним GENCLS,
одним двухклеточным motor и inactive OC pickups; дополнительные smoke tests
не расширяют независимую квалификацию на произвольные масштабы и топологии.

## R06.41 / DES-04 — GENCLS + RMS R(T), фиксированное возбуждение

Добавлен opt-in `winding_generator_profile: GENCLS_fixed_field` для совместной
работы двигателей с R(T) и классических синхронных машин без регуляторов.
Сетевой directional injection теперь складывает motor `b_x-Y_xV` и generator
rotor injection; аналитические motor/GENCLS actions используют общий voltage
response. Проверены J·v, inconsistent-V residual и complex Schur DAE blocks.

- Квалифицирован ограниченный authored профиль: один downstream GENCLS100kVA,
  P20kW/V0.96pu, двухклеточный двигатель75kVA; mutual0/.02, Ts/Tr20/20 и90/70C,
  hold и finite balanced fault0.1–0.2s. **8 случаев /32 native runs /16 независимых
  траекторий /64 comparisons**, Radau/DAE и две output grids.
- Независимые SI winding/KCL, generator swing и собственный P/V/load root.
  Offline consumer пересчитал16 trajectories и64 comparisons; уточнение
  мощности отличается максимум на3.49e-6W/var при absolute gate0.003W/var.
  Тест запрещает вызов native initialization/generator/winding tangent helpers
  из эталона; frozen-temperature electromechanical spectrum также сопоставлен.
- Loaded initialization согласует electrical/motor/rotor states при заданных
  температурах. Thermal rates в metadata явно ненулевые: это **не совместное
  тепловое равновесие**. После PV initialization возбуждение постоянно, AVR нет.
- Same-host Python-I installed wheel replay:14 checks, включая4 новых позитивных,
  2 refusals и8 прежних. Исторические evidence остаются привязаны к прежним wheels.
- Примеры: `cases/mixed-winding/{hold,fault}.json`; воспроизведение описано рядом.
  Evidence: `reports/mixed-winding-{qualified,accepted,installed-replay}-2026-11-15`.
  Локальная поставка: `reports/mixed-winding-delivery-2026-11-15/delivery.json`.

**Следующая физика:** совместное mixed PV/heat stationary initialization;
затем отдельные профили возбуждения/регуляторов и generator thermal coupling.
GENROU/AVR/PSS/governors, генераторный нагрев, islands/reclose и более широкие
топологии этим этапом не квалифицированы. OC pickups в новых сценариях подняты:
защиты не участвуют, поэтому их координация не объявляется проверенной.
Параметры генератора assumed/authored, не OEM. Полное закрытие DES-04 и
превосходство над другими симуляторами не заявляются. GitHub/CI не используются.

## R06.40 / DES-04 — two-cage RMS R(T),cage losses и joint stationary state

Расширен существующий double-cage RMS: теперь обе клетки участвуют в R(T),
потерях, thermal dynamics,analytic DAE actions и stationary elimination.
Сохраняются coordinates E=u1+u2 иu1, rotor mutual leakage. Общая Tr и alpha
масштабируют разные reference R1/R2; это не separate cage temperatures/hotspots.

- Нативные A(T)/B(T), rotor currents и per-cage copper losses; total rotor heat
  равен сумме двух потерь. Пер-клеточные loss channels теперь используют R(T),
  а не reference R. Объединение diagnostics исключает duplicate channel samples.
- Full stationary3n search использует2×2 rotor response и analytic derivatives
  по speed/Tr,включая auxiliary u1 rows. Watt-based heat gate сохранён; полный
  physical spectrum содержит8 states с I² replica и7 без неё. Direct DAE,
  inconsistent-V residual,J·v,complex Schur и mixed single+two-cage group проверены.
- **33 authored radial scenarios /120 completed native runs /66 independent
  trajectories**:cold,fixed-hot,full equilibrium;mutual0/.02;hold,primary,
  CB-failure,CT-loss,no-UV,3 cage-label permutations;Radau/DAE/output grids.
  Ещё **12 initial refusals** по6 причинам/двум engines.
- Отдельные physical SI equations двух rotor fluxes и5-row winding/KCL,
  собственный T-circuit/heat root и independent hybrid clocks/actuators.
  Запрет native cage/init/tangent helpers проверен тестом. Offline gate повторил
  все66 references и12 native refusals,пересчитал240 comparisons,8-state spectra,
  loss identities/hold/label invariance;helper hooks сохранили single-cage refs.
- Перестановка клеток сохраняет terminal/thermal traces иevents;individual
  EMF/current/resistance/loss меняются местами. Identical cells точно переходят
  в single cage с Rr/2,Xr/2 при mutual0;fault+heat limit проверен.
- Max spectrum disagreement≈2.15e-7/s,stationary hold drift≈9.72e-16,cage-loss
  sum error≈1.46e-11W,stationary power error≈5.75e-10W. Permutation powers
  согласованы в cap3mW. Это implementation agreement,не OEM accuracy.
- **841 targeted tests passed**,0 failures/errors/skips;4 optional ANDES
  checks явно deselected. Не полный repository suite.16 новых two-cage tests
  плюс transfer regression проверяют также explicit omission acknowledgement:
  supported single-cage RMS→Gamma больше не silently drops winding_thermal.
  Two-cage→single Gamma по-прежнему отвергается.
- Workflow postconditions:84 protected runs отключают мотор через UV,
  12 holds сохраняют connection,24 no-UV controls сохраняют connected motor.
  Это numerical/postcondition coverage,не inherited engineering approval.
- **8 installed checks**:4 новых completed,2 initial refusals,2 legacy RMS/phase.
  CLI owner study выполнен. Host/dependencies общие;Python-I/import guard не
  OS sandbox/another-host qualification. Old corpora retain old wheels/fingerprints.

Runtime fingerprint:
`f252e6a892a2138d7b60f05e2ca7d1213fd175ec339bc02dc6b6130664cfec63`.
Wheel SHA256:
`2b9b3714c4aa2ab5d59eb21c10c051b84e80b75880dc7b2228b6d0a49b3b2c44`.
Source boundary относительно R06.39:6 changed runtime files
(induction,induction_network,coupled_rms,rms_winding,rms_winding_equilibrium,
motor_transfer),0 added/missing runtime files.

Evidence: `reports/two-cage-winding-final-qualified-2026-11-15`,
`reports/two-cage-winding-accepted-2026-11-15`,
`reports/two-cage-winding-installed-replay-2026-11-15`,
`reports/two-cage-winding-delivery-2026-11-15/delivery.json`.
Команды/5 runnable examples: `cases/two-cage-winding/README.md`.
Первый intermediate corpus до усиленного label-mapping gate сохранён отдельно;
final receipt использует final gate и runtime. GitHub/CI/publication не использовались.

Открыто: **mixed synchronous generators с R(T)** (пока явно rejected),3+ cages
в RMS,separate cage thermal nodes/material coefficients,temperature-trip/
selectivity,wide topology/uncertainty qualification и OEM parameter identification.
Фазный full-winding multicage coverage не переносится автоматически в RMS.
DES-04 остаётся in_progress;не заявляем arbitrary-scale/all-equipment superiority.

## R06.39 / DES-04 — полное stationary electrical/mechanical/winding equilibrium

Новый opt-in `operating_point.winding_state: equilibrium` совместно находит
speed/EMF/network/Ts/Tr, а при наличии I² replica — её equilibrium level.
Начальные temperatures становятся search seeds. Default `passport` и прежний
canonical JSON сохранены. Общий initializer подключён к coupled/induction paths.

- Bounded reduced3n system, analytic reduced Jacobian,4 speed seeds; exact
  synchronous no-load boundary и passive cooling matrix gate. Профиль1–32
  connected thermal single-cage motors без генераторов. Размеры1/2/4 проверены
  тестами, не заявляем qualification всех32 или широкой топологии.
- **Heat balance решается в мощности**, а не только в dT/dt: отдельный watt
  gate `max(1e-7W,1e-10·Srated)` на каждом узле. Проверена invariance stationary
  point при увеличении capacities в1e9 раз. Это закрывает ложное equilibrium
  из-за tiny thermal derivatives при огромном C.
- Original physical ODE/KCL/static-dynamic voltage checked. Native analytic
  spectrum теперь включает E/speed/Ts/Tr/I² replica; это fixed-topology
  physical stability, не hybrid/global stability защит/контакторов.
- **20 physical scenarios /40 seed configurations /144 completed native runs**:
  4 cable-temperature/source-voltage endpoints,primary/CB-failure/CT-loss/no-UV,
  5s holds,2 seeds,Radau/DAE. Faults —2 output grids,holds —1.
  Ещё **12 initial rejections**,6 причин×2 engines:cooling singularity,
  temperature ceiling,excess load,replica passport mismatch,disconnected/missing
  thermal model. Неуспех local search не объявляется global nonexistence.
- **80 independent trajectories /288 comparisons**:separate SI T-circuit+heat
  root,numerical Jacobian,all-flux winding/KCL/thermal/clock DOP853. Запрет
  native helpers проверен тестом. Offline verifier повторил все80 references
  и12 native refusals;пересчитал root/spectrum/seed/hold/numerical gates,
  stationary active-power balance и copper-heat removal из trace measurements.
- Max root disagreement≈2.27e-9(native units),physical spectrum≈4.48e-8/s,
  5s hold drift≈1.42e-10,power-balance error≈7.03e-8W. Это agreement моделей,
  не измеренная OEM accuracy/temperature precision.
- **791 targeted tests passed**,0 errors/failures/skips.4 optional ANDES
  checks явно deselected;не полный repository suite.22 equilibrium tests
  включают analytic reduced-Jacobian finite differences,1/2/4 motors,
  no-load boundary,no replica,linear heat-network limit,validity/refusals,
  independent root/dynamics и heat-capacity independence.
- **8 installed checks**:4 новых completed,2 expected initial refusals,
  2 legacy RMS/phase. Common arrays/axes/events совпадают с replay baseline;
  old corpora не переквалифицированы целиком. CLI owner study тоже выполнен.
  Host/dependencies общие;Python-I/import guard не OS sandbox/another-host proof.

Final runtime fingerprint:
`030241c27246bfb18ff1e8f28d42c83118dc709b3eaed5a52ea0f4cbed166bb3`.
Final wheel SHA256:
`cb8ce943ec9f5be76b71f5dea5815bafaf1a9f6300de51fba3ab61ea0b1660c2`.
Source boundary относительно R06.38:4 changed runtime files
(induction,coupled_initialization,motor_operating_point,induction_network),
1 new rms_winding_equilibrium.py,0 missing. Previous wheels/fingerprints retained.

Final evidence: `reports/rms-equilibrium-power-qualified-2026-11-15`,
`reports/rms-equilibrium-power-accepted-2026-11-15`,
`reports/rms-equilibrium-power-installed-replay-2026-11-15`,
`reports/rms-equilibrium-delivery-2026-11-15/delivery.json`.
Команды/5 runnable cases: `cases/rms-equilibrium/README.md`.
Промежуточные корпуса до final power-normalized heat gate сохранены отдельно,
с собственными runtime identities;final receipt использует только final series.
GitHub/CI/publication не использовались.

Далее: double/multicage и mixed-generator electrothermal stationary profiles,
separate cage thermal nodes,temperature-trip/selectivity,широкие topology/
uncertainty matrices и внешняя идентификация RC/material parameters.
DES-04 остаётся in_progress;нет OEM/global/hybrid qualification.

## R06.38 / DES-04 — opt-in R(T) в RMS/DAE и аналитическая сетевая связь

Добавлен `InductionMotor.winding_thermal`: физические Ts/Tr и R(T) для
single-cage RMS, отдельно от прежней I² thermal replica. Начальные температуры/
RC/alpha требуют provenance и проверки validity. Default fixed-R/canonical JSON
сохранены. Профиль ограничен motor-only/all-single-cage/direct_solve;
генераторы и mixed double-cage явно отвергаются.

- Динамическая Norton stamp `Y(x)` участвует в каждом voltage solve и RHS.
  В augmented DAE/Schur учтён `C=b_x−Y_x·V`, включая inconsistent algebraic V;
  аналитические rotor-R/heat derivatives, локальные sparse blocks/GMRES и
  temperature rollback проверены. Новые heat/temperature/resistance channels.
- Loaded initialization согласует электрическую/механическую сеть при
  **fixed initial winding temperatures**. Initial thermal derivatives явно
  опубликованы и могут быть ненулевыми. DAE consistency не подменяет thermal
  equilibrium; spectral certificate относится к frozen-T connected subset.
- **32 authored physical scenarios /128 native runs /64 independent trajectories**:
  cold/loaded,Ts/Tr20/20 и90/70,primary/CB-failure/CT-loss/no-UV,
  feedback/frozen-initial-R controls,Radau/DAE,2 output meshes. Самостоятельные
  SI winding/KCL/thermal/clock/actuator DOP853 references с refinement.
  Offline verifier повторил все64 reference integrations и все256 comparisons.
- Начальные физические состояния/сопротивления control совпадают; дальнейший
  feedback меняет current magnitude на max≈0.000765–0.001321pu в этих случаях.
  Max event difference≈6.38e-14s,temperature≈7.25e-10°C,heat-power≈2.72e-5W.
  Это agreement двух реализаций, не точность относительно OEM.
- Validity exit не клиппируется: failed solve с точной model-validity причиной,
  без completed trajectory. Это не температурная защита/локализованный trip.
- **769 unique targeted checks с итогом pass**:747 non-RMS regression cases
  и22 final RMS tests. Broad XML сохранил2 неверных status-envelope assertions;
  контракт теста исправлен под existing numerical-failure envelope с проверкой
  exact model-validity cause, затем весь22-case класс прошёл повторно.
  Production runtime между этими двумя final runs не менялся. Raw XML не
  редактируется/не скрывается; delivery receipt сверяет node IDs и supersession.
  4 optional ANDES checks явно deselected. Не полный repository suite.
- **8 installed new-wheel replays**:4 новых +2 прежних phase +2 прежних RMS.
  Оси/события/common numeric arrays совпали побитово; проверены также1e-12
  abs+relative caps. Host/dependencies общие; Python-I/import guard не OS sandbox.
  CLI с сохранённым owner study выполнен. Старые корпуса не переквалифицированы
  целиком: прежние wheel/fingerprints сохранены.

Final runtime fingerprint:
`5894df506412766e90d5a13cf7c5381f7f6ec8f2959f6be1ae8c15d0184b09fa`.
Final wheel SHA256:
`b74b35cf6b09dfebfbe0a715229d523c4ebc5c0fd5e47c04146f107c7baab1eb`.
Source boundary относительно R06.37:10 changed runtime .py +1 new
`rms_winding.py`,0 missing; no phase-equation changes.

Evidence: `reports/rms-winding-final-qualified-2026-11-15`,
`reports/rms-winding-final-accepted-2026-11-15`,
`reports/rms-winding-final-installed-replay-2026-11-15`,
`reports/rms-winding-delivery-2026-11-15/delivery.json`.
Команды/9 runnable examples: `cases/rms-winding/README.md`.
Промежуточные корпуса относятся к промежуточным fingerprints; final receipt
использует только final inputs. GitHub/CI/publication не использовались.

Далее: full stationary electrothermal equilibrium, double/multicage thermal
nodes и mixed generators; temperature-trip/selectivity, широкие topology/
uncertainty matrices и внешняя идентификация thermal parameters. Не утверждаем
full EMT energy из RMS, OEM qualification или универсальное превосходство.
DES-04 остаётся in_progress.

## R06.37 / DES-04 — горячие обмотки и electrothermal fault feedback

Фазная full-winding модель уже содержала R(T). Добавлены раздельные начальные
температуры статора/ротора с обязательным provenance и проверкой validity,
сохранением прежней ambient initialization/canonical JSON при отсутствии полей.
Опубликованы Rs/Rr каждой клетки и net/ambient/cross heat channels, включая
zero-core-loss port. Изменены ровно3 runtime files:phase_network.py,
phase_core_motor.py,vf_drive.py; RMS/DAE motor equations на этом этапе не менялись.

- **24 authored physical scenarios /96 native runs /48 independent trajectories**:
  single/three-cage saturated core-loss motor, cold start/hot rotating state,
  LL/3ph/repeated LL faults, R(T)/reference20-alpha0 control. Radau/DOP853,
  200/100µs max step; independent all-flux SI RLC/KCL DOP853 с50/25µs refinement.
- Для hot alpha0 с R20 статический температурный эффект смешивается с дальнейшей
  обратной связью. Поэтому отдельно **6 frozen-initial-R controls /24 native /
  12 independent trajectories**:начальные физические состояния/Rs/Rr совпадают,
  сохраняются thermal ODE, затем R фиксирован. Изолированная dynamic feedback
  меняет ток≈0.328–0.921A в выбранных случаях, не универсально для оборудования.
- Итого **120 native runs /60 independent trajectories /240 comparisons**.
  Проверены температуры/R(T)/heat-flow identities, электрические/механические
  каналы, event sides, energy/KCL. Offline gate повторил все60 independent
  интегрирований, перепроверил owner bindings и численные критерии.
  Main-corpus max temperature difference≈4.29e-10°C; relative energy residual
  native≈4.31e-11, reference≈3.28e-14; max KCL≈4.45e-14A. Это agreement одной
  модели, не фактическая accuracy относительно OEM или непрерывный peak bound.
- **630 targeted tests passed**:613 regression +15 phase/protection adapters
  +2 frozen-control physical identity tests.0 errors/failures/skips;
  4 optional ANDES cases явно deselected. Не весь repository suite.
- **8 installed new-wheel replays**:4 новых,2 legacy phase,2 legacy RMS.
  Оси/события совпадают, common channels проверены с roundoff1e-12 abs+relative.
  Два phase baseline заново рассчитаны прежним pinned wheel. Source-boundary
  proof подтверждает ровно3 изменённых .py,0 добавленных/удалённых runtime .py.
  CLI с сохранённым owner JSON тоже выполнен. Host/dependencies общие;
  import guard/Python-I не OS sandbox/another-host qualification.

Новый runtime fingerprint:
`848e36083c4da3dabaae55dab6484b01817cc3771a6b801ac424d02d6d50af34`.
Новый wheel SHA256:
`62500d8cd7fd5ae7efe80ad5515b99c950b616ac10f0000a79c3a2e59c7ef434`.
Все предыдущие sealed результаты сохраняют прежнюю runtime identity и wheel;
выборочные legacy replays не переквалифицируют старые большие корпуса целиком.

Evidence: `reports/electrothermal-fault-qualified-2026-11-15`,
`reports/electrothermal-frozen-fault-qualified-2026-11-15`,
`reports/electrothermal-fault-accepted-2026-11-15`,
`reports/electrothermal-fault-delivery-2026-11-15/delivery.json`.
Команды и runnable cases: `cases/electrothermal-fault/README.md`.
Диагностический первый matrix с numpy-bool serialization failure сохранён;
он не считается принятой квалификацией. GitHub/CI/publication не использовались.

Открыто: **RMS motor R(T)** с state-dependent stamps/Jacobians, согласованная
loaded electrothermal initialization, широкие топологии/отдельные cage thermal
nodes, параметрическая идентификация RC по внешним данным, thermal trip/
selectivity при этой фазной физике. Authoring60ms/transient hot state и2Ω faults
не подменяют реальный паспорт/steady operating point/bolted КЗ. DES-04 открыт.

## R06.36 / DES-04 — независимый гибридный эталон защит

Закрыта независимая дифференциальная сверка **ограниченного loaded radial
single-cage OC/UV/thermal профиля**. Эталон использует собственный T-root,
SI winding/KCL и DOP853; сам интегрирует OC progress, ищет pickup/dropout/trip,
исполняет delayed breaker opening/failure/backup, CT loss/restoration,
local UV timer и thermal latch. Native RHS/init/relay/contactor helpers
не вызываются; это проверено запретом их вызовов в тесте.

- **32 endpoint-сценария ×2 точности =64 independent trajectories**,
  сопоставленных с64 ранее сохранёнными Radau/DAE traces.4 условия сети,
  2 тепловые политики,primary/CB failure/CT loss,matched primary no-UV.
  Собственное refinement сравнивает события и каналы без native решения.
- **14 extension trajectories /28 новых native runs**:definite/SI/VI/EI
  ×primary/CB failure/CT loss; CT restoration/reset/repeated fault;thermal trip.
  Это отдельные выбранные случаи, не полная матрица допусков всех curves.
- Итого **78 independent trajectories /92 distinct native comparisons**
  (156 с повторными сравнениями refinement). Проверяются события, их порядок
  и outcomes, clocks/validity/actuators и motor/fault electrical channels.
  Совпавшие pre/post samples привязываются к соответствующим independently
  found roots; regular samples остаются на абсолютной шкале. Через скачки
  не интерполируем; event timing gate проверяется отдельно с cap200ns.
- Max observed event difference≈5.89e-13s, fault-current difference≈2.38e-7A.
  Это agreement двух реализаций одной модели, не фактическая OEM точность.
- **502 tests passed**,0 errors/failures/skips;4 optional ANDES-dependent
  cases явно deselected. Не весь repository suite. Сохранён diagnostic XML
  с ошибкой имени класса в isolation test и промежуточный extension probe;
  финальный цикл учитывает истёкший failure check без защитного действия.
- Runtime fingerprint и wheel R06.35 **не изменены**. Нового installed replay
  или другого host этот этап не заявляет. Нет GitHub/CI/publication.

Доказательства:
`reports/loaded-protection-winding-final-2026-11-15`,
`reports/loaded-protection-winding-extensions-final-2026-11-15`,
`reports/loaded-protection-winding-regression-tests-2026-11-15.xml`,
`reports/loaded-protection-winding-delivery-2026-11-15/delivery.json`.
Локальный receipt связывает manifests/producers/runtime, сохраняет исходники,
XML и неизменившийся wheel. Hashes — integrity, не аутентичность.
Команды и границы: `cases/motor-only-loaded/README.md`, раздел R06.36.

Далее: winding R(T)/electrothermal fault feedback и независимые эталоны для
более широких topologies/машин/ATS/reclose. DES-04 остаётся in_progress:
continuous uncertainty, несимметрия/DC/peak/EMT и OEM qualification не закрыты.

## R06.35 / DES-04 — согласованная нагруженная motor-only инициализация

Численное ядро `coupled_rms` теперь принимает `machines: []` с явным
`operating_point`. Совместно решаются скорости/ЭДС машин и сеть без фиктивного
генератора; проверяются исходный RHS, KCL, static/dynamic voltage consistency
и local connected-machine spectrum. Новый mode `motor_only_loaded_equilibrium`.
Cold initialization по умолчанию сохранена. Изменены только два runtime
модуля: `coupled_initialization.py`, `coupled_rms.py`.

- Электрическое/механическое равновесие отделено от thermal policy:
  `equilibrium` задаётIpu², `passport` сохраняет initial level и явно показывает
  ненулевую тепловую производную. Initial thermal limit/нет подключённых
  машин/неподходящий speed branch получают диагностику до интегрирования.
- `initial_active_automation_relays` и network-automation stationarity публикуются
  отдельно. Certificate относится к connected E/speed/cage states; clocks,
  контактные guards, future events иglobal/hybrid stability им не подменяются.
- **512 native launches**:504 интегрированные траектории +8 init rejections.
  **456 positive /56 negative**:288 single-cage normal/programmed-fault,
  24 symmetric2/4-machine single/double-cage hold;144 loaded protected
  primary/CB-failure/CT-loss;48 matched no-UV failures.4 temperature/voltage
  endpoints,2 heat policies, оба решателя/3 output meshes,252 solver pairs.
- **48 независимых loaded winding trajectories /96 native comparisons**,
  включая16 programmed-fault trajectories на motor/source bus. T-circuit root
  через bracketing/Brent,4 SI winding/KCL equations, DOP853; не берёт native
  EMF/init/derivatives. Initial eigenvalues тоже сверены; max error≈3.02e-8/s.
  Max EMF≈1.02e-9pu иfault-current error≈5.35e-8A — agreement двух реализаций,
  не фактическая точность относительно OEM. Active-protection ODE не проверена
  этим эталоном: там guards отвергают активные OC/UV/thermal действия.
- Защищённые loaded cases сохраняют150ms/0.1A/20ms criteria: worst full
  extinction≈127.953ms; motor-fed current after grid isolation до145.02A.
  Conditional SI algebra/clocks/actuators повторно проверены, I²t по192
  protected/control traces и64 fine-pairs. Это nominal relay settings;
  R06.34 cold-runup corner matrix на loaded policy автоматически не переносится.
- **466 целевых тестов** без ошибок/пропусков;4 ANDES-dependent cases исключены
  как unavailable, не записаны вpassed. Финальный XML:
  `reports/motor-only-loaded-final-tests-2026-11-15.xml`; первый diagnostic XML
  с отсутствующим extra и исправленной ошибкой fixture сохранён отдельно.
- **7 new-wheel replays** вне checkout:5 positive, no-UV negative иinit
  rejection. Шесть имеющих траектории случаев воспроизвелись побитово;
  init rejection воспроизвёл status/reason. **6 old-wheel cold/gen bridge
  traces** имеют exact axes/events иchannel agreement приabs/rtol1e-12,
  observed max absolute delta≈1.8e-15. Первоначальный bitwise-probe сохранён.
  Same host/shared dependencies/import guard, не независимый host/OS sandbox.

Профиль и runnable JSON: [cases/motor-only-loaded](../cases/motor-only-loaded/README.md).
Общий gate: `reports/motor-only-loaded-final-accepted-2026-11-15/summary.json`.
Поставка: `reports/motor-only-loaded-delivery-2026-11-15/delivery.json` с новым
wheel, старым pinned wheel дляbridge,48 independent reference packets,
7 installed packets и6 bridge outputs; основной512-run corpus привязан к
sibling indexes. **Runtime fingerprint новый**; R06.34 доказательства остаются
на старом wheel, wholesale requalification под новый fingerprint не заявляется.

DES-04 **in_progress**. Loaded initialization закрыта в указанном1/2/4-machine
профиле (включая double-cage, отдельные тесты nonlinear load, inactive members
иzero-load boundary). Остались большие неоднородные группы/топологии,
независимая динамика активных OC/UV/thermal protections, winding R(T)/feedback,
несимметрия/peak/DC/EMT иOEM подтверждение. Normative approval, training reward,
полный repository suite иGitHub/CI не заявляются; git mutations не выполнялись.

## R06.34 / DES-04 — полная конечная voltage/temperature матрица и независимая нормальная динамика

Ревизия **primary TMS.009 / backup TMS.0155** прошла полное заявленное
произведение20/90°C ×0.95/1.05U ×nominal+16 relay corners ×6 normal/fault modes
×Radau/DAE ×3 output meshes: **2448** новых native studies. Все прежние
критерии пуска/повторных провалов/выбега, защит и sampled-погасания КЗ сохранены.
Сопротивление кабеля выводится из owner-bound паспортного каталога; Rт и
параметры обмоток мотора фиксированы. Это не electrothermal feedback.

- Центр+8 воспроизводимых6D Latin-strata точек (четыре relay coordinates,
  conductor T иsource U), все6 режимов/оба решателя/3 сетки: ещё **324** native.
- Всего **2772** новых положительных native runs, **1386** solver pairs,
  **1386** аварийных траекторий для по-портового I²t, **462** fine-pair проверок.
  Все результаты повторно пересчитаны owner-bound verifier без скрытой
  интеграции/ремонта отсутствующих исследований.
- Худшее grid isolation/full fault extinction145.857ms при150ms deadline;
  минимальный measured margin4.143ms. После grid isolation motor-fed ток
  достигает164.25A — критерии источника и полного погасания не объединяются.
  Max fine-pair relative I²t change≈7.13e-5 при cap.002.
- **Новый независимый дифференциальный normal-mode эталон**: три примитивных
  SI winding/KCL equations, rotor flux/speed/thermal ODE, DOP853, cold state.
  **15** самостоятельных траекторий (3 normal modes ×4 endpoint conditions
  ицентр), **30** сравнений с native finest Radau/DAE. Не использует native
  EMF traces для продвижения, production derivatives/stamps/constants/init.
  Max errors: speed1.63e-9pu, EMF5.87e-8pu, current2.26e-7pu, voltage7.18e-9pu,
  thermal replica4.58e-10. Это эквивалентность формулировок, не OEM точность.
- **311 целевых тестов**, включая34 новых, без ошибок/пропусков:
  `reports/running-cartesian-final-tests-2026-11-15.xml`.
- **6 native replays** unchanged pinned wheel вне checkout:5 positives и
  1 сохранённый R06.33 negative, physical arrays/events совпали точно.
  Same host/shared dependencies/Python-I/import guard, не независимый host.

Общий gate: `reports/running-cartesian-accepted-2026-11-15/summary.json`.
Поставка: `reports/running-cartesian-delivery-2026-11-15/delivery.json`;
включены6 installed-native packets ивсе15 independent winding trajectories,
2772 native traces привязаны хешами sibling stores. **Runtime не менялся**.
Профиль/команды: [cases/running-fault-cartesian](../cases/running-fault-cartesian/README.md).
Добавлены soft wall budget, число studies до запуска, reuse sealed work,
сохранение incomplete native traces как отрицательных результатов.

DES-04 **in_progress**: конечное Cartesian coverage закрыто для этого профиля,
но endpoints+8 probes не доказывают continuous uncertainty extrema. Независимая
динамика **аварий/защит** ещё открыта: там conditional SI oracle, replay clocks
иshared-physics Radau/DAE. Следующая физическая очередь — согласованная loaded
motor-only initialization (пока cold guard), независимые fault/protection ODE,
другие машины/топологии, winding-temperature feedback иearth/peak/DC.
Normative/OEM approval, полный repository suite, независимый host иtraining
reward не заявляются. GitHub/CI иgit mutations не выполнялись.

## R06.33 / DES-04 — все16 допусковых углов и выявленные границы селективности

Квалифицирована отдельная VI-ревизия **primary TMS.009 / backup TMS.0155**.
**612** новых расчётов: nominal+16 pickup/TMS corners ×6 normal/fault cases
×Radau/DAE ×3 output meshes. Полностью повторно проверены owner recipes,
критерии, clocks, условная SI algebra,306 solver pairs и I²t по306 аварийным
траекториям (102 fine-pair проверки). Нормальные критерии не ослаблялись.

- Ещё **48** положительных boundary samples: два выбранных угла1100/0011,
  primary/measurement-loss,20/90°C,0.95/1.05U,оба решателя/3 output meshes.
  Самое позднее отключение145.857ms при deadline150ms. Это не все16 углов
  при каждой температуре/напряжении и не continuous robustness proof.
- Прежняя .01/.015 теряет селективность при1.05U; промежуточная .01/.0155
  проходит собственную горячую612-run matrix, но не cold20°C/1.05U.
  **6** парных отрицательных контролей последней сохранены; новая ревизия
  отличается от неё только primary TMS и label, от R06.32 — двумя TMS и label.
- Исследовательская программа: **1332 native runs**, включая промежуточные
  варианты и отказы; это не1332 положительных исследования финальной модели.
- Исправлены два дефекта отдельного SI oracle: ЭДС источника теперь берётся
  в физических вольтах, а точно изолированная невозбуждённая нулевая строка
  не требует фиктивного шунта. Все108 boundary traces переаудированы без
  перезаписи/новой интеграции; физические6 отказов промежуточного варианта
  остались отказами. Старые неуспешные oracle отчёты и код сохранены.
- **277 целевых тестов**, включая13 новых, без ошибок/пропусков;
  `reports/running-corner-final-tests-2026-11-15.xml`.
- **6 native replays** прежнего pinned wheel вне checkout:5 positive/1
  negative, массивы/события совпали точно. Тот же host/shared dependencies,
  Python-I/import guard; не независимый host/OS sandbox.

Профиль/команды: [cases/running-fault-corners](../cases/running-fault-corners/README.md).
Финальный gate: `reports/running-corner-accepted-2026-11-15/summary.json`.
Поставка: `reports/running-corner-delivery-2026-11-15/delivery.json`.
**Runtime не менялся**, reused wheel R06.32 и package fingerprint сохранены;
новые benchmark/case/status файлы не переквалифицируют другие physics profiles.

DES-04 остаётся **in_progress**. Конечный corner-профиль .009/.0155 закрыт;
остаются полное voltage/temperature Cartesian coverage, interior/adversarial
uncertainty, другие машины/топологии, earth/peak/DC, тепловая обратная связь
и независимый динамический эталон. Normative/OEM approval и training reward
не выдаются. GitHub/CI и git add/commit/push не выполнялись.

## R06.32 / DES-04 — работающие двигатели, защиты и погасание КЗ

Добавлен `running_fault_protection`: owner-bounded критерий реального sampled
погасания полного тока КЗ, отдельно от отключения линии/источника. Проверяются
работающий подключённый двигатель перед КЗ, deadline150мс, RMS≤0.1A с
наблюдением≥20мс до внешнего удаления, состояния/токи аппаратов, pre/post
открытия, задержки запросов, ожидаемые реле и независимый replay часов.

- **60 новых native runs**:36 аварийных (18 UV-positive/18 no-UV negative),
  6 отрицательных исходной backup-TMS и18 нормальных с сохранёнными
  критериями пуска/повторных провалов/выбега и повторного пуска.
- **27 Radau/DAE пар**, SI algebra oracle на всех60 траекториях, обусловленный
  сохранёнными ЭДС; отдельный динамический эталон этим не заменяется.
- Без UV двигатель продолжает питать КЗ после отключения линии:≈109.5A
  сразу после grid isolation. UV-контактор0.3pu/90ms (без auto-restart)
  даёт полное погасание в90ms при primary grid opening65.585ms.
- Исходная VI backup TMS.07 не выполняет новый150ms deadline при потере CT;
  шесть явных контролей сохраняют progress≈.7303135<1. Отдельная ревизия
  **TMS.015**, и только она+label, проходит nominal: failure115.585ms,
  independent backup117.991ms. Критерии UV/no-UV пар строго одинаковые.
- I²t по36 аварийным траекториям:12 fine-pairs проходят0.2%, max≈7.12e-5.
- **264 целевых теста**, включая58 новых, без ошибок/пропусков;
  `reports/running-fault-complete-tests-2026-11-13.xml`.

Поставка `reports/running-fault-delivery-2026-11-13/delivery.json` включает
wheel, исходники протокола,264-test XML и6 заново интегрированных installed
случаев (3 положительных/3 отрицательных). Вне checkout, Python -I, запрет
reference imports; физические arrays/events совпали точно. Host и библиотеки
общие, OS sandbox и независимая машина не заявляются. Все6 пакетов сохранены.

Профиль и команды: [cases/running-fault-protection](../cases/running-fault-protection/README.md).
Общий gate: `reports/running-fault-acceptance-final-2026-11-13/summary.json`;
owner-физика/критерии и часы пересчитаны, signed/OEM acceptance не заявляется.
DES-04 **in_progress**: новая TMS имеет nominal-квалификацию,16 corners
предыдущих уставок на неё не переносятся; остаются uncertainty, другие машины,
топологии, earth/peak/DC, тепловая обратная связь и независимая динамика.
Новый runtime fingerprint не переписывает исторические доказательства.

## R06.31 / DES-04 — подпитка КЗ двигателями и физические токи портов

Добавлен `motor_fault_contribution`: фазовое разложение замороженных ЭДС
одно-клеточных асинхронных машин и источника, произвольная точка КЗ на
радиальном кабеле, постоянный Z нагрузки, точный болт/конечный импеданс,
несколько машин и отключённый источник. Подавление ЭДС сохраняет импеданс;
отключение машины удаляет ветвь. Проверяются KCL, невязка и суперпозиция.
Источник, кабельные порты, двигатели и КЗ имеют отдельные физические токи.

- **216** независимых скалярных сравнений, max ошибка3.1e-12A.
- **24** динамических запуска:4 сценария ×2 решателя ×3 сетки;3с разгона
  до КЗ, cold/hot/пониженное U, upstream-КЗ и отключение двигателя во время КЗ.
- **168** условных алгебраических срезов и **12** сравнений полных Radau/DAE
  траекторий прошли. Это не независимая динамическая модель.
- По-портовое I²t на24 траекториях:8 fine-grid сравнений проходят0.2%,
  фактический max сдвиг7.084e-5. Полный ток КЗ не подставляется в кабель.
- **206** целевых тестов без ошибок/пропусков, включая43 новых разложения
  и8 интегрирования. XML: `reports/motor-fault-complete-tests-2026-11-13.xml`.
- Первый диагностический прогон сохранил ошибку CT-reference: при upstream-КЗ
  линейный CT ошибочно сравнивался с источником. После исправления привязки
  добавлен regression и заново выполнены все24 запуска. Положительный пакет:
  `reports/motor-fault-contribution-qualified-2026-11-13/`.

Поставка: `reports/motor-fault-delivery-2026-11-13/delivery.json`, закреплённый
wheel и6 точных повторов установленного пакета вне исходников с запретом
импорта benchmark/tests/reference packages. Тот же host и общие зависимости;
это не независимая машина. Пакеты исходных траекторий привязаны хешами.

Документация и остальные пакеты:
[cases/motor-fault-contribution](../cases/motor-fault-contribution/README.md).
DES-04 остаётся **in_progress**: это снимки single-cage RMS и измеренное I²t,
не общая граница динамической подпитки и не квалификация селективности с ней.
Остаются связанные защиты с этими сценариями, более широкие машины/топологии,
нагруженная инициализация, несимметрия, DC/peak и температурная обратная связь.
Новый runtime имеет новый fingerprint; прежние R06.29/R06.30 доказательства
относятся к своим закреплённым версиям, не автоматически к текущему пакету.

## R06.30 / DES-04 — холодный/горячий диапазон токов КЗ

Добавлен `power_grid_env.fault_temperature_envelope`: независимый от запуска
динамики инженерный экран выбранной пассивной радиальной сети. Непрерывные
границы токов по температуре меди, напряжению, месту и сопротивлению КЗ
следуют из монотонности положительных последовательных импедансов.
Для времён защит, селективности и I²t используются достаточные интервальные
оценки; отрицательная оценка селективности может быть неопределённой.

- Источники: существующий паспортный каталог, медь20..90°C, c=.95..1.05,
  вся длина кабеля, Rкз=0 и отдельный профиль0..0.01Ω. Rт фиксировано.
- Два sealed-пакета `reports/fault-temperature-{envelope,resistive}-qualified-2026-11-13`:
  **4802 узловые точки и86436 проверок времён с допусками**, все пройдены.
- Резистивный профиль: I=3405.965..6314.769A, primary clear≤86.183ms,
  достаточный запас grading≥101.182ms; оба аппарата и заданные тепловые
  ограничения проходят. Это постоянный RMS ток, не интеграл динамического КЗ.
- **155 целевых тестов**, включая20 новых, без ошибок/пропусков;
  `reports/fault-temperature-final-tests-2026-11-13.xml`.
- Отрицательные тесты сохраняют отсутствие чувствительности при Rкз=1Ω;
  JSON null не превращается в успешное отключение. Артефакт проверяется
  пересчётом с обязательными owner-inputs, включая защиту от resealed-подмены.

Пример и команды: [cases/fault-temperature-envelope](../cases/fault-temperature-envelope/README.md).
DES-04 остаётся **in_progress**: холодный/горячий экран закрыт только в этом
пассивном профиле; motor contribution, динамическая R(T), peak/earth faults,
общие топологии и непрерывная динамическая селективность остаются открытыми.
Runtime расширен новым модулем, поэтому package fingerprint изменился.
Исторические1836 динамических запусков относятся к закреплённому runtime R06.29
и его wheel, а не к новой общей квалификации текущего пакета. GitHub/CI и git
операции не выполнялись.

## R06.29 / DES-04 — все три inverse-семейства, найденный EI отказ и отдельная ревизия

DES-04 остаётся **in_progress**; полный finite-corner профиль расширен на
Very Inverse и Extremely Inverse. Пройдены SI с backup TMS=.06, VI с .07
и **отдельный согласованный EI-вариант с .06**. Исходный EI с .08 не объявляется
прошедшим: у него найден и сохранён воспроизводимый отрицательный контроль.
Нативный runtime не менялся, package fingerprint остаётся прежним.

Содержательный результат:

- Исходная EI TMS=.08 проходит nominal, но corner0011 (primary pickup/time−,
  backup pickup/time+) не выдерживает потерю измерения основной защиты.
  Оба решателя и три сетки дают complete/numerically_qualified=true,
  passes_declared_profile=false. Backup progress достигает≈.8827298335<1,
  и реле не trip до prescribed fault removal в.8с. Clock audits тоже проходят:
  отказ не подменяется ошибкой численного интегрирования. Проверено36 реальных
  grid runs исходного контроля; вся16-corner матрица исходной EI не исполнялась.
- Создан отдельный вариант backup TMS=.06. Меняются только project setting
  и envelope label. Оборудование, pickup/tolerances, primary setting, apply/clear,
  horizon, opening delays и все критерии сохраняются. Revision helper строго
  сравнивает canonical data с исключением ровно этих двух полей и пишет hashes.
  Это authored candidate, не автоматическая оптимизация или OEM-рекомендация.
- **1224 новых положительных grid runs**:VI и revised-EI ×2решателя ×17вариантов
  (16углов+nominal) ×6сценариев ×3сетки. Все проходят исходные4с пуск/провалы/
  самозапуск и1с аварийные случаи. Нормальные final-speed/voltage/current/thermal
  критерии не ослаблены, время снятия КЗ не продлено ради успеха.
- SI612 из предыдущего этапа повторно проверены/связаны по hashes с тем же
  текущим runtime; не переинтегрированы и не считаются новыми расчётами.
  Итого положительная full-family база **1836**, плюс36 исходных отрицательных
  EI controls отдельно. Требования трёх семейств имеют общий normalized hash;
  исключаются только явные relay-design choices, source descriptions и labels.

Проверки:

- Всего918 пар Radau/DAE,1836 conditional SI-unit algebra/measurement checks,
  918 measured fault-duty checks в положительной базе. Новые VI/EI references
  рассчитаны заново; прежний SI reference закреплён сохранёнными producer bytes
  из предыдущей delivery, а не приписан изменённому benchmark script.
  SI oracle — физические единицы и saved EMF inputs, не независимая ODE-траектория.
- Общий acceptance collector сверяет оба17-study store каждого семейства,
  owner mapping, curves, source/index hashes, все reference children, duty,
  общие требования, исходный rejection и допустимую revision. Внешние исходные
  данные/производитель этим не аутентифицируются; normative_approval=false.
- **20 новых tests passed**:immutability изменения TMS, запрещённые изменения
  критериев/оборудования/событий/допусков, все corner recipes, общность requirements
  и полнота family layout. Прежние441 passing tests сохранены с тем же numerical
  runtime; они **не запускались заново** в этом этапе.461 — число разных
  сохранённых passing checks, не размер нового pytest run.3 ANDES tests всё ещё
  unavailable/deselected, полный suite проекта не заявляется пройденным.
- Закреплённый прежний wheel переиспользован offline, без rebuild/новых зависимостей:
  code fingerprint и wheel SHA совпадают. В новой venv вне checkout перенесены
  VI corner1100, revised-EI corner0011 и original-EI corner0011. Все проверены
  с owner anchors. Заново выполнены9 grid-0 DAE runs:6 VI scenarios,2 revised-EI
  fault cases и1 original-EI failing case. Каналы/времена/события совпали точно;
  исходный отказ воспроизведён как отказ, а не превращён в успешный результат.
  Parent/all9 workers блокировали benchmarks/tests/motulator; тот же host/shared
  dependencies, не OS sandbox и не независимая машина.

Материалы: cases/full-inverse-families/README.md;
сводная квалификация: reports/three-family-envelope-qualified-2026-11-13/;
поставка/индекс: reports/three-family-envelope-delivery-2026-11-13/;
VI stores: reports/full-vi-radau-2026-11-13/, reports/full-vi-dae-2026-11-13/;
revised-EI stores: reports/full-ei-radau-2026-11-13/, reports/full-ei-dae-2026-11-13/;
original controls: reports/original-ei-radau-2026-11-13/, reports/original-ei-dae-2026-11-13/;
revision proof: reports/full-ei-revision-2026-11-13/;
новые tests: reports/full-inverse-family-tests-2026-11-13.xml.
Package fingerprint (без изменений):
`9d4f382a9e8ef62c2d8889f1eeb01d08405ea2b0b3a380245ed7bd7e1de1fbc0`.

Открыто: внутренние точки/непрерывная неопределённость, cold/hot и связанный
нагрев, общий machine-fault duty, mixed-curve stages, CT/instantaneous/reset/
reclose, земляные/ударные токи, broader DES-03 topologies и loaded motor-only
initialization. Доказан только один authored75кВА/400В двухузловой профиль,
а не произвольные модели/масштабы или серийные аппараты. Все rewards=null;
полный DES-04/нормативное одобрение не заявляются. GitHub/CI/git mutations
не выполнялись.

## R06.28 / DES-04 — полный пуск/восстановление на всех SI corners, оба решателя

DES-04 остаётся **in_progress**; ограниченный полный SI corner-профиль закрыт.
Исходные три нормальных4с сценария (пуск, повторные провалы, выбег/самозапуск)
и три аварийных1с сценария выполнены на каждом из16 крайних сочетаний pickup/TMS
основной/резервной ступени плюс nominal, на Radau и network_dae, по3сетки.
Исходные требования нормальных случаев, включая final speed>=.9, не ослаблены.
Это следующий уровень после короткого0.2/0.6с brief предыдущего этапа.

Реализация:

- protection_envelope_store.py и CLI protection-envelope-store /
  verify-protection-envelope-store: каждый из17 studies имеет отдельный bounded
  manifest, индекс — свой manifest с именами/recipe/result hashes/объёмами.
  Global artifact caps не увеличены. Верхний store не является обычным sealed
  study artifact; требуется специализированный verifier всего индекса и данных.
  Extra/missing/reordered studies, подмена metadata, ссылки и перезапись
  существующего output отвергаются. Incomplete не даёт complete positive store.
- Streamed paired reference проверяет готовые запечатанные пары по мере
  появления и публикует положительный итог только после проверки обоих индексов.
  В конце повторно проверены все child manifests и index identities.
- Дополнительный measured-duty benchmark использует фактические аварийные
  RMS-трассы: время до действительного открытия, I²t кабеля, pre-opening RMS
  аппарата, sampled transformer RMS и заданные токовые/временные пределы.
  Для основной ступени проверен owner clearing budget; резерв — отдельно по
  фактической нагрузке и заданной стойкости. Фиксированные R/X и начальная
  температура остаются допущениями, electrothermal feedback не добавлялся.

Результаты текущего producer:

- **612 фактических расчётных grid runs**:17вариантов ×6сценариев ×3сетки ×
  2решателя. Все варианты complete, numerically qualified и проходят профиль.
  Все4с нормальные требования проверены на каждом углу, а не только nominal.
- **306 пар Radau/DAE**, **612 условно независимых SI-проверок** сетевой
  алгебры/измерений и **306 measured fault-duty checks** прошли.
  SI reference использует сохранённые EMF: не независимая differential trajectory.
- Max clock replay errors по сеткам:
 3.8743753e-7,9.7134876e-8,2.4481142e-8 при прежнем допуске2e-4.
  Primary clearing на fine grid: absolute t=.1635045134–.1772387320с,
  то есть63.5045..77.2387мс от КЗ в.1с, внутри owner budget100мс.
  Максимальное fine-pair relative change measured I²t≈5.081e-8 при cap.002.
  Это sampled quantities в заявленном fixed-RMS профиле, не continuous extrema.
- **441 различных selected tests passed**:431 основной выборки плюс10 новых
  аналитических/отрицательных duty tests. Всего29 новых проверок этапа.
  Три ANDES tests по-прежнему явно deselected из-за отсутствующего extra,
  не объявлены пройденными; полный suite проекта не запускался.
  Storage scheduling tests используют явно обозначенный fake backend;
  физическую квалификацию обеспечивают отдельные612 реальных интегрирований.
- Payload raw studies **1,016,956,128 bytes** в двух отдельных stores.
  Лимиты256MB на один bounded artifact сохранены, материалы читаются по studies.
  Source-boundary proof: против предыдущего wheel добавлен только store module,
  изменён только cli.py; все другие runtime Python files побайтово совпадают,
  включая уравнения машин/сети/реле. Старые traces не переименовывались:
  все612 прогонов выполнены с новым package fingerprint.
- Offline установленный wheel вне checkout проверил **перенесённый306-grid
  Radau store** и два конкретных DAE corners. Заново выполнены **9 grid-0 DAE
  runs**:все6 сценариев corner1100 и3 аварийных corner0011. Каналы/времена/
  события совпали точно, clocks/actions перепроверены. Parent/all9 workers
  блокировали benchmarks/tests/motulator. Тот же host/shared dependencies,
  не OS sandbox и не независимая машина.

Команды/границы: cases/full-protection-envelope/README.md.
Stores: reports/full-si-envelope-radau-2026-11-13/ и
reports/full-si-envelope-dae-2026-11-13/;
paired reference: reports/full-si-envelope-reference-2026-11-13/;
measured duty: reports/full-si-envelope-duty-2026-11-13/;
поставка/индекс: reports/full-protection-envelope-delivery-2026-11-13/;
тесты: reports/full-protection-envelope-final-tests-2026-11-13.xml и
reports/full-protection-duty-tests-2026-11-13.xml;
source intervention: reports/full-envelope-source-boundary-2026-11-13.json.
Package fingerprint:
`9d4f382a9e8ef62c2d8889f1eeb01d08405ea2b0b3a380245ed7bd7e1de1fbc0`.

Открыто: full corner matrices Very/Extremely Inverse, внутренние точки и
непрерывная неопределённость, cold/hot/связанный нагрев и общий machine-fault duty,
CT/instantaneous/reset/reclose модели и более широкие топологии DES-03.
Проверен один authored75кВА/400В мотор и радиальные два узла. Не заявлены
произвольный масштаб, все положения/виды КЗ, OEM-стойкость, normative approval
или полный DES-04. Все rewards=null.16крайних точек не доказывают внутренность
диапазона. Automated resume store пока отсутствует; partial данные сохраняются,
повторное исполнение требует нового output. GitHub/CI/git mutations не выполнялись.

## R06.27 / DES-04 — inverse-time часы и все16 конечных углов допусков

DES-04 остаётся **in_progress**, заявленные inverse/finite-corner профили прошли.
Добавлены relay_clock_audit.py, protection_envelope.py и CLI protection-envelope /
verify-protection-envelope. ClosedLoopPlan поддерживает явный
clock_policy=sampled_integral; прежний definite_analytic остаётся отдельным
режимом. Нативные уравнения машины/сети и relay ODE не менялись в этом этапе.

Inverse trip проверяется восстановлением прогресса по event-sided RMS-токам,
а не расчётом времени по одному начальному току КЗ. Восстановитель использует
свои коэффициенты/выражения, трёхточечную гауссову квадратуру интерполянта тока,
разделение при pickup и точный exponential reset. Шаги нулевой длины отделяют
скачки. Проверяются progress, достижение1 при trip, latch/event coverage,
дальнейшие задержки и реальные состояния аппаратов. Допуск2e-4 не меняется
агентом через план. Это sampled audit, не доказательство непрерывных экстремумов.

Матрицы и доказательства:

- **108 уникальных физических grid runs**:Standard/Very/Extremely Inverse ×
  Radau/network_dae ×6 нормальных/аварийных сценариев ×3 сетки.
  Полные пуск, повторные провалы, самозапуск и три аварийных сценария сохранены.
  SI/Radau18-run пакет уже был рассчитан с тем же текущим producer и owner plan,
  проверен и скопирован без переинтегрирования; команда matrix выполнила90 новых
  runs. Копирование не посчитано ещё18 расчётами.
- **54 пары Radau/DAE** прошли сравнение состояний и pre/post событий.
  Времена сравниваются с1e-7с без интерполяции через скачки; другие fixed caps
  сохранены. SI-алгебра отдельно проверяет напряжения и relay/fault currents.
- **204 физических grid runs**:Standard Inverse, Radau, все16 декартовых
  сочетаний ±5% pickup/±10% TMS основной/резервной ступени плюс nominal;
 4сценария ×3сетки. Конкретные effective settings и знаки сохранены в recipes,
  применённые допуски обнуляются в effective plans. Все17 исследований прошли.
  Negative corner не скрывается, incomplete не проходит, reward=null.
- Это отдельный явно записанный **ранний пусковой/аварийный brief**:
  normal0.2с с minimum_final_speed=0; faults0.6с, apply0.1/clear0.5с.
  Полные разгон и повторные восстановления на всех углах **не квалифицированы**.
  Изначальный длинный brief не редактировался; оба набора имеют свои данные/hashes.
- Независимые SI-уравнения перепроверили **все204 corner traces**.
  Максимальные clock replay errors на трёх сетках:
 6.2571309e-8,1.5642095e-8,3.9165762e-9. Это наблюдаемое уменьшение ошибки,
  не доказательство универсального порядка сходимости. SI oracle использует
  сохранённые EMF-состояния: независимость алгебры/измерений, не всей ODE-траектории.
- **412 selected tests passed**,0 failures/errors/skips;36 новых tests:
  аналитические часы трёх кривых, независимая quadrature, exact reset,
  threshold clipping, event-sided corruption, полные corner recipes,
  cancellation/negative scheduling, настоящий inverse closed loop и подмена
  progress/measurement.3 ANDES tests явно deselected из-за отсутствующего extra,
  как зафиксировано в предыдущем этапе. Полный suite проекта не запускался.
- Установленный offline wheel вне checkout заново выполнил **9 DAE grid-0
  fault runs**:3семейства ×3аварийных сценария. Все физические каналы/события
  совпали точно; часы восстановлены повторно. Перенесённые17 corner plans и
  три18-grid DAE пакета проверены с owner anchors. Parent/all9 workers блокировали
  benchmarks/tests/motulator. Тот же host/shared dependencies, не OS sandbox
  и не независимая машина.

Команды/границы: cases/inverse-protection/README.md.
Family artifacts: reports/inverse-protection-families-2026-11-13/;
corner artifacts: reports/inverse-protection-envelope-2026-11-13/;
corner SI oracle: reports/inverse-protection-envelope-reference-2026-11-13/;
поставка/индекс: reports/inverse-protection-delivery-2026-11-13/;
тесты: reports/inverse-protection-final-tests-2026-11-13.xml.
Большие family/corner пакеты имеют отдельные manifests; delivery содержит
их anchors и не выдаётся за единый контейнер всех raw traces.
Package fingerprint:
`03f53f56d691c02b736af462d83a4f8e03b21ac58c0b5250c6fd90b72b56016a`.

Далее: полный run-up/recovery на углах, другие inverse families/DAE corners,
внутренние точки и непрерывная неопределённость, cold/hot и machine-fault duty,
CT/instantaneous/reset/reclose модели и топологии DES-03. Задержки аппаратов,
reset/dropout, схема и измерения в текущих углах фиксированы.16 крайних точек
не доказывают пригодность всех внутренних параметров. OEM и нормативное
одобрение, пригодность проекта и полный DES-04 не заявляются; reward=null.
GitHub/CI/git mutations не выполнялись.

## R06.26 / DES-04 — замкнутые действия защит в motor-only сети

DES-04 остаётся **in_progress**. Добавлены closed_loop_protection.py и CLI
closed-loop-protection / verify-closed-loop-protection. Компилятор переносит
выбранное оборудование в coupled-модель и подключает непрерывные часы реле,
основной выключатель линии и резервный выключатель источника. Проверяются
реальные изменения сети и остаточный поток двигателя после отключения.

CoupledSettings допускает явно заданный machines=[] при отсутствии генераторов.
Пустой gen_buses имеет dtype=int; motor-only cold-state инициализируется без
фиктивных PV-генераторов. Loaded motor-only initialization явно запрещена.
Генераторные сети по-прежнему требуют своих паспортов. Существующие уравнения
генераторов не изменялись; это не квалификация произвольного масштаба motor-only.

Профиль: single-cage75 кВА/400 В, выбранные кабель Nexans/Trihal-equivalent,
радиальные два узла. Нормальные критерии оборудования сохраняются. Аварийные
критерии заданы в плане отдельно: отключённая установка не обязана разгоняться.
Номинальные definite-уставки; допуски RelaySetting в динамике не варьируются.

Результаты:

- **36 фактических grid runs**:6 случаев ×3 сетки ×Radau/network_dae.
  На обоих решателях все случаи численно квалифицированы и проходят профиль.
  Нормальные: пуск, повторные провалы, выбег/самозапуск без nuisance trips.
  Аварийные: downstream КЗ в0.1с, основное размыкание в0.16с; failed-to-open,
  подтверждение отказа и резервное размыкание в0.21с; реальная потеря измерения
  в0.05с и резервное реле с размыканием источника в0.38с.
- Verifier пересчитывает motor shaft/algebra/I²t/thermal audits, согласие сеток,
  точную компиляцию плана, временную последовательность событий, отсутствие
  нежелательных trip, точный набор open actions, состояния аппаратов и
  электрическую изоляцию. Owner plan передаётся отдельно. Cancelled/incomplete
  или ошибочные действия не могут пройти; reward=null всегда.
- **18 сравнений пар Radau/DAE** (все cases/grids) прошли. Времена и стороны
  событий сравниваются отдельно, без интерполяции через скачки.
  Независимые SI-уравнения подтверждают напряжения и relay/fault currents
  на обеих траекториях. Входами служат сохранённые EMF-состояния двигателя:
  это независимая алгебра/измерения, **не независимая дифференциальная траектория**.
- **376 selected tests passed**,0 failures/errors/skips,29 новых closed-loop tests.
  Первичная расширенная регрессия:163 passed,3 failed из-за отсутствующего
  ANDES extra. Эти3 внешние проверки явно deselected в финальной выборке,
  исходный отрицательный JUnit сохранён; внешняя ANDES-регрессия не заявляется
  пройденной. Полный suite проекта не запускался.
- Offline установленный wheel вне checkout заново выполнил **3 grid-0 DAE
  аварийных случая**: каналы, времена и события совпали точно. Перенесённый
  полный18-grid DAE artifact проверен с owner plan. Parent и все3 workers
  блокировали benchmarks/tests/motulator; тот же host/shared dependencies,
  не независимая машина и не OS sandbox.

Материалы: cases/closed-loop-protection/README.md;
поставка: reports/closed-loop-protection-delivery-2026-11-13/;
первичные: reports/closed-loop-protection-radau-qualified-2026-11-13/ и
reports/closed-loop-protection-dae-qualified-2026-11-13/;
эталон: reports/closed-loop-protection-reference-qualified-2026-11-13/;
тесты: reports/closed-loop-protection-final-tests-2026-11-13.xml.
Package fingerprint:
`b4df6df6d0053bcfb9ecd0844cb2ea51d0e7eee824796d351a8d6741b1c5ca61`.
Предварительные first-артефакты сохранены со своим producer. После замены
начальной недоступности измерения на действительное событие потери измерения
обе18-grid матрицы рассчитаны заново, старые результаты не переименовывались.

Открыто: замкнутые inverse/instantaneous стадии и допуски; CT/reset-модели,
холодные/горячие границы, общий machine fault duty, земляные/ударные токи,
более широкие топологии DES-03 и loaded motor-only initialization.
В этих уравнениях есть остаточная ЭДС двигателя при КЗ, но общая квалификация
токов КЗ/аппаратной стойкости для всех машин этим не доказана.
Нормативное одобрение и полный DES-04 не заявляются.
GitHub/CI/git mutations не выполнялись.

## R06.25 / DES-04 groundwork — проверка уставок пуска и дискретной координации

DES-04 переведён в **in_progress** для предварительного screen; закрытие
DES-02/03/04 не заявляется. Добавлены protection_design.py, CLI protection-screen /
verify-protection-screen и воспроизводимый benchmark. Источник — текущий
owner-anchored catalog-study. Его manifest записан в доверенном плане;
проверяется весь вложенный каталог и физические аудиты. В переносимый пакет
копируется исходное исследование. Запись внутрь исходного артефакта запрещена.

Ток фидера восстанавливается из двух фазоров и фактического Zline, включая
вспомогательные нагрузки; проверено согласие с source P/Q и напряжением.
Для трёх сеток каждого сценария вычисляются sampled peak и его изменение.
Строгое условие no-pickup использует нижний допуск pickup и заданный запас.
Это консервативный критерий: краткий pickup, который реальное реле могло бы
переждать, здесь может дать отказ. Непрерывный максимум между отсчётами не доказан.

КЗ проверяются после основной защиты в пяти положениях кабеля при трёх
множителях напряжения (15 точек). Используется одно рабочее температурное
состояние, не cold/hot envelope. Для definite/трёх IEC inverse-кривых учитываются
крайние pickup/time tolerances, максимальное открытие выключателя, временной
запас до раннего trip резервной защиты, owner clearing budget, interrupting
rating, кабельное I²t и ток/длительность стойкости трансформатора при основной
и резервной защите. Необнаруживаемое КЗ даёт null-время и отказ, а не Infinity.
Исходные owner assumptions стойкости трансформатора остаются непроверенными.

Результаты:

- **212 selected tests passed**,0 failures/errors/skips;34 новых protection tests.
  Полный suite не запускался. Проверены аналитические времена трёх inverse
  кривых, допуски, ток фидера, перенос, owner anchors, resealed corruption
  итогов/токов/summary, невозможность наград и границы контракта.
- Матрица восьми наборов: два допустимых (definite/inverse primary) и шесть
  отказов по pickup, grading, медленной основной защите, пропущенному КЗ,
  interrupting и позднему резерву. Matrix verifier заново пересчитывает
  решения и внешний эталон, включая отрицательные случаи и summary.
- Физическая основа: **9 новых grid runs** (три сценария ×три сетки).
  Восемь postprocessors не считаются72 интегрированиями. Базовый sampled
  peak≈506.011 А; временной запас definite≈.161 с с заданными допусками.
- **15 независимых OpenDSS решений КЗ**; все токи трёх фаз укладываются
  в2e-6 relative tolerance. Это не внешний динамический relay reference.
- Offline установленный wheel вне checkout повторил **9 интегрирований**;
  времена, каналы, события и результат screen совпали точно (manifest нового
  источника привязан отдельно). Parent/all9 workers блокировали imports
  benchmarks/tests/motulator. Перенесённый screen проверен с owner plan.
  Тот же host/shared dependencies; guard не является OS sandbox.

`reward=null` всегда; closed_loop_protection_qualified=false,
normative_approval=false. Реле не подключены к интегратору, действий на
выключатель и сброса/instantaneous stage в этом screen нет. Нельзя использовать
положительный screen как метку законченного пригодного проекта для обучения.
Далее нужны closed-loop действия и отказы аппаратов, полные токовые/температурные
границы, вклад машин в КЗ и расширение топологий вместе с DES-03.

Команды/границы: cases/protection-screen/README.md.
Матрица: reports/protection-screen-matrix-2026-11-13/;
переносимый пакет: reports/protection-screen-portable-2026-11-13/;
поставка: reports/protection-screen-delivery-2026-11-13/;
тесты: reports/protection-screen-final-tests-2026-11-13.xml.
Package fingerprint:
`10e26cff2204076a55c5ac27c26ccb8ce8df75269fd76ad7fdb53e60e87b9de9`.
Первый protection-catalog-source сохранён с прежним producer; после проверки
входа timing выполнен новый protection-catalog-qualified с текущим fingerprint.
GitHub/CI/git mutations не выполнялись.

## R06.24 / DES-02 — прослеживаемые паспортные выдержки и компиляция

Статус **in_progress**. Добавлены equipment_catalog.py и CLI catalog-design /
verify-catalog-design. Каждое число имеет единицу, источник, locator, дату и
тип evidence. Manufacturer-поля не могут ссылаться на owner-assumption;
допущения модели не могут маскироваться ссылкой на product passport.
В compilation.json сохраняются lineage всех полей, уравнения производных,
хеши каталога/brief/скомпилированного задания и точная строка температурной
таблицы. Чтение URL и выполнение внешнего кода отсутствуют: каталог offline.

Численные данные Nexans ref084193001: медный XLPE 4G95, air30 rating298 А,
Rdc20=.193 Ом/км. Schneider Trihal2018, печатная стр68 / PDF page67 с нуля,
36kV C0 Ck:250/400 кВА, вторичное400 В, uk6%, Pk120°C4000/5700 Вт.
Это выдержки опубликованных характеристик, не паспорта серийных аппаратов
и не подтверждение нынешней доступности. Полные байты документов не архивированы;
локальные hashes не удостоверяют автора или текущую удалённую страницу.
Источники/даты/границы описаны в cases/equipment-catalog/README.md.

Rработ вычисляется из Rdc20, явно заданных alpha, температуры и AC/DCfactor;
ur трансформатора — из нагрузочных потерь, включая добавочные потери,
при опубликованной температуре. Это не DC-сопротивление обмоток.
Ток/длительность стойкости трансформатора10000 А/1с остаются **непроверенным
owner assumption**, а не паспортом Trihal. Реактанс кабеля — общий fallback;
alpha и AC/DCfactor также явно обозначены. Положительный reward условен
относительно этих исходных данных, не является независимым OEM-одобрением.

Только50 Гц и одиночная цепь на воздухе. Температурные строки30/40°C1/.91
переносятся из общей инженерной таблицы с явным указанием допущения;
совместимость конкретной установки не выводится автоматически.
Неизвестная температура, интерполяция, групповая/подземная прокладка,
несогласованные catalog/brief или единицы отвергаются до создания output.

Результаты текущего producer:

- **36 фактических grid runs**:250/400 кВА при30°C,250 кВА при40°C,
  отказ по аппарату; три сценария ×три сетки на каждый вариант.
  Все численно квалифицированы; три accepted, один rejected. Матрица
  заново проверена с независимыми owner catalog/brief/selection anchors.
- **178 selected tests passed**,0 failures/errors/skips,34 новых catalog tests.
  Полный suite не запускался. Проверены conversion/lineage, единицы,
  неизвестные документы, недопустимые условия, cancellation, owner anchors
  и подмена derivation/lineage/reward после resealing.
- **Четыре независимых OpenDSS решения КЗ** для новых скомпилированных
  параметров: два трансформатора ×начало/конец кабеля. Четыре различных
  публичных численных проверки также проходят на каждом выборе.
  Это внешняя статическая сверка, не внешняя моторная валидация.
- Offline установленный wheel вне checkout повторил **9 интегрирований**:
  физические каналы, времена, события, compilation и verdict совпали точно.
  Parent и все9 children имели import guards для benchmarks/tests/motulator;
  перенесённый пакет проверен с owner anchors. Тот же host/shared dependencies,
  не независимая машина и не OS sandbox.

Поставка: `reports/equipment-catalog-delivery-2026-11-13/`;
матрица: `reports/equipment-catalog-final-2026-11-13/`;
эталоны: `reports/equipment-catalog-final-reference-2026-11-13/`;
тесты: `reports/equipment-catalog-qualified-tests-2026-11-13.xml`.
Воспроизведение: benchmarks/equipment_catalog_acceptance.py generate/verify/reference.
Package fingerprint:
`59405d32b2380fa0a512fdf1afb473d5deb0fd2f35b60d036d5b72ecc287b8a8`.
Предварительные equipment-catalog-qualified/reference сохранены со старым
producer; после ограничения50 Гц выполнена новая матрица final, старые
артефакты не переименовывались в текущую квалификацию.

Далее: подтвердить недостающие выдержки стойкости и AC-параметры, расширить
условия прокладки с доказанной совместимостью, затем топологии DES-03 и
координацию DES-04. DES-02 полностью не закрыт. GitHub/CI/git mutations
не выполнялись.

## DES-02 — совместный выбор оборудования и динамическая приёмка

Статус **in_progress**, ограниченный синтетический сквозной профиль проверен.
`equipment-design` / `verify-equipment-design` объединяют electrical-sizing и
DES-01. Выбранный кабель задаёт фактические R/X/длину/rating, трансформатор —
импеданс Тевенина, приведённый к стороне НН. Положительный вердикт требует
всех статических проверок и всех обязательных трёхсеточных моторных исследований.
Исходные критерии и события сохраняются. Статический отказ не пропускает
динамику; incomplete/numerically_unqualified дают reward=null. Проверяется
точная компиляция и owner anchors обоих вложенных исследований.

Профиль: два узла, один источник/кабель, 400 В в примере (контракт до 1 кВ),
один synthetic single-cage 75 кВА, пассивные вспомогательные нагрузки.
Статическая мощность покрывает nameplate VA двигателей и auxiliary envelope.
Ограничения сети и положительные реактансы проверяются до создания артефакта.
Исходный импеданс источника заменяется эквивалентом выбранного трансформатора;
это не модель его намагничивания/броска тока. Начальные условия таблиц и
время отключения предоставлены владельцем; нормативное одобрение отсутствует.

Результаты:

- **36 фактических grid runs**: четыре варианта ×три сценария ×три сетки.
  Пуск, повторные провалы, выбег/повторное включение. Принятый вариант и три
  отрицательных: недостаточный кабель, отключающая способность и строгая
  скорость. Все численно квалифицированы и повторно owner-anchored verified.
  Недостаточный кабель здесь проходит динамику, но отклоняется статически.
- **144 selected tests passed**, без failures/skips: 27 новых equipment tests,
  electrical-sizing и прежние design/motor-engineering/project tests.
  Полный suite не запускался. Проверены cancellation, reward semantics,
  подмена ТЗ/источника/итогового вердикта после resealing, физические параметры
  обеих выбранных компонент и границы применимости.
- Четыре независимых **OpenDSS** решения КЗ: два кабеля ×начало/конец линии.
  Команды, версии движка и фазные токи сохранены. Допуск 2e-6 задан в producer;
  четыре численных примера Schneider EIG также прошли. Это внешняя сверка
  статического эквивалента, не моторных траекторий и не IEC 60909 certification.
- Установленный offline wheel вне checkout повторил **9 grid runs**;
  каналы, временные шкалы, события и полный вердикт совпали точно.
  Во всех девяти workers запрет импорта benchmarks/tests/motulator;
  все четыре перенесённых варианта повторно проверены. Тот же host и общие
  system dependencies; import guard не является OS sandbox.

Примеры/команды/источники: `cases/equipment-design/README.md`.
Поставка: `reports/equipment-design-delivery-2026-11-13/`;
матрица: `reports/equipment-design-qualified-2026-11-13/`;
тесты: `reports/equipment-design-final-tests-2026-11-13.xml`.
Import guard и installed-check сохранены отдельно:
`reports/equipment-design-installed-protocol-2026-11-13/`.
Текущий package fingerprint:
`a2cdfc2fb79bdc7761ce7d58e5e65a365bfc29fee58cc0f98ef8cab5d09f6cb6`.
Предыдущий exploratory `equipment-design-first` сохранён с собственным старым
producer, не переименован в текущую квалификацию.

Далее: реальные паспорта и таблицы условий прокладки, расширение топологий,
КЗ с вкладом двигателей и координация защит. Каталог пока синтетический;
DES-02 целиком не закрыт. Температура кабеля от пусков, трансформаторный inrush,
земляные/ударные токи и селективность не следуют из положительного verdict.
GitHub/CI и операции записи git не выполнялись.

## R06.22 / DES-01 — проверяемый вердикт проектировщика и decision corpus

Закрыт ограниченный DES-01: **выбор эквивалентного импеданса питающей ветви**,
а не подбор физического кабеля. `run_design` теперь использует существующий
MotorEngineeringPlan: все обязательные случаи, три сетки, shaft/algebra/I²t/
thermal audits, grid consensus и bounded isolated workers. `verify_design`
пересчитывает evidence и при наличии trusted task/proposal anchors отвергает
подмену ТЗ, каталога или поданного решения. Верификация manifest сама по себе
не подтверждает личность/полномочия автора.

Вердикт v2 различает accepted/rejected/numerically_unqualified/incomplete.
Только квалифицированный accepted даёт reward1, квалифицированный отказ по
критериям —0; недостаточная точность и неполнота дают rewardnull. Поле
qualified_electrical_design остаётся false: ampacity, fault duty, selectivity
и нормативное соответствие ещё не выполнены. Independent physics oracle
для этого workflow не заявлен; это проверка известных уравнений и сеток.

Первый exploratory corpus не был допущен к экспорту: h10ms не проходил thermal
audit, а выбранный сильный фидер нарушал исходный heat cap3. Сохранён полный
отрицательный пакет `reports/designer-corpus-first-2026-11-12/`. Теперь compiler
делит шаг владельца пополам до <=1/(8*f), сохраняя исходные точки, и считает
три сетки h/h2/h4. Пороговые значения ТЗ и численные допуски не ослабляются.
Исходные task/proposal **с теми же хешами** пересчитаны заново: все три режима
численно квалифицированы и вновь отвергнуты по исходному ТЗ, reward0:
`reports/designer-original-strict-recheck-2026-11-12/`. Старый rewardnull не был
переклассифицирован без нового расчёта.

Новый корпус содержит шесть явно заданных задач с обычным heat cap8 и строгим
cap3, разные ID/ТЗ, один синтетический двигатель1MVA/10kV на задачу, по три
режима (пуск, повторные провалы, выбег/повторное включение) и два импеданса.
Cap3/8 — безразмерная stator I²-replica, не температура обмотки в °C.
Два строгих ТЗ не имеют допустимой позиции **в этом каталоге**. Ответ
no_feasible_option получает1 только при квалифицированном отклонении всех
позиций; это не утверждение о невозможности других схем или оборудования.

Train: radial/chain (2), dev: fork (1), test: loop/two_paths/ladder (3).
Полный перебор перестановок до6 узлов даёт каноническую сигнатуру графа,
поэтому переименование узлов/ветвей не переносит ту же топологию между splits.
Private verdict/reward/raw evidence не попадают в prompt exports, SFT labels
экспортируются только из train. Файлы доступны владельцу, это не OS sandbox
и не секретный benchmark. Корпус детерминированный, имена описательные,
сильные claims об агентном обобщении на этих fixtures недопустимы.

Результаты:

- **108 фактически выполненных grid runs**: 6 задач ×2 позиции ×3 режима ×3
  сетки. Все12 решений численно квалифицированы; четыре accepted и восемь
  rejected, два ТЗ полностью infeasible в своём каталоге.
- **122 selected tests passed**, без failures/skips: 27 новых проверок DES-01
  и95 прежних design/motor-engineering/project tests. Полный набор проекта
  не запускался. Проверены owner anchors, resealed corruption, отмена,
  topology relabelling, запрет held-out SFT labels и некорректных predictions.
- Реальные CLI generate/verify/export/evaluate прошли. Экспортированы2
  decision-only SFT-примера (выбор и отказ) и3 test prompts без private answers.
  Oracle-контроль получил1.0, constant opt-a0.0. Это контроль scoring plumbing,
  а не качество обученной модели. Trainer, VLM-adapter и tool-use demonstrations
  этим блоком не квалифицированы.
- Оценка использует заново проверенное cached physical evidence конкретных
  owner task/proposal/current producer; повторной симуляции на каждый score нет.
  Train evaluations/held-out SFT export, пропущенные/повторные/чужие task IDs,
  неизвестные позиции и candidate reward injection запрещены.
- Installed wheel вне checkout повторил9 реальных grid runs (три режима ×три
  сетки). Parent и все9 дочерних workers имели import guards для benchmarks/
  tests/motulator; physical channels/clocks/event_log/settings и весь итоговый
  verdict совпали с corpus в точности, anchored verify и ZIP roundtrip прошли.
  Это тот же host/OS/system-site-packages, не независимый host/CI и не sandbox.

По сравнению с предыдущим pinned wheel изменены только cli.py/design_study.py,
добавлен design_corpus.py; все остальные runtime Python sources, включая
численные уравнения, совпадают в точности. Proof:
`reports/designer-source-intervention-2026-11-12.json`.
Новый package fingerprint:
`f8488820c5408f59bd68c72d981e6b5b4ee5b0f65619ce3e9aa00748673fa5cd`.

Команды/примеры: [cases/design-curriculum](../cases/design-curriculum/README.md).
Corpus: `reports/designer-corpus-qualified-2026-11-12/`;
tests: `reports/designer-final-tests-2026-11-12.xml`.
Поставка: `reports/designer-delivery-2026-11-12/`; установленный пакет и
исходные результаты: `reports/designer-installed-2026-11-12/`. Перенесённый
комплект повторно проверен установленным wheel вне checkout: все owner anchors,
аудиты корпуса и исходный строгий отказ подтверждены без новой интеграции.
Протокол: `reports/designer-portable-2026-11-12/` (тот же host; reference imports
заблокированы). Все три комплекта запечатаны manifest-проверкой.
**Далее — DES-02:** прослеживаемый каталог, исходные installation conditions,
электрический выбор кабелей/трансформаторов и независимая сверка sizing.
DES-03/04, VIS-01, LEARN-01 и независимая оценка качества агента остаются открыты.
GitHub/CI/git mutations не выполнялись.

## R06.21 / PHY-21 — измеренная матрица динамических сетей

Закрыт объявленный ограниченный профиль PHY-21: **12 случаев, 47 primary и
21 независимо рассчитанная reference trajectory**, новый процесс на каждый
запуск. Два повтора Radau/network DAE, а для автоматики и островов — ещё
отдельный DAE-запуск с обратным порядком оборудования, паспортов и команд.
Фазные случаи рассчитываются Radau и сверяются с точными expm-решениями.

Охват разделён явно:

- Mesh RMS: 64/4/1, 256/8/2, 512/64/16 и 2000/128/32 узлов/двигателей/
  генераторов, **0.6 s**, групповой пуск и два провала/восстановления питания.
  Большие точки используют double-cage и GENROU/TGOV1/SEXS. Все узлы и внутренние
  состояния проверены отдельной SI winding/MNA реализацией с собственным
  начальным режимом; источник данных — авторские параметры, не конкретная площадка.
- Два 45-s случая: три одновременно живых острова, механические возмущения,
  повторное разделение/соединение и безопасно заблокированные несинхронные
  включения. Три узла, два GENCLS, моторные состояния отключены. Напряжения,
  частоты/углы и разрешения проверены отдельными SI-уравнениями.
- Overcurrent → отказ line breaker → upstream backup; успешный АВР и
  блокировка АВР при неразомкнувшемся основном выключателе. Проверены выдержки,
  действия, interlocks и break-before-make, в обоих интеграторах и при перестановке.
- 12/48/96 независимых несимметричных RLC-ветвей, 15/51/99 узлов с источниками,
  24/96/192 электрических состояния, **20 ms**. Все токи и напряжения совпали
  с отдельно записанными 4×4 matrix exponentials; повторные изменения R имеют
  обе стороны события, без скачка тока L/напряжения C.

Ни один из этих результатов не является проверкой всех перечисленных функций
одновременно в крупнейшей сети. Длительный пуск больших сетей, двигатели при
островном пуске, arbitrary PSS/OEL/UEL/protection combinations и общая meshed
несимметричная динамика остаются расширениями.

Первый полный прогон прошёл 11 случаев и корректно отклонил phase96 до
интегрирования: 288 команд превышали schema limit256. Отрицательная поставка
сохранена: `reports/network-dynamic-full-2026-11-12/`; она не является
положительной квалификацией. В `PhaseCase` лимит расширен до512. Точное сравнение
всех runtime Python sources с предыдущим wheel подтвердило единственную замену
числа256→512 в этом поле; уравнения и алгоритмы не менялись. 513 команд,
неоднозначные simultaneous actions и превышение retained-output budget
по-прежнему отвергаются. **Вся матрица пересчитана заново**, старые timings и
trajectories не переименовывались под новый producer.

Измерения финального прогона (два fresh-process повтора, один CPU/BLAS thread):

- 64/4/1: 22 states, median Radau3.554 s / DAE9.753 s.
- 256/8/2: 44 states, median Radau4.992 s / DAE13.490 s.
- 512/64/16: 480 states, median Radau14.490 s / DAE31.432 s;
  max current-exec RSS186.09/151.44 MiB.
- 2000/128/32: 960 states, median Radau33.260 s / DAE91.670 s;
  median CPU33.252/82.730 s, max current-exec RSS265.55/221.57 MiB.

Здесь DAE не быстрее Radau. VmHWM включает imports/construction/solve/JSON
serialization, не incremental solver allocations. Compute wall/CPU clocks
исключают bootstrap/global imports и serialization. Host load/frequency не
контролировались; время и память не являются рейтингом среди всех решателей.
Все raw измерения сохранены, compact size/accuracy/time/memory matrix:
`reports/network-dynamic-resource-matrix-2026-11-12.json`.

**130 selected tests passed**, без skip/deselect: 40 новых PHY-21 +90 прежних
automation/DAE/islands/RMS-scale/phase tests. Полный набор проекта не запускался.
Verifier повторно вычисляет physical checks, repeat equality, размерности,
resource summaries и current producer/input identities. Проверены 12 видов
resealed corruption, включая timings/RSS, method path, events и waveform.
Реальные RHS/event/output-budget failures сохраняют completed=false и точный
resource/limit/attempted; CLI возвращает2 даже при solved=true после успешной
инициализации. Такие результаты не могут стать положительными SFT/GRPO labels.

Installed wheel вне checkout, при запрещённых imports benchmarks/tests/motulator,
повторил целиком islands_mismatch и phase96/288commands. Clocks/sides/channels/
event_log совпали с source matrix **в точности**, ZIP roundtrip прошёл. Это
тот же host/OS/system-site-packages, не независимый host или внешний CI.

Корпус и команды: [cases/network-dynamic](../cases/network-dynamic/README.md).
Evidence: `reports/network-dynamic-qualified-2026-11-12/`;
tests: `reports/network-dynamic-tests-2026-11-12.xml`;
scope-change proof: `reports/network-dynamic-capacity-intervention-2026-11-12.json`.
Локальная sealed поставка: `reports/network-dynamic-delivery-2026-11-12/`.
Все frozen sources, case fixtures, raw results и resource summaries перенесены
в `/tmp/phy21-transfer-bundle`; установленный wheel повторно проверил все12
случаев при запрещённых imports motulator/ANDES/pandapower, без checkout
production imports и без изменения manifest (`python -B -I`). Полная reference
интеграция при этой проверке повторно не запускалась. Receipt:
`reports/network-dynamic-portable-2026-11-12/`.
Новый runtime fingerprint:
`32f2b7500bee238a5892cec98b8b908e6bfa5635ef4546c71fa99030970ab6d8`.
Прошлые drive-profile receipts относятся к своему pinned producer; расширение
phase capacity не переобъявляет их новой полной квалификацией.

**Следующая очередь — DES-01:** включить three-grid физический аудит в verdict
проектировщика, расширить обязательные сценарии и отделить скрытую оценку.
Затем DES-02 — прослеживаемый каталог и электрический подбор оборудования.
GitHub/CI/git mutations не выполнялись.

## R06.20 / PHY-20 — непрерывная независимая сверка многоклеточного привода

Закрыт объявленный ограниченный профиль PHY-20: отдельные уравнения всех
Gamma-клеток, насыщение, положительный Gc, обмоточные R(T)/RC, source RL,
диоды/DC, MOSFET/FOC/PWM, switching-energy и тепловая защёлка ротора. Runtime
физика в этом блоке **не менялась**; проверены ранее реализованные сочетания.
Это не завершение оставшейся физики сетей или универсальная квалификация.

`benchmarks.multicage_drive_reference` самостоятельно выражает стационарные
комплексные all-flux уравнения и использует независимую непрерывную схему
R06.19. Production imports в workers запрещены. **Многоклеточный RHS не является
внешним motulator simulator.** Одноклеточный предел отдельно проверен с
неизменённым SHA256-pinned motulator0.23.1 RHS, включая насыщение, winding R(T),
момент и полную короткую гибридную траекторию. Внешний пакет для самого
multicage worker не требуется.

Физические проверки сверх сравнения двух интеграторов:

- Одинаковые параллельные клетки с одинаковыми flux-состояниями точно
  сокращаются к одной клетке с R/n и L/n. Для произвольных различных постоянных
  времени это сокращение не заявляется.
- Перестановка клеток сохраняет stator/network, момент, общую температуру
  и энергию; противоположная разностная мода затухает как exp(−R/L·t).
- 48 sinusoidal cases совпали с отдельно выведенным equivalent impedance:
  1–4 клетки ×5/50/200 Hz ×slip0/0.02/1/−0.1, включая генераторный режим,
  баланс электрической/механической мощности и потерь.
- 243 проверки runtime-порта: 2–4 клетки ×27 gate states ×zero/energized/unequal
  fluxes. Проверен ток всех роторных ветвей, а не только первой клетки.
- Независимые per-cage heat quadratures положительны и в сумме дают полный
  rotor heat. Это не отдельные температуры клеток: rotor RC-lump пока общий.

Полный корпус: double linear, double/triple/four saturated warm, four-cage
sag/block/recovery и four-cage rotor trip. Все случаи 4 ms, кроме 12-ms
восстановления. Параметры дополнительных клеток и ускоренный тепловой стресс
авторские, не паспорт и не идентифицированная skin-depth характеристика.

Результаты:

- **12/12 primary** budgets0.05/0.0125 и **12/12 continuous references**
  DOP853/Radau прошли исходные physical caps и events. Максимальный primary
  channel error0.00289798 cap (0.289798% cap), disagreement references
  1.61704e-7 cap. Maximum event error3.64205e-10 s при cap1e-8 s;
  heat error6.62209e-8 J при cap1e-5 J. Это comparison caps, не строгая
  математическая верхняя граница ошибки решения.
- **531 selected tests passed:** 339 новых PHY-20 +192 прежних reference/
  native/evidence checks. Семь прежних reporting-only migration tests явно
  deselected. Полный набор проекта и production572 повторно не запускались:
  package Python sources неизменны после R06.19.
- Recomputing verifier подтвердил все шесть случаев и full declared inventory.
  Проверены десять видов resealed tampering: limits, source hashes, solver
  controls, ложное external claim, producer, fourth-cage flux, cage heat,
  summary, passport и full-inventory flag. Manifest не является внешней подписью.
- Установленный wheel вне checkout выполнил полный four-cage rotor-trip case,
  совпал с source result в точности, прошёл continuous comparison и ZIP
  roundtrip при запрещённых imports benchmarks/tests/motulator. Host/OS и
  system-site-packages общие; независимый host не заявлен.

Корпус и команды: [cases/multicage-continuous](../cases/multicage-continuous/README.md).
Evidence: `reports/multicage-continuous-first-matrix-2026-11-12/`;
tests: `reports/multicage-continuous-tests-2026-11-12.xml`.
Локальная sealed поставка: `reports/multicage-continuous-delivery-2026-11-12/`.
Весь bundle перенесён в `/tmp/phy20-transfer-bundle`; установленный wheel
с bundled benchmark sources/fixtures повторно проверил все шесть случаев
при запрещённых imports motulator/ANDES/pandapower. Включён явный `python -B -I`,
чтобы импорт не создавал bytecode внутри sealed inventory. Первый probe без
`-B` корректно отклонён после появления `__pycache__`; original inventory
восстановлен, успешный verifier не меняет manifest. Receipts:
`reports/multicage-continuous-portable-2026-11-12/`.
Runtime fingerprint остался
`249dfffd323acdee2b316f53ed0ad7a1b335f9feab4bc5b55e6cc5269bb8f231`.

Для будущего SFT/GRPO зафиксирована неидентифицируемость: нельзя оценивать
произвольный порядок клеток или эквивалентные паспорта как единственную
истину по terminal traces. Совпадение отклика не гарантирует уникальные
физические параметры. Обучающий/публичный VLM-adapter этого корпуса ещё открыт.

**Далее — PHY-21:** измеренное масштабирование phase/DAE сетей, острова,
каскадные топологические защиты и повторные возмущения. Параллельно остаются
DES-01 three-grid verdict и DES-02 подбор оборудования. Полный circuit с Gc=0,
charge dynamics и длительные произвольные режимы этим профилем не закрыты.
GitHub/CI/git mutations не выполнялись.

## R06.19 / PHY-19 — связанный электротепловой профиль

Закрыт объявленный ограниченный профиль PHY-19: положительный Gc, одна клетка,
насыщение, нагрев обмоток, semiconductor R(T), коммутационная энергия,
тормозная ветвь и тепловые защёлки. Это не закрытие всей оставшейся физики.

Реализован отдельный `benchmarks.electrothermal_drive_reference` поверх
независимой R06.18 схемы и оригинального motulator 0.23.1 RHS. Исторический
continuous reference не изменён. Новые уравнения включают R25[1+alpha(T−25)],
RC-нагрев, корни температурных порогов, тормозную проводимость и точный перенос
switching energy из дифференциальной DC-ёмкости в нагрев инвертора. Поток,
скорость и common-mode rails при switching jump не сбрасываются.

Выполнены семь исследований: warm losses, switching-energy trip, conduction
trip, stator trip, brake trip, rectifier trip и lossy block/recovery. Первые
шесть — 4 ms, восстановление — 12 ms. Малые теплоёмкости намеренно ускоряют
события; параметры авторские, не OEM/измерения. Любая тепловая защёлка блокирует
управляемые ключи инвертора, но **не размыкает питающий выключатель** и не
отключает пассивные диоды. Собственная защёлка тормоза убирает его проводимость.

Новый rectifier case выявил реальный численный дефект: terminal core-current
Newton застревал возле diode hinge/free common-mode gauge, вызывая min-step
failure. В `SwitchingMagnetics.terminal_current` добавлен ограниченный перебор
27 пассивных active sets только после отказа прежнего метода. Допуски KCL
и physical caps не ослаблены, состояния не проецируются. Сохранённое состояние
воспроизводит отказ pinned предыдущего wheel; новое решение совпадает с
исходной внешней машиной. На 54 общих состояниях старый/новый результат
совпал в точности. Сравнение Python inventory wheel подтверждает единственное
runtime-изменение — `switching_magnetics.py`; все семь study неизменны.

Итог:

- **14/14 primary trajectories** на budgets 0.05/0.0125 и **14/14 references**
  DOP853/Radau прошли прежние caps, audits и события. Максимальный primary
  channel error — 2.59814% соответствующего cap; reference disagreement —
  1.44937e-7 cap. Максимальная ошибка времени события — 7.554e-11 s при cap1e-8 s;
  максимум heat error — 3.660e-8 J при ранее принятом extended cap1e-5 J.
- Независимые reference workers проверяют SHA256 wheel/всех native sources и
  запрещают production imports. Полный recomputing verifier прошёл 7 случаев:
  input/controls/native identity/current producer, raw channels/heats/events,
  assessments, summary и manifest. Manifest обеспечивает целостность, не
  внешнюю подпись происхождения.
- **764 selected tests passed:** 572 production/engineering +192 reference/
  native-port/evidence tests; 7 исторических reporting-only migration tests
  явно deselected после отдельного численного изменения. Полный набор проекта
  не запускался. Проверены 27 gate combinations ×2 temperatures, captured
  failure, forced Newton stall, energy jumps и восемь видов resealed tampering.
- Прежний warm-core 40-ms профиль повторён на двух primary budgets и прошёл.
  Независимые reference curves переиспользованы только при точном совпадении
  reference source, wheel, settings и study; это не повторная квалификация
  всех прежних R06.18 случаев на новом runtime.
- Установленный новый wheel вне checkout выполнил полный rectifier-trip case,
  совпал с source result в точности, прошёл сравнение с continuous reference
  и ZIP roundtrip. Imports benchmarks/tests/motulator запрещены. Host/OS и
  system-site-packages общие, независимый host не заявлен.

Корпус: [cases/electrothermal-drive](../cases/electrothermal-drive/README.md).
Итоговые raw результаты: `reports/electrothermal-drive-corrected-2026-11-12/`.
Сохранённый отрицательный полный прогон до исправления:
`reports/electrothermal-drive-qualified-2026-11-12/` (6 passed/1 failed,
не является положительной квалификацией, несмотря на историческое имя каталога).
Pinned intervention: `reports/electrothermal-port-ablation-2026-11-12.json`.
Source fingerprint: `249dfffd323acdee2b316f53ed0ad7a1b335f9feab4bc5b55e6cc5269bb8f231`.

**Следующие блоки:** PHY-20 — continuous multicage/reference и физические
пределы; PHY-21 — большие связанные phase/DAE сети и топологические защиты.
Параллельно остаётся DES-01 three-grid verdict, затем DES-02 подбор реального
оборудования. VLM/SFT/GRPO не считаются выполненными этой проверкой физики.
GitHub/CI/git mutations не выполнялись.

## Актуальное расширение: агент-проектировщик и оставшаяся физика

По решению владельца добавлена роль **мультимодального инженера-проектировщика
промышленного электроснабжения и электроприводов**. Его результат — предварительная
схема, выбор из прослеживаемого каталога и расчётное обоснование по всем режимам ТЗ.
Экономика исключена. GitHub, внешние CI и автоматические git-действия запрещены.
Исполняемая очередь с зависимостями и критериями: [agent-designer.yaml](agent-designer.yaml).
Это продолжение R06.18, а не объявление всей физики или проектировщика готовыми.

Порядок закрытия:

1. **PHY-19:** независимый совместный профиль switching-energy, semiconductor
   R(T), торможения и тепловых отключений. Компоненты уже существуют; требуется
   проверка комбинации непрерывным эталоном, энергобалансом и сходимостью событий.
2. **PHY-20:** независимая непрерывная сверка многоклеточных насыщенных машин,
   потерь в стали и нагрева обмоток, с проверкой предельных случаев.
3. **PHY-21:** измеренное масштабирование связанных phase/DAE сетей, островов,
   каскадных защит и повторных возмущений. Публиковать время и peak RSS вместе
   с точностью; старые размерные точки не считать новой квалификацией.
4. **DES-01 → DES-04:** неизменяемое ТЗ и каталог → электрический выбор
   оборудования → редактирование схемы → совместные пуск/КЗ/защиты/резервирование.
5. **VIS-01 → LEARN-01 → EVAL-01:** реальные публичные схемы и осциллограммы,
   квалификация мультимодального адаптера Qwen3.5-4B, SFT/GRPO и независимые
   семейства задач. Обученная инженерная модель пока не получена.

### DES-01: первый исполняемый профиль, частично реализован

`power_grid_env.design_study` и CLI `feeder-design` принимают доверенное ТЗ
с обязательными моторными сценариями и предложение с одним `option_id`.
Агент не может менять критерии, выдумывать каталог или исключать неудачные
режимы. Выбранный полный импеданс ветви применяется одинаково во всех режимах;
расчёты выполняются существующими изолированными workers. Сохраняются ТЗ,
предложение, результаты сценариев, хеши и общий manifest. Solver failure и
невыполненный сценарий не дают положительного заключения.

Это **выбор эквивалентного питающего импеданса**, пока не выбор физического
кабеля: ampacity/derating, КЗ, селективность и нормативное соответствие не
проверяются; `qualified_electrical_design=false` даже при успехе динамики.
Пример с прямым пуском и провалом/восстановлением:

```bash
python -m power_grid_env.cli feeder-design --input cases/design/industrial-feeder.json --proposal cases/design/stiff.json --out reports/design-stiff
python -m power_grid_env.cli feeder-design --input cases/design/industrial-feeder.json --proposal cases/design/weak.json --out reports/design-weak
```

Код возврата 0 означает выполнение только объявленных динамических критериев;
2 — нарушение либо ошибку. Выходной каталог должен быть новым. Параметры
примеров авторские, не паспорт действующей установки. До завершения DES-01
остаются three-grid audit в verdict, расширение сценариев и скрытый evaluation
split. Следующие блоки не закрываются фактом появления этого интерфейса.

Выполнены CLI-расчёты обоих примеров: stiff прошёл оба режима, weak нарушил
оба при успешно завершившемся решателе. Итоговая скорость stiff ~0.98662 pu,
weak ~0.18128/0.17707 pu; минимальное напряжение в объявленных окнах stiff
~0.96994/0.97171 pu, weak ~0.30997/0.30642 pu. Это различает непригодный
проект и ошибку расчёта. Оба верхних manifest проверены. Эти synthetic
результаты не являются независимой повторной верификацией физического ядра.
Проверка изменения: **95 passed** (`test_design_study`, `test_motor_engineering`,
`test_engineering_project`), включая 15 новых тестов контракта и реальных
расчётов; проверена подмена proposal после sealing. Полный набор проекта
и прежняя R06.18 matrix на изменённом package fingerprint не перезапускались.

Предыдущий блок — R06.18: полная непрерывная сверка одноклеточного привода
с внешним motor RHS motulator0.23.1 и независимо записанным source/RL/diode/
DC/bridge/FOC/PWM/thermal circuit. Четыре scenarios ×2 primary budgets и
четыре×2 reference methods: все8 primary и8 continuous references passed.
DOP853/Radau disagreement <=1% исходных caps; фактический максимум0.006625%
cap, максимум primary error27.23% cap. 676 selected tests passed. Исправлена
report-only ошибка sample clock/gate permission на границах block window;
все8 before/after physical arrays/ledgers/counters совпали в точности.
Сеть и контроллер reference — независимый adapter, а не целый motulator
simulator; universal superiority/OEM accuracy не заявлены. Далее —
switching-energy/thermal protections/device R(T), multicage continuous
reference и измеренное масштабирование phase/DAE сетей.

## Сравнение с другими моделерами и порядок развития — 9 ноября

**Вывод: универсальное превосходство проекта не доказано.** Ни число тестов,
ни малое расхождение с SI-эталоном, ни наличие LLM-интерфейса не являются
общим рейтингом. Ниже — аудит текущих исходников/локальных receipt и обзор
официальной документации/первичных публикаций. Новые head-to-head запуски
чужих пакетов в рамках этого обзора не выполнялись. Возможности из документации
не приравниваются к независимо воспроизведённым результатам.

### Что уже подтверждено у нас

Последний обязательный профиль — 1590 passed, 0 failed/skipped; 2082 вне
профиля не выполнялись. Motor-engineering прошёл 8 SI-сверок и 24 расчёта
на трёх шагах. Это квалификация выбранных balanced single-cage случаев,
не всех моделей проекта и не натурная точность. Receipt:
`reports/motor-engineering-delivery-2026-11-08/delivery.json`.

Проект шире последнего одно-/двухдвигательного корпуса: есть DAE/RMS,
многоклеточные и насыщенные потокосцепленные машины, фазная RLC-сеть,
RC/MOV-коммутация, ограниченные GFL/GFM и V/f-порты, защита и острова.
Границы и независимые сверки описаны в
[обмоточной физике](../docs/EQUIPMENT_AUTOMATION_VALIDATION_RU.md),
[фазном профиле](../docs/PHASE_NETWORK_AND_RESEARCH_RU.md) и
[финальных исследованиях](../docs/REMAINING_BLOCKS_ACCEPTANCE_RU.md).
Наличие этих профилей не означает поддержку любой их комбинации.

Историческая приёмка действительно содержит 2000 узлов / 128 двигателей /
32 генератора, 6 s, оба интегратора и отдельные SI-сравнения:
`reports/blocks1-5-qualified-v6-2026-11-05/summary.json`, производитель
`cb6269ddd052e1140401c43c07063fc221eff20f54a1d0b13fae2c20c830b01c`.
Это не повторная квалификация этих больших точек на нынешнем производителе.
Там же storage slots уменьшались при DAE, но wall time снимка производных
увеличивался; RSS не уменьшался сопоставимо. Это не времена полной траектории,
не сравнение с конкурентом и не доказательство ускорения GRPO.

Часть статического сетевого backend использует OpenDSS
(`power_grid_env/backend_opendss.py`). В такой постановке нельзя считать
совпадение с тем же OpenDSS независимой проверкой ядра или заявлять более
точный power flow только за счёт нашей обвязки. Собственная динамика и другие
расчётные профили оцениваются отдельно.

### С кем сравнивать и в какой постановке

- **OpenDSS:** распределительные режимы, КЗ, гармоники и dynamics.
  Не следует описывать его как только статический power flow.
  [EPRI capabilities](https://opendss.epri.com/AboutOpenDSSCapabilities.html).
  Сравниваем покрытие переносимых схем, QSTS, проверки ввода и накладные
  расходы workflow; при одном upstream solver не выдаём это за два независимых ядра.
- **pandapower и Power Grid Model:** сетевые компоненты, режимы, state
  estimation, КЗ; PGM имеет C++ core и symmetric/asymmetric batch-расчёты.
  [pandapower](https://www.pandapower.org/about/),
  [PGM](https://power-grid-model.readthedocs.io/en/stable/).
  Наше наличие динамических двигателей не делает нас автоматически лучше
  в этих иных задачах. Чужое наличие функции также не доказывает нашу ошибку.
- **ANDES и PowerSimulationsDynamics.jl:** динамические модели, инициализация,
  transient/small-signal, регуляторы и IBR.
  [ANDES verification](https://docs.andes.app/en/latest/verification/index.html),
  [PSID](https://sienna-platform.github.io/PowerSimulationsDynamics.jl/dev/).
  Сопоставляем общие физические модели, состояния, исходный режим и события;
  разные DAE-формулировки не обязаны иметь одинаковые вспомогательные состояния.
- **motulator:** приводы IM/SyRM/PMSM, discrete-time controls, фильтры и
  GFL/GFM. Есть и прямой пуск; поэтому DOL сам по себе не уникален.
  [Документация Aalto](https://aalto-electric-drives.github.io/motulator/).
  Сохранённый у нас oracle 0.19.0 остаётся закреплённым историческим эталоном;
  текущий сайт описывает более новую библиотеку, а не тот же исполняемый артефакт.
- **Grid2Op + LightSim2Grid и PowerGym:** agent environments, действия,
  наблюдения, сценарии и быстрые сетевые циклы.
  [Grid2Op](https://grid2op.readthedocs.io/en/latest/),
  [LightSim benchmarks](https://lightsim2grid.readthedocs.io/en/latest/benchmarks.html),
  [PowerGym paper](https://proceedings.mlr.press/v168/fan22a.html).
  Опубликованные solver timings исключают часть end-to-end работы и не являются
  оценкой нашего обучения или доказательством ускорения на нашей машине.
- **VeraGrid:** публикует cross-software проверки RMS, small-signal и EMT
  Floquet. Независимые сверки как подход тоже не являются нашей уникальностью.
  [Области и ограничения сравнений](https://veragrid.readthedocs.io/en/latest/md_source/dynamic_benchmarks.html).
- **PowerFactory и PSCAD:** ориентиры промышленного охвата. PowerFactory
  документирует разные схемы пуска, включая reactor/autotransformer/star-delta/
  thyristor softstarter; PSCAD — EMT и библиотеку устройств.
  [Motor Starting Toolbox](https://www.digsilent.de/en/motor-starting-functions.html),
  [PSCAD](https://www.pscad.com/software/pscad/overview).
  В проекте простая voltage-ramp soft-start проверка не равна тиристорной модели.
  Документация коммерческого продукта не заменяет выполненный численный benchmark;
  без доступной реализации/законно доступных эталонов численного сравнения нет.

### Конкуренция именно в AI

LLM + tools + hidden physical evaluator уже опубликованы в
[PowerAgentBench-SS](https://arxiv.org/abs/2606.18789), а динамические задачи
review/risk screening — в [PowerAgentBench-Dyn](https://arxiv.org/abs/2606.20401).
[X-GridAgent](https://arxiv.org/abs/2512.20789) описывает автоматизацию анализа,
а [Grid Agents, 2 ноября 2026](https://arxiv.org/abs/2611.03534) заявляет корпус
519 задач и проверку replay tool calls. Это авторские исследовательские
результаты/препринты; мы их модели и заявленные преимущества не воспроизводили.
Но заявлять новизну лишь за счёт verifier, журналов или LLM-обвязки нельзя.

У нас реализована проверяемая база SFT/GRPO, но текущая приёмка — 24 synthetic
restoration-задачи четырёх topology families. Это не промышленное разнообразие
и не результат инженерного Qwen. Подтверждён processor Qwen3.5-4B, однако
multimodal training/inference adapter и качественный агент ещё не выполнены.
Возможное направление преимущества — инженерная динамика электроприводов,
схемы/осциллограммы и воспроизводимое обучение агента на безопасных/опасных
решениях. Это гипотеза для измерения, не установленная уникальность.

### Следующая очередь: критерии завершения

**R01 — общий сравнительный протокол, перед новыми заявлениями о лидерстве.**

**R01.1 выполнен в зарегистрированном subset:** единый runner и portable
verifier, все 8 обязательных пар прошли в трёх tracks: 2 AC/pandapower 3.5.6,
4 RMS/ANDES 2.0.0, 2 full-winding/motulator 0.19.0 + независимая SI-сеть.
Qwen остаётся `not_performed`, общий EMT — `not_applicable`:
`full_declared_inventory_executed=false`, рейтинг/превосходство не заявляются.
Полный обязательный профиль: **1638/0/0**, 2082 вне профиля не выполнялись;
включены 48 новых тестов. Исправлена ошибка integer arrays в balanced AC
(pu-нормализация и fractional slack dispatch). Подтверждены явные pu-to-MVA
shunt mappings и совместимость JSON integer/float входов.
Wheel вне репозитория проверил 8 пар и ZIP-перенос при запрещённых импортах
pandapower, ANDES, motulator и benchmarks: чтение evidence не требует решателей.
Системные core-зависимости переиспользованы, чистая ОС не заявляется.
Производитель: `c343d0cd10b089e28e8da6028616a6e6f2597266ad73e4f13367c07d3f671712`.
Поставка: `reports/comparison-delivery-2026-11-09/delivery.json`.
Source snapshot проверен до этих итоговых заметок; Python после тестов не менялся.
[Описание, преобразования и команды](../docs/COMPARISON_PROTOCOL_RU.md).
R01.2 остаётся открытым для более широкого общего корпуса/agent workflow;
скоростной рейтинг относится к R03, не к нынешней численной сверке.

Зафиксировать case manifest, версии, параметры и допустимые subsets. Раздельно:
static network, motor/RMS dynamics, phase/full-winding, end-to-end agent workflow.
Для каждого слоя выбрать независимый применимый reference; несовместимый
режим помечать not_applicable, а не проигрышем чужого инструмента.
Done: один локальный запуск воспроизводит сравнение, публикует mappings,
исходные режимы, события, допуски и все неуспехи. Новая функция не считается
преимуществом без выбранной сопоставимой задачи.

**R02 — законченный промышленный motor/network profile: главный физический блок.**

**R02.1, 9 ноября 2026: реализована общая RMS-обвязка.**
`motor-engineering` теперь проводит аудит motor_start и coupled_rms единым
путём: три шага, полные event sides, энергия каждого вала, I²t, электрические
величины, восстановление и инженерные ограничения. Валидация идентичности
включает генераторные настройки. Новый входной пакет
`cases/motor-engineering/multibus-generator-restart.json`: три синтетических
single-cage двигателя на разных узлах, ступенчатый пуск с генераторами,
самозапуск с генераторами и от сети. Девять production-расчётов прошли аудит
и согласование сеток; два самозапуска допустимы по заданным критериям,
короткий ступенчатый пуск ожидаемо недопустим. Итог пакета regression=true,
engineering_acceptable=false. Исходные double-cage fixtures не изменены;
варианты явно маркированы как синтетические, не fitted equivalents.
`benchmarks.multibus_engineering_reference` проверяет сохранённый пакет
отдельными SI winding/network уравнениями с прежними фиксированными допусками.
Это не внешний моделлер и не натурный эталон. Все три fine-grid траектории
прошли SI-сверку. Проверены 138 разных тестов затронутых и смежных модулей:
76 motor-engineering/trace, 60 остальных с первого прогона и 2 ANDES после
восстановления локальных зависимостей. Полный release suite в этом этапе
не запускался. Отчёты: `reports/multibus-engineering-2026-11-09` и
`reports/multibus-engineering-reference-qualified-2026-11-09`.
**R02.2: реализован конечный дизайн неопределённости.**
`motor-robustness` / `verify-motor-robustness`: один желаемый режим,
OAT или Cartesian, до 8 точек; каждая независимо инициализируется и
проходит полный трёхсеточный аудит. Вход фиксирует происхождение границ.
Отдельные статусы для полного прохождения, нарушений и неопределённости;
неквалифицированные/отменённые точки нельзя молча исключить. Проверка
заново строит дизайн и сверяет все входы, аудиты и выводы; поддержан ZIP.
Для coupled_rms оси двигателя используют тот же механизм изменения
параметров, что и прежний sensitivity, не меняя старый ограниченный API.
Новый пример `generator-restart-robustness.json`: 4 сочетания +25% инерции
и +20% нагрузки одного из трёх двигателей, 12 production-прогонов.
Все точки проходят объявленные критерии. Это синтетический stress design,
не паспортные допуски и не доказательство непрерывного диапазона.
108 тестов текущего этапа прошли (21 robustness и 87 смежных); полный
release suite не запускался. Новый тест включён в обязательный профиль.
Все 4 fine-grid траектории дополнительно прошли независимую SI-сверку
с прежними фиксированными допусками. ZIP export/extract/reverify сохранил
идентичность. Wheel собран и установлен в отдельный venv с системными
зависимостями; проверка из `/tmp` с запрещёнными импортами benchmarks,
ANDES, motulator и pandapower прошла. Это тот же host, не внешняя поставка.
Артефакты: `reports/motor-robustness-2026-11-09`,
`reports/motor-robustness-reference-2026-11-09`.
При изменении Python-кода прежние fingerprint-bound пакеты считаются
историческими; новые подтверждения относятся к текущему snapshot.

**R02.3: реализован явный перенос паспортов RMS/T ↔ Gamma → phase.**
`motor-transfer` / `verify-motor-transfer`, схема `motor-transfer.schema.json`.
Для single-cage выполнено SI-приведение с сохранением линейного входного
сопротивления, J и механической нагрузки; обратный T split задаётся явно,
не считается идентификацией. Несовместимые клетки и трение не теряются
молча; тепловые/контакторные свойства и нелинейности имеют точный список
подтверждённых omissions. Фазный трёхпроводный isolated-star порт сохраняет
насыщение, до 4 Gamma-клеток и winding thermal; Gc требует явного исключения.
Инициализация только cold_zero; переноса рабочей точки и всей сети нет.
Приёмка: три 40 ms cold-профиля (линейный паспорт в 50/60 Hz представлениях
и насыщенный с авторским thermal/mechanism) прошли full-winding ↔ phase
и сверку с исходными RHS motulator 0.19.0 плюс независимая SI-сеть.
Для двух линейных случаев прошла отдельная SI-интеграция полной T-схемы.
Это не равенство динамических RMS и full-winding траекторий и не три
независимых моторных семейства. Краткое включение не квалифицирует весь разгон.
194 разных теста затронутых/смежных модулей прошли: общий прогон 191,
дополнительный transfer-прогон 34 с пересечением 31. Полный release suite
в этом этапе не запускался. Wheel и ZIP reverify прошли вне репозитория
на том же host с запрещёнными импортами эталонных пакетов.
Артефакты: `reports/motor-transfer-qualified-2026-11-09`,
`reports/motor-transfer-passport-2026-11-09`.

**R02.4: согласованная периодическая инициализация full-winding сети.**
`FluxNetworkSettings.operating_point` решает токи ветвей/всех обмоток,
скорости и напряжения совместно: dI=jωI, исходный KCL и Te=Tload.
Изотропное насыщение поддержано; thermal/Gc в новом initializer отклоняются,
прочие модели проекта не удалены. До 128 физических состояний,
ограничены residual evaluations и wall time; workflow до 8 двигателей.
Нормировка механики по моменту не даёт огромной инерции скрывать небаланс.
При неудаче явный initialization_failed, без fallback на cold.
Новые CLI `warm-flux-study` / `verify-warm-flux-study`, входной пример
`cases/motor-engineering/warm-flux-saturated-sag.json`.
`MotorPort.initial_currents_ab_a` и `phase_ports` передают все winding
токи и скорость; без нового поля холодные случаи сохраняют поведение.
Сеть фазного расчёта должна согласовываться отдельно, это не импорт
произвольного послеаварийного RMS-состояния или автоматическая миграция сети.
Три native случая прошли (hold, насыщенный sag, meshed pair);
в motulator 0.19.0 equilibrium решён независимо в flux coordinates,
из собственного seed, без production state import. Один явный warm phase
перенос также прошёл. Длины native случаев 60–80 ms, не длительная квалификация.
После исправления нормировки момента повторно прошли 231 unit/regression
тест без skips/failures; полный release suite не запускался.
Wheel и ZIP reverify вне репозитория прошли на том же host с запретом
импортов benchmarks/ANDES/motulator/pandapower.
Проверка артефакта пересчитывает начальный equilibrium и целостность
траектории, но не интегрирует всю траекторию заново. Устойчивость не доказана.
Артефакты: `reports/warm-flux-final-qualified-2026-11-09`,
`reports/warm-flux-final-user-case-2026-11-09`.

**R02 целиком открыт:** перенос произвольных послеаварийных состояний и целых
сетей, thermal/core-loss equilibrium, расширение независимых публичных
моторных семейств, исследование
неопределённости вне конечного дизайна, внешняя квалификация полного
workflow и расширенные генераторные критерии остаются.


Объединить доступные multi-bus/transformer/generator-backed RMS и restart
исследования в единый пользовательский workflow. Для существующих full-winding/
phase профилей добавить явный перенос паспортов/начальных условий, отчёт
о потерянных свойствах и отдельные инженерные критерии. Не склеивать профили
с разными физическими предположениями молча. Обогатить каталог независимыми
публичными двигателями/механизмами; вариации одного паспорта не считать новыми
семействами. Проверять идентифицируемость, исходные режимы и неопределённость
параметров. Done: исследование от схемы до заключения без внутренних Python-
правок, каждый вывод имеет область применимости и проверенный reference.
Это не требование иметь своё силовое оборудование: допустимы опубликованные
измерения и открытые эталонные реализации с явной маркировкой происхождения.

**R03 — измеренное масштабирование полного расчёта и rollout.**

**R03.9 — распределённый P/Q-профиль подключён к CPF и envelope, 2026-11-09.**

Интеграционный долг R03.8 закрыт в заявленном статическом профиле.
Существующие `ac-pseudo-arc` и `ac-envelope` принимают `options.dispatch`
с теми же участниками и отдельной фазовой опорой, что `ac-dispatch`.
Требуются `enforce_q_limits=true` и совпадающие `tolerance_pu` dispatcher/CPF;
при значениях по умолчанию достаточно указать участников. Только Pd/Qd могут
меняться между start/target, остальные оборудование, расписания и политика
фиксированы. Без dispatch прежние режимы CPF сохраняются, включая отдельно
объявленное неограниченное Q классического slack.

В общий вектор входят углы, Vm всех узлов, delta в pu и λ. Используется
метрика `||[angles_rad, Vm_pu, delta_pu]||²/(2n) + dλ²`. Построены аналитические
разреженные блоки и столбец производной по λ с учётом действующих Q-проекций.
Обычный статический dispatcher даёт начальное состояние; затем шаги, касательные,
откаты, точная цель и envelope используют одну систему уравнений. Snapshot,
turn refinement и ZIP сохраняют delta, участие, режимы P/Q и фазовую опору.
Аудитор проверяет распределение каждого агрегата и баланс всех узлов, включая
прежний slack: физический смысл нового профиля не подменяется старой проверкой
фиксированных Pg и неограниченного slack.

P-переходы отдельно содержат generator_index, before/after и уточнённый
интервал со снапшотами; Q-переходы сохраняют bus_id. Поддерживаются насыщение,
возврат и остановка `participating_p_limits_active` на проверенной точке,
если все производные активного участия исчерпаны. Это локальная причина
остановки, не глобальное исключение других решений. На вырожденной начальной
ветви без свободного участия возможен явный `initial_tangent_failed`.
Нулевые диапазоны P/Q отмечаются `fixed` и не создают ложных переключений;
для нулевой ширины Q-проекции исправлена постоянная производная, в том числе
в прежнем projected-PV профиле. Проверены конечными разностями оба типа.

Envelope использует те же распределённые состояния при уточнении напряжений,
МВА и pu-токов. `timeline_events` ссылается на P-, Q- и сетевые события;
`timeline_groups` упорядочивает их по обходу ветви и группирует перекрывающиеся
интервалы. `timeline_complete` относится только к обнаруженным событиям
сохранённой ветви, не к отсутствию скрытых пересечений. Точная цель, завершение
мониторинга, соблюдение ограничений и полнота уточнённого timeline — отдельные
признаки. Отмена проверяется также в уточнениях и перед публикацией точной цели;
отмена последнего перехода не превращается в успешный итоговый режим.

Примеры:
`cases/ac-pseudo-arc/distributed-participation.json` — чистый CPF;
`cases/ac-dispatch-arc/analytic-entry.json`, `analytic-release.json` и
`case14.json`, `case30.json`, `case118.json` — общий envelope.
Публичные корпуса используют диапазон нагрузки 0.9→1.1, исходные пределы
оборудования и ранее объявленную авторскую политику весов. Токовые пороги —
демонстрационные 1.05 от независимого распределённого исходного тока;
это не паспортные амперы. Ограничения и принятые точки могут включать последнее
превышение λ=1; точная цель проверяется отдельно.

Приёмка: 5 сценариев × 2 шага (0.04/0.02), 519 сопоставленных состояний,
52 локализованных события (6 P, 16 Q, 30 сетевых). Проверены все принятые точки,
оба конца каждого P/Q/сетевого интервала и точная цель. Эталон использует
оригинальные PP 3.5.6 Ybus/Yf/Yt, отдельный residual той же заявленной политики
и SciPy hybr с численным Якобианом, холодное приближение на каждом новом λ;
совпадающие λ могут использовать кэш эталонного ответа. Наши состояния,
касательные, Якобиан и контроллер продолжения в эталон не передаются.
Независимый CPF/классификация ветви другим пакетом не заявляются.

Максимальные расхождения: Vm 4.865e-10 pu, delta 1.507e-8 MW.
Изменение λ событий при уменьшении шага ≤1.784e-8 при допуске 2e-6;
это наблюдение согласованности, не доказательство порядка сходимости.
Все 10 точных целей достигнуты; 4 удовлетворяют только включённым ограничениям,
6 нарушают хотя бы одно. Положительный флаг решателя не скрывает эти нарушения.
`reports/ac-dispatch-arc-final-qualified-2026-11-09/summary.json`.

Поставка R03.9: `reports/ac-dispatch-arc-delivery-2026-11-09/delivery.json`.
Приёмка сохранённых результатов проверена отдельно. Установленный wheel вне
checkout выполнил три общих envelope (включая IEEE118), чистый CPF и ZIP-перенос;
импорты benchmarks/pandapower/andes/motulator были заблокированы. Проверены
обе пары CLI-команд. Это тот же host и venv с системными зависимостями,
не независимая ОС и не повтор полной release-квалификации.


260 целевых тестов прошли без ошибок/пропусков: точный аналитический момент
P-насыщения и возврата, Jacobian/λ при базах 1/100 MVA, совместный envelope,
переопределение фазовой опоры, нулевые диапазоны, исчерпание участия,
отмена на последнем событии, бюджеты, семантические подмены, CLI и ZIP.
Полный release-профиль и все исторические внешние benchmarks не повторялись;
исторические receipt относятся к своим fingerprint.

Далее: расширение измеренной размерности именно объединённого профиля,
сопоставимые длительные/тяжёлые кампании, затем политика островов и восстановления
соединения. Не объявлены автоматическая смена топологии, первичная динамика
частоты/AGC, термическая динамика рейтингов, полная глобальная ветвь или
универсальное превосходство над другими моделерами.


**R03.8 — ограниченная распределённая балансировка AC, 2026-11-09.**

Реализован самостоятельный профиль `ac-dispatch` / `verify-ac-dispatch`.
Он отделяет опорный фазовый угол от источника балансировки мощности:
`Pg_i = clip(Pschedule_i + alpha_i * delta, Pmin_i, Pmax_i)`.
Все Pg, включая прежний slack, должны быть заданы явно и лежать в пределах;
положительные веса участников нормируются. После достижения P-предела
оставшиеся участники принимают остаток через общую delta. Неучаствующие
генераторы сохраняют расписание; потери сети входят в общий баланс.
Это статическая политика, без экономической диспетчеризации и без динамики
частоты, времени AGC или первичного регулирования.

P-уравнения включают все узлы. Неизвестные: углы кроме одного, Vm всех узлов
и delta (2n). Разреженный аналитический блочный Якобиан включает производную
P-участия по delta с учётом насыщения. Q/voltage-проекция действует на всех
исходных PV и прежнем slack: при Q-пределе напряжение освобождается, при
восстановлении условий регулирование возвращается. Фиксирование угла не
создаёт виртуального неограниченного источника P/Q. Опорный угол можно явно
перенести на любой существующий узел, включая PQ; физическое решение после
выравнивания фазы сохраняется. Классические метки типов входного case не
переписываются: новая семантика заявлена отдельным профилем и собственным аудитом.
Автоматическая обработка островов и коммутации не выполняется.

Newton использует damping, области Vm, лимит итераций, кооперативный deadline
и отмену. Классический PF служит только начальным приближением, не допустимым
ответом нового профиля. Исчерпание активных производных участия даёт
`participating_p_limits_active`, не успешный режим и не глобальное доказательство
отсутствия решения. Неудачные расчёты не публикуют итоговый operating point.
Для успеха проверяются уравнения распределения и Q-проекции, мощности каждого
агрегата, измерения ветвей и заново собранный баланс. Отдельно выдаются остатки
регулировочного диапазона P и соблюдение всех ограничений обычного audit.
Newton-журнал/время не воспроизводятся, SHA не является подписью происхождения.

Корпус `cases/ac-dispatch/`: IEEE14/30/118 при 90/100/110% исходных Pd/Qd,
ограничение P, ограничение Q прежнего slack и переопределение опорного угла.
Расписания и пределы публичных вариантов не расширялись; веса по диапазонам P —
явно авторская политика исследования, не паспортный droop. Все 12 случаев
сошлись и сопоставлены с отдельным эталоном: оригинальные PP 3.5.6 makeYbus/Yf/Yt,
независимо написанный residual той же заявленной математической политики,
SciPy hybr с численным Якобианом и холодное приближение. Основной residual,
Якобиан, allocation и рассчитанные состояния в эталон не передаются.
Это не встроенный ограниченный distributed_slack pandapower. Отдельный тест
неограниченного случая сопоставлен с реальным runpp(distributed_slack=True).

Максимальные ошибки корпуса: Vm 1.070e-11 pu, углы 1.447e-11 rad,
терминальные P/Q 6.307e-9 MVA, Pg 2.007e-9 MW, Q по узлам 9.470e-9 Мвар.
Независимый checker суммирует терминальные токи напрямую. Исходники и входы
закреплены SHA, сохранённая приёмка проверена отдельно:
`reports/ac-dispatch-qualified-2026-11-09/summary.json`.

Поставка R03.8: `reports/ac-dispatch-delivery-2026-11-09/delivery.json`.
Установленный wheel вне checkout выполнил четыре новых расчёта, включая IEEE118,
и ZIP-перенос с запретом импортов benchmarks/pandapower/andes/motulator;
CLI также проверен. Это тот же host и venv с системными зависимостями,
не независимая ОС. Реальный runpp в неограниченном тесте — отдельная проверка;
benchmark 12 ограниченных случаев использовал PP-stamps + независимый SciPy root.


236 целевых тестов прошли без ошибок и пропусков. Покрыты оба P-предела,
оба Q-предела прежнего slack, возврат Q-регулирования, потери, несколько
агрегатов, участник без диапазона P, фиксированные неучастники, 1 узел,
PQ-фазовая опора, перестановка оборудования, отказ островной схемы,
бюджеты, отмена, производные, независимость эталона, семантические подмены,
CLI/Schema и ZIP. Полный release-профиль и старые внешние benchmarks не
повторялись; исторические receipt относятся к прежним производителям.

**Интеграция R03.8 завершена в R03.9:** через `options.dispatch` профиль
подключён к `ac-pseudo-arc` и `ac-envelope`, с событиями насыщения/возврата P
и единым envelope. Статический R03.8 остаётся отдельным доступным workflow.
Не заявлены произвольная автоматика
переключения slack, island/reconnection, динамическая устойчивость или глобальное
преимущество над другими пакетами.

Методический контекст распределённого активного баланса:
[pandapower 3.5.6 slack_weight](https://pandapower.readthedocs.io/en/v3.5.6/elements/ext_grid.html).
Ограниченная P/Q-политика выше — собственный явно заданный профиль, не перенос
поведения OPF и не утверждение о полном совпадении политик разных решателей.


**R03.7 — наблюдаемые пределы напряжения, мощности и тока на AC-ветви, 2026-11-09.**

Добавлен `power-grid ac-envelope --input INPUT --out NEW_DIRECTORY` и
`power-grid verify-ac-envelope DIRECTORY`. Обёртка использует существующий
pseudo-arclength, включая проектируемые Q-ограничения и возврат регуляторов.
В артефакте раздельно сохраняются исходный trace и инженерный envelope.
Достижение целевого режима и соблюдение проверяемых ограничений — разные поля:
`target_reached` и `target_within_monitored_limits`. Код 0 означает завершённый
расчёт/мониторинг; даже тогда целевой режим может нарушать пределы.

Проверяются Vmin/Vmax каждого узла и `rate_a` в МВА отдельно на обоих концах
включённой ветви. Нулевой rate_a означает отсутствие этого ограничения.
Токи задаются отдельно: `monitor.current_limits` с `branch_index`,
`terminal: from|to`, `maximum_pu`. Они вычисляются из полных комплексных
терминальных уравнений с R/X/B, tap и phase shift. Без номинальных напряжений
канонической модели значения в амперах не выводятся. МВА не подменяются
токовым пределом; это статические ограничения сбалансированного AC-профиля,
не динамика нагрева, токи КЗ или аппаратная защита.

Для обнаруженной смены знака запаса уточняются два решения на общей
предикторной гиперплоскости. По умолчанию интервал ≤1e-7 по координате шага,
максимум 24 уточнения, 4096 corrector-solves, 256 событий и 120 s мониторинга.
Wall deadline и отмена кооперативные, с проверками между операциями; это не
жёсткое прерывание внутреннего SciPy-вызова. Незавершённое уточнение сохраняет
интервал и причину отказа, без фиктивного успешного заключения.

Отчёт включает исходные нарушения, наихудшие наблюдённые запасы, вход/выход
из ограничений, точный целевой режим и группы событий в порядке обхода ветви.
Перекрывающиеся уточнённые интервалы не получают произвольного строгого порядка.
Первая группа обнаруженного выхода публикуется только после завершения всего
мониторинга. Проверка воспроизводит физические состояния, терминальные измерения,
остатки уравнений, гиперплоскости, журналы бисекции, интервалы и бюджет попыток.
SHA подтверждает целостность, а не подлинность происхождения.

Область отчёта — принятые точки расчётной ветви, включая возможное последнее
превышение λ=1; точная цель проверяется отдельно. Одноимённые допустимые состояния
на концах шага не исключают скрытых пересечений внутри него. Касания без смены
знака и исчерпывающий поиск всех экстремумов не заявляются. Допуск наблюдаемых
нарушений по умолчанию 1e-8 pu; он также подавляет смены знака целиком в численном
околопороговом диапазоне. Запас МВА нормируется на base_mva. Поле «within»
относится только к включённым ограничениям, не ко всем инженерным требованиям.

`cases/ac-envelope/`: аналитические выход/возврат и IEEE14/30/118 с +20% Pd/Qd.
Авторские демонстрационные токовые пороги закреплены заранее: 1.05 от независимого
исходного тока первой выбранной ветви. Это не паспортные амперные пределы
реального оборудования. Данные сети и происхождение берутся из замороженного
корпуса R03.6. Независимый benchmark использует оригинальный pandapower 3.5.6
с отдельным active-set возвратом, flat start на каждой точке, а ток вычисляет
из независимых терминальных P/Q и Vm. Наши токовые stamps, состояния и
Якобианы в эталонный решатель не передаются. Повторяются шаги 0.04/0.02;
сравниваются принятые точки, оба конца каждого уточнённого события и точная цель.

209 целевых тестов прошли без ошибок и пропусков. Включены аналитические
моменты верхнего/нижнего напряжения, различия МВА и тока, вход/выход, совместные
Q-переходы, группировка одновременных событий, перестановки, бюджеты,
неудачный corrector, отмена, подмены артефактов, CLI/Schema и ZIP.
Полный release-профиль не запускался. Исторические приёмки имеют собственные
fingerprint и не означают повтор всех benchmarks на нынешнем производителе.
Приёмка: `reports/ac-envelope-qualified-2026-11-09/summary.json`.

Итог R03.7: 6 ветвей, 292 независимо сопоставленных состояния (включая 48
состояний концов 24 обнаруженных пересечений), все интервалы локализованы.
Максимум расхождения Vm 1.30145e-11 pu, нормированных запасов 6.51284e-11 pu;
изменение оценок λ при делении шага пополам не превышает 5.017e-8.
Все точные цели достигнуты, но нарушают хотя бы один включённый предел:
результат не маскирует нарушения успешным флагом решателя.
Проверка сохранённой приёмки выполнена отдельно. Wheel вне checkout прошёл
три новых расчёта (включая IEEE14), проверку ZIP и CLI с заблокированными
импортами benchmarks/pandapower/andes/motulator. Это тот же host, venv с
системными зависимостями; независимая ОС не заявляется.
Поставка: `reports/ac-envelope-delivery-2026-11-09/delivery.json`.


Дальше: ограничение/смена slack с явной политикой перераспределения P/Q,
расширение тяжёлых сетевых корпусов и кампании граничных инженерных сценариев.
Нынешний монитор не изменяет топологию и не выдаёт доказательств устойчивости,
глобальной допустимости или исключения всех других решений.

Семантика рейтингов и терминальных уравнений:
[MATPOWER Data File Format](https://matpower.app/manual/matpower/DataFileFormat.html),
[MATPOWER Branches](https://matpower.app/manual/matpower/Branches.html).
MATPOWER не запускался; ссылки являются методическими, не сравнительным прогоном.


**R03.6 — продолжение ветвей с реактивными ограничениями, 2026-11-09.**

Добавлен профиль `options.enforce_q_limits=true` в существующий
`ac-pseudo-arc`. Неизвестные теперь включают Vm всех нереференсных узлов;
размер системы не меняется при достижении ограничения. Для каждого PV-узла
используется проектируемое уравнение (Q генерации в pu):
`Q - clip(Q - (Vm - Vset), Qmin, Qmax) = 0`.
Внутри диапазона оно удерживает Vset; на верхнем пределе допускает Vm≤Vset,
на нижнем Vm≥Vset. При восстановлении условий автоматически возвращается
регулирование напряжения. Используются аналитические разреженные блочные
производные выбранного активного режима; на изломе выбирается обобщённая
производная. Q нескольких агрегатов одного PV-узла ограничивается суммарно.
Slack сохраняет напряжение и балансирует Q без ограничения: смена slack,
P-ограничения и удалённое регулирование в этот профиль не входят.

Обнаруженные изменения режимов уточняются бисекцией в координате
предикторной гиперплоскости до интервала 1e-7, максимум 20 уточнений.
Сохраняются оба электрических состояния и журнал уточнений. Ограниченный
бюджет оставляет явный `localized=false`; скрытые повторные переключения
между одинаковыми режимами на концах шага не объявляются исключёнными.
Прямой переход между противоположными пределами отклоняется с уменьшением
шага, чтобы сохранить промежуточный режим регулирования. Одновременные
ограничители обрабатываются общей системой, без последовательного изменения
списка неизвестных. Проверяющий заново вычисляет режимы, уравнения,
касательные, балансы, гиперплоскости и интервалы переходов; поддерживается
точный целевой режим, остановка по бюджетам и отмена.

Публичный корпус: `cases/ac-reactive-arc/` — верхний/нижний предел,
возврат и смена противоположных пределов; заморожены IEEE14/30/118 из
pandapower 3.5.6 с происхождением и SHA. Приёмка масштабирует Pd/Qd до +20%
при фиксированных Pg и Vset, сравнивает каждую принятую точку и точную цель,
повторяет шаги 0.04/0.02 и проверяет согласование λ переходов до 2e-6.
Независимый эталон использует оригинальный Newton/stamping pandapower,
flat-start для каждого решения и отдельный ограниченный active-set контроллер
с возвратом из Q-ограничения. Он не использует наши состояния, касательные,
Якобиан или контроллер продолжения; это отдельная обёртка проекта над runpp,
а не встроенный CPF pandapower/MATPOWER.

Сохранён отрицательный пример IEEE118: однонаправленный стандартный
`runpp(enforce_q_lims=True)` и политика возврата могут давать разные состояния
регулятора при корректном AC-балансе. На выбранной точке остаток проектируемых
уравнений у первого 6.46e-5 pu, у независимого расчёта с возвратом 5.87e-11 pu.
Это различие политик управления, не утверждение об общей ошибочности pandapower.
Артефакт: `reports/reactive-arc-policy-counterexample-2026-11-09/`.

182 целевых теста прошли без пропусков, включая производные в трёх режимах,
оба ограничения и возвраты, аналитический момент перехода при двух шагах,
несколько агрегатов, одновременные ограничения, перестановки, откаты,
повреждение журналов и перенос проверяемых пакетов. Полный release-профиль
в этой итерации не выполнялся. Старые receipt относятся к своим версиям
производителя; их нельзя выдавать за повторную квалификацию текущего кода.
Приёмка: `reports/reactive-arc-qualified-2026-11-09/summary.json`.

Следующая очередь: события пределов напряжения и токов/потоков ветвей,
расширение корпуса тяжёлых Q-ограниченных схем и отдельная политика
ограничения/смены slack. Динамическая устойчивость, глобальная полнота решений
и универсальное превосходство над другими пакетами не заявляются.

Семантика стандартных Q-limit событий описана в
[MATPOWER: Event Detection and Location](https://matpower.app/manual/matpower/EventDetectionandLocation.html).
Проектируемая формулировка и возврат выше — явно выбранная политика этого профиля.


**R03.5 — pseudo-arclength continuation, 2026-11-09.**

Реализованы разреженный tangent predictor, Newton corrector с гиперплоскостью,
адаптация шага, откат неуспешных попыток, бюджеты и уточнение поворота ветви.
Метрика координат: ||dx||²/n + dλ²; углы в радианах, напряжения в pu.
Профиль фиксирует PQ/PV и меняет Pd/Qd; переключение по Q-ограничениям не
поддерживается. Действует прежняя граница AC API 2048 узла.

Двухузловой аналитический эталон с точным λ=2/3 проходит, включая нижнюю
ветвь. На RTE1888 независимые электрические уравнения и производные pandapower
3.5.6 подтвердили ветви и оценки поворота при двух шагах. Контроллер продолжения,
координаты и представление результата общие; независимый MATPOWER runcpf не
запускался. Для направления −10% оценка λ≈0.665858414, для +10% λ≈0.565797690;
согласование при делении шага пополам лучше 1e-9. Это наблюдения локальной ветви,
не сертифицированные пределы нагрузки, доказательство отсутствия других решений,
динамической устойчивости или коллапса. Исходные цели ±10% остаются открыты;
точные цели ±5% достигнуты и независимо сопоставлены.

CLI: `power-grid ac-pseudo-arc --input INPUT --out NEW_DIRECTORY` и
`power-grid verify-ac-pseudo-arc DIRECTORY`. Частичная ветвь сохраняется с
явной причиной остановки (код 2); успех требует точного целевого режима.
Проверяются электрические состояния, касательные, гиперплоскости, журнал
откатов и уточнение поворота; SHA-манифест не является цифровой подписью.
Пример: `cases/ac-pseudo-arc/two-bus-fold.json`.

Приёмка: `reports/pseudo-arc-final-qualified-2026-11-09/summary.json`;
155 целевых тестов прошли, полный release-профиль в этой итерации не запускался.
Следующий крупный шаг — переключение Q-ограничений и событий внутри continuation,
затем независимая проверка этого изменяемого состава уравнений.

Методическая опора: [MATPOWER continuation power flow](https://matpower.app/manual/matpower/ContinuationPowerFlow.html)
и [predictor](https://matpower.app/manual/matpower/Predictor.html).


**R03.4: bounded load continuation реализован; RTE ±10% ещё открыты.**
Новый `ac_continuation` с sparse Newton, проверенным исходным режимом,
адаптивным шагом, rollback, отдельной проверкой баланса/расписаний/control
setpoints, бюджетами и кооперативным deadline. Точный target или incomplete,
никакого округления частичного пути в успех. CLI + schema + provenance +
sealed journal/verify/ZIP. Исходные frozen RTE inputs теперь tracked в
`cases/ac-continuation/reference-inputs`, запуск без ignored old reports.
Matched controller runs с отдельными Newton/state histories (наш и PP3.5.6):
95%/105% RTE1888 достигнуты, matched V/angle/flows и отдельный terminal-current
balance у всех принятых шагов. 90%/110% не достигнуты обоими, minimum_step;
старые два open points НЕ закрыты, global/all-target qualification false.
Native progress ~.66578 к 90% и ~.56579 к 110% — только последние accepted
точки этой траектории, не доказанные границы устойчивости/существования.
Shared DC seed и shared continuation controller не являются независимой
инициализацией или independent controller proof. Отдельный physics checker
и независимые Newton backend отмечены по своим ролям.
134 проверки прошли; полный release suite не запускался.
Итог `reports/ac-continuation-qualified-subsets-2026-11-09`: verified=true,
qualified_load_factors=[0.95,1.05], all_targets_qualified=false. Установленный
wheel из `/tmp` с запрещёнными benchmark/PP/ANDES/motulator импортами
проверил completed и incomplete журналы, ZIP roundtrip и заново решил
RTE +5%; это тот же host, не независимая поставка.
Далее predictor/
corrector и pseudo-arclength CPF для этих ветвей; Q-limit/batch/rollout
и полная конкурентная матрица остаются отдельными задачами.



**R03.3: крупный sparse PF API и частично пройденный нагрузочный корпус.**
API sparse/auto до 2048 узлов; dense PF/OPF/audit остаются 512,
estimation 128. Граничная цепь 2048 прошла независимый terminal-current
balance; это synthetic, не реальная дополнительная сеть.
Крупный корпус 7 позиций: PEGASE1354 ±10%/nominal flat, RTE1888 ±10%/nominal
с общим предварительно вычисленным DC seed и отрицательный RTE flat.
Четыре converged режима прошли независимую сверку; RTE ±10% не сходятся
у обоих и остаются открытыми; negative flat подтверждён. Общий corpus
passed=false, не считать закрытым! Shared DC seed не доказывает independent
end-to-end initialization. На больших точках public API пока медленнее PP:
PEGASE nominal 45.20/20.04 ms, RTE nominal 63.35/30.66 ms.
Исправлена подготовка PF reference inputs: pinned public exporter выбирал
OPF и принудительно Q-limit policy; явный PF/Q-disabled rebuild убрал
mismatch fixed sgen Q без ослабления допусков. 112 разных проверок прошли,
полный release suite не запускался.
Артефакт `reports/large-ac-final-assessment-2026-11-09`; recompute verification
возвращает verified=true и corpus_passed=false с двумя open IDs.
Установленный wheel проверил оба крупных номинальных случая из `/tmp` с
запрещёнными импортами benchmark/reference пакетов; это тот же host.
Далее bounded continuation/initialization и повтор тех же RTE stress точек,
с сохранением исходных отказов; потом широкие Q-limit/batch/rollout профили.



**R03.2: sparse AC реализован и проверен.**
CSC Ybus/аналитические sparse derivatives/Jacobian, SuperLU; sparse audit
также без dense Ybus. Auto до 128 узлов dense, выше sparse; выбор измерен
на пилоте, не оптимален доказанно для всех сетей. Явные dense/sparse доступны.
Balanced AC лимит 512 сохранён. Оптимизированы общие membership,
агрегация генерации и branch postprocessing; семантика ограничений сохранена.
Benchmark v2: auto/dense/sparse/pandapower, отдельные процессы, matched
accuracy и повторяемые public-API latency. Все 4 схемы × 3 наших режима
прошли сверку против PP и независимый balance checker.
На IEEE300: dense 23.091 ms, auto(sparse) 11.306 ms, явный sparse 11.958 ms,
PP 10.625 ms. Меньшие случаи auto оставляет dense; PP на 300 в этом прогоне
по-прежнему быстрее. Полный RSS и matrix storage не смешиваются:
Jacobian 280900→3736 stored values, 2247200→46956 bytes.
102 разных теста прошли; полный release suite не запускался.
Артефакты `reports/sparse-ac-qualified-2026-11-09` и
`reports/sparse-ac-profile-2026-11-09`. Старые fingerprint-bound receipts
сохраняют историческую область. Следом — независимые новые размеры/состояния,
Q-limit/failure cases и batch/end-to-end workflow; R03 целиком открыт.



**R01.2 / R03.1, 9 ноября 2026: выполнен внешний IEEE AC pilot.**
`benchmarks.ieee_ac_comparison` сравнивает собственный AC PF с pandapower
3.5.6 на IEEE 14/30/118/300 packaged variants. Все V/angle/branch-PQ
и независимый узловой баланс прошли. Фиксируются входы и source hashes,
совпадают фактические Newton pu-допуски; numba включена у эталона.
В отдельном процессе/CPU affinity, после 2 warmups и 5 repeats public API
медианы (наш/PP, ms): 0.703/7.182; 1.157/8.059; 5.734/8.178; 26.498/10.224.
На 300 узлах медленнее — следующий кандидат на profiling/sparse AC.
Это наблюдение локального public API, не kernel/whole-agent/всемирный рейтинг;
CPU turbo/governor/внешняя host нагрузка не контролируются. VmHWM послеexec
отделён от inherited rusage peak. 89 тестов прошли, полный release suite
не запускался. Recompute verification проверяет coverage/physics/медианы,
не повторяет solver runs и не аутентифицирует источник замеров.
Корпус: `reports/ieee-ac-final-qualified-2026-11-09`.
Полная сравнительная матрица по 6 направлениям и ссылки на внешние стандарты
в `docs/COMPARISON_PROTOCOL_RU.md`. PGM/LightSim/GPU и новые AI policy runs
не выполнены; мотulator 0.19.0 остаётся pin, актуальный 0.23.1 требует
отдельного adapter/qualification. R01/R03 целиком открыты.


Повторить исторические большие случаи на выбранном release snapshot; расширять
размеры/длительности только после успешных измерений. Разделять cold/warm start,
initialization, solve, event processing, journal/export и agent inference.
Сохранять wall/CPU, peak RSS, nnz, RHS/Newton/rejected steps, failures и точность.
Проводить timing последовательно на одном host, при одинаковых threads и
физике; отдельно измерять process/batch throughput. Для повторов показывать n,
median и разброс; маленькое n не выдавать за надёжный p95/SLA.
Done: воспроизводимая матрица точность–время–память, подтверждённое преимущество
на конкретном workflow либо честная запись отсутствия выигрыша.

**R04 — motor LLM tasks и multimodal данные на проверенной физике.**
Генерировать задачи о пуске, самозапуске, перегрузке, защите и выборе допустимых
действий. Учитель и reward получают gold только на стороне verifier.
В prompt — схема/осциллограммы/паспорта/наблюдения, доступные на момент решения.
Все views/crops/augmentations и RMS/full-winding варианты одного физического
случая остаются в одном split. Holdout — по топологии, семейству машины,
механизму и типу возмущения; параметрический RNG split недостаточен.
Done: replay SFT-траекторий, проверенные GRPO-кандидаты, отрицательные случаи,
resource budgets и явный запрет будущих/привилегированных данных.

**R05 — Qwen3.5-4B VLM и оценка настоящего агента.**
Conditional-generation loader + AutoProcessor, сохранение image tensors/grids,
проверка label masks, image-token/context budgets и gradient/memory preflight.
После этого baseline → bounded SFT → GRPO; native multi-turn отдельно от full-plan.
Тесты зрения: matched/blank/wrong image и text-only при сопоставимых budgets.
Мерить task success, false-safe, feasibility/restoration, tool calls, simulator
failures, tokens и wall time; несколько seed и неизменный sealed holdout.
Done: воспроизводимый выигрыш над исходным Qwen и scripted baseline на новых
случаях. CPU whole-message bandit не засчитывается как инженерный результат.

**R06 — расширение по результатам пробелов, а не числа модулей.**
Следующие кандидаты: несимметричный разгон/negative-sequence heating,
пусковые устройства с реальными уравнениями, converter faults/current limiting,
более полная коммутация и сетевой EMT, перенос моделей и round-trip результатов.
Для каждого — свой profile, опубликованные уравнения/паспорта, reference,
предельные случаи и ресурсные измерения. Наличие GFM/V/f порта не равно
произвольному inverter plant; наличие phase RLC не равно полной библиотеке PSCAD.

Порядок: **R01 → R02**, R03 параллельно на закреплённых snapshot; затем
R04 → R05. R06 открывается конкретными пробелами исследования. Внешний GitHub,
CI, собственное силовое оборудование и экономическая часть не требуются.
Разрешённая формулировка будущего преимущества: «лучше X по метрике Y на
корпусе Z, версиях V и бюджете B». Общий процент готовности/ранг не назначается.

## Текущий этап: обучение LLM на физических задачах

- [x] L01 — процедурные restoration-задачи и разделение по неизоморфным
  топологиям; варианты с доступным, перегруженным и заблокированным резервом.
- [x] L02 — SFT полных планов, действий и tool-диалогов с фактическими ответами;
  публичный экспорт, приватный verifier, хеши и семантический replay.
- [x] L03 — физическая награда и GRPO-группы, отдельный отказ при сбое решателя,
  TRL callback сырых наград, ограниченный интерактивный tool-runner.
- [x] L04 — offline local SFT/GRPO и полная dev/test-оценка checkpoint;
  проверка контекстного бюджета, веса/tokenizer/version receipts.
- [x] L05 — обязательная регрессия 1543/0/0, CPU SFT/GRPO optimizer smoke и
  wheel-поставка производителя этого этапа. 2082 теста вне профиля не выполнялись.
- [ ] L06 — LLM-задачи пуска/самозапуска двигателей, частичная телеметрия,
  native multi-turn GRPO, большой topology holdout и качественный реальный LLM.

- [x] L06.1 — пакет motor-engineering: shaft energy, I²t, тепловое
  интегральное уравнение и согласованность трёх сеток. Независимая приёмка прошла
  на 8 сценариях (6 DAE и 2 Radau, по 3 сетки), включая 3 отрицательных
  инженерных исхода. 5 допустимы; затяжной пуск, тепловое отключение и поздний
  самозапуск остаются недопустимыми. 47 новых тестов; обязательный профиль
  **1590/0/0**, ещё 2082 вне профиля не выполнялись.
  Wheel вне репозитория воспроизвёл все 8 итоговых решений и ZIP-перенос
  с системными зависимостями. Крупная тепловая сетка 10/5/2.5 ms отвергнута
  на текущем производителе, а 1/0.5/0.25 ms прошла неизменный критерий.
  Максимальные SI-расхождения: speed 1.21e-9 pu, current 3.50e-8 pu,
  voltage 3.05e-10 pu, I²t relative 1.27e-9; это software-reference проверки,
  не натурная точность. Производитель:
  `1d5d35c38cd8c34c3d9cd729a9becf92205a13820a955c5cf3c8d757d540825f`.
  Поставка: `reports/motor-engineering-delivery-2026-11-08/delivery.json`.
  Точный тестовый snapshot сохранён до итоговых заметок; Python после тестов
  не менялся. Это база для motor LLM tasks; VLM-датасет и обучение ещё открыты.
Описание: [инженерный пакет](../docs/MOTOR_ENGINEERING_RU.md).

Целевая модель L06 — **Qwen3.5-4B VLM**, подтверждён полный BF16 checkpoint
`/home/user/workspace/cad-env/artifacts/models/qwen35-4b`. Проверены оба SHA256,
индекс/заголовки safetensors и реальный image processor (80 image tokens).
Текущий текстовый адаптер не совместим с этой VLM; мультимодальный loader,
датасет и обучение остаются открыты. GPU в текущей сессии недоступен.
Фиксация: `requirements/qwen35-local-target.json`, требования в руководстве
SFT/GRPO. Исходные веса/окружение cad-env не изменялись.

Профиль L01–L04: синтетический квазистационарный feeder restoration, а не
универсальный генератор всех исследований. Реализованный путь обучения не
означает обученную инженерную модель или доказанное превосходство. Подробности:
[SFT/GRPO](../docs/LLM_TRAINING_RU.md).

Приёмка L01–L05 (8 ноября 2026): 24 синтетические задачи, 12/6/6 train/dev/test;
публичные планы, 152 step-примера и 176 tool-примеров по всем split. У всех 24
выбранных демонстраций допустимые физические результаты; полное восстановление
возможно в 8 задачах, остальные содержат намеренно ограниченный/запертый резерв.
Повторная генерация побайтно совпала; проверены публичный ZIP-перенос и
семантический отказ подменённым журналам/наблюдениям.

Настоящий CPU smoke: SFT 2 шага, GRPO 8 шагов; в обоих меняются веса.
У GRPO 6 информативных групп и 6 ненулевых gradient steps. Whole-message GPT2
здесь лишь технический bandit; все 6 test-задач оценены, средняя награда −1.
Это проверка optimizer/verifier wiring, не обученный инженерный LLM.
Использован изолированный symlink-view установленных Python-библиотек без
несовместимых необязательных GPU-пакетов; чистая ОС/полный dependency lock
не заявляются. Wheel вне репозитория прошёл generate/verify/export/score-group
с системными зависимостями. Сборка, данные и проверки выполнены локально.

Производитель L01–L05:
`e476dd675eefa6114bcadd1fd90dd1a566c6e0d3f24ac824b46a32dab852db62`.
Данные: `reports/llm-data-qualified-2026-11-08/summary.json`.
CPU: `reports/llm-cpu-qualified-2026-11-08/smoke.json`.
Полная поставка: `reports/llm-training-delivery-2026-11-08/delivery.json`.
Точный тестовый snapshot сохранён до обновления этих итоговых заметок;
Python-реализация между регрессией и фиксацией результата не менялась.

Этот файл — основной список текущих работ по согласованным 12 блокам.
Нумерация сохраняется между этапами. Подробные исторические планы и численные
свидетельства остаются в [industrial-readiness.yaml](industrial-readiness.yaml),
[описании блоков 1–5](../docs/DAE_AND_DOL_BLOCKS_RU.md) и
[описании оборудования и автоматики](../docs/EQUIPMENT_AUTOMATION_VALIDATION_RU.md).

## Как ведём работу

- `[x]` означает выполненную работу в явно описанном профиле, `[ ]` — открытую.
- Реализация, независимая сверка и квалификация поставки учитываются отдельно.
  Выполненный пример не закрывает все возможные схемы и режимы блока.
- Перед реализацией выбираем конкретную открытую задачу с ID ниже. После неё
  отмечаем результат, ограничения и путь к проверке в этом же файле.
- Закрываем задачу после реализации и прохождения её критериев, без обязательных
  пропусков/xfail. Указываем точные входы, версию эталона и идентичность исходников.
- Счётчики ниже относятся к последнему зафиксированному прогону. После изменения
  кода старые отчёты остаются историческими и не квалифицируют новую версию.
- Для численной валидации используем опубликованные данные, независимые уравнения
  и реализации. Собственное оборудование не является обязательным условием.

## Историческая очередь B01–B20

Последний завершённый этап этой исторической очереди — B20: независимая DC-опора Rs, три условных
raw-аудита и проверенный перенос потерь locked-rotor при шуме U=0.01 V.
Это новый протокол с дополнительной информацией, а не перенос успеха на
исходный подбор без опоры. Отчёт:
`reports/anchor-identification-delivery-2026-11-08/delivery.json`.
Предыдущий B19: `reports/sampled-identification-delivery-2026-11-08/delivery.json`.
Предыдущий B18: `reports/calibrated-motor-delivery-2026-11-08/delivery.json`.
Предыдущий B17: `reports/noisy-identification-delivery-2026-11-08/delivery.json`.
Предыдущий B16: синхронные CSV-записи, преобразование координат/единиц;
`reports/motor-recordings-delivery-2026-11-08/delivery.json`.
Предыдущий B15 сохраняет собственный отчёт:
`reports/dynamic-identification-final-2026-11-06/delivery.json`.

Обязательные подпункты текущей очереди B01–B20 закрыты в указанных профилях.
Реальные ограничения остаются явно открытыми по применимости: noisy locked-rotor
без независимой опоры не проходит 0.5 W; неизвестные температуры, AC/DC-различия,
общие ошибки датчиков и непрерывные гарантии неопределённости требуют отдельных
постановок. Это не завершение всех возможных видов промышленного моделирования.

**Согласованные остатки B03.3b, B04.1b и B05.3b закрыты в описанных ниже профилях.**

Итог: [приёмка и команды](../docs/REMAINING_BLOCKS_ACCEPTANCE_RU.md), `reports/blocks-final-root-fix-2026-11-06/delivery.json`.

Выполненные подпункты и реальные оставшиеся ограничения перечислены ниже.
Последний полный обязательный прогон B20: **1494 теста без ошибок и пропусков**,
включая 34 проверки DC-опоры, условных контрактов и суммарных лимитов. 2082 теста вне объявленного профиля
не выполнялись и не засчитываются пройденными. Старые release receipt
остаются историческими; этот результат не является внешней или универсальной
промышленной квалификацией.

Реализованы аналитическая автоматика/DAE, многоклеточные эталоны, вихретоковые
потери, идентификация, линейное возбуждение трансформатора/резистивные нагрузки
и фазный common-point LLG. Результаты и хеши сохраняются в
`reports/blocks678-expanded-final-2026-11-06/delivery.json`.

**Указание пользователя от 6 ноября 2026 года: агент не обращается к GitHub,
не публикует изменения и не запускает CI. Авторизация не требуется.**
Внешний EX-04 отменён как обязательный этап, а не засчитан пройденным.
Конфигурации перенесены в `.github/archived-workflows/`; активных workflows нет.

Реализован локальный workflow B09; текущие ограничения описаны в
[инструкции](../docs/ENGINEERING_WORKBENCH_RU.md). B10 реализован в ограниченном двухфидерном QSTS и сбалансированном AC-профиле; далее расширение проверенных размеров/паспортов
и дальнейшая физика по приоритетам пользователя.

## Блоки 1–5: реализованный базовый профиль

### B01. DAE-формулировка и согласованная инициализация

Статус: **реализован в объявленном index-1 RMS-профиле**.

- [x] B01.1. Общий residual динамики и алгебраических напряжений, блочные действия производных.
- [x] B01.2. Согласованная инициализация нагруженных схем и диагностика невозможного режима.

Критерий базового профиля: одновременно выполнены динамические и сетевые
уравнения, ошибки инициализации не превращаются в успешный расчёт.
Произвольные высокоиндексные DAE в этот результат не входят.

### B02. DAE-интегратор и итерационное решение

Статус: **базовый интегратор и ограниченный ресурсный профиль реализованы; длинные исследования учитываются отдельно**.

- [x] B02.1. Неявное интегрирование, управление ошибкой, откат шага, Schur/J·v, GMRES и предобуславливание.
- [x] B02.2. Сверки с Radau и проверки согласованности в объявленном корпусе.
- [x] B02.3. Последовательные DAE/Radau-замеры закреплённых сетей 512/64/16 и 2000/128/32, по 3 повтора на окне 0.25 s, зарегистрированная уставка nominal_1p0; исходная 0.995 сохраняется диагностикой. Отдельные память J·v и lifetime RSS. Время и память — раздельные результаты; ускорение полного интегрирования не предполагается.

Критерий B02.3: опубликованы размеры задач, допуски, повторы, окружение и
потребление ресурсов; преимущество заявляется только для измеренных случаев.
Параллельные диагностические времена не заменяют такого сравнения.

### B03. Общий механизм гибридных событий

Статус: **политика событий, локальные сбросы и численные сквозные производные через инициализацию/автоматику реализованы в проверенном корпусе**.

- [x] B03.1. Обработка совпадающих корней, приоритетов и дискретных действий в проверенных профилях.
- [x] B03.2. Локальное произведение матрицы скачка для одного трансверсального события.
- [x] B03.3. Производные полного исследования через последовательности событий и некоммутирующие каскады.
- [x] B03.3a. Сквозные начальные направления индукционной RMS-сети: аналитические J·v, последовательности заданных коммутаций, тепловой saltation, три независимых шага полных разностных расчётов.
- [x] B03.3b. Пять сквозных параметрических направлений через PV-инициализацию, АВР, контактор и границы SEXS/UEL; локальные аффинные сбросы. Три шага разностей и односторонний аудит; касания/смена последовательности отклоняются. Общий аналитический adjoint не заявляется.

Критерий B03.3: независимая проверка производных и явная диагностика касаний,
смены последовательности событий и случаев, где производная не определена.

### B04. Масштабирование динамических исследований

Статус: **проверены отдельные размеры; произвольный масштаб не квалифицирован**.

- [x] B04.0. Объявленные динамические случаи до 2000 узлов / 128 двигателей / 32 генераторов; разреженный поиск критических мод.
- [x] B04.1. Расширенная матрица длительностей, кольцевых схем, повторных возмущений, островов и совместных ограничителей.
- [x] B04.1a. Дополнительные кольцевые случаи 45 с (внешняя сеть / генератор) и 12 с (64/4/1, 256/8/2, DAE 512/64/16, Radau и DAE 2000/128/32), пуск и возмущения, независимая SI MNA-сверка всех узлов и состояний.
- [x] B04.1b. Четыре 45-секундных случая трёх островов (Radau/DAE, разрешённое и запрещённое соединение) и 30-секундная сеть 512/32/8 с активными PSS/OEL2C/UEL, два первичных интегратора против независимого FMI/CVODE. Время/RSS и ошибки закреплены; произвольный масштаб не квалифицирован.

Критерий B04.1: для каждого сочетания опубликованы точность, время, память,
длительность и способ независимой проверки. Предел API не равен проверенному масштабу.

### B05. Инженерный пакет пуска и самозапуска

Статус: **реализован ограниченный пакет исследований прямого пуска от сети**.

- [x] B05.1. Одиночный, групповой и ступенчатый пуск; провал, выбег, восстановление и тепловое отключение.
- [x] B05.2. Паспорта, критерии, изолированные запуски, воспроизводимые оценки и ожидаемые отрицательные случаи.
- [x] B05.3. Пополнение проверенных паспортов двигателей/механизмов и повторная приёмка пакета при расширении физики.
- [x] B05.3a. Каталог трёх наборов Aalto: линейный/насыщенный вентилятор и насыщенный вязкий электротепловой привод; полный потокосцепленный расчёт 1 с с провалом/восстановлением, независимый motulator, два повтора каждого набора.
- [x] B05.3b. Восемь полных потокосцепленных защитных исследований трёх наборов, оригинальный motulator, два повтора каждого. Пуск/провал, заторможенный ротор, температурное отключение, блокировка горячего включения и охлаждение/повторное отключение. Явные авторские уставки I²-реле, конечный RC/MOV-контактор, KCL/энергия и инженерный HTML; заводская калибровка не заявляется.

Критерий B05.3: происхождение параметров, применимость модели и проверки разгона,
напряжения, токов, нагрева и защит доступны для каждого нового набора.

## B06. Расширенная физика двигателей и оборудования

Статус: **расширенные физические профили реализованы и проверены численно; границы — по связанному отчёту**.

- [x] B06.0. Полная обмоточная Γ-модель, насыщение, дискретные клетки ротора, медные потери, температурные сопротивления и тепловой баланс.
- [x] B06.0a. Независимые сетевые сверки одноклеточных насыщенных двигателей: радиальная/кольцевая сеть, трансформаторы, несколько источников, провал и восстановление.
- [x] B06.1. Независимый нелинейный сетевой эталон для нескольких клеток ротора; проверка предельных режимов и идентификации параметров.
- [x] B06.2. Постоянная вихретоковая проводимость по опубликованным уравнениям, независимый исходный RHS и идентификация электрических параметров с hold-out. Гистерезис/прочие законы потерь сюда не входят.
- [x] B06.3. Линейное намагничивание/потери холостого хода трансформатора, резистивные нагрузки; совместные аналитические профили существующих GENCLS/GENROU/PSS/ESST1A/OEL. Новые нелинейные семейства требуют отдельного допуска.

Критерий закрытия каждой модели: происхождение параметров, независимый эталон,
предельные режимы и баланс энергии. Авторские теплоёмкости и клетки не выдаются
за измеренные OEM-параметры. Дискретные клетки не объявляются универсальной
моделью вытеснения тока.

## B07. Сетевые защиты, автоматика и изменение состава сети

Статус: **объявленные RMS/DAE и стационарные фазные профили реализованы и проверены численно**.

- [x] B07.0. МТЗ, задержки и отказы выключателей, резервное отключение, ограниченное АПВ, АВР, синхроконтроль и образование островов.
- [x] B07.0a. Независимые SI-сверки защит и АВР, включая отказ основного ввода, положения аппаратов и динамику резервного генератора.
- [x] B07.1. Автоматика с DAE и аналитическими действиями производных, корректная смена контекста при событиях и откатах.
- [x] B07.2. Проверенные случаи двух живых островов, раздельного восстановления, потери источника и каскадного резервного отключения. Произвольные размеры не квалифицированы.
- [x] B07.3. Фазный корпус из 26 SLG/LL/LLG/common-LLG/3ph схем с несимметричной нагрузкой. Динамика отрицательной последовательности и EMT остаются B11.

Критерий B07.1: совпадение с проверенным Radau/SI-эталоном по траекториям,
измерениям, выдержкам и действиям; невязки KCL/баланса в допуске после каждой
смены топологии; откат не оставляет устаревшие таймеры или якобиан. Нужны
отрицательные случаи, проверка порядка оборудования и фактического пути решения.

Критерий B07.2–3: проверенные токи, измерения, времена и положения аппаратов;
небезопасные переходы отвергаются. Поддержка схемы не означает OEM-сертификацию защиты.

## B08. Независимая численная валидация и локальная поставка

Статус: **локальная воспроизводимость проверяется без GitHub и CI**.

- [x] B08.0. Закреплённые версии/хеши эталонов, сырые траектории, протоколы и обязательный локальный тестовый профиль.
- [x] B08.1. Повторяемые колесо/кандидат/архив и запуск в двух чистых локальных окружениях.
- B08.2. **Отменён пользователем:** опубликованный коммит, внешний hosted CI и подписанное происхождение EX-04. Никаких действий агента на GitHub.
- [x] B08.3. Локальный корпус новых физических/инженерных примеров и независимых эталонов с происхождением параметров.
- [x] B08.4. Локальная поставка: замороженные исходники, hash-locked зависимости, offline-установка, полный обязательный профиль и CLI-повторы.

Критерий B08.4: воспроизводимый запуск из пакета без доступа к исходному рабочему
каталогу и сети, совпадение входов/траекторий/событий, целостность всех артефактов.
Два окружения на одном host не называются другим host или внешней аттестацией.
Внешняя подпись и CI не требуются для завершения согласованного локального блока.

## B09. Инженерное рабочее место и обмен моделями

Статус: **реализован локальный сквозной workflow; сквозная приёмка выполняется командой benchmarks.engineering_workbench**.

- [x] B09.1. Сквозное исследование: схема и паспорта → единицы и полнота данных → настройка → запуск → сравнение → графики и отчёт.
- [x] B09.2. Импорт через существующий адаптер с отчётом полноты, переносимый data-only ZIP и проверка целостности. Новые форматы этим не заявляются.

Критерий: новый пользователь готовит и повторяет исследование без редактирования
внутренних Python-модулей; входы, результаты и происхождение данных сохраняются.

## B10. Длительные режимы и дополнительные исследования

Статус: **ограниченный двухфидерный QSTS и единый AC-профиль реализованы; новые виды/масштабы требуют отдельного расширения**.

- [x] B10.1. Два SMART-DS-фидера с собственными исходными CSV: первые 96 и 672 отсчёта каждого, native/import/Engine, JSON-восстановление посередине с проверкой всех оставшихся отсчётов. Отдельно — ранее проверенные 30-суточные синтетические схемы.
- [x] B10.2. Единая сбалансированная AC-схема для PF, OPF, AC WLS и статического ветвевого N−1; data-only MATPOWER, перенос проверенного dispatch, ограничения обоих концов ветвей, неизменяемые попытки и продолжение.

Область B10.2: одна связная сеть с одной опорой, PV/PQ, явные MW/Mvar/pu,
диагональная ковариация измерений. Новый профиль не подменяет несимметричную
`Network`, не делает island redispatch и не заявляет глобальную оптимальность.
Независимая сверка: исходные PF ANDES 2.0.0 для PGLib 5/14/118, нативные
терминальные измерения и bad-data, 9 заранее выбранных отключений, OPF-dispatch
3/5/14, полный N−1 118 с продолжением. Q-limit switching проверяется отдельно
авторскими предельными случаями; в независимом ANDES-профиле он выключен.

Инструкция: [BALANCED_AC_WORKFLOWS_RU.md](../docs/BALANCED_AC_WORKFLOWS_RU.md).
Приёмочный снимок этого этапа: `reports/balanced-ac-final-2026-11-06/delivery.json`.
B10.1: `benchmarks.two_feeder_qsts`; первый p1udt104 и заранее выбранный второй p1udt942.
56 собственных профилей второго фидера получены с официального OEDI S3 и закреплены
в `requirements/smartds-second.lock.json` (65 файлов, 37 537 390 байт). Это синтетические
публичные профили и согласованность переноса внутри семейства OpenDSS, не два независимых
физических решателя. B10 закрыт только в этих явно перечисленных профилях.

Критерий: независимая сверка полного исследования, включая нарушения,
несходимость, отказы и восстановление после остановки.

## B11. Преобразователи, несимметричная динамика, гармоники и EMT

Статус: **реализован связанный ограниченный фазный RLC/Γ/усреднённый converter-профиль; это не универсальный switching EMT**.

- [x] B11.1. Связанные фазные GFL/PLL, GFM/droop, ограничения тока и остров; активный выпрямитель/DC-C/V/f/полный Γ-двигатель, отключение и восстановление AC-порта.
- [x] B11.2. Явные фазные/нейтральный проводники, взаимные L, несимметричные нагрузки, отрицательная последовательность в Γ-роторе; линейные гармоники/THD и мониторы 0/+/−. Изолированная звезда двигателя блокирует нулевой ток.
- [x] B11.3. Монотонная магнитная нелинейность, index-1 ёмкостно-алгебраическая сеть, KCL/энергия, события и согласованный линейный harmonic→EMT-мост. Высокоиндексные схемы, PWM и OEM-модели не входят.

Критерий: отдельный независимо проверенный профиль каждого вида расчёта.
Общий статус «универсальный EMT» без корпуса проверок не присваивается.

## B12. Доказанное преимущество и AI-возможности

Статус: **ограниченный исследовательский профиль реализован; общее превосходство над другими моделерами не заявляется**.

- [x] B12.1. Сопоставимый cold compile+fundamental solve авторской четырёхпроводной схемы: исходный OpenDSS, точность V/I, 9 отдельных процессов на движок, чередование порядка, время и RSS. Выигрыш относится только к измеренному workflow.
- [x] B12.2. Существующая Γ-идентификация дополнена конечным физическим поиском до 729 сценариев, контролем неудач, продолжением и повтором критической трассы. Непрерывный глобальный оптимум не заявляется.
- [x] B12.3. JSON-surrogate фиксированной степени, независимый holdout, ранг/обусловленность и ограничение выпуклой оболочкой. Пакетная фильтрация измеряется отдельно; запрос по умолчанию проверяется физическим решателем.

Критерий: преимущество выражено конкретными задачами и метриками при одинаковых
условиях. AI-результат не заменяет проверку исходным физическим решателем.

## Последний зафиксированный прогон расширения B06–B07

Снимок от 6 ноября 2026 года:

- 1119 обязательных тестов пройдены; собрано 3201, 2082 вне профиля не исполнялись.
- 16 физических сетевых случаев, включая независимо написанные многоклеточные
  уравнения, исходный одноклеточный RHS motulator и комбинацию насыщения/нагрева/Gc.
- 28 сравнений автоматики: 7 SI-сценариев × Radau/DAE × 2 повтора с проверкой
  фактического аналитического пути и точного повторения траекторий.
- 26 фазных КЗ с отдельным MNA-эталоном; общий ground LLG отличён от двух
  независимых ветвей, токи фазных ветвей и земли экспортируются раздельно.
- Идентификация проверена на 18 fitting и 18 held-out синтетических наблюдениях,
  полученных из исходного RHS, с проверкой ранга/обусловленности и бюджетов.
- Отпечаток проверенного Python-пакета: `73ca17582f67a376aa1972671cdca5f732f270a92fb25dc49429e7467727ab3a`.

Текущая сводка и хеши: `reports/blocks678-expanded-final-2026-11-06/delivery.json`
и `manifest.json`. Внешний EX-04 отменён; локальная целостность и воспроизводимость учитываются отдельно от внешней аттестации.

## Исторический опорный прогон до расширения B06–B07

Снимок от 6 ноября 2026 года:

- 1031 обязательный тест пройден; собрано 3113 тестов, 2082 вне этого профиля не исполнялись.
- 9 независимых нелинейных сетевых случаев с исходными компонентами motulator.
- 7 SI-сценариев автоматики, включая 2 случая АВР; повторены 7 базовых физических эталонов.
- 10 CLI-запусков в двух чистых установках с точным совпадением траекторий и событий.
- Отпечаток проверенного Python-пакета: `6e56712aeeb9bd7510e4d22b48f65b89bffe09065f51b97b77aecc61d531cdc1`.

Локальный отчёт: `reports/meshed-final-2026-11-06/delivery.json` и его `manifest.json`.
`reports/` исключён из Git; на другом host результаты нужно воспроизвести по
сохранённым исходникам, lock-файлам и workflow. Это не свидетельство внешнего EX-04.

## Журнал текущего плана

- 2026-11-06 — зафиксированы исходные 12 блоков, открытые задачи и критерии закрытия.
  Следующей выбрана B07.1; исторические результаты сохранены как результаты своих профилей.

- 2026-11-06 — добавлены расширенные профили оборудования, аналитическая автоматика
  с DAE и расширенный фазный корпус. Галочки B06/B07 относятся к явно указанным
  профилям; завершение численной приёмки проверяется по отчёту текущего производителя.
  Для B08.2 запрошена авторизация GitHub; локальный результат внешний этап не закрывает.

- 2026-11-06 — завершён численный приёмочный профиль расширения: 1119 тестов,
  16 физических сетевых случаев, 28 DAE/Radau-сверок, 26 фазных КЗ. Внешний B08.2
  не закрыт локальными результатами и ожидает авторизацию/фактическое выполнение.

- 2026-11-06 — по прямому указанию пользователя прекращены действия на GitHub
  и отменена необходимость CI. B08.2 отменён без присвоения статуса «пройден»;
  обязательным результатом B08 стал локальный воспроизводимый пакет B08.4.

- 2026-11-06 — реализован B09: версионированный проект, изолированный worker,
  неизменяемые попытки, таймаут/отмена, QSTS checkpoint в новом запуске, строгий
  обмен и offline HTML/CSV/SVG. B10: выполнены сутки/неделя первого SMART-DS
  с прямым OpenDSS; второй фидер и полный внешний workflow учитываются отдельно.

- 2026-11-06 — B10.2: реализованы AC WLS с аналитическими измерениями/SVD/leverage,
  Newton PV→PQ, единый перенос OPF/PF/N−1 и проверка паспортных ограничений.
  NaN/неверные размеры больше не проходят audit; большие конечные MVA-лимиты
  проверяются. Импортёр отклоняет неизвестное оборудование и вычисляемые
  присваивания MATPOWER; исходные MATLAB-файлы не исполняются.

## Приёмка большого фазного этапа

Инструкция и границы B11/B12: [PHASE_NETWORK_AND_RESEARCH_RU.md](../docs/PHASE_NETWORK_AND_RESEARCH_RU.md).
Результат текущего производителя: `reports/phase-industrial-final-2026-11-06/delivery.json`.
Галочки B11/B12 относятся к описанным профилям, а не ко всем видам EMT и AI.
Открытые работы сохранены: B03.3 (сквозные производные полного RMS-исследования),
B04.1 (расширенная длительная матрица) и B05.3 (паспорта). B10.1 закрыт после
нахождения правильного официального S3-пути внутри scenarios/base_timeseries и
получения собственных профилей второго фидера. Чужие профили не подставлялись.

- 2026-11-06 — реализованы фазная динамика, преобразовательные порты, DC/Vf-привод,
  несимметричный Γ-порт, физический поиск, эмпирический surrogate и локальные
  сравнительные workflows. Свидетельства старых производителей остаются историческими.

- 2026-11-06 — закрыта двухфидерная сверка B10.1; восстановление Engine проверяется не только на одном шаге, а на всей второй половине суточного/недельного окна. Официальные URL и SHA-256 закреплены; без `--download` сетевых запросов нет.

## Расширение B03 / B04 / B05 от 2026-11-06

Итоговый производитель после исправления TGOV1/SEXS: `429c36428e86825f95eac0c1820495b541a5789a18eeca9f9c19ef922315ffec`.
Предварительные отчёты `3976eb...` сохранены как исторические.

- `reports/remaining-blocks-final-2026-11-06/hybrid/summary.json`: производные полного расчёта, три шага независимых разностных траекторий. Последовательность событий проверяется отдельно; при её смене градиент не принимается. Направления относятся к физическому состоянию после инициализации, а не к паспортным параметрам или производной самого равновесия. Начальное состояние и направления сохранены; вариационные вычисления учитываются в общем бюджете RHS. Проверены также физические координаты network DAE и несколько направлений одновременно.
- `reports/remaining-blocks-final-2026-11-06/catalog/summary.json`: 6 успешных сравнений трёх наборов с закреплённым motulator 0.19.0. Сохранены токи, потокосцепления, момент, скорость, напряжения; для теплового набора — температуры и потери. Электрические параметры опубликованы; механика и тепло помечены как authored. Это две эквивалентные электрические модели Aalto, а не три независимо измеренных двигателя OEM.
- `reports/remaining-blocks-final-2026-11-06/duration/summary.json`: 4 длительных сравнения с независимыми SI-уравнениями и MNA. Большие точки: `large512/summary.json`, `large2000radau/summary.json`, `large2000dae/summary.json` внутри того же каталога. Статус принимается только из завершённого отчёта. Время/память сохранены для каждого процесса; другие локальные проверки могли работать параллельно, поэтому эти времена не служат сравнительным заявлением о производительности. Номинальная PV=1.0 — явно названный профиль; исторический 0.995 не заменён задним числом.
- В TGOV1/SEXS для `coincident_roots_v1` свободная производная и её аналитические производные действуют до найденного события; после события удержание задаётся дискретным режимом. Исправлена искусственная задержка близких срабатываний около 19 мкс в 2000-узловом случае. Новая сверка Radau даёт максимум расхождения контроллерных событий 1.7e-11 с при прежнем допуске 1e-6 с.
- Независимый эталон самостоятельно ищет временные корни. В расширенном профиле без моторных защит/контакторов допускается различное разбиение сбросов **разных** TGOV1/SEXS на группы только в пределах существующего допуска 1e-6 с. Сохраняются идентичность, порядок и время действий каждого регулятора; команды, защиты и другие семейства сбросов этим правилом не объединяются. Отрицательная проверка отвергает прежнее расхождение 19 мкс. Сравнение всех каналов и балансов остаётся обязательным.
- Каждый итоговый протокол фиксирует хеши исходников benchmark-модулей, первичного решателя и эталона. Изменение производителя во время прогона отклоняется. Общая приёмка: `reports/remaining-blocks-final-2026-11-06/stage.json`: 7 длительных случаев, 6 сравнений каталога, 2 разностных аудита производных; 1258 обязательных тестов и отдельный целевой прогон 146 тестов без ошибок/пропусков (счётчики прогонов частично перекрываются). Это локальное свидетельство этапа, не новый полный release receipt.

На этом историческом этапе B03.3, B04.1 и B05.3 оставались открытыми; они закрыты последующим приёмочным этапом ниже.
Предыдущая поставка `phase-industrial-final-2026-11-06` относится к прежнему производителю;
её полный release receipt не переносится автоматически на этот код. GitHub/CI не используются.

## Закрытие оставшихся профилей B03 / B04 / B05

- Исправлен дребезг OEL2C: найденный временной корень больше не отменяется
  повторной сверкой неизменного состояния в тот же момент. Прежние попытки
  с лимитами 1024/8192 сохранены как неуспешная диагностика. Итоговый профиль
  использует исходный лимит 1024 без изменения точностных допусков; Radau
  и DAE обрабатывают по 435 пакетов, по 24 перехода scaled_low без повторов
  одного и того же перехода в тот же момент.
- Все пять направлений чувствительности, четыре островных исследования и обе
  длинные PSS/OEL/UEL-сверки завершены. Защитный корпус: восемь случаев,
  независимый motulator и два первичных повтора каждого.
- 1283 обязательных теста: ноль ошибок, отказов и пропусков. Новый wheel собран
  локально; обе команды проверены вне рабочего каталога из распакованного wheel.
- Отпечаток Python-пакета: `854360c3870ba16a57fea69c21d01629340c45b219e51692bab92c43c8045668`. Исторические отчёты
  относятся к своим производителям. Текущие входы, исходники эталонов, метрики,
  JUnit и SHA-256 закреплены в `reports/blocks-final-root-fix-2026-11-06/delivery.json`
  и `final-manifest.json`.
- Инструкция: [локальная приёмка](../docs/REMAINING_BLOCKS_ACCEPTANCE_RU.md).
  Численная сквозная производная не называется общим аналитическим adjoint;
  проверенные масштабы не означают произвольные сети. Авторские реле/тепло
  не означают измеренный паспорт OEM. GitHub/CI не использованы.
- В согласованном локальном плане незакрытых обязательных подпунктов больше нет.
  Новые виды оборудования, размеры, EMT и OEM-паспорта требуют собственных
  независимых критериев: этот результат не объявляется универсальной
  промышленной квалификацией всех возможностей API.

## B13 — серийные инженерные исследования

Статус: **B13.1–B13.3 закрыты в объявленном Linux-профиле**.
Инструкция: [ENGINEERING_CAMPAIGNS_RU.md](../docs/ENGINEERING_CAMPAIGNS_RU.md).

- [x] B13.1. Строгие планы и сетки до 64 случаев, физическая предварительная проверка, изолированные Linux-процессы, лимиты времени/памяти/вывода, отмена и явные инженерные критерии.
- [x] B13.2. Неизменяемые попытки, продолжение и восстановление по запечатанным завершённым расчётам; самодостаточный детерминированный data-only ZIP, проверка байтов и смысловой согласованности результатов.
- [x] B13.3. Серия из 21 случая, 16 независимых физических сравнений, пять проверок чувствительностей, точное сохранение результатов после экспорта/импорта/продолжения, CLI и установка из wheel.

Критерий закрытия: успешный замороженный протокол
`reports/campaign-final-2026-11-06/summary.json` и текущий обязательный тестовый
профиль. Это последовательная конечная серия выбранных RMS/полных моторных
моделей; внутритраекторный checkpoint ODE, распределённый кластер и непрерывная
вероятностная гарантия в B13 не входят. Предыдущая приёмка B03/B04/B05 сохраняет
свой точный отпечаток и не переносится автоматически на нового производителя.

### Итог приёмки B13

- 21 случай выполнен отдельными ограниченными процессами; 16 физических
  сравнений с независимыми RHS/MNA успешны. Пять чувствительностей проверены
  заранее объявленными точными/аналитическими границами.
- Продолжение повторно использует все 21 завершённый расчёт; после ZIP-экспорта,
  импорта и нового продолжения физические результаты совпадают точно.
  Повторный экспорт одного запечатанного результата побайтно одинаков.
- 1302 обязательных теста: ноль ошибок и пропусков. Проверены отмена работающего
  worker, таймаут, отрицательные критерии, подмена/смысловые противоречия,
  восстановление из незавершённой серии по запечатанным попыткам и ограничения сетки.
- Новый wheel запущен вне репозитория; серия и semantic verify успешны.
- Производитель: `85c164ff6c33dcd411c9f410931efa6cb1b74e04b5a2221b641c713ee9ce311e`. Входы, JUnit, wheel, снимок
  исходников и SHA-256 — `reports/campaign-final-2026-11-06/delivery.json`
  и `final-manifest.json`. Это локальное свидетельство B13, не внешний CI
  или универсальная промышленная квалификация всех профилей.

## B14 — локальные параллельные серии и надёжная публикация

Статус: **B14.1–B14.3 закрыты в объявленном локальном Linux-профиле**.

- [x] B14.1. FIFO-диспетчер 1–8 локальных процессов, общий бюджет резервов памяти, ранний AS-лимит worker, журнал допуска и независимый пересчёт пиков/полноты.
- [x] B14.2. Очистка процессов при отмене/сбоях, единая смысловая проверка новых и восстановленных попыток, обнаружение подмены успеха и изменения исходного пакета, атомарная публикация манифеста без перезаписи.
- [x] B14.3. Два worker в корпусе 21/16; точная последовательная/параллельная эквивалентность шести смешанных случаев; восемь лёгких worker, отрицательные случаи, полный обязательный профиль и проверка wheel вне репозитория.

Приёмочный профиль: `reports/parallel-campaign-qualified-2026-11-06/summary.json`.
Инструкция и границы — [ENGINEERING_CAMPAIGNS_RU.md](../docs/ENGINEERING_CAMPAIGNS_RU.md).
Новые поля плана по умолчанию сохраняют последовательное исполнение. Резервы AS
не называются измеренным RSS или лимитом всей машины. Распределённый кластер,
checkpoint внутри ODE и произвольные максимальные сети × восемь worker не заявляются.

### Итог приёмки B14

- 21 случай с двумя процессами, 16 независимых физических сверок; шесть
  смешанных исследований последовательно и с тремя процессами, физические
  каналы/события/оценки/производные совпали точно. Восемь лёгких процессов
  и ограничение допуска по памяти проверены отдельно обязательными тестами.
- 1319 обязательных тестов, ноль ошибок и пропусков. В том числе 36 проверок
  серий/планировщика: отмена активных групп, выход между poll/signal, сбой
  после fork, ошибка запуска, подмена passed при восстановлении, изменение
  родительского пакета и отказ/конкурентная публикация манифеста.
- Манифесты публикуются атомарно без перезаписи. Проверка пакета пересчитывает
  фактический журнал допусков и пиков резервов; все режимы продолжения
  используют единые критерии проверки результата. Временные файлы исключены
  из Git. Новый wheel проверен вне репозитория с двумя активными worker.
- Производитель: `eadb3ef3c7e1b88b56ff81b10faf01985d4a474a6dce6c6f6f44f970d3cdcf5b`. JUnit, исходники, wheel,
  трассы и SHA-256: `reports/parallel-campaign-qualified-2026-11-06/delivery.json`
  и `final-manifest.json`. Предыдущие отчёты остаются свидетельствами своих
  производителей; глобальная промышленная квалификация не присваивается.

## B15 — динамическая идентификация двигателей

Статус: **B15.1–B15.3 закрыты в объявленном одноклеточном профиле**.

- [x] B15.1. Временные U/I/ω-трассы в явных peak αβ SI, известные начальные потоки, независимый hold-out, происхождение/хеши данных и протокола, проверки разрешимости и лимиты работы.
- [x] B15.2. Условный электрический подбор во вращающихся координатах, интегральная оценка J/нагрузки, свободный проверочный replay, принятый паспорт только при успехе, автономный отчёт и применение в campaign.
- [x] B15.3. Четыре профиля, 12 исходных motulator-трасс (четыре hold-out), восстановление параметров, четыре независимые сетевые сверки найденного паспорта, перенос/продолжение, отрицательные тесты и wheel вне репозитория.

Инструкция: [DYNAMIC_MOTOR_IDENTIFICATION_RU.md](../docs/DYNAMIC_MOTOR_IDENTIFICATION_RU.md).
Приёмка: `reports/dynamic-identification-final-2026-11-06/summary.json`.
Профиль — одна клетка, постоянная температура, без Gc, известная электрическая
частота и начальные потоки. Шумовые масштабы задаёт автор; статистическая
доверительность, натурная/OEM-валидация и глобальный оптимум не присваиваются.
Также исправлена гонка контроля вывода campaign при атомарном переименовании
временного JSON; эта ошибка не засчитывается как численный отказ физической модели.

### Итог приёмки B15

- Четыре профиля: линейный/насыщенный вентилятор, насыщенный вязкий привод,
  заторможенный линейный двигатель с известной механикой. 12 исходных трасс
  из закреплённого оригинального motulator, из них четыре независимых hold-out.
- Успешны свободные электромеханические replay, восстановление параметров
  с относительным допуском 5e-5 на бесшумных синтетических данных и четыре
  независимые сетевые сверки найденного паспорта против истинного паспорта.
- В двух параллельных worker идентификация успешно повторена; перенос ZIP
  и продолжение сохраняют результаты точно. Непринятая идентификация
  автоматически не проходит campaign, принятый паспорт при отказе не создаётся.
- 1341 обязательный тест, ноль ошибок и пропусков. Проверены отдельность
  hold-out, неверная известная механика, вырожденное и слишком слабое
  возбуждение, ограничения модели/работы, данные и отчёт.
- Новый wheel запущен вне репозитория, линейный привод успешно идентифицирован
  и выдан проверяемый паспорт. Производитель: `ec9c953f72c3bca7e801b7c2f0cbb868aaf25e41d52cefbdb16333e76b02dfd6`.
- Входы, исходники эталонов, JUnit, wheel и контрольные суммы:
  `reports/dynamic-identification-final-2026-11-06/delivery.json`,
  `final-manifest.json`. Это локальная численная приёмка; статистическая
  уверенность, натурные данные и универсальная промышленная квалификация
  не присваиваются.

## B16 — подготовка синхронных записей двигателя

Статус: **B16.1–B16.3 закрыты в объявленном синхронном CSV-профиле**.
Инструкция: [MOTOR_RECORDINGS_RU.md](../docs/MOTOR_RECORDINGS_RU.md).

- [x] B16.1. Явные каналы и калибровки; мгновенные abc phase-to-neutral либо αβ; V/kV, A/mA/kA, rpm/rad/s, s/ms/us. Проверки clock, дискретизации, конечности, амплитуд, бюджета, нулевой/обратной последовательностей и повторов возбуждения после смены координат/единиц.
- [x] B16.2. Исходные CSV, протокол, JSON, автономные графики и манифест. Смысловая проверка заново выполняет преобразование; CLI `identify-dynamic-motor --recordings` проверяет пакет до расчёта. Перенос через data-only ZIP.
- [x] B16.3. Четыре закреплённых B15-набора × два формата координат; восемь успешных идентификаций со свободным hold-out, перенос и восстановление массивов; 46 новых тестов и связанная регрессия 115/0/0; wheel вне репозитория, импорт/verify/идентификация.

### Итог приёмки B16

- `reports/motor-recordings-qualified-2026-11-08/summary.json`: восемь сочетаний
  на **сохранённых** независимых синтетических наблюдениях motulator 0.19.0.
  Сквозной скрипт не запускает исходный motulator заново и не выдаёт эти CSV
  за натурные записи. Отдельные тесты B15 действительно запущены с закреплённым
  исходным wheel, SHA-256 проверен.
- Максимальная ошибка восстановления каналов 1.99e-13 в их SI-единицах;
  максимальная относительная ошибка подбираемых параметров 1.83e-5 при заранее
  заданном допуске 5e-5 на бесшумной синтетике. Сетка/единицы/инверсия/offset
  изменяются при кодировании, физические наблюдения не пересчитываются.
- 115 связанных тестов, ноль ошибок, отказов и пропусков; JUnit сохранён.
  Подмена данных/отчёта даже с новым манифестом, обход hold-out через abc/αβ,
  переполнение калибровки, разрыв времени и ошибочный порядок фаз отвергаются.
- Wheel установлен в отдельный venv с доступными системными зависимостями;
  вне репозитория успешно выполнены импорт трёх CSV, смысловая проверка и
  принятая динамическая идентификация. Это проверка установки пакета, а не
  доказательство установки всех зависимостей на чистой ОС без сети.
- Производитель Python: `29e52987ac146da2717f397d933cba95c040279853623e1bfc64484d18d12a2c`.
  Исходники, закреплённые входы, JUnit, wheel и локальная поставка:
  `reports/motor-recordings-delivery-2026-11-08/delivery.json`, `manifest.json`.
- Предварительный `motor-recordings-final-2026-11-08` не квалифицирован:
  при доработке код изменился во время прогона, проверка производителя это
  обнаружила и отклонила результат. Приёмка относится к завершённому
  `motor-recordings-qualified-2026-11-08` и указанному отпечатку.
- Профиль B16 не включает несинхронные clock, автоматическую фильтрацию,
  неизвестные потоки, RMS/фазорные или линейные напряжения, произвольную
  несимметрию и статистическую уверенность. Скрининг последовательностей не
  является доказательством всех свойств сигнала. Проверка хешей не удостоверяет
  внешнего автора. Принятый паспорт появляется только после критериев B15.
- Новый тестовый файл включён в обязательный локальный профиль. Полный прежний
  release receipt 1341 теста не переносится на изменённый код; в этом этапе
  выполнены перечисленные 115 связанных проверок. GitHub/CI не использовались.

## B17 — идентификация при шуме и ошибках датчиков

Статус: **B17.1–B17.3 закрыты в описанном шумовом профиле**.

- [x] B17.1. Воспроизводимый корпус из независимых наблюдений с заранее заданными реализациями шума, смещениями датчиков, разными возбуждениями/частотами и отдельным hold-out; источник и seeds фиксируются до подбора.
- [x] B17.2. Измеренные пределы разрешимости/ошибок параметров и свободного replay; качественный токовый fit не заменяет согласованную механику. Неподходящие данные получают отказ, а не паспорт.
- [x] B17.3. Сверка влияния обработки сигналов на идентификацию и сетевой перенос, отрицательные случаи, локальная поставка и обязательная регрессия. Статистические гарантии не присваиваются.

Синхронизация разных clock, неизвестные остаточные потоки и измерительные
задержки расширяют постановку обратной задачи и не считаются уже реализованными
в B16 или автоматически обязательной частью B17.

### Итог приёмки B17 — 8 ноября 2026

- Инструкция: [NOISY_IDENTIFICATION_RU.md](../docs/NOISY_IDENTIFICATION_RU.md).
  Четыре сохранённых независимых motulator-профиля × два seed × окна скорости
  0/9 = 16 успешных шумовых случаев. Два отдельных hold-out со смещением тока
  и дрейфом скорости отклонены новым raw-аудитом при успешной прежней приёмке.
- Невязки исходных iα/iβ/ω проверяются по максимуму, среднему, RMS и разности
  средних половин записи. Явное сглаживание применяется только к тренировочной
  скорости; итоговый свободный replay оценивается по необработанным данным.
  Смещения датчиков не подбираются, статистические гарантии не заявляются.
- Восемь сетевых переносов сравнены с заново выполненным исходным motulator/MNA
  при истинных параметрах. Все каналы уложились в заранее сохранённые допуски.
  Максимальная относительная ошибка параметров в корпусе — 1.84e-4;
  предельный критерий 0.02 не изменялся.
- Для шумового корпуса явно установлен бюджет 4000000 RHS. При 2000000 два
  случая получили корректный ресурсный отказ. Диагностика с большим бюджетом
  показала до 2734735 RHS; точностные/невязочные пределы не смягчались.
- Успешные результаты смешанной параллельной серии повторно используются
  точно после ZIP-переноса. Отклонённый случай рассчитывается заново, как
  предусмотрено campaign: все поля совпадают, кроме elapsed_s. Первый gate
  ошибочно требовал равенства этого времени; исправлена только приёмочная
  проверка. Численные расчёты не объявляются выполненными заново. Исходный
  неуспешный итог сохранён в original-summary.json; переоценка воспроизводима
  через `benchmarks.noisy_identification_acceptance --reassess`.
- Обязательный профиль: 1412/0/0, исходники неизменны во время прогона.
  Первый полный прогон с отсутствующим ANDES не принят; успешный повтор
  использует закреплённые ANDES 2.0.0 и motulator 0.19.0 из временных каталогов.
  140 связанных тестов частично входят в 1412 и не прибавляются к ним.
  Три отдельных теста проверяют исправленное правило восстановления.
- Wheel вне репозитория: принятие корректного шума и отказ смещения без
  passport.json. Venv использует доступные системные зависимости;
  чистая ОС/внешняя поставка не заявляются.
- Python-производитель: `e4682447669473b840999943f02141c3ed6f24dfb697149456d71861b0562204`.
  Итог численной приёмки: `reports/noisy-identification-qualified-final-2026-11-08/summary.json`.
  Полный исходный снимок тестового прогона, отдельная поправка checker,
  wheel, JUnit, входы и SHA-256:
  `reports/noisy-identification-delivery-2026-11-08/delivery.json`, `manifest.json`.
  GitHub/CI не использовались.

## B18 — ошибки напряжения и калибровки измерительного тракта

Статус: **B18.1–B18.3 закрыты в профиле известных калибровок и малого шума напряжения; расширенный 0.01 V профиль не квалифицирован**.

- [x] B18.1. Заранее закреплённые воздействия на напряжение, gain/полярность/offset каналов, границы разрешимости и независимый hold-out с исходными истинными сигналами.
- [x] B18.2. Разделены допустимая объявленная калибровка и неидентифицируемые совместные параметры датчика/двигателя. Неподдерживаемые сочетания отклоняются; неизвестный остаточный поток не заменяется нулевым.
- [x] B18.3. Сетевой перенос, отрицательные случаи, ресурсные пределы, документация и локальная приёмка объявленного малошумового профиля. Разные clock и задержки требуют отдельной явно проверенной постановки.

### Итог B18 — 8 ноября 2026

- [CALIBRATED_MOTOR_STUDY_RU.md](../docs/CALIBRATED_MOTOR_STUDY_RU.md): новый CLI
  `calibrated-motor`, строгий измерительный контракт и JSON Schema, объединённые
  CSV/отчёты/паспорт/манифест. Опоры известной калибровки обязательны для U/I/ω;
  это заявления автора, не проверенные сертификаты. Measured-записи не могут
  ссылаться на synthetic_known-опоры.
- Поддержаны объявленные сырые rails каналов; контакт с границей и выход за неё
  отвергаются до подбора. Прежние протоколы без rails остаются допустимыми,
  но отсутствие клиппирования для них не доказано.
- Проверена масштабная неоднозначность: при u′=a·u, i′=b·i можно получить
  R′/L′=(a/b)R/L, β′=β/a, J′/B′=ab·J/B при прежней скорости. Совместная
  неизвестная калибровка не маскируется успешным fit. Неизвестные начальные
  потоки не подменяются нулевыми.
- Квалифицированный профиль: четыре семейства с Gaussian voltage std=0.0001 V
  и current std=0.0001 A, фиксированные PCG64-потоки. Gain U=1.125/I=0.8,
  инверсия a-фазы и offset 0.013 kV/17 mA исправляются известным протоколом.
  Четыре паспорта приняты; четыре сетевых сравнения с исходным motulator/MNA
  при истинных параметрах прошли исходные допуски B15. Максимальная относительная
  ошибка параметров — 1.82e-5 при критерии 0.02.
- Неверный gain U только в hold-out (+2%) получает физический отказ без
  паспорта. Запросы неизвестных совместных масштабов и потоков отклоняются
  до решателя. Семь критериев подтверждены новым отдельным завершённым прогоном
  `reports/calibrated-motor-final-2026-11-08/summary.json`.
- Расширенный 0.01 V опыт **не квалифицирован**: 4000000 и 10000000 RHS
  исчерпаны после 21/52 оценок. `calibrated-motor-qualified-2026-11-08` сохраняет
  passed=false; его отказ не засчитывается успешной физической проверкой.
  Он доступен через `--extended-voltage-challenge` и остаётся входом B19.
- Полная обязательная регрессия: 1436/0/0; 2082 теста вне профиля не выполнены.
  Исходный снимок точно соответствует инвентарю тестового прогона. Выбор scope
  benchmark и обновление документации выполнены после него отдельно, без
  изменения численного Python-пакета. Проверены перенос ZIP и установка wheel
  вне репозитория на принятом и отклонённом физических случаях.
- Производитель: `fc362d8b851192d0d33253ba3386b4ab777b2f3a60df26c303b894e48e774f6f`.
  Источники/входы/эталоны/JUnit/wheel/неуспешный extended-профиль/SHA-256:
  `reports/calibrated-motor-delivery-2026-11-08/delivery.json`, `manifest.json`.
  Ни статистическая уверенность, ни универсальная промышленная квалификация
  не заявляются. GitHub/CI не использовались.

## B19 — эффективная идентификация по шумному входному напряжению

Статус: **B19.1–B19.3 закрыты для линейного/насыщенного вентилятора и вязкого привода; noisy locked-rotor transfer не квалифицирован**.

- [x] B19.1. Использован сохранённый 0.01 V вход B18; затраты forward/replay и численного якобиана измерены отдельно. Исходный неуспешный 4m/10m результат сохранён; данные и исходный протокол опубликованы в cases.
- [x] B19.2. Реализован явный sample_rk4 с контролем ошибки и шагами по узлам входа. Подтверждены сходимость и разрешимость; sigma, hold-out, критерии параметров и сетевые допуски не ослаблены.
- [x] B19.3. Три независимых сетевых переноса, отрицательный случай, ресурсный протокол, параллельная серия/ZIP/продолжение и обязательная регрессия для описанного профиля. Успех не переносится на произвольные датчики, частоты и модели.

### Итог B19 — 8 ноября 2026

- [SAMPLED_IDENTIFICATION_RU.md](../docs/SAMPLED_IDENTIFICATION_RU.md): новый
  явный sample_rk4, RK4 step-doubling/Richardson, контроль локальной ошибки и
  отказ при предельном делении. Ни шаг, ни его пробы не пересекают узел входа.
  Пределы rtol=2e-9/atol=2e-11 сохранены. DOP853 остаётся default, чистый вход
  не объявляется автоматически более быстрым на новом методе.
- Опубликован самодостаточный исходный CSV-пакет B18 и original-input/failure.
  Его байты и физический протокол совпадают; меняются только метод и более
  строгие рабочие лимиты 4m RHS/120 s вместо 10m RHS/600 s. Не менялись sigma,
  паспорт, набор параметров, hold-out, напряжение/ток, калибровки или допуски.
- Линейный полный подбор теперь принят при 1527168 RHS / 46 residual-evaluations.
  Счётчики optimizer отдельно показывают nfev/njev и дополнительные вызовы
  для численного якобиана. Forward/центральный log-якобиан измерены по три раза:
  89894→15480 и 743686→123840 RHS (5.81/6.01 раза). CPU/wall сохранены отдельно;
  тайминги относятся к этому host/входу, память и универсальное ускорение не заявлены.
- Приняты линейный/насыщенный вентилятор и вязкий привод с U noise std=0.01 V:
  три сетевые сверки против заново выполненного motulator/MNA при истинных
  параметрах, прежние допуски B15. Шесть conditional/free сравнений с DOP853
  при 1000-кратном ужесточении точности и 1→2→4 уменьшение шага успешны.
  Максимальная относительная ошибка параметров принятого корпуса — 1.59e-5.
- Неверный gain hold-out остаётся отказом без паспорта. Два положительных
  исследования выполнены параллельно, перенесены ZIP и точно повторно
  использованы при продолжении. Старые B16-пакеты без поля replay_method
  читаются с default dop853, байтовый манифест и остальные смысловые проверки
  обязательны; неизвестные явные поля не игнорируются.
- Locked-rotor extension успешно проходит raw-подбор и forward, но НЕ сетевую
  приёмку: rotor/stator copper различаются на 0.776/0.796 W при пределе 0.5 W.
  Первоначальный четырёхсемейственный trial сохранён с passed=false. Предел
  потерь не увеличивался; дополнительный --include-locked воспроизводит
  расширенный протокол. Его точность переноса — отдельный открытый B20.
- Обязательный профиль 1460/0/0; 2082 внепрофильных теста не выполнены.
  Включены 24 новые проверки (скалярные точные решения/порядок/стыки/stiff decay,
  восемь физических сравнений двух алгоритмов, лимит RHS, полный frozen-fit,
  счётчики численных производных и совместимость артефактов). Wheel вне
  репозитория принимает новый полный пример и отклоняет неверный gain.
- Производитель: `57b9fb8ae2b8151ee85b44ff4251ddd08efaa93c7c934c5bed297ca5b68824e1`.
  Приёмка: `reports/sampled-identification-final-2026-11-08/summary.json`.
  Источники/входы/неуспешный locked-profile/JUnit/wheel/SHA-256:
  `reports/sampled-identification-delivery-2026-11-08/delivery.json`, `manifest.json`.
  GitHub/CI не использовались, общая промышленная квалификация не присваивается.

## B20 — точность параметров и перенос потерь при шумном напряжении

Статус: **B20.1–B20.3 закрыты при дополнительной независимой высокоточной DC-опоре и прежних сетевых допусках; исходный случай без опоры не квалифицирован**.

- [x] B20.1. Locked-rotor 0.01 V профиль B19 повторён; численная ошибка forward/сети отделена от ошибки параметров. Исходные непройденные 0.5 W сравнения сохранены.
- [x] B20.2. Введена явная независимая DC-опора Rs с границами U/I, подключением и температурой. Hold-out и sigma не менялись; сетевой предел не ослаблен, доверительные интервалы не заявляются.
- [x] B20.3. Прежний и новый протокол проверены отдельно: три независимых сетевых сравнения, отказы, общий бюджет, worker/ZIP/продолжение и обязательная регрессия. Дополнительная информация и границы применимости описаны явно.

### Итог B20 — 8 ноября 2026

- [STATOR_RESISTANCE_ANCHOR_RU.md](../docs/STATOR_RESISTANCE_ANCHOR_RU.md): новый
  CLI anchor-dynamic-motor, JSON Schema, детерминированное пересечение DC U/I
  диапазонов, независимые nominal/lower/upper подборы и общий бюджет. Одобряется
  только номинальный паспорт после общего успеха; условные кандидаты не получают
  отдельных passport.json. Непрерывное покрытие диапазона не заявляется.
- Дополнительная информация обязательна и явна: установившиеся DC U/I,
  абсолютные границы ошибок, известное winding-only подключение и одинаковая
  температура. Phase-to-neutral и wye-line-to-line (2Rs) различаются;
  неизвестное подключение/контакты/температурная коррекция не подставляются.
  Пример — авторские синтетические DC-точки из опубликованного Rs=3.7 Ω,
  а не измерения OEM или гарантированная точность производственного прибора.
- Исходный unanchored аудит повторён без изменения данных: raw-fit проходит,
  перенос потерь остаётся 0.7962/0.7757 W при пределе 0.5 W. При одинаковом
  найденном паспорте численное расхождение сети лишь 5.23e-6 W; forward
  sample_rk4/DOP853-tight — 3.56e-8 A. Ошибка отделена от численного решения.
- Новая DC-опора даёт Rs 3.6999908667…3.7000091333 Ω при авторских 10 µV/1 µA
  погрешностях, ширина 4.94 ppm. Для nominal/lower/upper stator copper ошибки
  0.0497/0.0680/0.0609 W, rotor copper 0.0661/0.0878/0.0653 W. Все сравнения
  проходят неизменный B15-предел 0.5 W и остальные каналы. Raw-данные, sigma,
  hold-out, seed остальных параметров и сетевые допуски не изменены.
- Rs теперь известен по дополнительной опоре; неизвестных электрических
  параметров три вместо четырёх. Это новая условная задача. Остальные ошибки
  параметров не обязаны уменьшаться монотонно (максимум около 2.52e-4 при
  критерии 0.02); подтверждён целевой перенос потерь, не универсальное улучшение
  всех оценок. Старый случай без дополнительной информации не квалифицирован.
- Три аудита использовали 5712024 RHS при общем лимите 10 млн. Ранее неверная
  DC-опора исчерпывала этот лимит после двух завершённых отказов. Теперь один
  завершённый отрицательный контрпример достаточен для отказа: остальные
  точки явно skipped, bracket_completed=false; для успеха всё равно требуются
  все три положительных результата. Неверная DC-опора (+20% U) отвергнута
  физически при 3424560 RHS; противоречивые DC-данные — до решателя.
- Тип campaign anchored_dynamic_identification проверяет полный набор точек,
  их Rs по исходному измерению, признаки успеха и согласованность суммарного
  RHS. Проверены worker, точное продолжение и ZIP-перенос. Подменённые флаги,
  дублированные точки, неверные типы/значения и противоречивые бюджеты отвергаются.
- Полный обязательный профиль: 1494/0/0, исходники не менялись во время прогона;
  2082 теста вне профиля не выполнялись. Включены 34 новые проверки. Wheel
  вне репозитория принял положительный пример и отклонил неверную DC-опору без
  паспорта; venv использует системные зависимости, чистая ОС не заявляется.
- Производитель: `3e3a03dc30afb93fd57a424e01964ef5268ad9b167c9461f4fa7599ca0116c96`.
  Приёмка: `reports/anchored-identification-qualified-2026-11-08/summary.json`.
  Источники/исходные и новые данные/эталоны/предварительные отказы/JUnit/wheel/SHA:
  `reports/anchor-identification-delivery-2026-11-08/delivery.json`, `manifest.json`.
  Согласованная очередь B01–B20 закрыта только в заявленных профилях. Исторические
  receipt относятся к своим производителям; нынешний результат не означает
  повтор всех старых независимых benchmarks, натурную/OEM-валидацию или
  универсальную промышленную квалификацию. GitHub/CI не использовались.


Итог R03.6: все 6 ветвей IEEE14/30/118 (+20% Pd/Qd, два шага)
достигли точной цели. Проверены 238 принятых точек и 6 точных целевых режимов:
максимум расхождения Vm 1.30145e-11 pu, суммарной Q генерации по узлу
1.38350e-8 Мвар. Все зарегистрированные переходы локализованы; их λ согласуются
при уменьшении шага вдвое. Проверка сохранённых сравнений прошла отдельно.
Wheel вне checkout выполнил четыре сценария и ZIP-перенос с заблокированными
импортами benchmarks/pandapower/andes/motulator; проверен также CLI. Это тот же
host и venv с системными зависимостями, не новая независимая ОС.
Поставка: `reports/reactive-arc-delivery-2026-11-09/delivery.json`.


## R03.10 — масштабирование распределённого CPF/envelope, 10 ноября 2026

Добавлен воспроизводимый профиль `benchmarks.ac_scale_acceptance`:
IEEE118, PEGASE1354, авторские слабо нагруженные кольцевые сети 600/2048 узлов;
IEEE300 и RTE1888 зарегистрированы отдельно как открытые диагностические случаи.
Политика — равные положительные веса участников, явные исходные Pg и P/Q-пределы.
Это ограниченный balanced AC профиль, не квалификация любой сети такого размера.

Независимый разреженный Newton использует оригинальные Ybus/Yf/Yt и dSbus_dV
pandapower 3.5.6, отдельно реализованные P/Q-проекции и собственную классическую
PF-инициализацию. Первичный решатель не передаёт ему начальные состояния.
Эталон проверяет каждую принятую точку, оба конца локализованных событий и точную
цель; независимый CPF этим не заявляется. Электрические допуски прежнего
профиля сохранены. Для каждого положительного случая выполняются два шага
0.1/0.05, по три повтора, с побайтовым сравнением физических результатов.
Сравнение событий между шагами относится к заданному интервалу λ∈[0,1];
пробные точки за целью и их события сохранены и проверяются электрически,
но перечислены отдельно как extrapolation_events.

Время включает подготовку, расчёт, аудиты, сериализацию и запечатывание;
проверка артефактов измеряется отдельно. Каждый worker привязан к одному CPU,
BLAS/OMP/MKL — один поток. VmHWM включает импорты, три расчёта, верификацию
и осмотр начальной матрицы; это не дополнительная память одного Newton-шага.
Размер CSC относится к начальному состоянию, не максимуму LU fill-in.
Нагрузка host не контролируется, превосходство над другими пакетами не доказано.

Проверяющий `--verify` пересчитывает физические аудиты всех повторов,
инвентарь эталонных состояний, электрические сравнения, матричную память,
медианы и итоговые флаги. Он не удостоверяет происхождение часов/RSS,
не повторяет Newton и не превращает SHA в подпись производителя.

Открыто: IEEE300 — base_not_converged, отдельный эталон также не сошёлся;
это не доказательство отсутствия режима. RTE1888 — исходные Pg генераторов
10/24/26 вне P-пределов, вход отвергается без расширения паспортных границ.
Следующая очередь: робастная инициализация IEEE300, явно документированный
допустимый dispatch для RTE1888, более тяжёлые нагрузки/топологии и длительные
динамические исследования. Эти задачи не объявляются закрытыми новым профилем.

Команды (новая директория результата):
```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python -m benchmarks.ac_scale_acceptance --out reports/ac-scale-local
python -m benchmarks.ac_scale_acceptance --verify reports/ac-scale-local
```

Результат R03.10: все восемь положительных ветвей (четыре схемы × два шага)
прошли, 24 полных расчёта дали идентичные физические результаты внутри каждого
набора повторов. Проверены 210 состояний; 33 сохранённых события включают одно
пробное событие за целевым интервалом. Ошибка Vm не выше 1.45e-12 pu.
173 уникальных целевых теста прошли (171 в общем прогоне и два добавленных
позже, затем все 12 новых проверены вместе). Полный release-профиль не запускался.
Свежий wheel вне checkout выполнил PEGASE1354 и synthetic2048, проверил
артефакты и ZIP-перенос при заблокированных импортах независимых моделировщиков
и benchmarks; это тот же host, venv с системными зависимостями.
Итог: `reports/ac-scale-final-qualified-2026-11-10/summary.json`;
поставка: `reports/ac-scale-delivery-2026-11-10/delivery.json`.


## R03.11 — явная подготовка Pg и производный профиль RTE1888, 10 ноября 2026

Добавлен `power_grid_env.ac_schedule`: минимальная взвешенная евклидова
коррекция только объявленных регулируемыми Pg при неизменных P-пределах.
По умолчанию сохраняется исходная суммарная генерация; можно явно задать новую
сумму. Нерегулируемые генераторы неизменны; их недопустимые Pg дают отказ.
Несовместимая с агрегатными пределами сумма возвращает отказ без подготовленной
схемы. Вход не меняется на месте. Это геометрическая подготовка входных данных,
без стоимостей, потерь, сетевых уравнений и обещания AC-допустимости.

`ac-prepare-schedule --input INPUT --out OUTPUT` записывает исходную схему,
настройки, SHA, исходные/новые Pg, изменения каждого агрегата и подготовленную
схему. `verify-ac-schedule OUTPUT` пересчитывает проекцию и протокол; подмена
предела, Pg, общей суммы или признака электрической допустимости отвергается
даже после нового запечатывания. Кандидат передаётся отдельному AC-исследованию.

Сверка `benchmarks.ac_schedule_acceptance` использует неизменный
`cases/ac-scale/rte1888.json`. Исходный вход по-прежнему отвергается:
Pg агрегатов 10/24/26 ниже их Pmin. Производный вход явно меняет расписания,
сохраняя суммарный Pg и все характеристики оборудования/сети. Независимая
проекция решается SciPy brentq; электрические состояния — отдельным разреженным
Newton на исходных PP3.5.6 stamps/производных. Два шага CPF/envelope, три повтора,
все точки и концы событий сверяются; превышения исходных V/MVA-пределов
сохраняются. Завершение ветви не приравнивается к допустимости всех режимов.

Архивы повторов сжимаются только после проверки полного восстановления и SHA;
лимиты безопасной распаковки остаются прежними. `--reuse DIRECTORY` позволяет
повторно использовать уже запечатанные worker-расчёты того же производителя
с тем же входом. В этом режиме сравнения и аудиты выполняются заново, а
поле execution_policy явно сообщает, что нового замера времени не было.
Время/RSS — непроверенные внешне локальные измерения, не рейтинг производительности.

IEEE300 остаётся открытым. Исследовательская residual-homotopy от режима с
расширенными Q-пределами остановилась около t=0.8422, не достигнув исходной
системы. Широкие Q-пределы были только экспериментом и не вошли в продуктовую
квалификацию. Это не доказательство отсутствия решения и не основание менять
паспортные ограничения. Следующая очередь: устойчивость projected-Q решения,
независимые альтернативные формулировки, тяжёлые режимы и сценарии топологии.
Исторические R03.10 receipt относятся к прежнему SHA производителя; полный
старый корпус на новом SHA этим блоком не перепроверен.

Быстрый пример:
```bash
power-grid ac-prepare-schedule --input cases/ac-schedule/redistribute.json --out reports/schedule-demo
power-grid verify-ac-schedule reports/schedule-demo
```
`cases/ac-schedule/infeasible-total.json` демонстрирует явный отказ без
кандидата; CLI завершится кодом 2. Подготовленная схема находится в
`result.json` → `prepared_case`; остальные поля содержат протокол происхождения.

Итог R03.11: производный RTE1888 достиг цели на обоих шагах. Шесть исходных
worker-прогонов воспроизводимы; их запечатанные результаты повторно использованы
при финальной приёмке, без повторного измерения времени. Независимо сверены
436 состояний и 200 сохранённых переходов/ограничений (включая пробные события
за целью). В заданном λ∈[0,1] положение событий между шагами различается не
более чем на 7.97e-8. Максимальная ошибка Vm — 7.74e-12 pu; независимая
проекция Pg отличается не более чем на 3.95e-11 МВт, невязка суммы 7.15e-9 МВт.
Пройдено 194 уникальных целевых теста: общий прогон 190 и четыре добавленных
после него; все 21 тест нового модуля затем пройдены вместе. Полный release
не запускался. Wheel вне checkout выполнил подготовку/статический режим/
CPF-envelope/ZIP при заблокированных импортах эталонных пакетов; та же ОС,
venv с системными зависимостями, не независимый host.

Приёмка: `reports/ac-admission-final-qualified-2026-11-10/summary.json`.
Поставка: `reports/ac-admission-delivery-2026-11-10/delivery.json`.
Из-за ограничений размера это два отдельно запечатанных набора: полный каталог
приёмки и пакет исходников/wheel/переносимости. Пакет ссылается на SHA манифеста
приёмки; для переноса доказательств нужны оба каталога. Исходный RTE-профиль
остаётся отказом, IEEE300 остаётся нерешённым. Превосходство над всеми моделерами
этим результатом не доказано. GitHub, CI, коммиты и push не использовались.


## R03.12 — исследование IEEE300 и отдельный CPF-эталон, 10 ноября 2026

Добавлен `ac_loading_screen`: воспроизводимое исследование от явно заданного
начального коэффициента нагрузки к запрошенному. Масштабируются все signed
Pd/Qd; Pg, P/Q-пределы и электрическая схема сохраняются. Требуются явные
distributed-P/projected-Q настройки. Результат различает завершённое
исследование и достижение целевого режима. Остановка по бюджету/отмене
не создаёт заключения о допустимости исходного или целевого состояния.
Наблюдаемый разворот не является сертификатом отсутствия решения, динамической
устойчивости, уникальности ветви или гарантированного запаса загрузки.

IEEE300 с исходными P/Q-пределами исследован от 0.95 к 1.0 исходной нагрузки.
На выбранной ветви наблюдается разворот при коэффициенте 0.972077896064;
начальный режим уже нарушает часть V-ограничений. Это результат о ветви
уравнений, не разрешённая рабочая загрузка площадки. Холодный номинальный
расчёт остаётся открытым: другие ветви и существование иных решений этим
исследованием не исключены. Пределы не расширялись, нагрузки не выдаются за
исходный номинальный кейс, модели оборудования не заменялись.

Отдельно реализован эталонный CPF (`benchmarks.ac_independent_arc`) на
оригинальных PP3.5.6 Ybus/терминальных stamps/dSbus_dV: собственная
инициализация, касательная, predictor/corrector, откат и поиск разворота.
Он не вызывает основной CPF/Newton/residual и не получает его напряжения
как seed. Для сверки состояний задаются только λ и верхняя/нижняя сторона
ветви; начальная точка корректора берётся из собственной эталонной траектории.
Это наша независимая реализация уравнений на PP, не встроенный CPF pandapower.

Два native шага 0.03/0.015, tolerance 1e-12 pu, независимый шаг 0.01:
99 состояний с обеих сторон разворота и концы событий прошли прежние
электрические допуски. Максимальная ошибка Vm 1.40e-11 pu; расхождение
положения разворота с независимым CPF <3.48e-12 по λ; между native шагами
<3.93e-13. Все восемь зарегистрированных событий каждого шага локализованы;
положение событий между шагами отличается не более чем на 7.58e-9 по λ.
Это конечная проверенная выборка; полнота обнаружения всех возможных событий
не доказана.

212 целевых тестов прошли, включая аналитический двухузловой предел P=0.5 pu,
верхнюю/нижнюю ветви, независимость эталона от основного решателя,
численные производные, отмену/бюджеты и отказ при подмене отчёта, касательной
или независимого сравнения после нового запечатывания. Полный release-профиль
не запускался. Исторические приёмки других блоков относятся к своим SHA.

Команды:
```bash
power-grid ac-loading-screen --input cases/ac-loading/ieee300-investigation.json --out reports/loading-local
power-grid verify-ac-loading-screen reports/loading-local
python -m benchmarks.ac_loading_acceptance --out reports/loading-independent
python -m benchmarks.ac_loading_acceptance --verify reports/loading-independent
```
Все численные запуски выполнялись с BLAS/OMP по одному потоку. Малый пример:
`cases/ac-loading/two-bus-fold.json`. При завершённом исследовании разворота
CLI возвращает 0, хотя requested_target_reached=false; проверять нужно оба поля.
Приёмка: `reports/ac-loading-final-qualified-2026-11-10/summary.json`.
Поставка: `reports/ac-loading-delivery-2026-11-10/delivery.json`.
Следующая очередь: чувствительности и критические узлы на проверенной ветви,
более крупные тяжёлые режимы с независимым CPF, альтернативные ветви IEEE300.
Превосходство над всеми пакетами, глобальная разрешимость и динамическая
устойчивость не заявляются. GitHub/CI/коммиты/push не использовались.

Wheel R03.12 вне checkout выполнил оба поставляемых примера через CLI,
проверил отчёты и ZIP-перенос при заблокированных imports benchmarks/PP/ANDES/
motulator. Проверка проведена на том же host, venv с системными зависимостями;
чистая новая ОС и независимая внешняя поставка не заявляются.


## R03.13 — разреженные чувствительности и критические узлы, 10 ноября 2026

Добавлен `ac_sensitivity`: прямые производные для направлений изменения Pd/Qd
и сопряжённые градиенты выбранных выходов по всем нагрузкам. Для исходного
состояния J dx = -B dL; для выхода m решается Jᵀ z = ∂m/∂x. Одна LU
исходного Якобиана переиспользуется для всех направлений и сопряжённых задач;
это не утверждение об одной факторизации всего предшествующего Newton.
Плотный Якобиан и его обратная матрица не строятся.

Выходы: Vm, относительный угол в радианах, Pg отдельного генератора, общий
балансирующий delta в МВт, P/Q/MVA и модуль тока с обеих сторон ветви.
Ток — pu на принятой базе, не амперы. Направления задаются в МВт/Мвар за
единицу параметра; сопряжённые производные — выходных единиц/МВт и /Мвар.
Для каждого выхода возвращаются градиенты всех узлов и отдельные рейтинги
по абсолютному влиянию P и Q. Рейтинг не определяет доступность управления
нагрузкой и не является рекомендацией отключения оборудования.

Можно передать сохранённое состояние, но оно заново проверяется по сетевым,
P/Q-уравнениям, распределению генерации и предоставленным потокам. Без состояния
выполняется обычный dispatch. Его отказ не превращается в нулевые производные.
Расчёт допускается вне эксплуатационных V/MVA-пределов, если сетевые уравнения
выполнены; audit отдельно сообщает нарушения. Это математическая локальная
чувствительность выбранного режима, не подтверждение допустимой эксплуатации.

Обычные производные отвергаются возле порогов P-проекции и projected-Q
переключений. Расстояния считаются в пространстве этих проекций, не как
реальный резерв Q. Ненулевая ширина диапазона учитывается; фиксированные
нулевые диапазоны не создают ложный kink. Модуль нулевого тока/мощности
не выдаётся за дифференцируемый. При сингулярном Якобиане нет результата
с производными. Остатки линейных уравнений публикуются, но не являются
оценкой числа обусловленности или гарантией ошибки. Через переключения,
изменения топологии и сертифицированную конечную область доверия производных нет.

Профиль IEEE300: верхняя ветвь при факторах .95/.97/.972/.97207,
исходные P/Q-пределы и Pg сохранены. Выходной узел 235 выбран по минимальному
напряжению независимого начального режима; ветвь 399 — по максимальному
начальному модулю потока. Это внутренние ID закреплённого импорта.
Проверены 12 выходов, P/Q-возмущения выбранного узла, рост всех signed нагрузок
и отдельные возмущения первых узлов сопряжённых рейтингов P/Q.

Независимая инициализация берётся из собственной PP-CPF траектории;
первичные напряжения в эталонный Newton не передаются. На каждом из четырёх
режимов — пять направлений × два central-difference шага × две стороны:
80 отдельных нелинейных возмущённых режимов и четыре базовых сверки.
Все возмущения сохраняют исходный active set. Для производных заранее заданы
abs 1e-6 и rel 2e-3; оба шага прошли. Это допуски производных в выходных
единицах/параметр, не ослабление прежних допусков базового электрического
сравнения. Независимая нелинейная сверка не выполнялась для каждого из 600
сопряжённых столбцов: проверены объявленные направления и верх рейтингов.

Максимальный локальный градиент Vm выбранного узла вырос с 0.0093202
до 0.5435875 pu/МВт (около 58 раз) между выбранными крайними режимами.
Это наблюдение, не универсальная связь с расстоянием до потери устойчивости.
Прямой и сопряжённый расчёты согласованы лучше 1.54e-12 в опубликованной
нормировке. Отдельный unit-профиль synthetic2048 выполнил forward/adjoint
с запретом sparse-to-dense conversion; независимая нелинейная квалификация
производных здесь ограничена IEEE300 и аналитическим двухузловым примером.

Пройдено 233 уникальных целевых теста: общий прогон 230 и три добавленных
после него, затем все 21 нового модуля вместе. Проверены аналитические
производные, обе стороны трансформатора с tap/phase/charging, насыщенные
P/Q-ограничения, нулевые диапазоны, смена угловой опоры, один узел,
недифференцируемые нулевые модули, инвентарь независимых возмущений и подмена
результатов после запечатывания. Полный release-профиль не запускался.

Команды:
```bash
power-grid ac-sensitivity --input cases/ac-sensitivity/two-bus.json --out reports/sensitivity-demo
power-grid verify-ac-sensitivity reports/sensitivity-demo
python -m benchmarks.ac_sensitivity_acceptance --out reports/sensitivity-independent
python -m benchmarks.ac_sensitivity_acceptance --verify reports/sensitivity-independent
```
Второй пример — `cases/ac-sensitivity/ieee300-upper-97.json`: явно сохранённое
состояние повторно аудируется. Свежий wheel вне checkout выполнил оба CLI-примера,
проверку и ZIP-перенос при заблокированных imports benchmarks/PP/ANDES/motulator.
Это та же ОС и venv с системными зависимостями, не независимый host.
Приёмка: `reports/ac-sensitivity-qualified-2026-11-10/summary.json`.
Поставка: `reports/ac-sensitivity-delivery-2026-11-10/delivery.json`.
Исторические receipt других блоков относятся к своим SHA; они не повторены
полностью новым производителем. Следующая очередь — проверенные конечные
контрфактические изменения нагрузок с повторным физическим решением,
более крупные тяжёлые CPF-профили и альтернативные ветви IEEE300.
Всеобщего превосходства, отсутствия иных номинальных решений и динамической
устойчивости результат не доказывает. GitHub/CI/коммиты/push не использовались.


## R03.14 — конечные изменения нагрузок и нелинейная перепроверка, 10 ноября 2026

Добавлен `ac_counterfactual`: явный signed сценарий
Pd_after = Pd_before + amount·dp, Qd_after = Qd_before + amount·dq.
Параметры оборудования, топология, исходные Pg и P/Q-пределы сохраняются.
Допустимы положительный, отрицательный и нулевой amount. Это статический
balanced AC сценарий, не изменение частоты, динамика привода или действие защиты.

Сначала проверяется/решается базовый режим, затем сохраняется локальный прогноз
и выполняется полноценное CPF/envelope-прохождение к конечной нагрузке.
Выход содержит before, linear_after, actual_after, actual_change и linear_error
для каждого выбранного Vm/угла/Pg/delta/P/Q/MVA/pu-тока. Единицы сохраняются
по выходам; ошибки разных физических величин не сводятся в один максимум.
Прогноз не используется вместо физического конечного состояния.

Продолжение получило `initial_state` для native distributed-P/Q профиля:
состояние заново аудируется по сетевым уравнениям, проекциям P/Q и предоставленным
потокам. Начальная сохранённая точка должна точно соответствовать объявленному
аудированному seed. Холодный dispatch при таком запуске не выполняется.
Подменённый seed отвергается до публикации envelope-пакета. Legacy/reference
backend без объявленной distributed-P/Q политики не принимает этот seed.
Это выбор явно заданной ветви, не гарантия её динамической устойчивости.

completed=true означает достижение запрошенной нагрузки и завершённый мониторинг.
requested_change_reached отдельно сообщает о точном конечном режиме: при
неполном мониторинге цель может быть достигнута, но completed остаётся false.
Физическая допустимость по эксплуатационным ограничениям вынесена в target_audit
и target_within_monitored_limits. Сценарий может сойтись с нарушениями V/MVA/
углов/явного pu-тока; сходимость не считается разрешением эксплуатации.

Порог P/Q в начальном режиме или нулевой модуль тока/мощности могут запретить
линейный прогноз, сохраняя возможность нелинейного сценария. Через найденные
P/Q-переключения конечный режим всё равно рассчитывается; прогноз отмечается
observed_control_switch. no_control_switch_observed не доказывает отсутствие
скрытого переключения и не является конечной областью доверия производных.
События за λ=1 сохраняются с явной пометкой outside requested interval.

При развороте ветви до цели actual_after и target_audit отсутствуют:
недостигнутый результат не заменяется экстраполяцией. Признаки глобального
отсутствия решения и устойчивости остаются false. Отмена и бюджеты не создают
ложного конечного состояния; бюджеты базового решения, продолжения и мониторинга
относятся к отдельным этапам, не к общему таймеру всей цепочки.

Проверены шесть зарегистрированных сценариев: IEEE300 .97→.9701, .97→.971
с Q-переключением, .97→.969, .97→1.0 с разворотом до цели; авторские трёхузловые
вход/выход из P-ограничения. Пять целей достигнуты; для шестой сохранён
подтверждённый наблюдаемый отказ выбранной ветви без утверждения об иных ветвях.
Отдельный PP-CPF и отдельно инициализированный sparse Newton подтвердили
242 состояния, включая концы локализованных событий и достигнутые точные цели.
Максимальная ошибка Vm 8.62e-11 pu; прежние электрические допуски сохранены.
Первичные напряжения не использовались как начальные состояния эталона.

Пример измеренной нелинейности: для IEEE300 .97→.971 ошибка прогноза Vm235
около 0.00122005 pu, delta — 0.109483 МВт; для .97→.9701 соответственно
3.006e-6 pu и 3.033e-4 МВт. Это наблюдения конкретных сценариев, не универсальная
граница ошибок. Для двухузлового аналитического примера подтверждены точный
нелинейный Vm и квадратичное уменьшение ошибки прогноза при уменьшении шага.

256 уникальных целевых тестов: общий прогон 254 и два последующих; все 23
теста нового модуля затем пройдены вместе. Включены ограничения/освобождение,
исходный kink без производной, нулевой ток, нулевое/отрицательное изменение,
ветвь без цели, отмена, частичный мониторинг, защита происхождения базы,
подмена seed/прогноза/конечного результата/эталонных потоков после запечатывания.
Полный release-профиль не запускался. Исторические receipt других блоков
относятся к своим SHA и не перепроверены полностью новым производителем.

Команды:
```bash
power-grid ac-counterfactual --input cases/ac-counterfactual/two-bus.json --out reports/counter-demo
power-grid verify-ac-counterfactual reports/counter-demo
python -m benchmarks.ac_counterfactual_acceptance --out reports/counter-independent
python -m benchmarks.ac_counterfactual_acceptance --verify reports/counter-independent
```
Ещё шесть примеров — `cases/ac-counterfactual/`. Положительный CLI-результат
возвращает 0, недостигнутая цель — 2. Для verify также учитывается completed:
корректно проверенный отрицательный сценарий может вернуть 2 с verified=true.
Нужно читать оба поля, не приравнивать verified к достижению цели.
Свежий wheel вне checkout выполнил четыре примера через CLI: два с переключением,
аналитический и IEEE-fold; проверены положительные/отрицательный статусы и ZIP.
Импорты benchmarks/PP/ANDES/motulator заблокированы. Это тот же host и venv
с системными зависимостями, не новая независимая ОС.
Приёмка: `reports/ac-counterfactual-final-qualified-2026-11-10/summary.json`.
Поставка: `reports/ac-counterfactual-delivery-2026-11-10/delivery.json`.
Следующая очередь: пакетные исследования и проверенные сценарные метки для
SFT/GRPO с различением недостигнутой ветви и доказанной неразрешимости,
расширение независимого корпуса тяжёлых режимов. Всеобщего превосходства
этот профиль не доказывает. GitHub/CI/коммиты/push не использовались.


## R06.1 — потери в стали в связанной фазной сети, 10 ноября 2026

По текущему приоритету пользователя развиваем физику R06 сейчас; очереди
R04/R05 для LLM не считаются закрытыми и не блокируют эту работу.
Добавлен `PhaseCoreMotor`: полные потокосцепления статора и 1–4 клеток ротора,
насыщение, механика и необязательные две температуры в общей ABC RLC-сети.
Положительный Gc больше не отклоняется прямым фазным моторным портом.
Gc=0 сохраняет прежний токовый компонент; оба типа совместно работают
на общей шине, а нулевой предел Gc сверяется отдельно.

Основа — [Γ-модель Aalto, Core Losses, уравнение (5)](https://aalto-electric-drives.github.io/motulator/model/drive/induction_machine.html).
Ток статора is=(im−Σir+Gc·us)/(1+Gc·Rs), потери Pc=1.5·Gc·|us−Rs·is|².
Ток вихревых потерь исключён из момента. Это постоянная проводимость за
сопротивлением статора, не резистор на внешних зажимах и не модель гистерезиса.
Дополнительные клетки — пассивный дискретный RL-спектр, не пространственная
модель вытеснения тока. Численные Gc/добавленные клетки/сеть/тепловые параметры
в новых примерах явно авторские; электрический/магнитный seed имеет источник.

Напряжение-зависимый ток корректно включён в KCL: фазный блок
Gc/(1+Gc·Rs)·(I−11ᵀ/3). Он не создаёт проводимости нулевой последовательности.
Алгебраические напряжения решаются с этим физическим блоком; при нагреве Rs
блок пересчитывается. Дополнительных фиктивных R/C или заземления не вводится.
Физическая проводимость сердечника может сделать часть алгебраических строк
разрешимой; оставшаяся плавающая общая мода по-прежнему отклоняется.

Для Gc>0 начальное состояние задаётся потокосцеплениями
`initial_fluxes_ab_vs` (пиковые SI αβ-потоки, не RMS) либо нулевыми потоками
по умолчанию. Неоднозначная
инициализация токами и одновременные currents/fluxes отвергаются.
Gc=0 также принимает явно заданные потоки с преобразованием в прежние
токовые координаты. Инвентарь должен включать статор и каждую клетку.
На входе PhaseNetwork повторно проверяет каноническую копию схемы;
мутированный экземпляр модели не обходит новые ограничения.

Сохраняются физическая магнитная энергия, энергия вала, тепловая энергия
и отдельные квадратуры работы/диссипации. Потери в стали поступают в статорный
тепловой узел; распределённая температура стали этим не моделируется.
Результаты содержат токи, момент, потери, поток статора и температуры
положительного Gc-порта. Ограничение бюджета выходных скаляров учитывает
расширенный набор каналов. Прежний CoreFluxNetwork остаётся отдельным профилем; V/f-привод расширен
следующим блоком R06.2. Смешанный парк R06.1 квалифицирован в фазной сети.

Пять новых связанных профилей: прямое включение смешанного парка на
алгебраической шине, тепловая многоклеточная пара, несимметричное межфазное
замыкание с ёмкостями, замыкание с алгебраическим скачком напряжений и
четырёхклеточный двигатель с провалом/восстановлением одной фазы.
Есть взаимные индуктивности питающих фаз. Длительности 0.04–0.20 s;
тепловая пара проходит 0.20 s, четырёхклеточный случай — 0.12 s.
Это проверка указанных переходных окон, не обещание полного разгона
или длительной тепловой установившейся работы.

Эталон — отдельно написанные исходная трёхфазная KCL и потоковые уравнения
для всех машин, включая Gc=0; собственный DOP853 против основного Radau.
Он не вызывает основной машинный или сетевой RHS. Для каждого профиля
выполнены coarse/fine расчёты с ужесточением rtol и уменьшением max_step;
fine приблизился к эталону во всех пяти случаях. Сравниваются напряжения,
токи линий и машин, момент, скорость, потери, поток и доступные температуры.
На fine максимальные расхождения: U 2.88e-7 V, iα 6.05e-9 A,
момент 9.11e-9 Nm. Максимальная относительная невязка полной энергии
2.89e-11 в объявленной нормировке. Это численная согласованность моделей,
не измеренная погрешность относительно настоящего двигателя.

В отчёте сохранены число состояний, RHS и выходных отсчётов для каждого
coarse/fine. Проверенный корпус имеет 6 узлов, 1–2 машины и до 24 физических состояний
плюс две квадратуры работы/диссипации.
Лимиты API 128 узлов/512 состояний не означают проверку всех сочетаний.
Статические результаты на 2048 узлах не являются масштабом этой EMT-проверки.
Скоростное превосходство над чужими решателями не измерялось.

Проверка приёмки заново запускает независимый ODE-эталон и сверяет его
с сохранёнными данными. Даже согласованная подмена primary/reference траекторий
с новым запечатыванием и пересчитанными строками отчёта отвергается replay.
SHA не является подписью производителя, повторный запуск native solver при
verify не выполняется. Пройдено 137 уникальных целевых тестов: общий прогон
134 и три добавленных после него; все 17 нового модуля затем вместе.
Полный release-профиль не запускался; прежние receipt относятся к своим SHA.

Дополнительно проверены локальный закон энергии, отсутствие вихревых потерь
при установившемся DC-потоке, блокирование нулевой последовательности,
Gc→0, температурная зависимость и аналитическая проводимость locked-rotor
с 1/4 клетками. В protected-модели общий диагностический интерфейс корректно
читает потоковые координаты. Unit-профиль Gc с RC/MOV-размыканием подтвердил
сохранение потоков и баланс энергии; отдельная независимая квалификация
всего protected-core workflow пока не заявляется.

Команды:
```bash
power-grid phase-study run --study cases/phase-core/thermal_multicage.json --out reports/phase-core-local
python -m benchmarks.phase_core_acceptance --out reports/phase-core-independent
python -m benchmarks.phase_core_acceptance --verify reports/phase-core-independent
```
`phase-study verify` проверяет целостность пакета; независимый физический replay
выполняет указанная benchmark-команда. В `cases/phase-core/` лежат все пять
воспроизводимых примеров. Свежий wheel вне checkout выполняет смешанный,
тепловой и fault-примеры с заблокированными imports benchmarks/PP/ANDES/motulator,
проверяет KCL/энергию и ZIP-перенос. Это тот же host, venv с системными
зависимостями, не независимая ОС.
Приёмка: `reports/phase-core-final-v2-qualified-2026-11-10/summary.json`.
Поставка: `reports/phase-core-delivery-2026-11-10/delivery.json`.
Следующая физическая очередь: потери и энергетика преобразовательного привода,
расширенный независимый RC/MOV/Gc-корпус, более длинные и крупные фазные
исследования с измерениями ресурсов. OEM-точность, гистерезис, произвольные
соединения обмоток/нейтралей, все масштабы и всеобщее превосходство не доказаны.
GitHub/CI/коммиты/push не использовались.


## R06.2 — потери, рекуперация и торможение преобразовательного привода, 10 ноября 2026

V/f-привод теперь поддерживает Gc>0 за инвертором: потокосцепленные состояния
машины, 1–4 клетки, насыщение и две температуры включены в энергетическую
цепочку AC-порт → активный выпрямитель → DC-конденсатор → инвертор → машина.
Gc=0 сохраняет прежний токовый компонент. Потери сердечника меняют входную
мощность двигателя, производную DC-энергии и запрос выпрямителя, а не только
рисуют дополнительный диагностический канал. Ток потерь не создаёт момент.

Для обеих полярностей мощности соблюдаются определения:
Pdc_rect=eta_rect·Pgrid при Pgrid≥0, иначе Pgrid/eta_rect;
Pdc_inv=Pmotor/eta_inv при Pmotor≥0, иначе eta_inv·Pmotor.
Потери выпрямителя Pgrid−Pdc_rect и инвертора Pdc_inv−Pmotor неотрицательны.
DC-баланс: dE/dt=Pdc_rect−Pdc_inv−Pbleed−Pbrake, E=C·Udc²/2.
Потери стали и меди попадают в моторную тепловую модель; при отсутствии
тепловых узлов выводятся наружу. Конверсионные потери и тепло внешних
резисторов выводятся из моделируемой системы; температуры электроники/резистора
не вычисляются. Энергия вала и механическая работа учитываются отдельно.

Добавлен необязательный усреднённый тормозной резистор. Между явно заданными
Ustart/Ufull duty линейно растёт от 0 до 1; Pbrake=duty·Udc²/R.
Используется среднее квадрата напряжения, не квадрат среднего duty·Udc.
Все параметры и происхождение обязательны вместе, Uref≤Ustart<Ufull, R>0.
Ufull означает полное включение, не гарантированную верхнюю границу Udc:
недостаточный резистор не заменяется искусственным ограничением энергии.
Это авторский непрерывный averaged-закон, не PWM-транзистор и не гистерезисный
контроллер. В корпусе отключён возврат в сеть, а энергия генерации двигателя
реально повышает DC-напряжение и рассеивается в резисторе.

Публикуются мощности обеих DC-сторон, потери ступеней/bleed/brake, энергия
конденсатора, duty, токи и поток статора, момент, моторные потери и температуры.
Токовые peak/vector-каналы — мгновенный модуль αβ-вектора, не максимум
за исследование; последний отдельно оценивается по выходным отсчётам.
`motor_initial_fluxes_ab_vs` задаёт пиковые SI αβ-потоки статора и каждой клетки;
Gc=0 преобразует их в прежние токовые координаты. Инвентарь и конечность
параметров проверяются. Ранние канонические JSON без flux/braking полей сохраняют
прежнюю форму. Бюджет выхода расширен с учётом нового числа каналов привода.

connected управляет идеализированным AC-портом. При его отключении DC-энергия
и потоки не сбрасываются, инвертор продолжает питать мотор из DC. При малом
Udc выходное напряжение ограничивается и непрерывно снижается к нулевому
вектору; это не физический разрыв обмотки. Недопустимо отрицательная DC-энергия
даёт ошибку модели, а не постфактум нарисованный баланс. current_limited
отражает превышение запроса до учёта connected; фактический ток отключённого
AC-порта равен нулю. Проверяется предел вектора AC-тока √2·Ilimit, не защита
всех линий, моторного тока или истинного фазного RMS при любых искажениях.

Шесть профилей на явной трёхфазной RLC-сети с взаимными индуктивностями:
разгон с Gc, нагрев четырёхклеточной машины при ограничении входного тока,
провал фазы/отключение/восстановление AC-порта, генерация с возвратом в сеть,
разряд DC с undervoltage/modulation и тормозной резистор без экспорта в сеть.
Электрический/магнитный seed взят из опубликованного Aalto-примера; дополнительные
клетки, Gc, КПД, сеть, тепловые параметры, контроллеры и тормозные параметры
явно авторские. Тормозной кейс использует линейную Γ-модель и начальные потоки,
вычисленные из аналитической генераторной проводимости при 180 rad/s.

Для каждого — coarse/fine Radau и отдельная all-flux RLC/controller реализация
с DOP853. Она не вызывает основное сетевое, машинное или приводное RHS.
Повторяются одинаковые объявленные контроллерные законы другим кодом;
это не сверка с OEM-контроллером. Сравниваются все опубликованные drive-каналы,
PCC-напряжения, токи линий, булевы режимы и полный баланс энергии.
Уточнение уменьшило максимальную нормированную ошибку во всех шести случаях.
Максимальные расхождения: Udc 3.26e-7 V, модуль моторного тока 7.25e-7 A,
скорость 7.75e-8 rad/s, core loss 6.78e-6 W. Максимальная конечная относительная
невязка полной энергии 6.17e-11 в объявленной нормировке. Это численная согласованность
реализаций; погрешность относительно настоящего привода не измерялась.

В генераторном примере Pmotor достигает около −4.32 kW, Pgrid −3.98 kW;
по экспортным отсчётам через моторный порт восстановлено около 103 J.
Это интеграл по отсчётам, не отдельная точная квадратура восстановленной энергии.
В тормозном примере Udc достигает около 736.22 V, duty 0.4056,
мощность резистора 5.50 kW при нулевом экспорте в AC-порт.
Это наблюдения конкретных входов, не рекомендации выбора аппаратуры.
Окна 0.12–0.20 s не доказывают длительный установившийся режим или
успешный промышленный разгон любого механизма. Корпус имеет 6 узлов,
один привод и до 26 состояний сети/контроллеров/машины/тепла плюс две
квадратуры энергии; поле ресурсов physical_states включает память регуляторов.
Лимиты общего API не считаются проверенными масштабами этого корпуса.

156 целевых тестов прошли: старые фазные/обмоточные/защитные профили и новые
локальные законы энергии, четыре сочетания полярностей мощности, Gc→0,
непрерывность энергии/потоков при блокировании, обратная связь core loss,
корректное усреднение мощности тормозного резистора и отрицательный DC-домен.
Согласованная подмена DC-диагностики и обеих primary/reference траекторий
после запечатывания обнаруживается новым запуском независимого эталона.
Полный release-профиль не запускался; старые receipt относятся к своим SHA.

Границы физики остаются существенными: усреднённый управляемый AC-порт,
постоянные КПД, скалярная положительная V/f-рампа, авторские PLL/PI и усреднённый
тормозной duty. ШИМ, переключения полупроводников, проводимость диодов при
блокировании ключей, предзаряд, ограничения напряжения реального AFE,
потери как функции I/T/f_switch, моторный current-control/FOC и тепловая
защита электроники не реализованы этим блоком. connected=false не следует
трактовать как реальную схему блокирования IGBT с оставшимися диодами.
Предел инверторной амплитуды — 0.5·Udc·M в данной объявленной модели.

Команды:
```bash
power-grid phase-study run --study cases/vf-core/braking_resistor.json --out reports/vf-core-local
python -m benchmarks.vf_core_acceptance --out reports/vf-core-independent
python -m benchmarks.vf_core_acceptance --verify reports/vf-core-independent
```
В `cases/vf-core/` лежат все шесть примеров. verify приёмки заново запускает
независимый ODE; phase-study verify подтверждает только целостность пакета.
Свежий wheel проверяется вне checkout с заблокированными imports benchmarks/
PP/ANDES/motulator: разгон, тепловой профиль, рекуперация, тормозной резистор,
KCL/энергия и ZIP-перенос. Это тот же host и venv с системными зависимостями,
не независимая ОС. В ресурсах сохранены RHS, размер состояния и число отсчётов;
скоростное превосходство над чужими пакетами не измерялось.
Приёмка: `reports/vf-core-final-qualified-2026-11-10/summary.json`.
Поставка: `reports/vf-core-delivery-2026-11-10/delivery.json`.
Следующая физическая очередь — реальные схемы силовой части: диодный мост,
предзаряд/DC, затем инвертор с регулированием моторного тока и независимые
сверки таких компонентов. Универсальная точность, все масштабы и общее
превосходство не доказаны. GitHub/CI/коммиты/push не использовались.


## R06.3 — диодный вход, предзаряд и контур тока инвертора (10 ноября 2026)

Реализован отдельный пассивный вход привода `diode_front_end`: шестипульсный
трёхпроводный мост, постоянное прямое падение и **положительное сопротивление
каждого диода**, общее последовательное сопротивление DC-ветви, предзарядный
резистор и его байпас. Все параметры и происхождение задаются явно. Сопротивления
диодов — не скрытая стабилизация: они входят в KVL, токи и выделяемое тепло.
При коммутации могут одновременно проводить три диода; карта терминальных
токов непрерывна, включая равные фазные напряжения. Внутренняя индуктивная
коммутация, reverse recovery и паразитные ёмкости диодов не моделируются.

Предзаряд допускает ровно нулевое начальное Udc. Для пассивного входа
DC-состояние — напряжение, а энергия вычисляется как C·Udc²/2; у прежнего
активного входа сохраняются координаты энергии и старый формат JSON.
Это устраняет сингулярность энергетической координаты при зарядке с нуля,
не добавляя искусственной начальной энергии. Баланс:
`C*dUdc/dt = Ibridge - Pinverter_dc/Udc - Udc/Rbleed - Ibrake`.
При Udc=0 применённое напряжение инвертора равно нулю и его мощность равна нулю.

`drive_precharge_bypass` — явное событие 0/1, а не неявное разрешение работы
по времени. Как и `converter_connection`, оно сегментирует интегрирование;
сохраняются обе стороны события без скачка Udc и потокосцеплений. В примерах
время байпаса авторское: автоматическая готовность по напряжению, диагностика
сваривания контактора и тепловая защита резистора ещё не реализованы.
`connected=false` здесь означает **разомкнутый AC-контактор**; это не команда
блокирования IGBT, не отключает моторный инвертор и не обнуляет поток.

Диодный вход не экспортирует энергию в AC: регенерирующий двигатель заряжает
DC-конденсатор, а опциональный тормозной резистор рассеивает её. Входной ток
определяется схемой, не `grid_current_limit_rms_a`; старые PLL/DC-PI состояния
в этом режиме не используются, `current_limited=false`. `rectifier_efficiency`
должен быть 1: потери моста уже явно рассчитываются по I²R и падению диодов.
Резистор предзаряда и диоды рассеивают тепло наружу; температурных состояний
полупроводников/резистора в этом профиле нет. При начальном RLC-звоне мост
может кратковременно импортировать энергию и в регенеративном исследовании;
запрет обратного потока не означает нулевой прямой ток во всё окно.

Опциональный `current_control` заменяет скалярную V/f-амплитуду на PI-регулятор
тока в заданной синхронной системе координат, с рампой задания, круговым
ограничением напряжения 0.5·Udc·M и tracking anti-windup. Угол и частота
по-прежнему задаются частотной рампой привода. Интегратор хранится в αβ и
имеет два дополнительных состояния; энергетических запасов он не добавляет.
При Gc>0 алгебраическая обратная связь тока решается **до** ограничения
напряжения; anti-windup использует ошибку фактического тока после ограничения.
Нет жёсткого ограничения моторного тока: PI может иметь переходное
перерегулирование. Это не rotor-flux FOC, не регулятор скорости/момента,
не наблюдатель потока и не заявленный устойчивый промышленный контроллер.
Независимый эталон интегрирует PI в dq, с отдельным преобразованием координат.

Пассивный вход соединён с общей фазной RLC-сетью. Нелинейный ток учитывается
в KCL, источниковой работе и энергии. Терминалы должны быть источниками
заданного напряжения либо иметь полностью динамические напряжения ёмкостей.
Терминалы с алгебраической свободой отклоняются понятной ошибкой: общий
нелинейный алгебраический решатель моста этим блоком не добавлен. Общий
потенциал трёх проводов не влияет на мост. Сетевые индуктивности моделируются
до PCC-ёмкостей; удалять эти ёмкости и трактовать модель как индуктивную
коммутацию непосредственно в мосту нельзя.

Семь авторских исследований `cases/diode-drive/`: предзаряд, ранний байпас,
провал/отключение/возврат AC, регенерация без/с тормозным резистором,
контур тока и контур тока при провале. Корпус: 6 узлов, один привод,
0.12 s; это не длительный промышленный разгон и не проверка всех сочетаний.
Два native-прогона на случай сверяются с отдельно написанным ODE:
в эталоне шесть диодов решаются из rail-KCL и DC-KVL, двигатель — во
flux-координатах, PI — в dq. Производственные RHS/bridge/control не вызываются.
Проверяются все экспортированные каналы привода/PCC/линий, энергия,
KCL и улучшение максимальной нормированной ошибки при уточнении расчёта.
Распределение потерь проверяется аналитическими локальными балансами;
предзаряд дополнительно сверяется с закрытой RC-формулой постоянного источника.
Это два кода объявленных уравнений, а не измерения OEM или запуск PLECS.

```bash
power-grid phase-study run --study cases/diode-drive/current_control_sag.json --out reports/diode-local
python -m benchmarks.diode_drive_acceptance --out reports/diode-independent
python -m benchmarks.diode_drive_acceptance --verify reports/diode-independent
```

`--verify` повторно интегрирует независимый эталон и проверяет текущие
исходники, параметры, инвентарь, каналы и выводы. SHA и манифест подтверждают
целостность, а не аутентичность происхождения. Исторические R06.1/R06.2 receipt
относятся к своим исходникам; новый код требует собственной приёмки.

Физические ориентиры: [PLECS — Three-Phase Diode Bridge Rectifier](https://docs.plexim.com/plecs-demos/basic-topologies/three-phase-diode-bridge-rectifier/),
[Imperix — DC bus pre-charge](https://imperix.com/doc/implementation/dc-bus-pre-charging-techniques),
[MathWorks — Rectifier (Three-Phase)](https://www.mathworks.com/help/simscape-electrical/ref/rectifierthreephase.html).
Публикации описывают топологии и области применения; наши авторские
сопротивления, паспорта и регулятор не являются параметрами этих изделий.

Следующая физическая очередь: индуктивная коммутация и общий нелинейный
алгебраический порт, автомат предзаряда с interlock/отказами, rotor-flux FOC
и управление моментом/скоростью, ключевой инвертор/ШИМ и измеренные карты
потерь/тепловой защиты. Универсальное превосходство не доказано.


### Проверки поставки R06.3

192 целевых теста прошли без предупреждений и пропусков (36 новых + 156
регрессий). Это не полный release-профиль. Прошли 7 новых исследований
(14 native-прогонов) и повторная квалификация всех 6 прежних active-V/f
исследований (12 native-прогонов): уточнение уменьшило максимальную
нормированную ошибку во всех 13 случаях.

Новый диодный корпус: максимальное расхождение экспортированных Udc с
независимым ODE — 3.70e-6 V, моторного αβ-модуля тока — 3.78e-7 A,
скорости — 9.00e-8 rad/s. Максимальная **конечная** относительная невязка
энергии — 6.93e-12; максимум KCL по экспортированным отсчётам — 1.43e-14 A.
Это ошибка между реализациями объявленных уравнений, не точность относительно
измерений. В новом корпусе 18–20 состояний, включая регуляторы, плюс две
энергетические квадратуры. В авторском тормозном сравнении пиковое Udc
снижается с 770.77 до 733.87 V; это наблюдение выбранного входа, а не предел
напряжения или рекомендация выбора оборудования.

Свежий wheel установлен вне checkout и проверен через CLI на трёх исследованиях:
предзаряд, контур тока с провалом AC и тормозной резистор. Импорты benchmarks,
pandapower, ANDES и motulator заблокированы; ZIP экспортирован, восстановлен
и проверен. Это тот же host/OS и venv с системными зависимостями.

Текущие результаты:
- `reports/diode-current-final-v2-qualified-2026-11-10/summary.json`;
- `reports/vf-core-diode-final-regression-2026-11-10/summary.json`;
- `reports/diode-current-final-v2-tests-2026-11-10.xml`;
- `reports/diode-current-portable-2026-11-10/receipt.json`.

GitHub, CI, индексация, коммиты и push не выполнялись. Общая физика проекта,
все масштабы и превосходство над другими симуляторами не объявляются закрытыми.


## R06.4 — связанный ключевой привод, sensored FOC и тепловые отключения

Исторический профиль; расширение магнитной физики приведено ниже в R06.5.

Реализован новый профиль `power-grid-env.switching-drive/v1`, команда
`power-grid switching-drive`. Это отдельный связанный электротепломеханический
решатель: три AC-ветви **R–L без PCC-ёмкостей** → шесть диодов → плавающие
DC-шины/конденсатор → шесть MOSFET-каналов с обратными диодами → изолированная
звезда линейной одноклеточной Γ-машины. Накоплена энергия входных реакторов,
DC и явно заданных rail-to-earth ёмкостей, обмоток, вращения и трёх тепловых
ёмкостей. Параметры сети и силовой части авторские, не паспорт изделия.
Этот профиль не меняет усреднённые R06.2/R06.3 и не подключается автоматически
к произвольной DAE/RMS-топологии проекта.

**FOC.** Наблюдатель потока ротора использует измеренные токи статора и
механическую скорость; истинное потокосцепление из модели ему не передаётся.
Переход к inverse-Γ: `gamma=Lm/(Lm+Ll)`, `LM=gamma*Lm`, `RR=gamma²*Rr`,
`Lsigma=gamma*Ll`. Наблюдатель интегрируется точным ZOH для постоянных
средних измерений за PWM-период: `dPsiR/dt=RR*(Is-PsiR/LM)+j*p*w*PsiR`.
Угол FOC определяется оценкой PsiR; d-ток создаёт поток, q-ток задаётся
PI-регулятором скорости через `Tem=1.5*p*|PsiR|*Iq`. При недостаточном потоке
момент не разрешается. Есть рампы потока/скорости, круг задания тока,
feedforward/PI тока, ограничение напряжения и anti-windup обоих регуляторов.
У скорости используется условное интегрирование при ограничении момента;
у тока — tracking back-calculation по насыщенному напряжению.
Это sensored FOC с известными постоянными параметрами, не sensorless,
не идентификация Rr и не управление насыщенной многоклеточной машиной.
Фактический ток не обрезается до задания: выбросы и ШИМ-пульсации остаются.

**ШИМ и ключи.** Центрированные импульсы с common-mode injection, удержание
задания на период, точное разбиение времени по фронтам и фиксированному
положительному dead time. Доля импульса ограничена явно, чтобы сохранить
ненулевые интервалы между комплементарными ключами. Shoot-through отклоняется.
MOSFET-канал — двунаправленное сопротивление во включённом состоянии; при
выключении канала остаётся обратный диод с Vf/R. Это не IGBT-модель.
Блокировка импульсов, в том числе тепловая, не обнуляет поток и токи; обратные
диоды допускают возврат магнитной энергии в DC-звено. Разрешение по Udc имеет
гистерезис; команда блокирования доступна явными временными окнами.

**Индуктивная коммутация.** Токи входных реакторов — динамические состояния.
Перенос тока между фазами получается из совместного решения сети/диодов,
включая интервалы одновременной проводимости более двух диодов. Это не
резистивный мост R06.3 и не ограничение тока постфактум. Сопротивления диодов
и каналов положительны и зависят от температуры; идеальное запирание остаётся
кусочно-линейным. DC-дроссель, reverse recovery, Coss, avalanche, dv/dt и
распределённая кабельная/EMI-физика не добавлены. Общий потенциал полностью
изолированного непроводящего двигателя выбирается как gauge без добавления
скрытого резистора/ёмкости; напряжения общей моды такого двигателя не
квалифицированы. Rail-to-earth ёмкости DC-шин, напротив, задаются явно и
участвуют в KCL и энергии.

**Тепло и защита.** Три RC-узла — выпрямитель/предзаряд, инвертор, тормозной
резистор. Проводниковые потери поступают в тепло, охлаждение — наружу.
У каналов добавлены Eon/Eoff по авторской линейной зависимости |I|, Udc и T.
Энергия переключения вычитается из DC-ёмкости и в тот же момент поступает
в тепловую ёмкость: это перенос энергии, а не дополнительный источник тепла.
Порог температуры локализуется повторным пробным шагом до принятия состояния;
отключение защёлкивается. Пробные решения не меняют физическое состояние.
При перегреве блокируются моторные ключи; реакторы не разрываются идеальным
контактором. При перегреве тормозного резистора его duty обнуляется. Выпрямитель
и обратные диоды могут продолжать проводить и нагреваться после trip —
порог не является жёстким потолком температуры. Автоповторного пуска нет.
Предзарядный резистор находится в каждой AC-ветви; байпас по Udc разрешается
на границе PWM-периода после минимального времени и запрещается после trip.
Сваривание контактора и аппаратные цепи отключения со snubber ещё не моделируются.

**Численный метод и применимость.** Совместный implicit midpoint для реакторов,
конденсаторов, обмоток и вала; 8 узловых напряжений, Newton/active conduction
states, масштабированное линейное решение и повторная итерация механической
скорости. Ёмкости, индуктивности и кинетическая энергия входят в общий аудит.
Энергетическая невязка не исправляется после интегрирования. Тепловой RC имеет
midpoint-шаг; сопротивления и duty тормоза фиксируются на принятом подшаге.
Цифровой FOC обновляется раз в период. Совпадающие carrier/sample границы
объединяются лишь в пределах 0.5 ps; минимальные поддержанные max-step/sample/
dead-time — 1 ns. Этот предел не означает точность физических моделей на ns.

Профиль намеренно принимает только линейную Γ-машину с одной клеткой, Gc=0,
свободным валом и без моторных тепловых состояний. Насыщение, несколько клеток,
потери в стали, locked-rotor и моторная thermal-модель **явно отклоняются**,
а не молча упрощаются. В прежних усреднённых профилях соответствующие
возможности остаются. Есть 13 непрерывных состояний накопителей плюс память
цифровых регуляторов, дискретные ключи/защиты. Это один привод, не доказанное
масштабирование произвольной ключевой промышленной сети.

Семь авторских исследований `cases/switching-drive/`: пуск FOC (0.48 s),
блокировка/включение ШИМ, провал сети, тепловые trip трёх отдельных узлов и
рекуперация (остальные по 0.16 s). Три шага — 20/10/5 us; частота ШИМ 2.5 kHz,
dead time 2 us. Сравниваются Udc, скорость, температуры и оценка потока,
энергия, KCL, порядок и времена событий. Мгновенный RMS и непрерывные экстремумы
не сертифицируются; токовый максимум учитывает принятые подшаги. Каналы
`last_midpoint_*` относятся к последнему внутреннему интервалу, а не точно
к моменту экспортированного отсчёта.

Независимый stage-oracle формулирует KCL/KVL, потокосцепления и движение вала
напрямую в 16 смешанных неизвестных и решает их SciPy root, без производственных
Norton-stamps/RHS/FOC/ШИМ. Проверяются 12 разных состояний, включая dead time,
обратные диоды, вращение обоих знаков и холодные/нагретые ветви. Это отдельная
реализация **одного связанного шага**, не независимая полная замкнутая траектория.
Дополнительно наблюдатель сверяется с ODE, тепловой RC — с аналитическим
экспоненциальным решением и вторым порядком сходимости. Нельзя называть такую
приёмку внешним OEM/head-to-head тестом всего привода.

```bash
power-grid switching-drive run --study cases/switching-drive/foc_start.json --out reports/switching-local
power-grid switching-drive verify reports/switching-local
power-grid switching-drive export reports/switching-local --out reports/switching-local.zip
python -m benchmarks.switching_drive_acceptance --out reports/switching-independent
python -m benchmarks.switching_drive_acceptance --verify reports/switching-independent
```

CLI `verify` проверяет целостность. Benchmark `--verify` повторяет **все 21
native-траектории** и 12 независимых stage-oracle, проверяет текущие параметры,
исходники и вычисляет выводы повторно. Манифест не аутентифицирует источник.
GitHub, CI и операции добавления/коммитов/push не нужны и не выполняются.

Уравнения и физические ориентиры: [Aalto — Induction Machines](https://aalto-electric-drives.github.io/motulator/model/drive/induction_machine.html),
[Aalto — Current-Vector Control](https://aalto-electric-drives.github.io/motulator/autoapi/motulator/drive/control/im/index.html),
[Imperix — Dead time](https://imperix.com/doc/help/dead-time-selection-for-imperix-power-modules),
[Imperix — Thermal simulation](https://imperix.com/doc/wp-content/uploads/post_to_pdf/PN132.pdf).
Численные паспорта, коэффициенты Eon/Eoff и настройки регуляторов авторские.
Они не перенесены из параметров изделий этих производителей.


### Результаты проверки R06.4

233 целевых теста прошли без предупреждений/пропусков: 41 новый тест ключевого
профиля и 192 регрессии фазных сетей, обмоток, защит, усреднённых приводов,
диодного входа и контуров тока. Полный release-профиль проекта не запускался.

Прошли все 7 исследований на 3 шагах (21 траектория). Во всех случаях
fine→finest ошибка меньше coarse→finest; порядок событий совпал. Максимальное
расхождение времени событий между 10 и 5 us — 2.82 us. Для 10 us максимальная
конечная относительная невязка всей энергии — 9.13e-12, KCL — 1.46e-7 A.
Малая невязка доказывает согласованность дискретного баланса, не точность
относительно реального оборудования. Начальная энергия/механическая работа
учитываются при нормировании, поэтому показатель нельзя напрямую сравнивать
с иначе нормированными отчётами прежних профилей.

Пуск FOC: задание 30 rad/s, в 0.48 s получено 30.534 rad/s, максимум по
экспортированным отсчётам около 30.723 rad/s. Задание тока ограничено 10 A
по αβ-модулю; фактический подшаговый максимум около 10.645 A — ток не
обрезается в решателе. Входная индуктивная коммутация и проводимость обратных
диодов наблюдаются в связанных прогонах. Тепловые события отдельно получены
для выпрямителя, инвертора и тормозного резистора. Низкие стресс-пороги в
примерах авторские. Выпрямительный тест использует 40 J/K и остаётся ниже
27 °C, вместо непригодного для инженерной квалификации экстремального нагрева.

12 независимых смешанных stage-oracle прошли; максимальная невязка их
собственных KCL/KVL/flux/shaft уравнений — 4.06e-10, расхождение тока с
основным шагом — 5.38e-14 A. Это математические проверки выбранных стадий.
Полной независимой замкнутой траектории и внешнего OEM-бенчмарка здесь нет.

Установленный wheel проверен вне checkout через CLI: пуск, перегрев инвертора
и рекуперация; ZIP восстановлен и проверен. Imports benchmarks/pandapower/
ANDES/motulator заблокированы. Это тот же host/OS, venv с системными зависимостями.

Приёмка умеет `--reuse-from EXISTING`: в **новый** каталог копируются только
проверенные sealed-результаты с тем же producer SHA и полностью совпавшим
каноническим исследованием; изменённые входы пересчитываются. В финальной
сборке так переиспользованы 18 результатов, 3 пересчитаны после уточнения
теплового стресс-входа. Benchmark `--verify` всё равно повторяет все 21
траекторию с нуля, а не считает кэш независимым запуском.

Текущие артефакты:
- `reports/switching-drive-qualified-final-2026-11-10/summary.json`;
- `reports/switching-drive-release-final-tests-2026-11-10.xml`;
- `reports/switching-drive-release-portable-2026-11-10/receipt.json`.

Сеть этого корпуса имеет balanced source, общую амплитудную envelope и
одинаковые входные RL; произвольные топологии/несимметрия этим профилем
не квалифицированы. Следующие расширения — независимая полная closed-loop
траектория, общая DAE-сеть, насыщенные/multicage/core-loss машины в ключевом
профиле, hardware-fitted карты потерь/тепла, sensorless/адаптация и собственно
зарядовая/высокочастотная физика полупроводников. Это уточнение применимости,
а не требование иметь собственное оборудование. Все четыре направления
R06.4 реализованы в указанном ограниченном профиле; превосходство над всеми
симуляторами и завершение всего проекта не объявляются.


Финальный benchmark `--verify` успешно повторил все 21 native-траекторию и
12 независимых stage-oracle на текущем производителе. Итоговый пакет исходников,
входов, результатов, JUnit и wheel: `reports/switching-drive-delivery-2026-11-10/`.
Его манифест подтверждает целостность; это локальная поставка без GitHub/CI,
а не аутентифицированная независимая публикация.


## R06.5 — насыщенные многоклеточные двигатели с потерями в стали в ключевом приводе

Историческое ограничение motor.thermal снято расширением R06.6 ниже.

Расширен именно связанный ключевой решатель, а не только его диагностические
каналы: 1–4 роторные RL-ветви, монотонное радиальное насыщение и положительная
вихретоковая проводимость Gc теперь совместно работают с входными реакторами,
диодным мостом, DC-звеном, PWM MOSFET-инвертором и его тепловыми защитами.
Электрические состояния каждой клетки сохраняются отдельно; перестановка
клеток с их параметрами/начальными потоками не меняет сетевое решение.
Это дискретный спектр клеток, не автоматически идентифицированный skin-effect.
Паспортные значения и дополнительные клетки/кривая насыщения в корпусе авторские.

**Нелинейная энергия.** Обычный midpoint заменён в магнитной характеристике
радиальным дискретным градиентом. Для Wmag = 1.5/Lm·[r²/2+beta^e·r^(e+2)/(e+2)]
он удовлетворяет точной дискретной цепочке изменения энергии. Разделённая
разность радиусов вычисляется конечной суммой целых степеней без вычитания
почти равных энергий; проверены нулевые/равные радиусы, разворот потока и
степени 1,2,3,7,20. Есть аналитический блочный Jacobian характеристики и
итерационное обновление Norton-порта до согласования тока/напряжения.
Инкременты потоков решаются напрямую: это избегает потери точности при
вычитании близких полных потокосцеплений на коротких PWM-интервалах.
Изменение энергии не компенсируется исправлением отчёта после расчёта.

В проводниках и реакторах остаётся midpoint. Для Gc ток статора равен
магнитному/роторному току плюс Gc·dPsi_s/dt; в тепло уходит
`1.5*Gc*|dPsi_s/dt|²`. Вихретоковая составляющая **не создаёт момент**.
Реальная нелинейная магнитная энергия, энергия всех роторных ветвей,
кинетическая/DC/реакторная и тепловые энергии входят в общий баланс.
Принимаемая невязка конститутивного уравнения двигателя — ниже 1e-8 V;
KCL пересчитывается с фактическим нелинейным током, а не с одним линейным tangent.

**Ток на ключах.** При Gc>0 поток непрерывен, но терминальный ток имеет
прямую зависимость от напряжения и может измениться сразу при коммутации.
Добавлен отдельный мгновенный трёхузловой solve при фиксированных потоках,
DC-напряжении и текущих gate/body-diode состояниях. Экспорт тока, измерение
FOC и Eon/Eoff больше не используют устаревший ток предыдущего интервала.
Eon оценивается по току после команды включения, Eoff — до выключения,
при DC-напряжении до переноса switching-energy в тепло. Это объявленная
авторская модель потерь, не измеренные кривые устройства. Блокирование не
обнуляет потоки и состояния клеток. Мгновенный solve не изменяет физическое
состояние; тест проверяет баланс переноса DC-энергии в тепло.

Потери в стали интегрируются отдельным диагностическим счётчиком
`motor_core_heat_j`; их максимум по принятым midpoint-стадиям доступен как
`peak_substep_motor_core_loss_w`. Это важно при PWM: редкие экспортированные
отсчёты могут попадать в одну фазу carrier и существенно занижать видимый
пик. Интеграл не вычисляется трапециями по прореженным каналам.
`last_midpoint_*` остаются данными последнего интервала, а не гарантированным
значением точно в момент вывода. Измерения на совпавшей gate/sample границе
используют предыдущее gate-состояние; `gates_enabled` — разрешение управления,
а не снимок шести фактических ключей. Непрерывные экстремумы не сертифицируются.

**Номинальная модель FOC.** Для насыщения, нескольких клеток либо Gc обязателен
явный `observer_motor`: отдельный линейный одноклеточный паспорт без core loss,
с тем же числом пар полюсов. Первая клетка не выбирается молча. Наблюдатель
по-прежнему получает только измеренные токи/скорость и не читает истинные
потоки модели. В примерах multicage номинальная редукция задаёт параллельные
низкочастотное Rr и высокочастотное L_leak, а PI тока перенастроен под эту
утечку; это объявленная аппроксимация, не точный эквивалент всего спектра.
При насыщении/steel loss наблюдатель также остаётся номинальным. Его оценка
не объявляется истинным потоком любой клетки; идентификация, насыщенный
observer и sensorless этим блоком не реализованы. Старый линейный одиночный
двигатель может использовать прежний интерфейс без нового паспорта.

`motor_initial_fluxes_ab_vs` задаёт статор и каждую клетку; неполный инвентарь
отклоняется. У насыщенных примеров явно объявлены начальные потоки .6/.58 Vs,
их энергия учтена с первого отсчёта. Это authored warm state, не решение
нагруженной согласованной инициализации. `motor.thermal` и locked rotor
в ключевом профиле пока отклоняются. Моторные медные/steel потери рассеиваются
наружу; температуры и изменение Rs/Rr обмоток здесь не моделируются.
Тепловая модель силовых модулей предыдущего блока сохраняется.

**Проверки.** Семь extended-сценариев `cases/switching-extended/`: steel loss
одноклеточного двигателя, double cage, four cage с Gc, насыщение с Gc,
насыщенный двухклеточный двигатель с блокированием PWM, рекуперация и
насыщенный четырёхклеточный двигатель с Gc. Окна по .12 s — ранний переходный
процесс, а не законченный промышленный разгон до номинала. Дополнительно
повторён прежний линейный FOC-пуск .48 s. Каждый случай рассчитывается при
20/10/5 us. Хранятся потоки каждой клетки, steel heat, истинная магнитная
энергия, KCL, конститутивная невязка и все прежние силовые/тепловые каналы.
Данные новых клеток, beta=2/Vs и Gc=.006 S авторские, не OEM-валидация.

Независимый смешанный stage-oracle расширен до 16–22 неизвестных и
непосредственно пишет сетевые KCL/KVL, обмоточные и механические уравнения.
Он вычисляет радиальную энергоразность в extended precision; производственный
метод использует другой алгоритм — конечную сумму степеней. Проверены 14
связанных состояний с включёнными ключами и полным gate blocking.

Отдельный continuous ODE oracle вообще не импортирует производственные
магнитные/RHS/control функции. Для семи паспортных случаев он интегрирует
все потоки и энергетические квадратуры под заданным вращающимся напряжением
180 V peak / 40 Hz, phase .2 rad, при фиксированной скорости 35 rad/s.
Это 7 зарегистрированных сравнений магнитной подсистемы; winding-условия
saturated_core и saturated_regeneration здесь совпадают при фиксированном
вале, хотя полные приводные исследования различны. Проверяется вся .01 s
траектория из 101 отсчёта, работа источника и интеграл steel heat. Нативная
подсистема имеет 3 шага, continuous reference — DOP853 с малым шагом/строгими
допусками. Это независимая динамическая сверка **магнитной подсистемы**,
не полная независимая замкнутая траектория PWM/FOC/сети.

```bash
power-grid switching-drive run --study cases/switching-extended/saturated_four_cage.json --out reports/extended-local
python -m benchmarks.switching_extended_acceptance --out reports/extended-independent
python -m benchmarks.switching_extended_acceptance --verify reports/extended-independent
```

Benchmark `--verify` повторяет все 24 полных native-траектории, 14 смешанных
oracle и 7 continuous ODE-сверок с нативными winding-прогонами на трёх шагах.
CLI `switching-drive verify` проверяет лишь целостность. Кэш допускается только
при совпадении текущего producer SHA, полного канонического исследования и
sealed-инвентаря; окончательный replay всё равно выполняется с нуля.
Исторические receipt R06.4 относятся к своему производителю, не автоматически
к новому расширенному решателю. Полный release-профиль проекта не запускался.

Физическая основа: [Aalto — Γ model](https://aalto-electric-drives.github.io/motulator/model/drive/induction_machine.html).
Структурный принцип: [McLachlan & Quispel, Discrete gradient methods have an energy conservation law](https://www.aimsciences.org/article/doi/10.3934/dcds.2014.34.1099).
Конкретная магнитная дискретизация и её проверка здесь реализованы для
объявленного lumped Γ-паспорта; публикации не удостоверяют наш решатель.


### Итог R06.5: проверенные результаты

276 целевых тестов прошли без предупреждений и пропусков: 43 новых и 233
регрессии. Прошли 8 связанных исследований на 3 шагах (24 native-траектории),
14 независимых смешанных стадий и 7 зарегистрированных continuous ODE-сверок
магнитной подсистемы с 21 native-winding прогоном. Ошибка при уточнении шага
уменьшилась во всех связанных и winding-случаях.

При 10 us максимальное расхождение winding-потоков с continuous reference —
4.42e-7 Vs, интеграла steel heat — 3.26e-7 J. Максимальная конечная относительная
невязка всей энергии полного привода — 9.04e-12, KCL — 4.45e-6 A. Это согласование
объявленных уравнений и дискретизации, не OEM/hardware-точность. Полный
независимый closed-loop PWM/FOC/network benchmark по-прежнему открыт.

Старый одиночный линейный FOC-пуск воспроизведён новым обобщённым решателем:
30.534 rad/s при задании 30 rad/s в .48 s. Extended-окна .12 s показывают
раннюю динамику: конечные скорости не являются утверждением о завершённом
промышленном разгоне. Учёт core current не ограничен диагностикой: изменения
Gc меняют поток, DC-динамику и ток на ключах; это отдельно проверено тестом.
Для core_single интеграл steel heat 16.142 J и максимум по midpoint-стадиям
около 1183.83 W, при том что прореженный канал может показывать гораздо
меньший пик. Никакого токового clipping по ограничению задания не выполняется.

Финальный `--verify` заново выполнил все 24 native-траектории, 14 смешанных
oracle и 7 continuous reference/winding сравнений. До этого 17 неизменённых
sealed-прогонов были переиспользованы по точным входам и producer SHA;
это не засчитывается как независимый запуск. Replay выполнен с нуля.

Свежий wheel проверен вне checkout с запрещёнными imports benchmarks/
pandapower/ANDES/motulator: насыщенные four-cage и регенеративный случаи,
KCL/энергия/steel heat, отдельные cage channels и ZIP-восстановление.
Это тот же host и venv с системными зависимостями, не независимая ОС.

Текущие артефакты:
- `reports/switching-extended-final-qualified-2026-11-10/summary.json`;
- `reports/switching-extended-final-tests-2026-11-10.xml`;
- `reports/switching-extended-portable-2026-11-10/receipt.json`;
- `reports/switching-extended-delivery-2026-11-10/delivery.json`.

Следующие очереди: motor winding thermal/R(T) в ключевом профиле,
насыщенный/многоклеточный observer вместо номинальной редукции, независимая
полная drive-траектория и подключение к общей DAE-сети. Отдельно остаются
hardware-fitted карты потерь и charge-dynamic/EMI-профили. GitHub/CI,
индексация/коммиты/push не выполнялись. Полный release-профиль не запускался.
Универсальное превосходство и готовность всего проекта не объявляются.


## R06.6 — нагрев обмоток, Rs/Rr(T) и защита в ключевом приводе

`motor.thermal` теперь поддерживается в связанном ключевом профиле, включая
насыщенную четырёхклеточную Γ-машину с Gc. Два дополнительных состояния —
температуры статора и общего роторного узла. Все клетки имеют отдельные токи,
потоки и сопротивления, но общую Tr и один температурный коэффициент паспорта.
Модель не разрешает пространственные hotspots/отдельные температуры клеток.

Опциональные поля исследования:
- `motor_initial_temperature_c: [Ts,Tr]`, по умолчанию обе равны `ambient_c`;
- `motor_temperature_trip_c: [Ts_trip,Tr_trip]`, отдельные защёлкивающиеся
  пороги; отсутствие этого поля означает отсутствие моторного thermal-реле.

Оба поля требуют `motor.thermal`. Начальные температуры должны быть не ниже
ambient и строго ниже `maximum_c`; пороги — строго между ambient и maximum.
Для такого двигателя обязателен отдельный `observer_motor`: FOC остаётся
номинальным и не получает фактические температуры/истинные потоки скрытым
входом. Температурная адаптация наблюдателя этим блоком не добавлена.
Старые входы без `motor.thermal` сохраняют прежнюю электрическую модель.

**Обратная связь с электрической частью:**
`Rs=Rs_ref*(1+alpha*(Ts-Tref))`, каждая `Rr_k=Rr_k_ref*(1+alpha*(Tr-Tref))`.
Сопротивления вычисляются при midpoint-температурах внутри общей итерации
с потоками, сетью и скоростью. Контроль сходимости — 1e-9 °C для midpoint-T,
1e-10 rad/s для скорости и прежние 1e-8 V для магнитного уравнения.
Номинальные Rs/Rr в паспорте не изменяются. Мгновенный core-current solve
и потери переключения используют актуальную температуру принятого состояния.
Тест подтверждает изменения потоков/DC-динамики при alpha>0, а при alpha=0
электрическая траектория совпадает с изотермическим случаем.

**Тепловые пути:** медные потери статора и вихретоковые потери поступают в Ts,
сумма медных потерь всех клеток — в Tr. Между узлами действует симметричный
Gcross; наружу уходят Gs*(Ts-Tambient) и Gr*(Tr-Tambient). Двухузловой RC
решается midpoint-матрицей. Ограничение шага сохраняет положительное
релаксирование; неразрешённые RC-параметры отклоняются. Коэффициенты охлаждения
постоянны, зависимости вентиляции от скорости и отдельного iron/frame-узла нет.

Тепловая энергия обмоток входит в общий `stored_j`. При motor.thermal медь/Gc
не списываются наружу второй раз: внешние потери содержат только тепло,
реально переданное ambient. Диагностические интегралы `motor_stator_heat_j`,
`motor_rotor_heat_j`, `motor_winding_ambient_heat_j` показывают это разложение.
В старом изотермическом режиме моторные потери по-прежнему сразу уходят наружу.

**Реле:** начальное превышение порога логируется при t=0 и запрещает PWM/байпас.
При нагреве первый из пяти порогов (3 силовых узла + Ts/Tr) локализуется
пробными шагами до принятия состояния. Пробные расчёты не изменяют физические
состояния; одновременные моторные действия имеют стабильный порядок stator,
затем rotor. Отключение блокирует каналы, оставляя body diodes и запасённую
энергию. Температура может продолжить расти после trip; это не жёсткий потолок.
Охлаждение ниже порога не сбрасывает защёлку, автоматического повторного
включения нет. `maximum_c` — граница применимости паспорта, **не порог реле**:
при её достижении принятый шаг отклоняется без частичного изменения токов,
потоков, температур или счётчиков энергии. Для поиска более раннего trip
допускаются непринятые математические trial-состояния за этой границей.

Экспортируются Ts/Tr, фактический Rs, Rr каждой клетки, отдельные признаки
моторных trip и начальные `initial_winding_thermal_trips`. Общий `thermal_trip`
учитывает мотор и силовые узлы. Это идеальные состояния модели, не моделирование
RTD-датчиков/шума и не предсказание измеренного winding hotspot.

**Приёмка:** 7 новых случаев `cases/switching-winding-thermal/` по .12 s:
холодный/тёплый двигатель, насыщенный four-cage с нагревом, отключение статора,
отключение ротора, initial trip с охлаждением и чистое охлаждение. Добавлен
исходный nonthermal core_single; всего 8 случаев на 20/10/5 us (24 траектории).
Пороги 80.02 °C в стресс-примерах авторские и не являются настройками реального
теплового реле. Короткие окна не доказывают установившуюся температуру или
долговременную эксплуатацию; профиль по-прежнему ограничен одной машиной,
фиксированной balanced RL/DC/PWM-топологией, free shaft и общей схемой FOC.

Независимый mixed-stage oracle дополнен двумя температурными неизвестными:
теперь 18–24 неизвестных и прямые winding heat/R(T) уравнения одновременно
с KCL/KVL/потоками/валом. Проверяются 14 принудительно заданных gate-стадий;
это проверка физического шага, а не решений реле/FOC независимого контроллера.
Отдельный continuous ODE заново формулирует все потоковые и тепловые уравнения,
не импортирует производственные machine/thermal/RHS и проверяет 7 паспортных
случаев: 180 V peak, 40 Hz, phase .2 rad, фиксированные 35 rad/s, .01 s/101
отсчёт. Его forcing отличается от полной приводной траектории. Два cooling
паспорта дают одинаковую подсистемную траекторию (6 уникальных условий);
в полном приводе они различаются памятью реле. Чистое охлаждение полного
привода также сверяется с аналитическим matrix exponential.

```bash
power-grid switching-drive run --study cases/switching-winding-thermal/warm_winding.json --out reports/winding-hot-local
python -m benchmarks.switching_thermal_acceptance --out reports/winding-thermal-independent
python -m benchmarks.switching_thermal_acceptance --verify reports/winding-thermal-independent
```

Benchmark `--verify` повторяет 24 полные native-траектории, 14 mixed-stage,
7 continuous ODE и соответствующие 21 native-winding прогон. Это независимая
проверка электротепловой **подсистемы** и стадий, не полная независимая
замкнутая PWM/FOC/network траектория. CLI verify — лишь проверка целостности.

Прошли 300 целевых тестов без предупреждений/пропусков: 24 новых и 276
регрессий. Во всех 8 связанных случаях ошибка уменьшается при уточнении шага,
порядок событий сохраняется. При 10 us максимум ошибки winding ODE —
4.42e-7 Vs по потоку и 3.72e-6 °C по температуре; в полном приводе конечная
относительная невязка энергии не превышает 2.25e-12, KCL — 2.55e-7 A.
Инициализированная тепловая энергия учитывается в нормировании; этот показатель
не сравним напрямую с профилями без такого начального запаса. Отдельный
баланс накопленного моторного тепла имеет невязку до 3.14e-9 J.
Отклонение чистого охлаждения от matrix exponential — до 1.54e-9 °C.
Эти числа характеризуют уравнения и дискретизацию, не точность относительно OEM.

В warm-примере за .12 s Ts меняется с 90 до 90.608 °C, Tr — с 70 до 70.289 °C;
статорное отключение получено примерно в .01370 s, роторное — в .002863 s.
После роторного trip Tr продолжает расти до 80.137 °C вследствие оставшейся
энергии. Initial-trip узел остывает с 90 до 69.689 °C, оставаясь заблокированным.
Ни температура, ни электрические состояния не обрезаются ради этих результатов.

Свежий wheel проверен вне checkout: warm_winding, rotor_trip и initial_trip_cooling,
запрещены imports benchmarks/pandapower/ANDES/motulator, ZIP восстановлен и проверен.
Это тот же host/OS и venv с системными зависимостями, не независимая ОС.
Финальный replay всех 24/14/7 проверок выполнен успешно.

Текущие артефакты:
- `reports/switching-winding-thermal-qualified-2026-11-10/summary.json`;
- `reports/switching-winding-thermal-tests-2026-11-10.xml`;
- `reports/switching-winding-thermal-portable-2026-11-10/receipt.json`;
- `reports/switching-winding-thermal-delivery-2026-11-10/delivery.json`.

Тепловые ёмкости, охлаждение и stress thresholds — авторские. Коэффициент
alpha20=.00393/K для номинального примера сопоставлен с первичным
[NBS/NIST Copper Wire Tables](https://nvlpubs.nist.gov/nistpubs/Legacy/hb/nbshandbook100.pdf).
Наличие линейного R(T) не делает RC-паспорт измеренным. Не добавлены winding
hotspots/FEA, aging/IEC-класс защиты, reset/reclose, locked rotor, температурно
адаптивный наблюдатель или hardware-fitted cooling. Старые receipt R06.5
относятся к своим исходникам. Полный release-профиль не запускался;
GitHub/CI/индексация/коммиты/push не выполнялись. Универсальное превосходство
и завершение всего проекта не объявляются.


## R06.7 — насыщенный многоклеточный наблюдатель FOC

Добавлен явный `observer_mode: "multicage_current"`. Его отдельный
`observer_motor` сохраняет 1–4 клетки и радиальное насыщение. По умолчанию
остаётся прежний `nominal_single_cage`; это значение не добавляется в
канонические старые паспорта. Старый режим по-прежнему отклоняет насыщенный
или многоклеточный observer вместо молчаливой редукции.

Новый current-model получает только усреднённый за carrier терминальный ток
и скорость вала. Начальные rotor-flux estimates равны нулю даже при ненулевых
истинных потоках двигателя. Истинные температуры/потоки и сопротивления
моторной thermal-модели не передаются. Observer имеет собственные постоянные
номинальные R; паспорт с thermal/Gc/locked_rotor отклоняется, число пар полюсов
должно совпадать. Ненулевой Gc и thermal **растения** допускаются, но вызывают
модельное рассогласование: разделения core-current и температурной адаптации
наблюдателя нет. Нельзя трактовать это как точный observer такой машины.

В αβ-координатах обозначим S=sum(1/Lk), B=sum(psi_rk/Lk).
Наблюдатель решает строго монотонное уравнение
`i_s+B = psi_s * [S+(1+(beta*|psi_s|)^n)/Lm0]`.
При заданном i_s это единственное радиальное решение. Далее каждая клетка
сохраняет собственное уравнение
`dpsi_rk/dt = Rk/Lk*(psi_s-psi_rk) + j*p*omega*psi_rk`.
Шаг — неявный midpoint с аналитическим блочным якобианом и проверкой
абсолютной невязки <=1e-10 Vs. Неуспешный solve не меняет состояние.
Радиальное обращение использует bracketed solve; в линейном пределе и при
нулевом токе сохраняется корректное поведение без деления на ноль.

При секантном Lm получаем A=S+1/Lm, эквивалентный ориентир потока `B/A`,
Lsigma=1/A и эффективный Lm_control=Lm*S/A. В одноклеточном линейном пределе
это прежняя inverse-Gamma редукция. Угол B/A совпадает с углом B, поэтому
электрическая частота вычисляется как `cross(B,dB/dt)/|B|²`, без формулы slip
только первой клетки. Torque PI, ограничения задания тока, antiwindup и
voltage feedforward сохранены; секантный feedforward **не является полным
нелинейным дифференциальным decoupling** при быстро меняющемся насыщении.
Ограничение задания не обрезает физический ток двигателя.

Основа Gamma/inverse-Gamma и роль насыщения сверены с первичным описанием
[Induction Machines, Aalto Electric Drives / motulator](https://aalto-electric-drives.github.io/motulator/model/drive/induction_machine.html).
Многоклеточное расширение и указанное исключение stator flux — вывод из
собственных паспортных уравнений; это не заимствованный аттестованный алгоритм
motulator и не опубликованное сравнение качества контроллеров.

Новые каналы `observer_cage_k_flux_alpha_vs/beta_vs` и
`observer_midpoint_residual_vs` отделены от истинных потоков двигателя.
Итог исследования содержит фактически выбранный `observer_mode`.
Усреднение входов за PWM-period — приближение измерительного тракта;
уточнение electrical max_step при неизменном carrier не проверяет сходимость
частоты выборки самого наблюдателя.

Четыре авторских паспорта `cases/multicage-observer/`: matched double cage,
saturated four cage, +30% Rr mismatch, saturated gate block/recovery.
Окно .08 s; пониженный torque-enable flux threshold .05 — авторская настройка,
не промышленная рекомендация. Проверяются именно начальный разгон и краткое
возмущение, не полный пуск/номинальная нагрузка/устойчивость на всех режимах.

```bash
power-grid switching-drive run --study cases/multicage-observer/four_cage_saturated.json --out reports/multicage-local
python -m benchmarks.multicage_observer_acceptance --out reports/multicage-independent
python -m benchmarks.multicage_observer_acceptance --verify reports/multicage-independent
```

Acceptance выполняет 12 полных траекторий на 20/10/5 us, проверяет энергию,
KCL, магнитную невязку, включение torque PI/разгон, совпадение последовательности
событий и уменьшение ошибки slow channels при уточнении electrical шага.
Отдельный непрерывный Cartesian oracle заново решает конститутивные уравнения
и все rotor ODE, без imports производственных observer/magnetics/controller.
DOP853 rtol=1e-11/atol=1e-13 сверяется с midpoint на 30/60/120 шагах:
4 зарегистрированных сравнения, 3 различных набора параметров; block и matched
saturated имеют одинаковые observer-параметры и prescribed forcing.
Forcing — i=8-3j A, omega=-25 rad/s, .006 s; не действующая PWM-траектория.
Проверка требует отношения ошибок >3.8 на двух уточнениях и конечной ошибки
<2e-7 Vs. Это независимая проверка **наблюдателя**, не всего замкнутого привода.
`--verify` повторяет все 12 полных и 12 observer-прогонов, сверяет точные
входы/producer/содержимое и повторно вычисляет критерии. Seal — целостность,
не подпись и не подтверждение внешнего происхождения.

Остаются core-current-aware/температурно адаптивный observer, согласование
нелинейного feedforward, измерительный шум/задержки, sensorless, проверка на
длительных режимах и независимая полная closed-loop drive-траектория.
Универсальное превосходство или завершение промышленного проекта не заявлены.


Результат R06.7: 300 существующих регрессий и 26 новых тестов прошли.
Общий целевой запуск включал 324 теста; после добавления ещё двух проверок
повторно выполнены все 26 новых тестов (326 уникальных тестов суммарно).
Полный release-профиль не запускался. Во всех четырёх случаях на 20/10/5 us
ошибка slow channels уменьшается; на finest конечные скорости составили
4.200, 3.084, 3.049 и 2.153 rad/s соответственно. Это начальный разгон,
не завершённый промышленный пуск. При 10 us максимум относительной конечной
невязки энергии 7.26e-12, KCL 6.84e-7 A. Эти метрики проверяют дискретизацию,
а не OEM-точность и не превосходство над другими моделерами.
Максимальная ошибка observer ODE при 120 шагах — 1.19e-7 Vs;
отношение ошибок на двух уточнениях близко к четырём во всех сравнениях.

Wheel установлен вне checkout без сетевых зависимостей; проверены saturated
four-cage и block/recovery, наличие всех оценок клеток, энергия/KCL, запрет
ключей во всём внутреннем окне блокировки и восстановление после него.
ZIP восстановлен с совпадением manifest. Imports benchmarks/pandapower/andes/
motulator запрещены. Это тот же host и системные зависимости, не независимая
ОС. GitHub/CI/индексация/коммиты/push не выполнялись.

Артефакты R06.7:
- `reports/multicage-observer-accelerating-qualified-2026-11-10/summary.json`;
- `reports/multicage-observer-tests-2026-11-10.xml` (324 теста);
- `reports/multicage-observer-specific-tests-2026-11-10.xml` (26 новых);
- `reports/multicage-observer-portable-2026-11-10/receipt.json`;
- `reports/multicage-observer-delivery-2026-11-10/delivery.json`.

Следующая крупная очередь: независимая **полная** замкнутая траектория
PWM/FOC/сети и сопоставимое внешнее сравнение, затем расширение источника
на общую DAE-сеть. В observer-направлении остаются core-current separation,
температурная адаптация, измерительные задержки/шум и нелинейный feedforward.


## R06.8 — прямая импульсная сверка с motulator и ускорение магнитного порта

Реализован `benchmarks.pwm_motor_comparison`: четыре заранее закреплённых
профиля, по 40 ms: linear PWM, saturated PWM, saturated zero-voltage/recovery,
regenerative interval. Используются опубликованные параметры Gamma-машины
из примера Aalto; инерция/нагрузка и PWM fixture — авторская постановка.
Нулевое терминальное напряжение здесь означает заданный zero vector, **не**
открытый выключатель/полную потерю питания. Regeneration имеет интервалы
отрицательной мощности и момента; суммарная энергия источника за окно остаётся
положительной. Не следует называть весь запуск net-рекуперацией.

Primary — рабочий `SwitchingMagnetics`, radial discrete-gradient и связанный
midpoint свободного вала. Reference — оригинальные `InductionMachine` Gamma
outputs/RHS motulator **0.19.0**, отдельно заданное уравнение вала и DOP853.
Оба получают одну независимо созданную кусочно-постоянную двухуровневую PWM
форму: 540 V, 2 kHz carrier, 30 Hz fundamental, modulation .8. Каждый switching
edge является границей интегрирования; не допускается сглаживание импульсов.
Три primary шага — 20/10/5 us; reference rtol=1e-11/atol=1e-12/max_step=50 us.
Это motor+shaft benchmark, **не** native full drive/controller мотулятора:
внешняя RL-сеть, DC-bus, diode network/dead time и FOC в сравнение не входят.
Native motulator не сводится к RMS или собственным переписанным уравнениям.

Зафиксированы колесо SHA256 из `requirements/motulator-reference.lock`,
версия и исходники **всех** установленых Python-модулей reference, producer
и исходник runner. Python reference dependency не нужна runtime-пакету.
Скачивание выполнено из PyPI с проверкой hash; GitHub не использовался.
Неуспех импорта/version/hash/source проверки не превращается в pass/exclusion.

Протоколы — `cases/pwm-motor-comparison/`. В каждом результате явно записаны
8 столбцов: stator/rotor flux αβ, stator current αβ, speed, torque, с SI
единицами в именах. Приёмка требует весь заранее объявленный горизонт/ось,
точный channel inventory, конечные значения и сходимость **каждой** группы
каналов. Два одинаково обрезанных вывода не проходят. Error caps объявлены
до решения: 5e-5 Vs, .005 A, .005 rad/s, .005 Nm; сравниваются fine и finest,
а ошибки всех групп должны уменьшаться coarse → fine → finest.

На 10 us максимальное расхождение с native motulator: 5.55e-7 Vs по потоку,
2.38e-5 A по току, 4.77e-5 rad/s по скорости, 7.24e-5 Nm по моменту.
Во всех четырёх случаях каждая группа ошибок уменьшается при уточнении.
Конечные/максимальные энергетические audits относятся к известным уравнениям,
не измеренному двигателю. У motulator также получена малая энергетическая
невязка; **преимущество по физической точности из этих чисел не следует**.

```bash
python -m pip download --no-deps --require-hashes -r requirements/motulator-reference.lock --dest wheelhouse/motulator
# Установить этот wheel в отдельное benchmark-окружение, не менять runtime-зависимости.
python -m benchmarks.pwm_motor_comparison --wheel wheelhouse/motulator/motulator-0.19.0-py3-none-any.whl --out reports/pwm-external-local
python -m benchmarks.pwm_motor_comparison --wheel wheelhouse/motulator/motulator-0.19.0-py3-none-any.whl --verify reports/pwm-external-local
```

`--verify` заново выполняет 4 native reference и 12 primary траекторий,
сверяет полное содержимое, входы, producers и повторно вычисленные критерии.
Время повторного запуска не обязано совпасть и не подменяет исходные timing.
Seal обеспечивает целостность, не подпись/внешнее происхождение.
Локальные wall times опубликованы, но это один sequential запуск разных
интеграторов с разными error controls. Это **не** измерение производительности
native motulator Simulation/полной системы; выигрыша над другим симулятором
по времени этим протоколом не установлено. Память отдельно не измерялась.

В runtime-порте убраны повторные построения постоянных identity/input/spin
матриц, ускорены двумерные нормы radial-gradient. Уравнения и полный набор
клеток сохранены; сопротивления читаются заново, температурная обратная связь
не кешируется. Пробное rotor-Schur исключение оказалось медленнее на малых
матрицах и в итоговую реализацию не вошло. 12 новых проверок напрямую сверяют
порт с независимо собранной полной Newton-системой для 1–4 клеток, ±180/0
rad/s, насыщения, Gc и тёплых обмоток.

`python -m benchmarks.switching_port_performance --out reports/port-timing-local`
содержит исходные pre-cache методы, проверяет равенство outputs перед замером,
фиксирует паспорта/точки/producer и чередует порядок прогонов. На этом host:
7 повторений по 1500 вызовов после warmup, BLAS/OMP=1; медианное ускорение
**1.255x** для one-cage warm и **1.186x** для saturated four-cage warm.
Это две фиксированные точки и только affine-kernel; не ускорение всей
траектории, не результат против motulator и не обязательная гарантия pass.

Прошли **347** целевых тестов (326 регрессий предыдущего блока и 21 новый),
без пропусков; это не полный release suite. После финального добавления
метаданных каналов повторены все 21 новые проверки. Новый wheel проверяется
вне checkout без imports benchmarks/motulator/ANDES/pandapower: full four-cage
FOC и warm thermal winding, энергия/KCL и ZIP round trip. Тот же host и
системные зависимости, не независимая ОС. GitHub/CI/git add/commit/push нет.

Основные артефакты:
- `reports/pwm-motor-comparison-release-qualified-2026-11-11/summary.json`;
- `reports/pwm-motor-comparison-final-tests-2026-11-11.xml`;
- `reports/switching-port-performance-2026-11-11/timing.json`;
- `reports/pwm-motor-runtime-portable-final-2026-11-11/receipt.json`;
- `reports/pwm-motor-comparison-delivery-2026-11-11/delivery.json`.

**Что это говорит о сравнении с другими моделерами.** Подтверждено совпадение
конкретного импульсного motor-профиля и локальное ускорение собственного ядра.
Не подтверждено общее лидерство по точности, скорости, масштабам или спектру
оборудования. Измеренный reference закреплён на 0.19.0; открытая сейчас
[документация saturated IM/CVC](https://aalto-electric-drives.github.io/motulator/drive_examples/current_vector/plot_2kw_im_sat_cvc.html)
обозначена 0.23.1. Сравнение не следует выдавать за проверку самой новой версии.
В документации уже есть sensorless CVC, saturated IM, sampling/dead-time,
converter и grid-converter профили; одного multicage observer недостаточно
для преимущества над этим проектом. Наличие возможностей в документации
не засчитывается как самостоятельно воспроизведённый benchmark.

ANDES решает задачи сетевой DAE/устойчивости с
[implicit trapezoidal TDS](https://docs.andes.app/en/v1.10.1/tutorials/04-time-domain.html),
а [OpenDSS dynamics](https://opendss.epri.com/Dynamics.html) содержит собственные
электромеханические и IndMach012-профили. Этот PWM benchmark не ранжирует их:
нужны отдельные сопоставимые сетевые постановки и внешние запуски.
Следующие незакрытые критерии: независимая полная source/DC/PWM/FOC траектория,
сверка актуальной версии reference, одинаковые accuracy budgets с повторными
измерениями времени/памяти, затем general DAE-network и масштабирование.


## R06.9 — актуальный reference, общие accuracy budgets и замкнутый привод

Три очереди R06.8 реализованы как отдельные проверяемые профили. Общего
лидерства над другими моделерами по-прежнему не установлено.

### Актуальный motulator и одинаковые допуски

Из PyPI получен совместимый с Python 3.12 выпуск **motulator 0.23.1**;
точный SHA256 закреплён в `requirements/motulator-current-reference.lock`.
[PyPI release](https://pypi.org/project/motulator/0.23.1/) — источник пакета,
GitHub/CI не использовались. Сверяются байты всех установленных Python-модулей
с wheel, версия и собственные producer/source hashes. Старый lock 0.19.0
сохранён. Адаптер использует native `set_outputs`/`rhs` и native output fields;
четыре reference-траектории 0.19.0 дополнительно пересчитаны без численных
изменений. Исторические receipts остаются привязаны к своим исходникам.

`benchmarks.pwm_accuracy_budget` использует прежние четыре imposed-PWM
motor+shaft постановки, но регистрирует фактические параметры отдельно:
primary h=100/50/20/10 us, DOP853 rtol=1e-4/1e-6/1e-8, atol=rtol*1e-3,
max_step=200 us. Общие caps заранее фиксированы: 5e-5 Vs, .005 A,
.005 rad/s, .005 Nm; ось/горизонт/8 каналов обязательны. Два строгих native
reference (rtol 1e-12 и 1e-13, max_step 20 и 10 us) должны отличаться меньше
1% каждого cap. Это проверка численного reference, не измеренная OEM-истина.

Каждая калибровка выполняется в свежем процессе. Выбирается самый быстрый
прошедший вариант **из конечной сетки**, затем выполняются 3 новых повтора
каждого provider, с чередованием порядка. Каждый повтор заново проверяет
accuracy budget. Время калибровки не выдаётся за медиану измерений. Во всех
процессах перед clock импортируются оба набора моделей; BLAS/OMP/MKL=1.
Есть wall/CPU time, min/median/max, Linux ru_maxrss KiB и peak import baseline.
Peak RSS включает интерпретатор и общий набор библиотек; это не чистые
allocations решателя и не deployment footprint отдельного продукта. CPU
frequency/affinity и сторонняя нагрузка host жёстко не контролировались.

Всего: 60 траекторий — 32 native reference и 28 primary, включая oracle,
калибровки и измерения. Все прошли. Численные траектории повторяются через
`--verify`; original wall/RSS не обязаны воспроизвестись и не подменяются
новыми замерами. Verifier пересчитывает критерии/выбор по исходным измерениям
и проверяет каждую численную траекторию заново. Seal — целостность, не подпись
измерений или независимая аттестация.

**Измеренный результат на этом host:** primary/reference по медианному wall
времени — 1.480, 1.843, 1.630, 1.935 для linear/saturated/sag/regeneration.
То есть primary здесь медленнее, а не быстрее. Все выбранные primary шаги —
100 us; reference rtol выбирается по прошедшей калибровке. Peak RSS примерно
115–117 MiB при общем наборе импортов; преимущества по памяти не установлено.
Это сравнение native motor RHS с нашей winding-port интеграцией на заданном
PWM, не измерение полных motulator Simulation/FOC/network workflows.

```bash
python -m pip download --no-deps --require-hashes -r requirements/motulator-current-reference.lock --dest wheelhouse/motulator-current
# Установить закреплённый wheel в отдельное benchmark-окружение Python >=3.12.
python -m benchmarks.pwm_accuracy_budget --wheel wheelhouse/motulator-current/motulator-0.23.1-py3-none-any.whl --out reports/current-budget-local
python -m benchmarks.pwm_accuracy_budget --wheel wheelhouse/motulator-current/motulator-0.23.1-py3-none-any.whl --verify reports/current-budget-local
```

### Независимая полная замкнутая траектория ограниченного профиля

`benchmarks.closed_loop_reference` самостоятельно ведёт весь контур:
трёхфазный источник/RL → шесть диодов/предзаряд → DC/rail-earth capacitances
→ MOSFET/body diodes с dead time/PWM → Gamma-машина/вал → измерение → FOC.
Три electronics RC-состояния и тепловой баланс также рассчитываются отдельно.
Нет imports runtime, его контроллера, PWM planner, Norton stamp или timestep
runner. Это проверяется отдельным процессом с запрещённым `power_grid_env`.
Контроллер/observer/PI/antiwindup, PWM, clocks и обработка границ заново
выражены из тех же уравнений. Midpoint-стадия использует независимый mixed
KCL/KVL/потоковый/механический root, а не production electrical solve.
Источник/диоды/DC реагируют на текущий ток собственного двигателя эталона;
это не replay терминального напряжения из primary и не open-loop waveform.

Пределы профиля намеренно проверяются: одна линейная клетка, согласованный
номинальный sensored observer, Gc=0, нет winding thermal и начальных потоков,
постоянные semiconductor R, switching Eon/Eoff=0, без thermal trips. Высокие
trip thresholds в fixtures означают исключённый сценарий реле, не промышленную
рекомендацию. Electronics RC-потери остаются, но не меняют сопротивления.
Насыщение/multicage, core feedthrough, switching-energy jumps, relay cascade,
sensorless и общая DAE-топология **не** заявлены независимо закрытыми.

Четыре полные постановки `cases/closed-loop-independent/` по .04 s:
nominal start, grid sag/recovery, PWM block/recovery, DC precharge с 300 V,
2-ohm precharge path и последующим bypass. В каждом профиле 20/10 us:
8 primary + 8 независимых траекторий. Сверяются 17 каналов, весь горизонт,
все source currents/потоки/токи/скорость/DC, оценки observer, electronics T,
gates и последовательность bypass. Проверяются разгон/torque enable,
блокировка/восстановление и фактический bypass предзаряда. Разности двух
шагов публикуются отдельно по каналам и единицам, не как смешанная норма.
Двух уровней недостаточно для нового утверждения о порядке сходимости.

Математическая дискретизация намеренно общая: midpoint/discrete-gradient.
Малое расхождение подтверждает независимую реализацию связанных уравнений,
но не исключает общий model/discretization bias. Это **не** сравнение полного
привода с motulator: внешняя сверка 0.23.1 выше пока ограничена motor+shaft.

При полном предзаряде найдена проблема самого mixed oracle: вычитание
почти равных состояний на почти совпадающих PWM edges (пикосекундные интервалы)
теряло значащие цифры в stiff derivative terms. Неизвестные переведены в
приращения, derivatives вычисляются прямо по ним, а не через разность больших
абсолютных величин. Все исходные residual equations сохранены; порог 2e-6
не увеличен. Используются физически согласованный scalar-diode source
predictor и rank-tolerant least-squares fallback. Это не clipping токов,
сглаживание импульсов и не объединение физически разных событий. Отдельный
регрессионный тест решает mixed-стадию на 1 ps. Старые receipts stage oracle
относятся к прежней реализации и автоматически не перепечатываются.

```bash
python -m benchmarks.closed_loop_acceptance --out reports/full-loop-local
python -m benchmarks.closed_loop_acceptance --verify reports/full-loop-local
power-grid switching-drive run --study cases/closed-loop-independent/precharge.json --out reports/precharge-local
```

`--verify` повторяет все 8+8 полных траекторий и критерии; reference не получает
истинные состояния primary. Полный release suite/другая ОС/оборудование не
входят в этот локальный профиль. Runtime package этим блоком не изменялся;
новые механизмы относятся к validation/benchmark, внешняя зависимость остаётся
опциональной. Следующий инженерный приоритет: ускорение интеграции по событиям
при заданной ошибке, затем расширение независимой замкнутой проверки на
насыщение/многоклеточность/Gc/тепловые события и сетевой DAE-профиль.


Финальный результат R06.9: полный локальный запуск **363 целевых тестов**
(347 прежних +16 новых) прошёл без пропусков. После финализации численного
эталона отдельно повторены все **124 затронутые проверки**, включая насыщение,
многоклеточные и электротепловые stage oracle. Это не полный release suite.
Все четыре замкнутых случая прошли на обоих шагах. Максимальное расхождение:
DC 1.28e-10 V, source-current A 1.20e-10 A, motor-current alpha 5.51e-12 A,
скорость 3.09e-13 rad/s; reference energy residual до 9.39e-12 J,
максимальная mixed-equation невязка до 2.08e-10. Такая близость ожидаема для
одинаковой математической дискретизации; это не заявленная физическая
точность двигателя с двенадцатью знаками. Конечные скорости за .04 s:
1.556, 1.558, 1.196, 1.241 rad/s (nominal/sag/block/precharge); это начальный
разгон, не завершённый промышленный пуск.

Все 60 latest-reference траекторий повторно выполнены; сравнение с motulator
0.23.1 при общих caps успешно. Artifact metrics сохраняют исходные измерения,
а не timings верификатора. Улучшения source predictor и общего потенциала
неактивной звезды касаются только начального приближения root; токи и потоки
не обрезаются. Для fallback используется абсолютная finite-difference
пертурбация в нормированных координатах, чтобы Jacobian не исчезал при малых
приращениях. Порог физических уравнений 2e-6 сохранён.

Артефакты:
- `reports/pwm-current-budget-final-qualified-2026-11-11/summary.json`;
- `reports/closed-loop-complete-qualified-2026-11-11/summary.json`;
- `reports/independent-drive-final-qualified-tests-2026-11-11.xml` (363);
- `reports/independent-drive-complete-targeted-tests-2026-11-11.xml` (124);
- `reports/independent-drive-delivery-2026-11-11/delivery.json`.

Runtime fingerprint остался прежним; новые изменения — в benchmark/reference,
протоколах, тестах и документации. Прежняя wheel/ZIP-проверка R06.8 относится
к этому же неизменённому runtime и не засчитывается как новый независимый
host/OS запуск. GitHub, CI, индексация, коммиты и push не выполнялись.
Следующее измеримое улучшение — сократить наблюдаемое отставание runtime при
тех же error caps, сохранив эти внешние и замкнутые критерии приёмки.


Финальный replay замкнутого профиля завершён: повторно выполнены все 8 primary
и 8 независимых полных траекторий с точным совпадением сохранённых результатов
и повторно рассчитанной приёмкой. Вместе с 60 latest-reference replay это
76 повторных траекторий двух разных профилей, а не 76 уникальных физических
схем. Артефакты запечатаны локальными SHA256 manifests; внешняя подпись и
независимая ОС этим не заявлены.


## R06.10 — ускоренный порт заданного напряжения без редукции клеток

Измеренное отставание R06.9 устранено **в четырёх зарегистрированных
imposed-PWM motor+shaft задачах**. Это отдельный профиль, не утверждение
о скорости всех сетевых/замкнутых исследований или превосходстве продукта.

Профиль CPU показал, что 2752 электрических итерации saturated PWM строили
полный Norton port/gain для уже заданного терминального напряжения. `affine`
и `stage` занимали основную часть времени. Добавлен runtime API
`power_grid_env.prescribed_voltage.voltage_step`: точное исключение всех
роторных неизвестных, два действительных stator unknowns, Newton для
радиального discrete gradient и совместная итерация свободного вала.
Новых runtime dependencies/JIT/скомпилированного backend нет.

При delta=midpoint-old для каждой клетки:
`Dk=2/h+Rk/Lk-j*p*wmid`,
`delta_rk=[-Rk/Lk*(psi_rk_old-psi_s_old)+j*p*wmid*psi_rk_old+(Rk/Lk)*delta_s]/Dk`.
Подстановка этих точных выражений в stator voltage equation оставляет
2x2 реальную систему; radial divided difference, saturation и Gc сохраняются.
Все 1–4 клетки имеют свои R/L/потоки/токи, а не сводятся к одной клетке.
Используются scalar complex arithmetic и аналитический 2x2 Jacobian вместо
построения полной Norton gain matrix для неизвестного voltage port.
Newton имеет line search/лимит итераций; электромеханическая итерация также
ограничена. Невязка Newton <=1e-10 V, итоговая constitutive <=1e-8 V,
согласование midpoint speed <1e-11 rad/s. Несошедшийся шаг выдаёт исключение;
входные flux/passport не изменяются. Адаптивного выбора h этим API не добавлено.

Входной вектор — `[psi_s_alpha, psi_s_beta, psi_r1_alpha, psi_r1_beta, ...]`
в Vs, напряжение — complex alpha-beta peak V, скорость — mechanical rad/s,
шаг — s. Возвращаются новые потоки/скорость, midpoint токи, момент, нагрузки,
раздельные copper/core powers, source power и diagnostics. Поддержаны
постоянный load torque, fan/viscous load и locked shaft при speed=0.
При motor.thermal обязательны **явно заданные** midpoint `[Ts,Tr]`;
R(T) учитывается, но RC-состояния самим этим методом не интегрируются.
Внешнее сопряжение температуры — ответственность вызывающего solver.
Некорректные температуры, неполные потоки и nonfinite inputs отклоняются; токи не clipping.

```python
from power_grid_env.prescribed_voltage import voltage_step
result = voltage_step(passport, flux_ab_vs, speed_rad_s, 230+40j, 20e-6)
# result['flux'], result['speed_rad_s']; принять только успешный шаг.
```

Существующий `SwitchingMagnetics.affine` и общий RL/DC/PWM network solver
с неизвестным терминальным напряжением **сохранены**. Замена сетевого solve
на заранее заданное/предыдущее напряжение могла бы нарушить связь DC и сети;
такой подмены нет. Выигрыш нового пути нельзя переносить на произвольную
сеть, защиты, OPF, QSTS или контролируемый полный привод.

`benchmarks.pwm_motor_comparison.native` теперь вызывает этот runtime API.
Остались те же PWM edges, sampling, механика, потери и энергетический audit
на каждом принятом шаге. Не удалены дорогие проверяемые каналы/physics.
В `benchmarks.prescribed_voltage_acceptance` сохранён предыдущий dense
Norton driver: 4 случая × 20/10/5 us × 2 реализации =24 траектории.
Сверяются шаги, полные оси/каналы, потоки/скорость и энергетические интегралы.
Максимальное расхождение двух реализаций: ток до 1.66e-11 A, поток до
4.40e-13 Vs, скорость до 1.49e-11 rad/s, момент до 2.57e-11 Nm;
энергетические интегралы — до 9.60e-11 J. Это совпадение дискретных уравнений,
а не физическая точность относительно OEM с таким числом знаков.

Повторён неизменённый accuracy-budget профиль **motulator 0.23.1**:
те же 4 случая, caps, конечная candidate grid и 3 свежих measured workers
после калибровки. Первичное измерение выполнено до параллельных собственных
тестов/replay. Host frequency/affinity/сторонняя нагрузка жёстко не фиксированы.
Во всех случаях выбран прежний primary max_step=100 us, все повторы прошли
caps. Primary/reference median wall ratio: 0.320, 0.551, 0.526, 0.599
(linear/saturated/sag/regeneration). В этом измерении новый профиль быстрее
native RHS/DOP853 reference примерно в **3.13, 1.81, 1.90, 1.67 раза**.
У R06.9 здесь было отставание; старые результаты не переписаны.
Это сравнение именно зарегистрированных terminal-voltage pipelines;
не полного native motulator Simulation/контроллера/сети и не всех возможных
настроек интегратора. Общие caps не означают одинаковую достигнутую ошибку.
Преимущество по памяти/масштабам не заявляется: RSS включает общий import
baseline, а candidate grid и три повтора дают ограниченное измерение.

Снова прошли все 8+8 траекторий независимого полного closed-loop профиля
R06.9. Новый путь там не используется; это regression, не замер ускорения
общей MNA-сети. Проверены 60 budget trajectories +24 equivalence trajectories
+16 closed-loop trajectories. Все **100** повторно воспроизведены через
соответствующие `--verify`. Это протокольные траектории с вариантами решения,
а не сто разных физических схем.

Пройдены **423 уникальных целевых теста**: 408 в общем запуске, затем все
60 тестов нового API после добавления интеграционных/трёхклеточных/load
проверок; 45 пересекаются. Новых относительно R06.9 —60, прежних —363.
Проверяются все 1/2/3/4 клетки, ±80/0 rad/s, насыщение/линейный предел,
Gc=0/.006, fixed T(R), энергетический баланс, перестановка клеток, вращение
координат, locked shaft, constant load, неверные входы и artifact serialization.
Полный release suite не запускался; test count не является рейтингом.

Свежий wheel установлен вне checkout. При запрещённых imports benchmarks/
motulator/ANDES/pandapower новый runtime API прошёл 200 связанных шагов
single-cage и saturated four-cage с Gc и imposed Ts/Tr. Энергетический
баланс до 8.1e-14 J на этих коротких проверках. Это тот же host/system deps,
не независимая ОС; fixed temperatures не означают проверку winding RC solve.

```bash
python -m benchmarks.prescribed_voltage_acceptance --out reports/voltage-port-local
python -m benchmarks.prescribed_voltage_acceptance --verify reports/voltage-port-local
python -m benchmarks.pwm_accuracy_budget --wheel wheelhouse/motulator-current/motulator-0.23.1-py3-none-any.whl --out reports/fast-budget-local
```

Артефакты:
- `reports/prescribed-voltage-budget-qualified-2026-11-11/summary.json`;
- `reports/prescribed-voltage-port-equivalence-qualified-2026-11-11/summary.json`;
- `reports/prescribed-voltage-closed-qualified-2026-11-11/summary.json`;
- `reports/prescribed-voltage-tests-2026-11-11.xml` (408);
- `reports/prescribed-voltage-complete-specific-tests-2026-11-11.xml` (60);
- `reports/prescribed-voltage-portable-2026-11-11/receipt.json`;
- `reports/prescribed-voltage-delivery-2026-11-11/delivery.json`.

Следующие приоритеты: отдельно профилировать/ускорять неизвестный сетевой
Norton/MNA-порт при тех же проверках; расширять independent closed-loop на
насыщение, multicage/Gc, winding thermal и switching-energy/relay events;
затем general DAE/network scale. Общее превосходство над другими моделерами
по-прежнему не доказано. GitHub/CI/git add/commit/push не выполнялись.


## R06.11 — оптимизация неизвестного сетевого Norton/MNA-пути

Оптимизирован `SwitchingDrive` с неизвестным терминальным напряжением: это
**фиксированный 8-node source/RL/rectifier/DC/PWM/motor профиль**, а не общий
sparse DAE/phase-network решатель. Порт заданного напряжения R06.10 сюда не
подставлялся. Сетевое напряжение по-прежнему находится из связанных уравнений,
source/DC/motor back-reaction и переключения сохранены.

Профилирование nominal .008 s показало 916 electrical-affine вызовов,
1766 линейных коррекций и 12152 вычисления residual; основная часть CPU
приходилась на `_electrical_affine`. Сделано:
- Предвычислены постоянные cap/DC/channel incidence stamps; 64 gate-mask
  матрицы покрывают 27 допустимых состояний трёх half bridges. Это примерно
  32 KiB gate-table на модель плюс небольшие служебные матрицы.
- Материал R/drop не зашит в таблицы: electronics temperature/R/drop читаются
  для каждого trial. Source phase offsets постоянны; для одноточечного
  envelope убраны лишние array/interpolation. Source midpoint вычисляется
  один раз и используется одинаково для KCL и power audit.
- `_network_context` собирает неизменные network terms один раз **на candidate**.
  Shaft/winding-temperature/magnetic iterations заново рассчитывают motor
  offset/gain, но используют тот же network context. Каждый новый шаг,
  откат/thermal bisection создаёт новый context. После возврата он не хранится
  в модели. Нет кэша active diode masks или численной факторизации между шагами.
- Симметричная часть матрицы и smooth gradient не пересчитываются в каждом
  backtracking trial. Критерии Newton/line search/KCL не изменены.

Если каждая motor phase соединена с DC через включённый канал или активный
body diode, применяется diagonally scaled direct solve. Перед принятием
коррекции проверяются finite values и обратная невязка scaled linear system
<=5e-12*(1+max|rhs|). При LinAlgError/неfinite/плохой невязке используется
прежний scaled least-squares/SVD с rcond=1e-14. Во всех остальных topology
active sets, включая floating isolated-star gauge, сохраняется SVD. Эти
состояния не обязательно все сингулярны: выбор намеренно консервативен.
Не добавлены шунт, численное заземление или диагональная регуляризация.

Новые additive metrics: `linear_direct_solves`, `linear_least_squares_solves`,
`linear_direct_fallbacks`. Они учитывают computational trials, включая
повторные Newton/thermal localization, а не только принятые physical steps.
На измеренном corpus direct fallback failures=0; искусственные failure
сценарии проверены тестами. Для nominal было 13724 direct и 2042 SVD solves;
для saturated four-cage —91670/15276. При initial trip/cooling direct=0,
SVD=47224: корректный floating-star путь не был заменён на regularization.

### Воспроизводимое сравнение предыдущего и нового пути

`benchmarks.mna_legacy_reference.LegacyDrive` сохраняет прежние constructor,
source, electrical assembly/coupling и candidate методы. Добавлены лишь
счётчики для совместимости diagnostics; magnetics/control общие и в этом
блоке не менялись. Это before/after reference собственного алгоритма,
**не** независимый физический эталон и не внешний симулятор.

`benchmarks.mna_network_acceptance` фиксирует 8 сценариев: nominal, grid sag,
PWM recovery, precharge (.04 s), warm winding, saturated four-cage thermal,
rotor trip, initial trip/cooling (.12 s). На каждом —3 свежих процесса каждого
implementation, чередующийся порядок; всего 48 траекторий. Время включает
constructor, расчёт и все прежние outputs/audits; imports и JSON transport
вынесены за clock. Измеряются wall/CPU/min/max/median и Linux peak RSS при
общем наборе импортов. BLAS/OMP/MKL=1; affinity/frequency/посторонняя нагрузка
host жёстко не изолированы. Измерение проведено до собственных параллельных
регрессий/replay. Общий RSS не доказывает преимущество по памяти.

Оси/горизонты/полный inventory channels обязательны. Для физических состояний
заданы неизменные caps: .02 V, .005 A, .002 rad/s/Nm, 1e-5 Vs/rad/ohm,
.001 °C; дискретные gate/trip/bypass/torque states должны совпасть. Frame angle
сравнивается по кругу, а не ложной разности 2*pi. Все event kinds/components/
causes сохраняются, timestamp error <1e-8 s; энергии/работы —до 1e-5 J.
Оба исполнения отдельно проходят energy/KCL/magnetic acceptance.

`last_midpoint_*` — snapshots последнего принятого интервала, а не uniform
physical-time канал. Их ошибки публикуются, но не входят в state acceptance,
поскольку почти совпавшие PWM edges могут менять последний микроскопический
интервал. `voltage_limited` проверяется при enabled gates; отличия диагностики
при disabled gates отдельно считаются (на corpus их 0). Диагностические
счётчики Newton/overlap/gate edges не приравниваются к физическим состояниям.
Ни один output/audit не удалён из timed workload ради выигрыша.

На этом host median speedup относительно прежнего пути:
nominal 1.141x; sag 1.187x; recovery 1.168x; precharge 1.177x;
warm 1.163x; saturated four-cage 1.202x; rotor-trip 1.166x; cooling 1.151x.
Итого **1.14–1.20x**, или примерно **12–17% меньше wall time** на этих
восьми workflows. Это ограниченное before/after измерение с тремя повторами,
не статистическая гарантия на другом host и не сравнение с motulator/ANDES/
OpenDSS. Измерения R06.10 для другого terminal-voltage профиля не умножаются
на этот коэффициент и не перепечатываются как новые.

Максимальные before/after state различия: DC 2.13e-10 V, motor-current alpha
3.24e-12 A, speed 6.16e-14 rad/s, stator T 1.92e-12 °C. Времена событий
совпали; энергетические интегралы отличаются до 3.12e-10 J. Это ожидаемое
совпадение одинаковых дискретных уравнений, не физическая/OEM-точность.

```bash
python -m benchmarks.mna_network_acceptance --out reports/mna-before-after-local
python -m benchmarks.mna_network_acceptance --verify reports/mna-before-after-local
python -m benchmarks.closed_loop_acceptance --out reports/mna-independent-local
python -m benchmarks.closed_loop_acceptance --verify reports/mna-independent-local
```

Дополнительно пройден независимый mixed-equation full closed-loop профиль
R06.9: 4 linear cases ×2 шага ×2 реализации =16 траекторий. Его собственный
FOC/PWM/root не использует runtime stamps/solver. Для extended thermal/
saturation cases before/after корпус не заменяет пока отсутствующий полный
независимый closed-loop oracle расширенной физики. Нужен следующий этап.

Пройдены **457 целевых тестов** одним запуском: 423 прежних +34 новых.
Новые тесты покрывают все 27 допустимых gate combinations, физические
line-line/source/DC/flux/heat states против прежней assembly, floating gauge,
полностью anchored solve, LinAlgError/NaN/неверную direct correction с
возвратом к SVD, обновление context между trials и изменение source envelope.
Это не полный release suite и не подтверждение всех масштабов.

Свежий wheel проверен вне checkout при запрещённых imports benchmarks/
motulator/ANDES/pandapower: precharge, saturated-four thermal и initial trip
с охлаждением ниже threshold без rearm. Проверяются actual direct/SVD
counters, энергия/KCL/magnetic residual и ZIP round trip. Это тот же host и
системные зависимости; другая ОС не объявляется проверенной.

Артефакты R06.11:
- `reports/mna-network-qualified-2026-11-11/summary.json`;
- `reports/mna-independent-closed-qualified-2026-11-11/summary.json`;
- `reports/mna-network-tests-2026-11-11.xml`;
- `reports/mna-runtime-portable-2026-11-11/receipt.json`;
- `reports/mna-network-delivery-2026-11-11/delivery.json`.

Следующие измеримые очереди: отдельно профилировать оставшиеся motor-affine/
stage и floating-gauge расходы, без обещания выигрыша до замеров; расширять
независимый closed-loop на saturation/multicage/Gc/winding thermal/switching
energy/relay events; затем проверять general DAE/phase-network размеры и
точность. Общего лидерства над другими моделерами по-прежнему не доказано.
GitHub/CI/индексация/commit/push не выполнялись.


Финальный R06.11 replay завершён: заново выполнены все 48 before/after
траекторий и все 8+8 независимого closed-loop профиля с совпадением сохранённых
численных результатов. Original timing/RSS остаются исходными измерениями,
не заменяются временем replay. Wheel/ZIP также прошли; initial-trip мотор
остался защёлкнутым при охлаждении ниже threshold, floating-star использовал
SVD, а precharge/four-cage прогоны действительно использовали direct solves.
Всего 64 протокольные траектории и 64 replay, не 64 уникальные схемы.


## R06.12 — расширенная независимая сверка полного привода

Следующий пробел после R06.11 — физическая проверка нелинейного моторного
порта в полной замкнутой сети, а не ещё один timing microbenchmark.
Production runtime в этом блоке не менялся: сохранены Norton/MNA, floating
star, RC и защиты; новые исходники относятся к независимой квалификации.

Добавлено:

- `benchmarks/extended_closed_loop_reference.py`: смешанное независимое
  решение source/RL/diode/DC/machine/shaft/winding RC, насыщение, все клетки и
  eddy-current Gc. Endpoint core-feedthrough заново решается при текущих
  ключах; observer использует только ток/скорость и явный номинальный паспорт.
- `benchmarks/extended_closed_loop_acceptance.py`: четыре зарегистрированных
  40-ms сценария, 20/10 us, 17–25 физических каналов, event inventory,
  интегральные потери статора/ротора/стали и отдельные собственные energy/KCL
  residual audits. Это 8 primary + 8 independent траекторий.
- `benchmarks/extended_closed_loop_refinement.py`: ещё 4+4 траектории на 5 us;
  уменьшение шага отдельно оценивается для network, machine/observer, thermal.
- `benchmarks/extended_temporal_budget.py`: ограниченный поиск шага по
  Cauchy-разнице физических каналов; все удачные и неудачные попытки хранятся.
  Исчерпание бюджета возвращает непрошедший результат и ненулевой exit code.
- `benchmarks/numerical_replay.py`: точная структура и дискретные данные,
  finite floating roundoff <=5e-10, время <=5e-13 s, без относительного
  ослабления. SHA-256 integrity остаётся побитовой. Исходные physical caps
  не меняются. Учтены NumPy float64 при JSON round trip.
- `cases/closed-loop-extended-independent/`: четыре публичных canonical JSON,
  пригодных для обычного CLI без импорта benchmark/reference-пакетов.
- `tests/test_extended_closed_loop.py`: все 27 gate patterns для Gc endpoint,
  nonlinear energy divided difference/equal-radius/tiny increments, 1/2/3/4
  cage inventory, winding heat/температуры, запрет production imports у
  reference, неполные/нефинитные данные, неподдержанные события, unit caps,
  numerical replay и совпадение публичных JSON с регистрацией.

Физические границы: common midpoint/discrete-gradient схема у двух решателей,
авторские параметры добавочных клеток/насыщения/RC, sensored nominal observer,
fixed semiconductor resistance, zero switching energy. Нет независимого
continuous-time полного drive oracle, OEM calibration, независимой полной
trip-localization/switching-energy/variable-device-R(T)/multicage-observer
проверки. Число клеток и 8-node topology не означают general DAE-scale coverage.

Различаем три вывода: согласие реализаций, уменьшение разницы при уточнении и
попадание этой разницы в абсолютные physical-unit caps. Первые два не дают
автоматически третий, а третий не является доказанной абсолютной ошибкой
относительно непрерывного решения. Непрошедшие попытки не исключаются из
публикуемого temporal receipt. Превосходство над другими моделерами не заявлено.


Диагностическое дополнение: `benchmarks/closed_loop_command_replay.py`
записывает команды и PWM clock, затем отдельно воспроизводит voltage-only
и voltage+PWM tape на трёх шагах. Source/DC/диоды/вал/RC продолжают решаться;
наблюдатель в replay не интегрируется, его output columns — метаданные tape.
В `double_cage` немонотонность сохранилась даже при fixed duty/edges и
одинаковом числе gate edges. Одного объяснения через FOC недостаточно;
причина пока не установлена. Следующий приоритет — выделить plant/network
временную погрешность и коммутационные интервалы, не менять caps ради pass.


Финальная проверка R06.12: **466 тестов**, включая **81 новых**, без failures/skip.
24 protocol-траектории (12 primary + 12 independent) и 24 numerical replay;
4 installed-wheel сценария/ZIP восстановления из `/tmp` с запрещёнными
benchmark/motulator/pandapower/andes imports. Это тот же host/OS и
system-site-packages, не независимая ОС и не чистый dependency resolver.
Дополнительный temporal поиск: 24 траекторий, общий passed=false; этот поиск не засчитан как прошедшая квалификация и не replay-qualified.
- `saturated_single_core`: passed=true, selected_step_s=6.25e-07, сохранено попыток=3.
- `double_cage`: passed=false, selected_step_s=None, сохранено попыток=3.
- `saturated_four_core`: passed=true, selected_step_s=6.25e-07, сохранено попыток=3.
- `warm_four_winding`: passed=true, selected_step_s=6.25e-07, сохранено попыток=3.

Дополнительно 21 frozen-command/PWM diagnostic траекторий: это тот же production solver, не независимая сверка. Observer/control output fields в них являются metadata записанной команды; physical plant/PWM/source/DC/RC/shaft продолжают интегрироваться.

Полный repository test suite не запускался. Runtime package fingerprint
`0e288ebc922a17abb9bc2abf52c9a5e6ae4e716351e9146d42b86499e5603129` не изменился относительно R06.11.
Итоговый пакет: `reports/extended-closed-delivery-2026-11-11/`;
`delivery.json` содержит точные hashes и раздельные критерии.
GitHub/CI/индексация/commit/push не выполнялись. Универсальное лидерство,
absolute continuous-time accuracy и общий temporal pass не утверждаются.


## R06.13 — публичный temporal workflow и расширенный ресурсный профиль

Реализованы `SwitchingTemporalLimits`, `qualify_switching_drive` и
`run_switching_qualification_artifact` в `power_grid_env/switching_convergence.py`;
CLI `switching-drive qualify --study ... --out ... --max-levels ... --limits ...`.
Сохраняются study/protocol, вход, полный result и assessment каждого уровня,
итоговый summary и manifest. Проверяются все клетки, observer flux, source/DC,
скорость, winding/electronics temperatures, physical actions и события.
Каждая траектория должна пройти собственные energy/KCL/magnetic audits.

Положительный результат требует трёх последовательных аудированных meshes,
физических Cauchy caps и уменьшения нормированных разностей по трём группам.
Solver failure разрывает трёхсеточную цепочку; неполный/нефинитный результат,
пропущенная клетка, неверный clock или плохой баланс не дают qualified.
Неудачные попытки остаются в артефакте. Native NumPy scalar audit metrics
нормализуются в JSON-compatible bool без изменения критериев.

Явный `step_budget`: default 300000, range 1000..2000000; default поле
опускается из canonical JSON. Прежняя плановая оценка 200000 сохранена
через резерв трети бюджета. Фактический лимит проверяется до следующего
physical advance; токи, потокосцепления, температуры, энергия и latch
при отказе не получают дополнительного шага. Плановое превышение отмечается
`step_budget_exhausted`/`not_run`; фактический RuntimeError принятого-step
бюджета записывается как `solver_failed` с исходной причиной.

Матрица публичного workflow при max-levels=6/default budget:

- `saturated_single_core`: qualified, 0.625 us, 5 траекторий.
- `double_cage`: unqualified/levels_exhausted, 6 траекторий. Отказ обязателен.
- `saturated_four_core`: qualified, 0.625 us, 5 траекторий.
- `warm_four_winding`: qualified, 0.625 us, 5 траекторий.

`benchmarks.switching_qualification_acceptance` проверяет именно этот
workflow, включая правильный отрицательный результат. Его passed=true
не означает all_cases_temporally_qualified=true.

Отдельный double-cage профиль: max-levels=9, step_budget=2000000, финальный
step=3.90625e-8 s. CLI завершился qualified=true. В последней траектории
1024754 accepted steps на 40 ms; это дорогой bounded профиль. Разница между
двумя последними уровнями: stator flux alpha 6.69178e-6 Vs, motor alpha
current 0.000708053 A, source phase-a current 0.000355463 A. Все прежние
caps сохранены; разности трёх групп уменьшились. Финальная относительная
energy ошибка около 2.32e-10, max KCL около 1.28e-5 A. Это не абсолютный
error bound, не универсальный выбор шага и не подтверждение причины всей
предыдущей немонотонности. Пробные zero-current guards не дали достаточного
результата и в production не включены.

Проверки и provenance:

- 537 selected tests, 71 новый; failures/errors/skips=0. Полный repository
  suite не запускался.
- 21 native workflow + 9 deep native + 16 independent-comparison trajectories.
  Это 46 protocol runs, не 46 уникальных схем. Независимый mixed reference
  разделяет midpoint/discrete-gradient математическую схему с production.
- Для workflow 21 и independent comparison 16 выполнен numerical replay
  исходным installed waveform-producer wheel. Deep 9 numerical replay
  отдельно не выполнялся.
- После исправления сериализации NumPy bool изменился только qualification
  scorer. Численные Python-модули проверены побайтово относительно исходного
  wheel; 21+9 сохранённых траекторий переоценены финальным scorer. Это 30
  reassessments с new_numerical_trajectories=0, не numerical replay.
- Два producer IDs сохраняются отдельно: waveform и assessment. Исходный
  и финальный wheel включены в поставку. Compatibility checker отвергает
  изменение любого numerical runtime module, несовпадение hash или inventory.
- Восемь default-budget траекторий R06.12/R06.13 совпали по всем сохранённым
  значениям в точности. Canonical входные JSON также сохраняются.
- Финальный installed wheel проверен из /tmp с запрещёнными reference imports:
  успешная CLI qualification, корректный resource отказ и ZIP round trip.
  Это два CLI workflows/четыре native trajectories, тот же host/OS и
  system-site-packages; независимый host не заявлен.

Основные артефакты: `reports/switching-qualification-workflow-2026-11-11/`,
`reports/switching-qualification-deep-double-2026-11-11/`,
`reports/switching-qualification-deep-reassessment-2026-11-11/`,
`reports/switching-qualification-independent-2026-11-11/`,
`reports/switching-qualification-final-portable-2026-11-11/`.
Итоговая поставка — `reports/switching-qualification-delivery-2026-11-11/`.

Следующие критерии:

1. Адаптивная/гибридная временная схема с измеренной стоимостью и проверкой
   error control: уменьшить миллион шагов без ослабления физических caps.
2. Независимая полная finer-mesh/continuous-time проверка сложного привода,
   включая switching-energy, thermal trips и variable semiconductor R(T).
3. Расширение подтверждённых general DAE/phase-network размеров и связей;
   затем использование явно ограниченных qualification metadata в SFT/GRPO.

Industrial/OEM accuracy, universal superiority, независимый внешний host
и общий absolute error certificate не заявляются. GitHub/CI/git mutations
не выполнялись.


## R06.14 — ускорение существующего расчёта при сохранении результатов

Профиль 25624 accepted steps показал лишние residual evaluations и allocations.
Внутри одного неизменного electrical trial теперь переиспользуются уже
вычисленные residual/diode/forward значения принятого Newton trial. Прежнее
OR-правило line search сохраняется: residual уменьшился — potential не нужен;
иначе вычисляется прежний potential merit. Symmetric matrix/gradient создаются
по потребности. Постоянная часть roundoff threshold вычисляется один раз,
зависимость от текущего voltage сохраняется. Кэш не переживает trial.

В magnetics beta=0 radial gradient обходит ненужные radius/power/outer операции
по той же линейной формуле. `np.r_` заменён concatenate в magnetic hot paths;
повторный `matrix @ old + offset` вычисляется один раз. Rs/Rr(T), core current,
насыщение, все клетки, winding RC, source/DC/PWM, energy и protections сохраняют
свои уравнения. Число/положение accepted steps не меняется.

Frozen `benchmarks/switching_hotpath_reference.py` воспроизводит affected
R06.13 methods/constructor до оптимизации. Совпадение с оригинальными
сохранёнными R06.13 waveforms проверено на всех пяти workloads, включая
million-step случай: не только два текущих класса сравниваются друг с другом.
Это прежняя собственная реализация, не внешний physical oracle.

Paired timing protocol: пять полных 40-ms workloads, три fresh processes на
реализацию/случай, чередование порядка, BLAS/OMP/MKL=1. Common imports и
BLAS warm-up вне clocks; constructor и полное формирование result внутри;
serialization вне clocks. CPU affinity/frequency и посторонняя OS нагрузка
не изолированы. Во время protocol agent не запускал CPU-heavy validations.

Медианы old → optimized:

- `saturated_single_core`, 10 us: 4.3951 → 3.7607 s, 1.1687x.
- `double_cage`, 10 us: 2.1005 → 1.7586 s, 1.1944x.
- `saturated_four_core`, 10 us: 4.3299 → 3.7875 s, 1.1432x.
- `warm_four_winding`, 10 us: 6.3599 → 5.5712 s, 1.1416x.
- `deep_double_cage`, 39.0625 ns: 267.8255 → 219.1555 s, 1.2221x,
  1024754 accepted steps в обоих вариантах.

Это около 12–18% меньше wall time, не 14–22% меньше времени. Четыре 10-us
случая — timing/equivalence workloads, не temporal qualification этим тестом.
Deep остаётся дорогим и не становится общим EMT/DAE профилем. External
simulator speed ranking и универсальный выигрыш не заявлены.

Все 30 results прошли physical-channel/event/energy audits; old/new values
совпали в точности во всех парах. Три результата каждого implementation
также совпали между fresh processes. Полный повтор `--verify` всех 30
выполнять отдельно не стали; available command не означает выполненный replay.

Дополнительная проверка:

- 760 selected tests, failures/errors/skips=0, 223 новых R06.14. Это 216
  coupled candidates (27 gate patterns × 4 plants × 2 steps), 3 damped Newton
  comparisons и 4 magnetic-output contracts. Остальные — регрессии прежних
  numerical/thermal/phase/control/API компонентов. Полный repository suite
  не запускался.
- Fresh current CLI qualification на 156.25/78.125/39.0625 ns: qualified,
  тот же selected step. Все три полных результата совпали с исходными R06.13
  fine meshes; новый finest result совпал со всеми тремя optimized timing
  results. Последний проход — дополнительный численный повтор fine trajectory.
- Независимый mixed-equation/control/PWM reference: 8 current + 8 independent
  полных trajectories на двух шагах, passed; затем 16 numerical replays,
  passed. Общая midpoint/discrete-gradient схема не является continuous-time
  error bound.
- Installed wheel из /tmp с forbidden reference imports: full warm-four
  расчёт, короткая CLI qualification, ZIP export/extract. Тот же configured
  host/OS и system-site-packages; независимая ОС не заявлена.
- Instrumented cProfile на одинаковом 25624-step случае: residual evaluations
  104586 → 52315, NumPy index-tricks calls 102586 → 25624. Instrumented wall
  times не используются как speed claim.

Core protocol: 30 paired timing runs + 3 fresh fine qualification + 16
independent-comparison runs = 49 trajectories; отдельно 16 independent
numerical replay и один повтор optimized fine через CLI qualification.
Это не 49 уникальных схем. Timing repetitions уже входят в 30, не прибавляются
повторно. Четыре installed-wheel native trajectories — отдельная portable
проверка (один full run и три коротких qualification levels).

Артефакты:

- `reports/switching-hotpath-qualified-2026-11-11/`: timings, 30 raw results,
  inputs, checks, identities и manifest.
- `reports/switching-hotpath-deep-qualification-2026-11-11/`: fresh CLI profile.
- `reports/switching-hotpath-independent-2026-11-11/`: независимая сверка.
- `reports/switching-hotpath-original-comparison-2026-11-11/` и
  `reports/switching-hotpath-fine-reproduction-2026-11-11/`: historical/current
  result comparisons.
- `reports/switching-hotpath-tests-2026-11-11.xml`: 760 tests.
- `reports/switching-hotpath-portable-2026-11-11/`: installed-wheel receipt.
- `reports/switching-hotpath-delivery-2026-11-11/`: итоговая поставка.

Далее — локальный adaptive/error-control workflow с сохранением energy/events
и проверкой против fine trajectories, измеренные ресурсы больших DAE/phase
схем, независимые полные switching-energy/trip/material-R(T) случаи. Около
миллиона шагов на 40 ms и причина coarse nonmonotonicity остаются открытыми
инженерными вопросами; R06.14 не объявляет их решёнными.
GitHub/CI/индексация/commit/push не выполнялись.

## R06.15 — адаптивный midpoint и выявленный предел уточнения

Реализовано:

- Явный opt-in `adaptive` в switching-drive study; штатный fixed-step путь
  сохраняет прежнюю арифметику и формат результата без adaptive-полей.
- Step doubling: принимаются два native полушага, без Richardson state
  extrapolation. Coarse/rejected/root trials имеют собственные массивы,
  события, реле и энергетические накопления. Цена всех проб учитывается.
- Отдельные бюджеты accepted steps/probe evaluations, ограничение роста и
  уменьшения шага, отказ на min_step_s и nonfinite input. Сбой не коммитит
  физическое состояние.
- Observer получает точные суммы midpoint-интегралов принятых подшагов;
  body-diode heat/overlap/peak агрегируются по принятым подшагам.
- Нулевой ток источника локализуется с bounded numerical projection;
  энергия коррекции записана отдельно и не приписывается физическому теплу.
- Fixed-mesh qualification отвергает adaptive: простое уменьшение потолка
  шага не является проверкой адаптивной сетки.
- `benchmarks.switching_adaptive_acceptance` сохраняет каждый уровень
  0.2 → 0.1 → 0.05, входы, fingerprints, hashes архивных результатов,
  physics audits, физические разности и отказы.

Проверки:

- 526 selected tests passed. Новые проверки: изоляция trials, принятые
  состояния/энергия против двух native полушагов, rollback при solve/budget
  failures, winding relay, diode-zero restart, malformed control inputs.
- Четыре полных fixed-step повтора совпали **в точности** с архивными
  R06.14 fine results; adaptive-поля отсутствуют.
- Installed wheel из /tmp: reference imports запрещены, короткий adaptive
  CLI run, sealed verify, ZIP roundtrip; package fingerprint в wheel
  совпадает с исходниками. Тот же OS/system-site-packages, не внешний host.
- Четыре полных 40-ms adaptive прогона с budget 0.2 прошли исходные
  physical-unit caps, действия автоматики и energy/KCL/magnetic audits.
  Accepted steps: single core 51090, double-cage 46608, four core 36566,
  warm-four 47444. Это не доказательство общего CPU speedup: probe/root
  overhead может сделать другие случаи медленнее fixed-step решения.

**Весь адаптивный профиль пока НЕ квалифицирован.** Double-cage с budget
0.1 и 0.05 честно останавливается на min_step_s. На диагностическом повторе
0.1 при t≈28.9197603 ms разность flux coarse/fine =1.99e-8 Vs при h=0.1 ns,
source-current difference≈1.78e-15 A; source-zero event отсутствует.
Нельзя считать проблему решённой или ослаблять global caps для получения
зелёного итога. Требуется разбор algebraic/body-diode switching и
обусловленности на этой поверхности, затем новый зарегистрированный прогон.

Артефакты: `reports/adaptive-corpus-2026-11-11/` (exploratory matrix),
`reports/switching-adaptive-refinement-2026-11-11/` (полная refinement matrix),
`reports/switching-adaptive-minstep-diagnostic-2026-11-11.json`,
`reports/switching-adaptive-default-replay-2026-11-11.json`,
`reports/switching-adaptive-tests-2026-11-11.xml`,
`reports/switching-adaptive-portable-2026-11-11/`.
Независимый full adaptive oracle, абсолютная ошибка/OEM accuracy и
лидерство над внешними моделерами не заявляются. GitHub/CI/git mutations
не выполнялись.

Для воспроизведения minimum-step диагностики без изменения критериев:
`python -m benchmarks.switching_adaptive_diagnostic --out reports/adaptive-diagnostic.json`.
Ожидаемый exit code при воспроизведённом отказе — 2.

Итог полной refinement matrix R06.15: **10/12 passed**, 2 ожидаемо
сохранённых solver rejections (double-cage 0.1/0.05). Single-core,
four-core и warm-four прошли все три бюджета. Итог всего профиля — false.
Delivery: `reports/switching-adaptive-delivery-2026-11-11/`;
package fingerprint `fc7741c3c96e429e3ebdfab249f3a3cf29100de2ae445dd794b5e73ba3875588`.

## R06.16 — Newton/диодная граница на слабом motor pole

Воспроизведён механизм исходного сбоя R06.15 на t≈28.9197603 ms:
неразрешённая motor row скрывается общим C/h roundoff threshold;
Newton-direction ~9 kV при ближайшей diode hinge в нескольких microvolts
требует больше 30 обычных halvings. All-row ужесточение порога испытано и
отклонено из-за новых line-search failures; его результаты не используются
как подтверждение production-версии.

Реализованы отдельный stopping scale motor rows и выбор начального
Newton trial у ближайшей положительной diode hinge. Сохраняются обязательные
merit/backtracking и прежние физические уравнения. Никаких fictitious leakage,
ёмкостей, демпфирования или повышения temporal caps не добавлено.

Публичный `circuit_solver`: `auto` (legacy fixed-step, motor-scaled adaptive),
`legacy`, `motor_scaled`. Default auto не добавляется в canonical JSON.
Фиксированный legacy путь сохраняет точные прежние результаты; для проверки
исправленного решателя на равномерной сетке задаётся motor_scaled явно.

Проверки текущих исходников:

- 538 selected tests passed; 12 новых относительно R06.15.
- Исходный regression state сохранён как data-only JSON в
  `tests/fixtures/switching_adaptive_hinge.json`. Frozen legacy воспроизводит
  coarse/two-half flux difference >1e-8 Vs. Новый нормированный estimator на
  том же state ≈1.330136e-5 вместо 19.90254.
- Независимые mixed stage equations проверены на h=0.05/0.1/0.2/1 ns:
  flux atol1e-10 Vs, motor pole potentials atol1e-3 V. Это четыре стадии
  одного state, не четыре независимые схемы или full trajectories.
- Fresh default adaptive matrix: single-core/double-cage/four-core/warm-four,
  четыре полных 40-ms результата, все archival physical-unit caps,
  discrete actions и energy/KCL/magnetic audits passed.
- Fresh double-cage budgets 0.2/0.1/0.05: все completed, solver_failures=0.
  0.2 passed, 0.1/0.05 failed по machine/observer differences к архиву.
  Их взаимная близость не доказывает absolute accuracy. Общий итог — false.
- Fresh fixed-mesh motor-scaled refinement 2.5 us → 78.125 ns: шесть полных
  результатов и все отрицательные оценки сохранены; qualified=false.
- Четыре full legacy R06.14 fine trajectories воспроизведены в точности.
- Новый wheel установлен в /tmp venv; reference imports запрещены;
  regression state и короткий adaptive CLI run passed. Тот же host/OS и
  system-site-packages, не независимая ОС или индустриальная квалификация.

Артефакты:
`reports/switching-hinge-tests-2026-11-12.xml`,
`reports/switching-hinge-matrix-2026-11-12/`,
`reports/switching-hinge-double-refinement-2026-11-12/`,
`reports/switching-motor-scaled-fixed-2026-11-12/`,
`reports/switching-hinge-legacy-replay-2026-11-12.json`.
Source fingerprint:
`1e630f468c7c759970002a2be26f1178cfcb45aa25438f6a01c01ea8447e02c3`.

Закрыт конкретный solver failure, а не весь adaptive/global-accuracy блок.
Следующая инженерная работа: motor/body-diode zero-current transitions,
независимый полный reference и исследование немонотонных mesh differences.
Следует различать физический energy audit, discrete residual convergence
и приближение continuous-time trajectory. GitHub/CI/git mutations не было.

Продолжение fixed-mesh проверки: fresh 39.0625 ns run завершён,
1024754 native steps. Последние три сетки 156.25/78.125/39.0625 ns прошли
исходный Cauchy/reduction criterion и physics audits. Максимальные
нормированные различия последней пары: network 0.03554, machine/observer
0.66918, thermal 7.13e-6. Это extension qualification текущего production
solver, не независимый continuous-time bound.

`benchmarks.switching_hinge_tail` проверяет sealed manifests, общий
package fingerprint, неизменность всей study кроме точного деления шага
пополам, смежность двух предыдущих сеток и исходные limits. Receipt:
`reports/switching-hinge-extension-2026-11-12.json`;
новая траектория: `reports/switching-hinge-fine39-2026-11-12/`.
Adaptive 0.2 проходит и этот fresh fine reference; 0.1/0.05 не проходят
(machine/observer normalized error около 4.43), несмотря на completed и
нулевое число solver failures. Это ограничение оставлено открытым.

## R06.17 — нулевой ток двигателя: bounded adaptive profile прошёл

Реализованы:

- motor_current_zero только для открытых управляемых каналов; root alignment
  coarse/fine с явным current tolerance и временным согласованием.
- canonical vector-zero event, когда у изолированной звезды совместно
  исчезают токи последних проводящих фаз. Signed roundoff больше не создаёт
  разные идентичности одного события для coarse/fine.
- Сохранение accepted native states/energy **без flux projection/reset**.
  Метаданные содержат фазы, время, current residual и flux_projection=false.
  Добавлен motor_boundaries counter. Обычный min_step_s не запрещает более
  короткий остаток до локализованного события.
- Два data-only regression states: одиночное событие и gate-block coincidence.
  Старое signed-only распознавание воспроизводит прежний отказ и rollback;
  исправленное принимает те же native physical arrays/ledgers.
- Source-only budget qualification: immutable caps/hashes/producer, три
  half-budget studies, event residual/work accounting, physics/reference
  audits, adjacent differences и reduction_factor0.85. Public fixed-step
  qualification намеренно не смешивается с adaptive workflow.
- Frozen R06.16 driver сохранён как собственный SHA256-pinned baseline в
  benchmarks; он не является независимым oracle и не импортируется runtime.

Доказательства:

- 564 selected tests passed, 26 новых относительно R06.16.
- Fresh final matrix: четыре 40-ms studies × budgets0.2/0.1/0.05 = **12/12**.
  Single-core saturation/Gc, double-cage, four-core sag/block/recovery,
  warm-four winding R(T). Все прежние global caps и audits прошли.
- Все четыре studies прошли также строгую бюджетную self-consistency
  qualification: decreasing adjacent physical differences при factor0.85,
  выбранный budget0.05. Это не asymptotic order proof или absolute bound.
- Double-cage machine/observer error к fine archive снизился примерно
  с normalized4.43 до0.32; исходные flux caps не повышались.
- Четыре independent mixed event pieces на двух состояниях: сравнение flux,
  source current, network voltage, speed и motor AB voltage; free common-mode
  gauge исключён. Reference использует ту же midpoint/DG схему.
- Ablation заменяет только adaptive driver. Frozen baseline точно совпадает
  с историческим R06.16 raw result и проваливает caps; current driver проходит.
- Четыре legacy full trajectories совпали с R06.14 в точности.
- Installed wheel из /tmp: оба regression states, полный double-cage0.1 run,
  verify и ZIP roundtrip. Полный результат включая numerical events совпал
  с source run **в точности**. Reference imports запрещены; OS/host и
  system-site-packages общие — независимый host не заявляется.

До совпадающего-event исправления первая полная matrix имела 11/12 passed;
этот отрицательный результат сохранён отдельно. Final matrix не подменяет
его и имеет другой producer fingerprint.

Артефакты:
`reports/switching-motor-zero-final-refinement-2026-11-12/`,
`reports/switching-motor-zero-budget-qualified-2026-11-12/`,
`reports/switching-motor-zero-independent-stages-2026-11-12.json`,
`reports/switching-motor-zero-pinned-ablation-2026-11-12/`,
`reports/switching-motor-zero-final-tests-2026-11-12.xml`,
`reports/switching-motor-zero-final-legacy-2026-11-12.json`,
`reports/switching-motor-zero-installed-replay-2026-11-12.json`.
Source fingerprint:
`0da006ecde9bb4172dd80e422e09102bfaee128b8fad04c59288ec05caf1aa24`.

Остаются: независимая full continuous-time/external simulator сверка,
switching-energy/thermal trips/material R(T) в едином профиле, charge-dynamic
EMT и измеренный рост размеров сетей. Успех данного authored профиля не
означает точность произвольной study и не доказывает превосходство над всеми
моделерами. GitHub/CI/git mutations не выполнялись.

Дополнительная граница применимости: integration_event_log — metadata
локализованных solver roots, не исчерпывающий реестр всех semiconductor
mode changes. В warm-four0.2 есть одна дополнительная motor-zero label около
33.485728 ms по сравнению с0.1; её state residual находится в tolerance,
physical actions/caps и decreasing differences passed. При изменении budget
полная идентичность этих numerical labels не заявлена и не является
критерием certificate. Это важно для подготовки SFT/GRPO labels.

Локальная sealed поставка R06.17:
`reports/switching-motor-zero-delivery-2026-11-12/` — raw runs, qualification,
negative pre-coincidence matrix, frozen-driver intervention, reference inputs,
installed wheel/receipts, выбранные sources/fixtures и результаты проверок.

## R06.18 — full continuous external-machine drive verification

Закрыт пробел между независимыми midpoint stage checks R06.17 и полной
непрерывной траекторией. Новый reference не импортирует production topology,
solver, ports, controls или event engine. До импорта native machine wheel
и все Python sources проверяются; preloaded production module вызывает
ошибку, затем meta-path blocker запрещает production imports.

Физический reference:

- Original motulator0.23.1 single-cage saturated Gamma RHS, constant positive
  Gc и winding Rs/Rr(T); без правки native source или RHS.
- Independently expressed RL source modes/guards, diode bridge, DC/earth
  capacitors, channels/body diodes, free motor common-mode gauge, FOC,
  current-model observer, PWM/dead time и RC thermal storage.
- Continuous DOP8531e-12/1e-14 и Radau3e-11/3e-13, max_step25us.
  Согласие <=1% каждого прежнего physical cap, energy/KCL/constitutive audits.
- Single-cage positive-Gc scope необходим для instantaneous algebraic port.
  Multicage external plant, zero-Gc constrained dynamics, switching losses,
  semiconductor R(T), active brake и thermal trips в этот профиль не входят.

Результаты:

- Четыре полных40-ms cases: linear core start, saturated core start,
  saturated sag/block/recovery, warm single winding R(T).
- 8 primary trajectories (budgets0.2 и0.05), 8 continuous references
  (два метода на case): **все passed**, без исключения failed cases.
- Максимальный normalized primary error0.2722962; reference-to-reference
 6.62501e-5. Это comparison budget, не mathematically rigorous global bound.
- Первоначально recovery получил failed из-за двух gates_enabled labels,
  а не physical caps. Reference использовал canonical24/28ms, runtime
  repeated-addition clock находился наulp раньше. Эти failed raw artifacts
  сохранены отдельно и не переписаны.
- Report() теперь выдаёт canonical sample tags и right-hand gate eligibility.
  Electrical states/current и held control snapshot остаются pre-switch.
  В интеграционный clock, equations или Newton изменения не внесены.
- AST comparator и pinned previous wheel проверяют только объявленную
  report-only замену. Все остальные runtime Python bytes идентичны.
- Все8 before/after physical channel arrays, energy ledgers, control values
  и work counters совпали **в точности**. Изменены clocks и4 boundary gate
  labels (24/28ms ×2 recovery budgets).
- 676 selected tests passed:566 production +110 native-reference/compatibility,
  совместный запуск с pinned native package, без failures/errors/skip.
  27 safe gate states ×3 flux states проверяют KCL и passive power;
  linear/saturated/warm tests проверяют полную storage derivative identity.
- Ready cases: `cases/continuous-drive/`, с qualified primary budget0.05.

Continuous reference curves после report-only fix переиспользованы строго
по identical study/reference source/wheel/solver controls; worker production
imports запрещены, поэтому runtime reporting code не влияет на них.
Primary8 после fix рассчитаны заново. Collector повторно проверяет native
source maps, physical caps, audits, reference disagreement и whole before/
after data. Это не разрешение повторно использовать primary из другого
producer и не маскирование source changes.

Артефакты:
`reports/continuous-drive-qualified-final-2026-11-12/`,
`reports/continuous-drive-all-tests-2026-11-12.xml`,
`reports/continuous-drive-production-tests-2026-11-12.xml`,
`reports/continuous-drive-tests-2026-11-12.xml`.
Source fingerprint:
`154bd1b976f0d42fb96fb9ce7c608691afdbb03db57f48ecf24ae7e3598e6385`.

Reference version/wheel и исходные physical caps закреплены до финальных
прогонов. Параметры авторские, не паспорт/measurement конкретной установки.
Это external motor component + independent full circuit adapter, не весь
внешний simulator. Никаких CPU superiority/OEM/all-network accuracy claims.
Следующий профиль — semiconductor R(T), switching energy и thermal trips;
затем multicage continuous reference и большие связанные сети.
GitHub/CI/git mutations не выполнялись.

Поставка/installed-wheel итог R06.18:
`reports/continuous-drive-portable-2026-11-12/` содержит новый wheel,
full recovery0.05 CLI run, continuous-reference comparison, точный source
replay и ZIP roundtrip. Imports benchmarks/tests/motulator в runtime
запрещены. Тот же OS/host и system-site-packages, не independent-host CI.
Локальная sealed поставка:
`reports/continuous-drive-delivery-2026-11-12/`.
