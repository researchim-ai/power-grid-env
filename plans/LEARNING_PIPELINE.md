# Пайплайн обучения инженерного агента Power Grid

## Текущий эксперимент: GPU solver-assisted SFT (19.11.2026)

Успех измеряется terminal физического решателя, а не CE или отдельными flags.
Одинаковый fixed96 recipe на64 full-action TRAIN targets дал на первом закрытом
TEST:0.8B3/6 и4B5/6 против0/6 у обоих base. Это3 базы/2 topology families;
4B repair2/3,0.8B repair0/3. Разработка API использовала DEV, а TEST раскрыт
однократно для уже зафиксированных финальных checkpoints. Все дальнейшие прогоны
на этих случаях — regression, нужна новая заранее закрытая выборка для promotion.

В compact observation остаются точные lines/catalogues, реальные constraints,
actual result/revision/freshness и гипотетические studies. Нет скрытого correct
catalog ID в prompt. Контроллер создаёт только TRAIN teachers и algorithmic control;
он никогда не исполняет действия вместо LLM. Все вызовы/ошибки тратят бюджет.
Preview не сертифицирует actual сеть. Нативные nested argument payload labels
проверяются на каждом примере, prompt замаскирован полностью.

`engineering_branch_sft` строит240 уникальных TRAIN rows из160 физически успешных
продолжений: все16 subsets previews на каждом из8 repair cases и recovery после
повторных расчётов исправной сети. TRAIN остаётся8 баз/4 семейства: больше states
не означает больше независимых сетей. Fixed240 updates,accumulation4,одно действие
каждого класса за update; модели стартуют с fresh pinned weights. Одновременно
меняются state coverage, число updates и seed; причинный вклад каждого не установлен.
Vision не используется, этот профиль нельзя называть VLM обучением.

Воспроизведение при наличии локальных sealed parent plans и pinned моделей:

```bash
.venv-qwen35-08b/bin/python -m benchmarks.engineering_gpu_workflow \
  --candidate qwen4b --device cuda:1 --steps 96 --out reports/my-workflow-4b
.venv-qwen35-08b/bin/python -m benchmarks.engineering_branch_sft build \
  --out reports/my-decision-coverage
```

Новая output directory обязательна; train требует manifest hash созданного корпуса.
В fixed96 runs обучались3,607,552 LoRA parameters у0.8B и8,116,224 у4B;
карты различаются (3090Ti/3090), поэтому timings не доказывают преимущество
скорости одной модели. Одинаковая структура LoRA не означает одинаковый бюджет
параметров.

GPU runtime вне песочницы: BF16/SDPA, frozen backbone, FP32 LoRA/AdamW, без quantization
и скачивания weights. Finite research run сохраняет adapter и логи, не durable
optimizer/RNG resume. Все manifests проверяют целостность, не внешнюю подпись.

Итог расширенной фазы:4B28/30 (TRAIN15/16,DEV7/8,раскрытыйTEST6/6),
0.8B23/30 (15/16,5/8,3/6). У4B сохранены все21 прежних успехов и добавлены7;
у0.8B —6 новых TRAIN успехов при1 потере, без роста DEV/TEST. Новая фаза не
подтверждает перенос на новую закрытую выборку и не устраняет все циклы инструментов.
Все180 эпизодов обеих моделей/трёх фаз проходят отдельный offline replay/proof audit.

Следом: разделить recipes data-only/steps-only на TRAIN-development splits;
заморозить новые physics/topology families для независимой оценки; провести
ID/catalogue-order perturbations; затем image-required observations и отдельный
GRPO experiment с terminal physical reward. Области за пределами radial constant-Z
требуют самостоятельных данных и профилей. Актуальные числа — CURRENT_STATUS.md.


Дата: 19 ноября 2026. Статус: выполненные этапы отмечены отдельно от очереди.
Цель: Qwen3.5-0.8B решает инженерные задачи через инструменты и проверяемый
физический расчёт, затем работает со схемами/графиками как VLM.
Экономика исключена. GitHub, CI и отправка данных внешним сервисам не требуются.

Актуальное продолжение — **R06.92 в конце этого файла**: native diagnosis,
контроли balanced/KL и transactional retention guard; full-policy — R06.87.
Раздел0 и прежние R06.* сохранены как история экспериментов, не текущий score.

## 0. Откуда начинаем

- Проверены batching, assistant-only labels, один native epoch из38 шагов,
  checkpoint/resume, неизменность базы и точное восстановление адаптера.
- Это38 префиксов семи train-задач, всего545 supervised токенов действий;
  префиксы одной траектории не являются независимыми сетями.
- Train: одна chain-топология; dev: одна star-топология; test: одна branched.
  Это недостаточный набор для вывода о широкой генерализации.
- В завершённом compact/guarded dev: base0/7 и adapter0/7; оба только повторяют
  simulate. Публичный алгоритмический контроль решает7/7 с настоящим PF.
- Epoch обучен на raw-историях; завершённый paired-прогон использовал compact
  view и repeat guard2. Неограниченный raw paired прерван, полного score нет.
- Адаптировались семь проекций только последнего decoder-блока, rank4.
  Это проверенный CPU smoke-профиль, не подтверждённый оптимальный SFT-рецепт.
- Текущий worker требует JSON-действие, помещает схемы инструментов в текст
  и ответы среды в user-сообщения. Локальный Qwen template также имеет native
  tools/tool_calls/tool_response формат. Их преимущество ещё не сравнено.

Evidence: [текущий статус](CURRENT_STATUS.md),
`reports/engineering-paired-guarded-dev-2026-11-18/summary.json`,
`reports/engineering-epoch-complete-2026-11-18/summary.json`.
Это не доказательство одной причины неуспеха: возможны protocol/view mismatch,
малый и узкий корпус, exposure bias, недостаточная адаптация или их сочетание.

## 1. Контракт среды и экспериментальные профили — LP-01

Сначала зафиксировать versioned EpisodeSpec, ObservationSpec, ActionSpec,
RewardSpec, RenderSpec и RunSpec. Один manifest связывает модель/processor,
chat template, протокол, view, маску инструментов, solver и judge,
генератор/каталог/seed, budgets, decoding, trainable modules и runtime versions.
Исходные требования принадлежат owner. Candidate получает только разрешённые
наблюдения; эталоны, будущие события и критерии приёмки нельзя редактировать.

Два интерфейса измеряются отдельно: нынешний JSON-action и native Qwen tools.
Native-вариант использует tools в template, assistant tool_calls и role=tool
ответы. Проверить именно закреплённый локальный template: append-only токеновый
prefix на нескольких tool-turns, call IDs, несколько аргументов, escaping,
EOS и enable_thinking. Не считать поддержку Qwen3 поддержкой Qwen3.5 без теста.

Для сравнения интерфейсов сохраняются один проект, те же разрешённые действия,
численные результаты, budgets и несколько эквивалентных планов. Парсер без
скрытого «исправления» недопустимых действий. Любой retry входит в бюджет.
Per-action output budget определить по токенизации самых длинных допустимых
действий ОБОИХ протоколов с запасом; прежние64 токена нельзя автоматически
перенести на XML tool calls. Отдельно ограничить число действий, общий context,
суммарные generated tokens и wall time. Для A/B дать достаточный общий cap
и заново измерить обе baseline, а фактическую стоимость формата отчитывать отдельно.
Сначала небольшие train protocol-probes; dev используется для выбора из
заранее объявленных вариантов, не для бесконечного перебора.

Train и evaluation используют одну версию observation projection. Raw и compact
— разные профили. Guarded и unrestricted — разные профили. Нельзя заменять
дорогой незавершённый прогон результатами другого режима. Если есть loop guard,
то он одинаков у base, SFT и RL и его stop reason сохраняется. Успех после
отключения guard не выдумывается, остановка guard не доказывает невозможность
решить задачу при большем числе действий.

Критерий: сериализация/replay/prefix/masks проходят; один manifest однозначно
воспроизводит выбранный интерфейс. Протокол и view заморожены до нового SFT.

## 2. Проверка обучаемости на крошечном наборе — LP-02

Взять8–16 train-траекторий трёх типов, включая ошибки и recovery; это могут
быть разные планы существующих семи задач, а не8–16 независимых сетей.
Для этого debug-gate достаточно ручных train-fixtures и минимальной recovery-
ветки эксперта из LP-04; не нужно ждать полного генератора LP-03/04. До массовой
генерации проверить, что модель способна переобучиться на них в CLOSED LOOP,
а не только снизить teacher-forced loss. Цель debug-gate —100% завершений на
этом самом маленьком memorization-наборе; это не качество на новых сетях.

Диагностика: первый выбранный инструмент, валидность аргументов, действие
после simulate, вероятность expert-action и нескольких допустимых альтернатив,
полная траектория до submit, неверные/stale расчёты, EOS/truncation и guard.
Action exact-match — вспомогательный показатель: разные корректные решения
не должны считаться ошибкой только из-за иного порядка или ID новой линии.

Сравнить исходный checkpoint, прежний last-block rank4 и небольшой языковой
LoRA-пилот по нескольким/всем подходящим decoder-блокам. Начальная проба —
rank16, затем при необходимости rank32; конкретные Linear-модули определить
по Qwen3.5 architecture, не копировать список attention-проекций другой модели.
Vision encoder и мультимодальный projector сначала заморожены. Один фактор
менять за раз, фиксировать seed/число обновлений/объём supervised токенов.

Если gate не пройден, остановить масштабирование: проверить реальную загрузку
адаптера, train/eval template, позиции causal labels, masking, logits, sampling,
trainable parameters и подходящую ёмкость адаптации. Тысячи новых примеров и
GRPO не должны скрывать неисправный тракт.

## 3. Генератор задач и split ДО траекторий — LP-03

Сначала разделить базовые проекты/семейства, затем получать варианты параметров,
ошибки, snapshots, изображения, траектории и prefixes. Все производные одной
базовой задачи остаются в одном split. Graph canonicalization инвариантна
перестановке узлов/ID/координат. Дополнительно группировать по исходному проекту,
каталогу и сценарию; близкие численные копии не должны попадать по разные стороны.

Обособить:
- train для SFT/aggregation/RL;
- train-validation для отладки оптимизации и раннего выбора checkpoint;
- development ID и development OOD для заранее ограниченных сравнений;
- новый закрытый final test ID/OOD для замороженного кандидата.
Старые семь dev — regression smoke, не достаточный benchmark. Owner/unit replay
существующего test не равен нейросетевому final test; для новой квалификации
нужен свежий корпус. Нельзя подглядывать в test при prompt/reward/model selection.

Порядок объёмов — стартовая гипотеза, не норматив достаточности данных:
32 сценария для debug →256 сценариев pilot →около2000 train-сценариев,
например50 структурных семейств с несколькими параметрическими вариантами.
Затем минимум порядка200 сценариев в каждой отдельной dev/final-ID/final-OOD
выборке при достаточном числе независимых семейств. Реальные размеры определить
по покрытию, learning curves, solver и compute-пилоту; не выдавать число
префиксов за размер независимой выборки.

Варьировать размеры/ветвление, маршруты, нагрузки, каталоги, сопротивления,
допустимые токи и расстояние до порога. Включать здоровые проекты, уже готовые
решения, исправимые ошибки, несовместимые каталоги, корректно доказанную
неразрешимость и неоднозначную диагностику. Для последних нужны отдельные
judge-контракты «недостаточно данных/невозможно», а не фиктивная успешная сборка.
Публичные IDs/порядок элементов/имя файла/цвет ground truth не кодируют ответ.

Каждая задача проходит positive public-expert replay и negative controls:
неверное исправление, изменение требований, устаревший расчёт, solver failure,
исчерпание бюджета, удаление обязательной нагрузки. Если независимая физическая
сверка для профиля отсутствует, явно сохранить ограничение oracle-проверки.

## 4. Публичный эксперт и supervised recovery — LP-04

Использовать public engineer как начального teacher, расширив его работу
из ПРОИЗВОЛЬНОГО допустимого текущего состояния. Он не может предполагать,
что первая операция всегда inspect_project или что прошлые действия идеальны.
Его область компетентности ограничена проверенными типами задач.

Создавать несколько корректных планов на сценарий: разный допустимый порядок
монтажа, выбор эквивалентного кабеля, запрос измерения перед правкой, отказ от
ненужной правки уже исправного проекта. Для каждой полной траектории — свежая
физическая приёмка; не оптимизировать копирование единственной сериализации.

Отдельно добавлять состояния после simulate первым действием, неправильного
ID/каталога, лишнего simulate, неудачной правки и solver error. На входе видны
реальная ошибка и текущий проект. Target — корректирующее expert-действие,
не ошибочная команда ученика и не текст «всё исправлено» без расчёта.
Безнадёжные/неидентифицируемые состояния получают честное завершение по своему
контракту, а не выдуманный успешный recovery.

Хранить episode_id, base-project/family/seed, state/revision, observation hash,
action, allowed tools, source(expert/student), public response, terminal verdict,
solver/version, protocol/view/template и masks. История bad actions может быть
контекстом, но её токены не становятся positive SFT labels.
Current eligible_for_gold_sft:false нельзя просто переключить: admission должен
выдать отдельное свидетельство replay, происхождения, наблюдаемости и masking.

Балансировать сценарии/типы/трудности, normal/recovery и роли инструментов.
Длинные траектории и частые simulate не должны подавлять редкие полезные правки.
Точные доли — эксперимент по train-validation, не магическое правило50/50.

## 5. SFT v2 и computational qualification — LP-05

Начать с prompt-completion формата «публичная история →следующее expert-действие».
Loss только на новых assistant action/final-answer токенах, включая нужные
протокольные delimiters/EOS. System/user, tool results, картинки и ошибочные
student actions маскируются. Один исторический assistant-turn не нужно снова
обучать во всех последующих prefixes. Если перейти к full-trajectory batching,
его weighting/masks должны быть отдельно проверены.

Проверять prefix identity после каждого append, labels[t]←logits[t−1], supervised
токены до/после collator, отсутствие masking всех targets, nonfinite градиенты,
неизменность frozen base, checkpoint roundtrip и настоящий optimizer resume.
При padding маскируется attention=0. Packing нескольких эпизодов требует
изоляции attention/positions; сначала он выключен.

CPU float32 остаётся correctness/debug-профилем. Для массовых прогонов измерить
step-time, tokens/s, peak RAM/VRAM и rollout latency на доступном устройстве;
наличие GPU здесь не предполагается. BF16, fused kernels, quantization и serving
ускорения вводить отдельно после численной и logprob-проверки. Не обещать
конкретную VRAM ёмкость без измерения на реальной длине и pixel budget.

LoRA по языковым слоям — основной первый кандидат после tiny-gate. Небольшой
заранее ограниченный поиск rank/LR/числа supervised токенов; начальный LR-пилот
порядка5e−5…2e−4,1–3 эпохи, несколько seeds после выбора конфигурации. Это
стартовые диапазоны, не утверждение об оптимуме. Выбор checkpoint по завершению
train-validation эпизодов, recovery и dev-критериям, не по минимальному loss.
Логировать trainable modules/count, sequence lengths и per-task breakdown.

Критерий: tiny closed-loop gate пройден; на новых train-validation проектах
есть успешные решения ВСЕХ текущих task kinds и recovery. Заранее закрепить
приёмочный порог для пилота (предлагаемый старт:≥90% по каждому типу на ≥100
случаях типа), диапазон числа ошибок/unsafe actions и доверительные интервалы.
Это внутренний gate к дальнейшему исследованию, не промышленная сертификация.

## 6. Итеративный сбор состояний ученика — LP-06

Запускать текущую policy только на train-сценариях. На посещённых состояниях
запрашивать у эксперта корректное действие/продолжение. В новой SFT-итерации
смешивать исходные демонстрации и эти corrections, сохраняя coverage/replay.
Это DAgger-подобная aggregation, а не стандартный on-policy GRPO.

Важно сохранять историю действий именно ученика: исправление после его ошибки
нельзя подменять красивой демонстрацией из reset. Expert-assisted успех
отчитывается отдельно; самостоятельный evaluator никогда не вызывает teacher.
Версии коллекции и policy неизменяемы. Проверять drift: какие состояния expert
не умеет решать, stale measurements, action loops, неоднозначность и симуляторные
ошибки. Не додумывать labels вне области эксперта.

Критерий: уменьшаются ошибки на НОВЫХ train-validation recovery-состояниях,
а не только loss на агрегированных prefixes. Dev используется ограниченно;
dev-траектории и ответы не попадают в тренировочную коллекцию.

## 7. Мультимодальный curriculum — LP-07

Вести параллельно после контракта среды, не ждать GRPO и не смешивать VLM-bug
с неспособностью выбрать инструмент. Сначала текстовый агент должен уметь
заканчивать базовые workflows; визуальные perception-микрозадачи можно готовить
раньше. Затем обучать их совместно с проверенными tool-use эпизодами.

Для одной точки времени/revision получать scene graph, текст и изображение:
однолинейная схема, табличка паспортов, затем графики U/I/скорости/тепла с
единицами и временной шкалой. Изображение отражает ТОЛЬКО доступные измерения:
нет будущего события, подписи скрытой неисправности, верного варианта кабеля
или финального отчёта до вычисления. Не путать снимок GUI с полным latent state.

Сначала чтение соединений/номиналов/оси и привязка ID к объекту; затем действие
по изображению и совмещённые данные. Проверять text-only, image-only, combined,
no-image/corrupted-image контроли и counterfactual пары: то же оформление,
другое соединение/паспорт/измерение, требующее другого ответа. Когда текст уже
содержит всё, успешный combined не доказывает использования зрения.

Разделять layouts/render styles/fonts и собственно topology/OOD; все картинки
и crops одной задачи остаются в её split. Хэши и имена файлов не являются
признаками ответа. Pixel/patch/resolution budget закреплён; crop не должен
удалять единицы, состояние аппарата или критическую ветвь.

Начать с frozen vision+projector и языковых LoRA. Если контролируемые vision
тесты не проходят, отдельно исследовать projector/последние vision-blocks с
retention-набором, а не размораживать всё сразу. Полный native collator обязан
сохранить pixel_values/image-grid metadata и маски prompt/image токенов.
Нынешний text-only string loader для этого непригоден без новой версии.

## 8. GRPO допускается после полезного starter-policy — LP-08

Переходить, когда стохастические TRAIN-rollouts имеют реальные успехи и группы
с различными проверенными исходами. Предлагаемая pilot-цель — заметная доля
неоднородных групп (например≥20% на фиксированном pilot-наборе), затем измерять
frac_reward_zero_std. Эти пороги — планируемый compute gate, не теорема.
Если все candidate-решения имеют одинаковую нулевую outcome-награду, соответствующая
группа не даёт group-relative сигнала качества; форматная награда/KL может
менять градиент, но это не появление инженерного успеха.

Группа K=4 для первого пилота, затем при необходимости8: одна задача, одинаковое
начальное latent state/сценарий/экзогенная случайность, отдельная среда для каждого
rollout и разные sampling seeds policy. Кандидаты не делят изменяемый проект.
Sampling для обучения отдельно от greedy evaluation. Pass@K отдельно от pass@1;
в ресурсах учитываются ВСЕ попытки, не только выбранная лучшая.

Начальная награда — верифицированный terminal success. Нет бонуса только за
валидный JSON, число simulate, красивый отчёт или изменение проекта. Внешние
ограничения/обязательные нагрузки неизменяемы, свежий успешный расчёт обязателен.
Ошибки, stale submit, budget failure и solver failure не становятся feasible.
Стоимость действий сначала учитывать как tie-break среди успехов; если включать
её в reward, успешные корректные решения должны всегда доминировать неуспех.
Dense/process shaping — отдельная ablation с adversarial тестами, не новый
источник «успехов» и не способ спрятать отсутствие terminal reward.

Logprobs/GRPO ratio/loss считаются только для сгенерированных assistant-токенов.
Среда/картинки/tool outputs не являются sampled actions и не участвуют в policy
loss. Expert corrections не подмешиваются как on-policy ответы: идут в SFT.
Sampling distribution и recomputed logprobs должны совпадать; grammar decoding,
top-k/top-p и vLLM corrections требуют отдельного теста, не только JSON-parser.
Начать с простого sampler, явно задать clipping/KL/temperature и pinned versions,
а не полагаться на меняющиеся defaults библиотек.

Для multi-turn GRPO нужен префикс-сохраняющий template, точная карта sampled
токенов и stateful environment facade с allowlist инструментов. Judge/private
methods не должны автоматически стать tools через reflection. Оценивать native
Qwen3.5 и VLM поддержку на конкретной стабильной сборке; текущие TRL main docs
не доказывают совместимость установленного окружения. Ничего не обновлять
автоматически и не устанавливать из GitHub.

Критерий pilot: конечные loss/advantages/gradients, корректные masks/logprobs,
реальная variance terminal reward, ограниченная доля loops/invalid/truncation,
нет reward hacking, улучшение standalone train-validation и заранее закреплённого
dev относительно SFT при том же evaluation budget. Сохранить regression SFT
корпус; смешивание/alternation SFT+RL — отдельный контролируемый вариант.

## 9. Оценка, абляции и final qualification — LP-09

На одном evaluator сравнивать public-control, base, SFT, SFT+aggregation,
SFT+aggregation+GRPO. Протокол, view, budgets, guard, decoding и solver одинаковы.
Нельзя приписывать обучению эффект изменения prompt/parser/view/контекста.
Ablations объявляются заранее: JSON vs native tools, raw vs compact, guard on/off,
text vs image vs combined, last-block vs broader LoRA, diverse vs recovery data.
Полный декартов перебор не нужен: менять один фактор и ограничить число runs.

Главные метрики: terminal physical success, completion каждого типа, recovery,
завершение корректным submit, unsafe/invalid/stale actions, loops, tool/solver
failures, context/generation truncation, abstention/infeasibility correctness.
Дополнительно: tool-call count, generated/input/pixel tokens, elapsed и память.
Syntax success и teacher-forced loss — диагностика, не замена физической цели.

Все ошибки/тайм-ауты сохраняются в знаменателе. Инфраструктурный сбой отделять
по причине; если делать повтор, то единая заранее объявленная политика и
отдельно исходный полный результат. Частичный прерванный run не получает
полного score. Confidence intervals/bootstrapping группировать по независимым
семействам, не считать correlated prefixes независимыми испытаниями.

После выбора recipe заморозить model+dataset+judge+environment и выполнить свежий
final ID/OOD test без tuning. Семь старых dev не квалифицируют промышленную
готовность. Полный test и отдельные физические эталоны нужны для заявленного
профиля, а не для обещания «лучше всех симуляторов».

## 10. Расширение инженерной области — LP-10

После устойчивого PF workflow расширять по одному физически проверенному профилю:
AC PQ/PV →трёхфазные КЗ/защиты →пуск/самозапуск двигателей →переключения/острова
и далее несимметрия/EMT/преобразователи в пределах проверенной физики.
Порядок допускает приоритет motor-start как первого продукта, но его judge,
сценарии, наблюдения и независимые численные проверки должны быть готовы до SFT.

Каждый профиль получает свой canonical dataset, view, экспертную область,
неоднозначные/неразрешимые случаи, regression corpus и release matrix.
Общая multi-task модель обучается с replay прежних навыков и проверкой forgetting.
Browser/API используют один owner-state и judge, но API-tool policy и GUI-click
policy — отдельные интерфейсы и отдельная квалификация.

## Ближайшая очередь реализации

1. LP-01: unified protocol/view/run contract и native-tool ablation harness.
2. LP-02: closed-loop tiny-overfit gate и broader language-LoRA pilot.
3. LP-03/04: split-safe procedural train generator и публичный recovery teacher.
4. LP-05/06: SFT v2, самостоятельные rollouts, aggregation и matched dev.
5. LP-07: визуальный perception curriculum и multimodal tool-use qualification.
6. LP-08/09: только после starter-policy — stateful GRPO и frozen final test.

Это новая очередь, не отметка об исполнении. Действующие sealed reports,
checkpoint metadata и старые score не переписываются.

## Первичные источники и границы применения

- [Qwen3.5-0.8B model card](https://huggingface.co/Qwen/Qwen3.5-0.8B): официальный
  multimodal/tool-use интерфейс; его наличие не доказывает решение наших задач.
- [TRL SFT Trainer](https://huggingface.co/docs/trl/main/en/sft_trainer):
  assistant-only/completion-only loss и VLM collator/template требования.
- [TRL GRPO Trainer](https://huggingface.co/docs/trl/main/en/grpo_trainer):
  tool/environment rollouts, prefix preservation и диагностика zero-std reward.
  Это main-документация, не lock нашего установленного runtime.
- [Ross, Gordon, Bagnell, DAgger (2011)](https://proceedings.mlr.press/v15/ross11a.html):
  основание для iterative aggregation состояний, посещённых учеником.

Численные объёмы, gates и hyperparameter pilots выше — проектные предложения,
а не значения, подтверждённые этими источниками или уже выполненными опытами.

## Сверка с конкурирующими работами и принятые решения — 18 ноября

Проверены первичные тексты статей; код из GitHub не открывался/не устанавливался.
Ни один из перечисленных результатов не воспроизведён нами как внешний score.
Ниже отделены опубликованные методы от наших выводов для Power Grid.

### PowerAgentBench-SS — оценка engineering workflow

[Статья, v1](https://arxiv.org/html/2606.18789v1) отделяет публичные инструменты
и evidence log от hidden physical scoring; сравнивает scripted и LLM-агентов
с общими budgets. Pilot — DC thermal N−2 на IEEE39, а не универсальный AC/DAE
симулятор. Strict-submit и auto-finalization различаются; считаются дубли
проверок и неподкреплённые утверждения. Собственный SFT/GRPO-рецепт эта работа
в рассмотренном pilot не предоставляет.

Применяем: общий judge для public-control и нейросети, strict-submit,
неизменяемые требования, budgets и учёт отсутствия действий. Не переносим
DC thermal score на качество наших motor/DAE/несимметричных профилей и не
объявляем JSON-протокол единственной причиной0/7: JSON используется и здесь.

### GridMind — orchestration готовых моделей

[Статья](https://arxiv.org/html/2509.02494v1) описывает многоагентное выполнение
ACOPF, contingency analysis и related workflows через инженерные solver-tools,
со структурированными данными и управлением контекстом. Описанный workflow
не является свидетельством, что маленькую модель обучили нашим SFT/GRPO.

Применяем: typed contracts и solver-backed результаты. Не копируем multi-agent
оркестрацию вместо обучения базовому инструментальному навыку. Она остаётся
отдельной baseline/ablation после появления самостоятельного single agent.

### RestoreBench — recovery как задача оценки

[Статья](https://arxiv.org/html/2609.00384v1) рассматривает восстановление
сходимости power flow на двух сетях, по46 случаев, с chatbot/single-agent/
multi-agent интерфейсами. Успех относится к их контракту восстановления
сходимости; это не равнозначно прохождению наших электрических ограничений.

Применяем: явные ошибки и диагностические продолжения как отдельное покрытие,
разделение convergence и инженерной допустимости. Их случаи здесь не
импортированы; для внешнего adapter нужны отдельные unit/model/provenance
проверки и сопоставимый action space. Текущие42 recovery-эпизода — по-прежнему
семь наших authored train-задач, а не аналог их92 независимых случаев.

### DAgger для LLM-агентов — teacher labels в посещённых состояниях

[Статья](https://arxiv.org/html/2605.12913v1) сравнивает SFT, OPD, mixture-rollout
DAgger/AggreVaTe и GRPO в software engineering. В mixed rollouts действия
выполняются teacher/student, а supervision берётся у teacher в посещённом
состоянии. В appendix SFT использует684 успешные полные траектории; онлайн
этапы собирают новые batches. Это существенно иной режим, чем38 prefixes
одной маленькой демонстрационной коллекции. Эксперименты —4B/8B и4 A100;
перенос выигрыша на наш0.8B power-grid агент ещё не доказан.

Применяем сначала публичные recovery-продолжения, затем настоящую train-only
aggregation с метками source. Scripted perturbations НЕ называем student
DAgger. Error-actions ученика могут быть контекстом, но target — expert action.
Опубликованную фильтрацию coding patches не превращаем в исключение неудачных
эпизодов из нашей оценки: eval знаменатель всегда полный. Их learning rates,
размеры batches и mixture schedules не считаем готовым рецептом для нас.

### Agent Lightning — разделение runtime и обучения

[Статья](https://arxiv.org/html/2508.03680v1) задаёт унифицированные agent-call/
state/reward transitions и разносит agent execution с RL-training. Доступны
терминальные и промежуточные rewards и механизмы credit assignment; примеры
не являются независимой квалификацией электрической физики.

Применяем provenance переходов, отдельный owner-runtime/learner и action-token
masks. Framework не устанавливаем. Рабочий transcript не должен становиться
положительным training label целиком: для нового recovery loader targets
выбираются по source и повторному физическому replay.

### AgentGym-RL — возможен RL без SFT

[Статья](https://arxiv.org/abs/2509.08755) исследует multi-turn RL без обязательного
SFT warm-start и curriculum по горизонту взаимодействия. Это важная поправка:
«сначала SFT» — наш инженерный выбор при текущем поведении, не универсальная
необходимость GRPO. Greedy0/7 само по себе не доказывает нулевую вариацию
стохастических rollouts. Перед RL потребуется отдельный train sampling pilot,
а не вывод о reward variance только из greedy evaluation.

## Исполненный первый блок после исследования

Добавлены `engineering_protocol.py`, `engineering_protocol_policy.py`,
`engineering_protocol_probe.py` и `engineering_recovery.py` в benchmarks.
RunContract связывает JSON/native protocol, public view, context/action budgets,
maximum actions, repeat guard и thinking mode. Это основа LP-01, не полная
реализация всех EpisodeSpec/RenderSpec и не завершённый выбор протокола.

Публичный RecoveryPolicy продолжает после ошибок и сторонних действий,
повторно инспектирует проект при расхождении с планом. Экспортированы42 train-
траектории:6 начальных сценариев ×7 существующих задач,42 успешных expert
replays,189 expert targets и35 perturbation-actions вне положительных targets.
Это bounded LP-04, без новых топологий, настоящего student mixing и обученной
recovery-способности. Dev/test не допускаются в training collector.

RecoveryDataset повторно исполняет переходы, проверяет source attribution и
формирует next-expert-action labels в обоих протоколах. Native Qwen processor
проверил189 targets в каждом формате: prefix identity, непустые labels, causal
mask и budgets. Tensor-ready dataset остаётся provisional; к старому trainer,
ожидающему ActionDataset v1, его нельзя молча подставить без нового recipe.

Однокейсовый TRAIN-probe базового Qwen при одинаковых compact view,4 действиях,
256 output tokens и guard2 после согласования exposed tool mask: JSON делает
simulate→validate→simulate→simulate; native —inspect_project→inspect_equipment(line1)
→inspect_equipment(line2)→validate. Оба используют4 действия без завершения. У обоих success=false. Это проверка wiring/поведения протокола,
не heldout ablation и не прохождение LP-02 tiny-overfit gate. Веса не менялись.

Current evidence:
- `reports/engineering-recovery-final-train-2026-11-18/summary.json`
- `reports/engineering-recovery-aligned-protocol-2026-11-18/summary.json`
- `reports/engineering-native-json-aligned-probe-2026-11-18/summary.json`

Следом: LP-02 closed-loop tiny-overfit с корректным trainer v2 и языковым LoRA
по проверенным слоям; LP-03 topology/parameter diversity; реальные student
prefixes LP-06. Старый guarded dev0/7 не переписывается новым train-probe.


Native transport сейчас покрывает скалярный ID/enum/string-подпрофиль текущих
задач, а не произвольные вложенные аргументы и delimiter-bearing explanations.
Экспорт артефактов исключён из exposed policy tools в ОБОИХ протоколах.
JSON, выданный вместо native call, не проходит как будто native parser сработал:
ошибка сохраняется в protocol_completion и расходует budget. Предварительные
pre-alignment probes сохранены отдельно, текущие evidence перечислены выше.


## Допуск разнообразных данных и trainer v2 — R06.80

Текущий результат подробно зафиксирован в [CURRENT_STATUS.md](CURRENT_STATUS.md),
раздел R06.80, и `data_quality_release_r0680` в agent-designer.yaml.
196 синтетических задач происходят от 28 сетей / 14 непересекающихся семейств:
112 train, 42 dev, 42 test. Все производные одной схемы остаются в одном split;
канонизация независима от IDs и порядка оборудования, исключены старые семьи.
784 train-пути дают 4292 next-expert-action labels, а не независимые сети.

LP-03 допускает только задачи с подтверждённой физической наблюдаемостью дефекта,
здоровым решением и успешным публичным expert replay. Независимый complex nodal
NumPy reference проверяет constant-Z radial профиль, конечность и полноту
измерений, U/I/loading и delivered source P/Q. Карантин действует на всю группу.
Это внутренний независимый математический контроль, не сторонний симулятор/OEM.
Три вида дефекта: пропущенное соединение, повышенное сопротивление, перегрузка.
Здоровые standalone, невыполнимые и неоднозначные задачи пока не включены.

16k context admission сохранил 16 overflow failures. При 24576 все 4292 targets
прошли настоящий processor без отбрасывания или усечения истории. Strict native
v1 устраняет разрешение prose в prompt при parser, требующем один call.
Один успешный base inspect_project проверяет транспорт, не инженерный workflow.

LP-05 реализован как provisional text trainer v2: kind/family/path-balanced
семплирование, causal next-expert labels, rank16 language LoRA по linear и full
attention плюс MLP, frozen vision/head, verified preflight, AdamW/RNG resume.
Нативный CPU debug — два optimizer steps / 868864 параметра в двух последних
блоках; roundtrip exact и frozen base unchanged. GPU/BF16/resource advantage и
новая standalone политика не квалифицированы. CLI пока использует native-v1;
для текущего strict-v1 нужен явный Python API argument protocol.

LP-07: 784 пары / 128 уникальных публичных изображений, без скрытых ответов и
будущих измерений; все784 прошли rerender, pixel/grid/prefix/label qualification
через Qwen image processor. Числа на картинке округлены; paired JSON сохраняет
точные паспорта. Это первые static snapshots после inspect_project, не полный
визуальный эпизод. VLM optimizer и image-only grounding не запускались.

Следующая приёмка по порядку:
1. LP-02: tiny subset 8–16 train-траекторий, фиксированный strict24k contract;
   подобрать проверенный language-LoRA span и достигнуть полного standalone
   завершения задач из memory subset. Считать все failures, не звать teacher
   из evaluator. При неудаче разбирать rollouts до расширения корпуса.
2. LP-05/09: matched base/SFT dev с полным знаменателем и breakdown по виду,
   семье, invalid calls, stale submits, edits, solver success и стоимости.
   Dev42 — 6 сетей / 3 семьи; зависимые задачи не считать независимыми сетями.
   Final test не использовать для выбора recipe.
3. LP-03/04/06: расширить контракты здоровых/невыполнимых задач, реальное
   train-only посещение student и correction labels публичного expert.
   Scripted recovery не объявлять student DAgger; ошибки — только контекст.
4. LP-07: train-only VLM optimization, counterfactual паспорта/топология,
   text/image/combined ablations, затем отдельная heldout visual оценка.
5. LP-08: проверить stochastic train reward variance, multi-turn logprob masks
   и hacking controls, затем GRPO. LP-10 физические curricula добавлять только
   с собственным независимым reference/judge допуском.

Все артефакты пишутся в свежие директории; истории не перезаписываются.
Ни teacher-forced loss, ни data admission не подтверждают quality gate модели.


## Исполняемый tiny learning gate — R06.81

`engineering_learning_gate` выбирает8–16 clean train-эпизодов: round-robin kind,
неиспользованная base network, наименее представленное family, lexical tie-break.
Пример8: assembly3/diagnosis3/repair2, восемь сетей и семейств,65 targets.
`epoch_schedule` включает все targets один раз за эпоху; не выбирает только
простые inspect/simulate шаги. Сначала target/data/preflight anchors, затем
обучение, затем самостоятельный rollout. Memorization gate не равен dev quality.

`engineering_tiny_run` разделяет train и evaluate: никакого вызова expert из
model evaluator. Strict24k contract общий; ProcessPolicy передаёт только public
messages. Full failure denominator, fresh simulation, terminal, edits, submit и
family/kind breakdown сохраняются. Public control8/8 прошёл physical replay;
это контроль достижимости, не результат модели. Model epoch65 и base8 запущены
на CPU; completed schedule /8-case successes на момент записи не подтверждены.

`engineering_learning_compare` проверяет оба owner manifests, точный список
задач и RunContract, source/candidate identities; replays реальные actions и
публичные responses через fresh engine, заново считает outcomes и counters.
Пропущенные cases, изменённые измерения и подмена success отвергаются.
`engineering_learning_campaign` дожидается sealed входов с конечным timeout,
проверяет полный schedule именно frozen suite, запускает adapter evaluation и
paired replay. Пишет state/summary/failure; сам не запускает новые эпохи,
heldout tuning, GRPO, GitHub или внешние сервисы.

После завершения базового прогона доступен LP-06 collector:
`engineering_student_recovery` принимает только owner-pinned audited TRAIN
native-model evaluation; для каждого реально посещённого prefix повторяет
исходные student actions, затем запускает публичное исправление в оставшемся
24-call/4-simulation budget. Не исправляет terminal submit задним числом.
Invalid/student действия сохраняются как context; positive targets только
expert continuation завершённых branches. Остальные branches сохраняются в
attempts и не исчезают из знаменателя. PublicCorrector после отклонений
повторяет inspect_project; не читает private cause/location.

Collector реализован и проверен тестами, но current-Qwen student corpus ещё
не экспортирован. Экспорт не равен admitted training dataset: требуется
полный native prefix/mask/token preflight и отдельный trainer recipe, прежде
чем делать новый optimizer update. Положительная SFT-примесь не должна
маскировать все исходные student failures в evaluation.

Канонические evidence и пути текущих jobs — CURRENT_STATUS R06.81. Пока не
получены complete receipts, LP-02 remains running; дальнейшая развилка:
8/8 train memorization → matched dev; иначе разбор ошибочных rollouts и
обоснованная смена recipe. Test остаётся замороженным.


## Проверяемое подключение actual-student данных — R06.82

Первая native tiny-эпоха65/65 завершена, frozen base и adapter roundtrip проверены.
Closed-loop tiny8/8 не подтверждён; base и adapter evaluation остаются отдельным
этапом. Частичные model traces не формируют ни итоговую accuracy, ни training
corpus. Native weights/optimizer receipt не заменяет completed policy report.

`engineering_student_dataset.StudentCorrectionDataset` принимает цепочку owner
train→suite→model evaluation→correction corpus. Replays весь evaluation, каждую
ветку, все отказы и attribution, сверяет attempts/positive files/counters.
Для labels доступны только экспертные действия успешных продолжений; student
и protocol-error tokens остаются prompt с labels=-100 в native packer.

`engineering_student_preflight` использует тот же pinned Qwen processor и
strict24k profile на КАЖДОМ target. Пустой набор или одна ошибка закрывает допуск.
Ничего не обрезается/отбрасывается; unique message/action fingerprints отделены
от числа зависимых branches. Admission относится к данным и токенам, не к
самостоятельной recovery-способности модели.

`engineering_learning_mix` требует passed full-source preflights обоих корпусов,
одинаковые train lineage, target SHA и RunContract. Сбалансированный128-step
text recipe записывает source/kind/family/action, индексы и replacement; не
подменяет failed episodes успешными при evaluation. Пропорция50/50 — гипотеза
для последующей проверки; это не измеренный optimum и не выполненное обучение.

`engineering_student_campaign` объединяет конечное ожидание complete source,
экспорт, all-target native qualification и sealed mixture plan. Если preflight
отвергнут, план не создаётся; все failures сохраняются. Нет новых optimizer
steps, dev/test tuning, GRPO или внешних запросов. Следующий actual SFT recipe
выбирается после анализа standalone результатов текущего65-step adapter.


Дополнительная проверка R06.82: posthoc65-step repair probe дал7 wire-valid
calls,3 schema-invalid calls с пустыми args,0 accepted edits и failed submit.
Это1 выбранная train-задача, не paired8-case итог. `engineering_action_quality`
отделяет wire-valid, schema-valid, filled required slots, tool errors и физический
terminal. Нельзя приравнивать0 protocol errors к полноценному tool skill.

`engineering_target_semantics` декодирует ФАКТИЧЕСКИЕ supervised input tokens,
парсит native call и сравнивает полное действие с expected. Все4292 targets
прошли,3784 argument fields сохранены. Сравнение hand-written serializer с самим
собой не заменяет эту проверку. Диагностическая fresh-base8-repeat conditional
call проверяет узкую argument learnability на train target1818; это не полный
policy replay, не heldout оценка и не продолжение65-step checkpoint.


Fresh-base8-repeat microgate завершён: Qwen точно сгенерировала set_line_catalog
с equipment_id=line_299195605 и catalog_id=copper-1 из публичного teacher prefix.
Подтверждена conditional-call memorization одного train-примера. Это отдельный
adapter; он не заменяет65-step policy, не решает full workflow и не является
heldout доказательством. Следующая гипотеза: повторение/баланс parameterized
calls плюс actual-student corrections; её качество нужно измерить отдельно.


## Actual-state admission и mixed trainer v3 — R06.83

Base0.8B strict TRAIN evaluation завершён0/8;117 calls,17 edits,3 submit,
wire/process failures0. На этих actual BASE states проверены83 successful
corrections,28 unsuccessful branches,6 terminal/exhausted attempts.686/686
expert targets прошли native processor;660 unique public context/action pairs,
max prompt14995. Это8 original networks; не считать branches независимыми.

Trainer v3 закрывает исполняемый переход admitted mixture→optimizer: полный
plan/source inventory, две passed data admissions, same target/contract,
семантика каждого actual target до update. Новая фаза от completed v2 adapter
сохраняет LoRA weights и явно начинает свежий AdamW; chunk resume восстанавливает
тот же фазовый AdamW/RNG/cursor. Policy loader принимает только complete
owner checkpoints с текущими fingerprints; это experimental inference.

Нативный two-source2-step pilot сравнивает uninterrupted против head/resume
и проверяет proxy loading. Он квалифицирует вычисления/поставку checkpoint,
а не усвоение engineering workflow. Условная128-step phase стартует только
после этого gate, сохраняется каждые8 steps, затем оценивается на полном tiny
suite и физически переигрывается в сравнении с65-step adapter.

Эта128-step гипотеза — corrective SFT на BASE-visited corrections от starting
adapter65, а не on-policy DAgger для65. Отдельный worker ожидает complete
adapter65 rollout и создаст его собственный qualified корпус. Version mixing
требует нового явного recipe; frozen phase не меняется по мере поступления data.
Нет автоматического добавления эпох, GRPO/dev/test tuning или resource-побед
по timing параллельных CPU jobs. LP-02/06 quality gates требуют standalone
workflow результата; новые optimizer updates сами по себе gate не открывают.


Нативный v3 pilot завершён: LoRA и AdamW uninterrupted/head+resume совпали
побитно, loading/frozen-base/roundtrip прошли. Conditional prediction не совпала
с выбранной expert меткой; full-policy quality не установлено.128-step phase
после этого gate перешла к обучению с8-step checkpoints и последующим полным
standalone comparison. Новые65-state данные собираются отдельно; source
замороженного recipe не меняется в процессе. Допуск backend не равен learned
recovery, VLM/GRPO или промышленной квалификации.

## Аудит и новая программа навыков — R06.84

**Подтверждённый результат:** strict TRAIN base0/8 с17 accepted edits;
adapter65 —1/8 (diagnose1/3, остальные0), accepted edits0. Целевой tiny gate
не пройден. Это8 фиксированных train-сетей/семейств. Причину нельзя вывести
из одного loss или распределения labels: недостаточные updates/capacity,
длинный контекст, простой шаблон и дисбаланс действий — проверяемые гипотезы.
Аргументы в actual labels присутствуют; повторные допустимые планы не следует
помечать ошибками только из-за несовпадения с одним expert action.

Аудит4292 targets:16 исходных сетей,8 families,57.4% no-argument calls,
median prompt4301/max16905;208 повторных пар и64 состояния с альтернативными
валидными действиями. Все исходные16 healthy networks физически допускают
оба варианта «всё copper-1»/«всё copper-2». Нельзя обещать инженерное обучение
из такого корпуса только за счёт размножения trajectories.

### Что уже реализовано

1. **Visual readout/abstention.**1852 записей из64 публичных factual scenes,
   включая паспорта, endpoints, требуемые routes/load nodes, counterfactual
   rating и zoom. Render содержит необходимые ID и единицы. Labels не передаются
   в image-only prompt. Missing-source batches учат null, а не невидимое число.
   Все5556 native image/text/none batches проверены без truncation. Renderer,
   font, pixels, scene, answer association и target processor закреплены.
2. **Public editor card.** Цель, текущая сцена, revision/budgets, свежие PF
   измерения; короткие публичные aliases вместо непрозрачных длинных ID.
   API adapter выполняет только отображение ID и bookkeeping, не выбирает
   инженерное действие. Это editor-card/v1, новый Observation/ActionSpec.
3. **Раздельные targets:**960 full-action,528 conditional binding (операция
   явно задана инструкцией),960 readiness из224 успешных public-teacher runs.
   Есть64 fresh-study-but-not-ready состояния; «расчёт есть» и «решение готово»
   различаются.2448 records на тех же16 сетях;4896 text/visual batches прошли
   native prefix, masks, semantic labels и pixel checks.
4. **Catalog physical decision:**32 train-сценария с одной разрешённой заменой,
   каталог физически допустим по schema, но не по токам. Safe catalog name
   меняется между парой, enum order одинаков; initial project одинаков.
   Учитель32/32, upgrade-all0/32 по operational constraint. Независимый NumPy
   reference проверяет before/after. Это synthetic passports, не реальные OEM.
5. **Action-choice interface:**1152 decision records с публичным меню, максимум
   59 вариантов. Меню построено по schema, без hidden feasibility filtering.
   Ошибочный каталог, неправильный объект и early submit остаются допустимыми
   вариантами выбора; их отвергает действующий physical/operational grader.
   Метка короткая, но schema validity обеспечивает adapter. Resolver реализован;
   новый standalone trained agent и constrained decoder ещё не квалифицированы.
6. **Настоящий VLM update:**2 optimizer steps на одной train image pair;
   vision encoder4 forwards, finite nonzero gradients, frozen base/vision,
   exact checkpoint roundtrip. Это smoke исполнения, не усвоение новой задачи:
   fresh base уже читает эту пару. Нужны отдельные learning/evaluation receipts.

### Что показали визуальные проверки

На одной train factual/counterfactual pair fresh base правильно прочёл216.12
и253.12 на полной схеме и zoom. Без изображения/с пустым изображением выдумал100
вместо null. Нельзя считать правильные ответы с JSON доказательством работы vision.

Отдельный фиксированный DEV regression:6 сетей/3 heldout families, по одной
линии и factual/counterfactual rating,12 чтений на candidate. По strict JSON
пары base4/6, adapter65 0/6. При диагностическом снятии ОДНОЙ Markdown fence
числа у ОБОИХ правильны6/6 пар. Исходные strict scores сохранены, policy pass
не повышен. Ошибка format compliance не равна потере визуального восприятия.
Не доказаны чтение произвольной схемы, топологическое reasoning или план ремонта.
Этот DEV regression можно применять при разработке; final TEST не трогать.

### Следующий конечный эксперимент: preregister до optimizer updates

1. Сначала завершить прежнюю frozen128-step phase и полный8-task comparison.
   Новые data/interface/modules в неё не добавлять. Сохранить и неуспехи.
   Состояния adapter65 уже собраны отдельно:50 prefixes,42 successful branches,
   364 targets прошли admission. Они не заменяют BASE-state источник прежнего recipe.
2. Зафиксировать новый маленький skill recipe, длину, seed, objective weights,
   trainable layers и decoding. Разбить исходные family/base IDs ДО всех
   trajectories, image variants, renamings и recovery branches. Веса sampling
   считать по skill/family/base, не по числу производных картинок. Отчёт отдельно
   показывает eligible examples, duplicates, alternatives и реальное покрытие.
3. Проверить **readout + insufficiency**: image/text/blank/none; фактическое
   изменение ответа при изменении только изображения, правильное null без
   источника, строгий формат отдельно от числового смысла. Чтение паспортов,
   topology/routes/load nodes — отдельные strata, не только лёгкие zoom цифры.
4. Проверить **conditional binding**: заданная операция → все required slots,
   правильные публичные entities/values. Успех этого уровня не равен выбору
   правильной операции. Tiny memorize target предлагается100%; закрывается
   только генерацией, а не teacher-forced argmax/loss.
5. Проверить **readiness + semantic selection**: structural completeness,
   fresh/stale study, physical violations, unsafe early submit; action-choice
   против native tools. В menu-режиме отдельно считать invalid choice, wrong
   object/catalog, premature submit, accepted edits и terminal physics.
   Reward за schema validity, выполненную simulate или submit без успеха не давать.
6. Полный tiny TRAIN workflow gate:8–16 фиксированных задач, каждый kind,
   заданные budgets и full failure denominator. Если gate не пройден, исследовать
   replay и одну контролируемую гипотезу: repetition/skill balance или LoRA
   depth2 против8/шире с сопоставимым budget. Не менять сразу интерфейс, данные,
   capacity и schedule и затем приписывать успех одной причине.
7. Только после gate — matched DEV families. Одинаковые веса сравнить на raw,
   card-text и card-visual interfaces; затем сравнить learning внутри ОДНОГО
   профиля. Сокращение IDs/context/контроллер — interface improvement, не чистый
   SFT gain. Отдельно image-only, text-only, combined и pixel counterfactual.
8. Actual-student TRAIN aggregation от КАЖДОГО checkpoint с явным source ID,
   public expert correction, сохранением failed/exhausted branches и independent
   replay. Ошибочные student actions в prompt не превращать в positive labels.
   Начинать GRPO только после useful reward-variance pilot и надёжного SFT:
   завершённое физически проверенное решение, constraints, tool/call budgets,
   stateful rollout, корректные logprobs и запрет reward hacking. Финальный
   test раскрывается один раз после заморозки выбора recipe.

### Чего в учебном мире ещё недостаточно

Текущий профиль: balanced radial400V, constant-Z loads,4–7 nodes,16 TRAIN base
networks/8 families. Не покрывает industrial motor physics, AC+transformers,
protection topology transitions, meshed/unbalanced/harmonic/EMT workflows.
Для каждой такой очереди отдельно нужны observations, actions, solver/reference,
judge и failure labels; наличие компонента в библиотеке этого не обеспечивает.

Нужны healthy/no-fault, impossible-by-catalog, insufficient-information,
multiple interacting defects, sensor noise/missing values и unit conflicts.
Для healthy/infeasible придётся расширить versioned diagnosis/judge schema;
не подсовывать эти ответы в прежний трёхклассовый judge. Feasibility/infeasibility
labels требуют solver/finite exhaustive certificate в объявленном профиле;
solver failure не доказывает физическую невозможность. Визуальные цели далее:
реальные topology reconstruction, графики переходов, защиты/события, чтение
паспорта с provenance; одну изменённую подпись не выдавать за физический сценарий.

Повторяемость подтверждается manifest/source/target hashes и локальным replay;
sealed inventory не является подписанным происхождением или внешней квалификацией.
Новые корпуса пока **не eligible_for_gold_sft**: processor admission, teacher
успех и2 визуальных updates сами по себе не доказывают learned-policy качество.

Primary implementation guidance: [Qwen3.5-0.8B model card](https://huggingface.co/Qwen/Qwen3.5-0.8B)
и [TRL SFT v0.27](https://huggingface.co/docs/trl/v0.27.0/en/sft_trainer): native
image inputs, assistant/tool structures, сохранение image tokens и маскирование
нецелевых токенов. Это ориентиры; именно наш pinned local processor и optimizer
проверены приведёнными receipts. Обновлять dependencies по документации не нужно.

## Раздельный skill pilot и дедупликация видимых входов — R06.85

Реализованы `engineering_skill_tasks`, `engineering_skill_pilot`,
`engineering_skill_audit` и `engineering_choice_episode`. Это отдельный
experimental action-choice/v2 и isolated-skill interface. Native raw-tool score
R06.83 не меняется. Пока исходная128-step phase идёт, независимый fresh-base
пилот выполняется параллельно: новая ограниченная диагностическая работа
уточняет прежнюю последовательность «сначала дождаться128». Ни новые данные,
ни модули, ни веса этого пилота в замороженную фазу не подмешиваются.

### Контроль данных и sampling

Собран общий TRAIN-only loader четырёх owner-pinned источников: card, choice,
catalog и visual. Проверяются manifests/schema roles, общая train lineage,
parent manifests меню, пересчитанное публичное меню и исходные visual records.
Из6344 производных записей48 точных source duplicates исключены:6296 остаются
(528 binding,960 readiness,1104 choice,3704 perception image/missing-source).

Аудит ВИДИМОГО модели входа, без filenames/family/owner labels, обнаружил3978
уникальных inputs/targets и2318 повторов. Противоречивых deterministic labels
не найдено. Одинаковый вопрос без картинки не становится новым наблюдением
от изменения невидимой схемы. Эти повторы нельзя объявлять независимыми
учебными примерами. Scene metadata восстанавливает base lineage visual rows;
семейство/исходная сеть не теряются при картинках и контрфактических вариантах.

Новый sampler циклически уравнивает4 skills, затем выбирает family, base network,
skill stratum и уникальную visible input/target pair внутри группы. Повторные
source occurrences не увеличивают вероятность в листе. Предложен256-target
schedule по64 каждого skill, но **это не запущенный optimizer recipe**.
Баланс не объявляется оптимальным, эффект нужно сравнить при одинаковом budget.

### Зафиксированный конечный пилот

План запечатан ДО загрузки модели/optimizer updates:
`reports/engineering-skill-pilot-plan-2026-11-18`,
manifest `9022c36373b0d99057a5807cdf41dc065c0f933bf8d8dbfe53b0d434ff3bb099`.

- 8 выбранных по teacher strata TRAIN exemplars:2 conditional binding,
  2 fresh readiness (true/false),2 paired catalog choices с противоположным
  safe catalog name,1 image readout и1 missing-image→null.
- 32 updates:4 предъявления каждого exemplara, fresh Qwen3.5-0.8B, rank16,
  alpha32, последние2 text blocks, frozen base/vision, AdamW1e-4, clip1.
- Menu order перемешивается независимо от labels; train seeds420–423.
  До и после updates генерируются8 ответов при seed420 и2 дополнительных
  catalog-choice ответа с НЕВИДАННЫМ порядком777. Это те же train states,
  а не heldout networks, и не полный engineering workflow.
- Все32 используемых native batches проходят prefix/pixel/actual-label admission
  до загрузки весов. Контекст8192, completion128, thinking false, truncation нет.
  Prompt/image tokens masked; finite nonzero gradients и frozen fingerprint
  проверяются. Checkpoint сохраняется с AdamW и exact roundtrip.
- Gate:10/10 строгих generated answers; malformed/fenced/extra-key output
  остаётся failure. bool1 не равен true. Нет авто-добавления epochs/GRPO,
  адаптивного изменения selection или выбора лучшего checkpoint по dev/test.
  Partial output не является completed model receipt, resume здесь не реализован.

Состояние/результат: `reports/engineering-skill-pilot-2026-11-18/state.json`
и, только после завершения, `summary.json`. Даже passed isolated gate не
квалифицирует полный агент; failed gate требует разбора вида ошибки. Остальные
тысячи записей не прошли новый optimizer-target admission и не выдаются за gold.

### Полные эпизоды нового интерфейса

`engineering_choice_episode.run` передаёт candidate только текущую public card,
публичные choices и messages. В меню сохраняются неправильные каталоги/объекты,
early submit; порядок меняется по seed/turn. Нет teacher fallback, исправления
JSON или скрытой фильтрации по feasibility. Неверный выбор/исключение candidate
расходует реальный call budget, ошибки остаются в denominator. Candidate получает
копию public request и не может изменить owner goal через мутацию аргумента.
Это data-flow separation, не OS sandbox.

Algorithmic PUBLIC control прошёл64/64 эпизода:32 cases ×2 menu seeds на16
исходных сетях. Учебные labels в inference не нужны; контроль использует
публичную validation/freshness/loading/catalog информацию. Это проверка runner,
не результат модели. Unit negatives проверяют early submit, invalid choices,
исключения и изменение goal; успех завершается физическим/operational judge.
Evidence: `reports/engineering-choice-episode-controls-2026-11-18`.

Следующий переход: complete pilot receipt → разбор навыков/ошибок → standalone
learned choice episodes при отдельно зафиксированном inference adapter, trace
feedback и matched interface. Затем family-balanced skill recipe с admission
всех реально используемых targets. Dev/test и расширение physical scope остаются
отдельными gates;8 заучиваемых примеров не являются квалификацией проектировщика.

### Завершённый результат R06.85 и следующая развилка

Пилот32/32 завершён, manifest
`6ebe842c76b00cc74b14e1a1be8d96330528b7944b7294827b3bfcba8421643a`.
Все32 используемых batches прошли native admission; vision encoder5 forwards
(4 training,1 after-eval), frozen base и exact adapter roundtrip прошли.
Строгие generated answers: **1/10 → 5/10**. По skills: binding0/2→2/2,
readiness0/2→2/2, choice0/4→0/4, perception1/2→1/2. Gate НЕ пройден.

Posthoc diagnosis сохраняет strict score и отдельно разбирает одну JSON fence
и известный binding wrapper. Semantic-content matches4/10→5/10: base уже
понимал readiness true/false, но добавлял Markdown; один binding сохранял
правильные значения во вложенном arguments. Значит4 новых strict passes не
равны4 новым инженерным умениям. Missing-source по-прежнему выдумывает100.
Все4 choice failures у каждого candidate означают **validate**, несмотря на
уже validated/fresh overloaded состояние. Ошибка не сводится к перестановке ID.

Read-only operation-name probe без меню/аргументов подтвердил0/2 у base и0/2
у adapter: оба выбирают add_line вместо set_line_catalog на этих двух train
states. Это posthoc prompt ablation, не отдельный industrial test и не доказанная
единственная причина провала. Новых optimizer updates не было. Receipts:
`reports/engineering-skill-error-diagnosis-2026-11-18`,
`reports/engineering-operation-probe-2026-11-18`.

Дальше нужен новый preregistered skill recipe: named operation selection на
сопоставимых public states (missing equipment / catalog overload / unvalidated /
stale / physically ready), затем conditional binding и полный v2 workflow.
Параллельно — отдельный missing-information gate на разных вопросах и картинках.
До новых updates измерять value-bearing token accuracy/probabilities отдельно
от syntax CE; repetition, loss weights или LoRA depth — отдельные гипотезы с
фиксированным budget. Нельзя считать низкий общий CE усвоением выбора операции.
Авто-добавления эпох после failed32 gate нет. Прежний128-step job остаётся отдельным.

### Найден и закрыт обход ограничения замены — equipment profile v2

Полный публичный adversarial control показал: catalog-intervention/v1 разрешал
перестроить ВСЕ линии через remove_line→add_line, не используя set_line_catalog.
Старый счётчик catalog edits оставался0;32/32 таких workflows получали success.
Прежние32 cases/receipts сохранены, но это исторический кандидат, не текущий gold.
Его expert actions остаются физически верными; неполнота касается operational
acceptance и отсутствовавшего отрицательного контроля, а не потери аргументов.

`ReplacementEpisode` объявляет **catalog-replacement/v2** с публичным
maximum_changed_routes=1 и changed_routes. Любое успешное изменение паспорта,
удаление или добавление линии отмечает её требуемый маршрут. Повторное построение
ОДНОГО маршрута учитывается один раз и разрешено. Изменение нескольких маршрутов
нарушает бюджет при любом API-пути; rejected operations его не увеличивают.
После submit сохраняются physics_success и operational violations раздельно.
Это новая версия задачи; прежние scores не перезаписываются.

Проверены все32 cases: public expert32/32, rebuild-all0/32 в v2 при физически
успешном расчёте; тот же rebuild-all32/32 в историческом v1. Unit test подтверждает
легитимный remove/add одного перегруженного маршрута. Case base networks16.
`reports/engineering-replacement-challenge-v2-2026-11-18`, manifest
`b8cf91ab6169f0b2ee4ecc58e6c3a7623e2ab9dc743a64a23c91191353b12e91`.
Исторический source первого choice-episode control сохранён отдельно в
`reports/engineering-choice-episode-controls-source-2026-11-18`.

Для новой версии экспортированы **416 targets:192 expert choice +224 readiness**,
включая32 hard negatives «физика проходит, equipment-change budget нарушен».
Перед экспортом заново проигрываются ВСЕ public cards, tool responses и terminal
обоих контроллеров; ни одно действие неуспешного rebuild-all не становится
positive action label. Manifest
`7371f93540ec4aa5e5c228dd6695f16bbefb39847aea0c6924274198a32a8072`,
`reports/engineering-replacement-teaching-v2-2026-11-18`.

`engineering_replacement_qualify` проверяет каждый target через pinned native
processor в text и visual-hybrid modes; action labels превращаются из choice в
точные alias-native calls, readiness сохраняет operational constraint. Новый
receipt — `reports/engineering-replacement-native-admission-v2-2026-11-18`.
Это отдельная encoding admission, не доказательство обучения, не право смешать
v1/v2 данные без нового recipe. Failed isolated learner gate и gold eligibility
остаются закрытыми. Следующий learned-policy workflow должен объявлять v2 goal.

## Named decisions, VLM abstention и физические контрастные пары — R06.86

### Выполненный40-step эксперимент

Новый finite recipe: fresh Qwen3.5-0.8B, rank16/alpha32/последние2 language
blocks, frozen vision/base, AdamW1e-4, clip1, seed421.10 TRAIN exemplars ×4:
6 preferred operation names (validate,simulate,set_line_catalog,submit,add_line,
add_load), factual/counterfactual image readouts и missing/blank→null.
Первые4 operation states происходят из ОДНОГО v2 workflow, так что profile name
не является уникальной подсказкой для set_line_catalog.2 дополнительных вопроса
без доступной информации НЕ входят в training schedule; это новые question
presentations в TRAIN distribution, не heldout families и не final test.

Recipe/actual native admission12/12 запечатаны до optimizer updates:
`reports/engineering-decision-payload-plan-admitted-2026-11-18`, manifest
`d47079ade9cea39bdf9b10a5b1fbb53d56bf00ce48de58ecb345795cc93feca3`.
Предварительный `engineering-decision-payload-plan-*` без admitted — историческая
проба до проверки unsupported primitive values; её source drift отвергается.
Использовать только admitted owner manifest. Данные и порядок не менялись по
результатам генерации. Никаких dev/test targets или дополнительных epochs.

`engineering_value_loss` проверяет flat JSON primitive values, token offsets и
ТОЧНЫЙ roundtrip actual assistant tokens. Prompt/image tokens остаются -100.
Payload weight4, прочие assistant tokens1, нормализация на сумму весов.
Изменение касается в том числе null и boolean literals. Unknown/nested/empty
values и несовпадение токенизации закрывают admission. Вес4 — гипотеза, не optimum.
В журнале отдельно CE syntax/payload, payload token accuracy и gold probability.
Тест с аналитическим gradient проверяет ровно4× отношение вкладов; weight1
совпадает с native selected causal CE.

**Результат:** strict generated answers **4/12→10/12**, preferred operation
1/6→4/6, perception/insufficiency3/6→6/6. TRAIN exemplars3/10→8/10,
новые question presentations1/2→2/2. Модель сохранила чтение216.12/253.12 и
возвращает null для выбранных missing/blank cases, включая from_node/to_node
нового вопроса. Это узкий measured transfer, не универсальная abstention.
40 updates выполнены, vision encoder18 forwards, frozen base и exact adapter
roundtrip прошли; AdamW сохранён. Общий12/12 gate **НЕ пройден**.

Остаются две preferred-action ошибки: simulate→validate (повтор validation)
и set_line_catalog→submit при перегрузке. Вторая означает unsafe early submit;
physical terminal его отклоняет. Preferred-operation exact match не является
оценкой всех допустимых планов: например remove/add одного маршрута может быть
валидной альтернативой прямой замене. Полный physical workflow — отдельный judge.
Новая цель/профиль и payload objective менялись вместе, поэтому улучшение нельзя
приписать только weight4 или сопоставлять напрямую со score menu pilot5/10.
Receipt `reports/engineering-decision-payload-pilot-2026-11-18`, manifest
`36697f32c00c83d18e3b639f199ea03b596fef3b26cd04b126ab2cd6bdd6c2f9`.

### Связанный двухэтапный API и настоящий conditional argument selection

`engineering_staged_agent` реализует staged-operation-args/v1: candidate сначала
выдаёт имя разрешённой операции, затем JSON аргументов по публичной schema и
текущим goal/equipment/measurements. Во втором запросе нет teacher directive,
выбранного оборудования или каталога. Это сложнее прежнего binding exercise,
где directive уже называл оба значения. Операция для второго запроса берётся
именно из ответа candidate. Пустые допустимые аргументы сериализует adapter,
optional fields опускаются; это не считается learned argument achievement.

Предыдущие ok/error/operation доступны в следующем public observation, а скрытые
эталоны и ответы нет. Два запроса предлагают budget128 tokens каждый; callback
API проверяет byte limit, будущий native policy adapter должен сам enforce
max_new_tokens/context. Это новый интерфейс с feedback и двумя стадиями,
не чистое изменение training и не OS sandbox. Malformed output/исключение
расходует реальный tool budget, нет teacher fallback или исправления JSON.
Early submit не становится успехом. Goal защищён копированием public requests.

PUBLIC algorithmic control прошёл32/32 полных v2 episodes. Это проверка API и
physical terminal, не результат trained policy. **Native learned-policy loader
для этого API ещё не квалифицирован**, Agent Lab не подменяется контрольным
алгоритмом. Receipt `reports/engineering-staged-episode-controls-2026-11-18`.

### Чтение ограничений и новые admission-qualified данные

`engineering_constraint_tasks` разделяет study_ready, measurement_feasible,
counts перегруженных ветвей/обесточенных/недонапряжённых узлов и equipment_budget_ok.
Без fresh converged study measurement fields равны null. Равенство порогу
разрешено, одна ULP за порогом — нарушение. Физическая допустимость не скрывает
превышенный mutation budget. Это assessment наблюдаемых измерений в balanced
профиле, не подтверждение полной структуры, паспортной идентичности или проекта.
Renderer округляет паспортные значения; precision-sensitive equality нельзя
выводить из него автоматически. Diagnosis/industrial physics требуют своего scope.

`engineering_staged_data` заново проигрывает все public cards/responses/terminals
v2 source и сверяет measurement labels с отдельным physical judge. Получено
**848 targets:192 preferred operations,32 conditional arguments,624 measurement
assessments**. Failed rebuild-all даёт только assessment labels. Все848 прошли
native processor, actual labels, prefix и payload masks, max prompt1588.
Это card-text профиль; картинки readout pilot не делают его visual-topology policy.
Receipt `reports/engineering-staged-teaching-admission-2026-11-18`, manifest
`c2b9528e9ce812a8b58645c6612c405faff5c4fe74c0ad80262e7ce6a1a77d33`.

### Контрастные случаи с одинаковой стадией и разной физикой

Провал fresh-overloaded→submit требует исключить stage-counter shortcut.
`engineering_counterfactual_cases` для каждого из32 source cases строит initial
feasible counterpart из его допустимых паспортов, пересчитывает сеть и начинает
исследование заново. После validate/simulate в обоих состояниях одинаковы goal,
revision0, budgets, validated/fresh flags, changed_routes=[], catalog_edits0 и
previous_feedback. Меняются физические паспорта и пересчитанные measurements.
Это физический case, а не изменение одной подписи на рисунке.

Получено **32 пары/64 observations/128 operation+assessment targets** на тех же
16 base networks. Preferred action — set_line_catalog против submit. Numeric
assessment даёт opposite feasibility labels. PUBLIC controls64/64 завершили
physical/operational workflow;128 independent NumPy before/after checks прошли.
Все128 targets допущены native processor, max prompt1547. Истинные labels и
variant/task IDs остаются вне candidate messages. Ни эти128, ни новые848 targets
ещё не использованы для optimizer updates. Native admission не открывает gold
eligibility и не отменяет failed40-step learner gate.
Receipt `reports/engineering-matched-stage-physics-2026-11-18`, manifest
`b0f7edf24c495fb585f9ceb9a6590911c521ba2c55c48a3fc1cdaca6242186bc`.

### Следующая конечная очередь

1. Завершить standalone evaluation прежнего frozen128-step job; не смешивать
   с новым named-operation pilot и не менять source/core во время выполнения.
2. Зафиксировать отдельный recipe из matched-stage pairs, measurement assessment
   и conditional arguments. Sampling по family/base/skill, с учётом повторов,
   preferred alternatives и пороговых случаев; sources848+128 не равны976 сетям.
3. Измерить понимание physical constraints до full planning: fresh feasible,
   fresh overloaded, no/stale study, physically feasible but over budget.
   Неправильный submit и отсутствие завершённого расчёта всегда reward0.
4. Квалифицировать native staged-policy loader с owner weights/plan hashes,
   generation/context budgets, source fingerprints и trace feedback. Затем
   маленький standalone learned v2 workflow с полным failure denominator;
   callback/control tests не заменяют этот этап.
5. При passed tiny gate — matched family-heldout comparison и image/text ablations.
   Расширить VLM с цифр до topology/routes/graphs, затем отдельные motor/AC/
   protection curricula. GRPO требует useful reward variance и стабильного SFT;
   повторять updates/увеличивать LoRA depth автоматически после failure нельзя.

## Полные native episodes, отрицательные результаты и capacity control — R06.87

### Завершён прежний frozen128-step запуск

Raw strict24k TRAIN suite: **adapter65 1/8 → mixed128 0/8**. Полный physical
replay сравнения прошёл; gate8/8 failed. У нового checkpoint26 attempts,
26 schema-valid calls,5 submit и0 required argument slots: он вообще не выбрал
parameterized actions. Source/corpus/profile не менялись, failures в знаменателе.
Эта фаза завершена; повторять её без новой гипотезы нельзя. Full run manifest
`c419d0f604c191376c54e4ec4dda48668abf3e5071b0bb119ebc75440e471d4e`,
comparison manifest `0665d224ab16def75044c7ef9c023f2b0316c980ff3ede134f2c0afb397035e1`.
Это TRAIN calibration, не перенос и не оценка всего физического моделера.

### Native staged inference и joint physics phase

`engineering_native_staged_policy` закрывает callback→реальный Qwen runtime:
owner manifests/checkpoint/plan/core/source проверяются до загрузки модели,
LoRA inventory/dtypes/shapes проверяются при restore. Принимаются только complete
frozen-base/roundtrip receipts. Failed learning gate не становится passed от
того, что checkpoint пригоден для диагностической загрузки. Partial weights
не являются inference policy. Stage/messages публичные, ровно system+user,
никаких engine/reference/teacher objects. Native generation greedy, thinking
false, context8192, max_new_tokens128 на stage; фактические token counts сохранены.
Optional empty args остаются adapter bookkeeping, не learned skill.

`engineering_physics_curriculum` связывает operator/slot/measurement targets и
VLM retention с общими TRAIN parent manifests. Вторая параметризованная запись
меняет безопасное имя каталога на той же сети.13 TRAIN targets:4 operation,
2 conditional arguments,3 measurement,4 image/missing/blank retention; ещё2
вопроса вне optimizer schedule.15/15 used-target native admissions прошли.

План новой фазы от adapter40 запечатан до обучения:
`reports/engineering-physics-joint-plan-2026-11-18`, manifest
`e8fffbd2c5e8e73e8a97a11a452c1d01ed482cf189ffadeb2b6ee60f0f2ca445`.
78 updates=13×6, seed423, rank16/alpha32/last2, fresh AdamW1e-4 при сохранении
parent adapter weights, value weight4/clip1. Это новая фаза, не same-phase resume.
Same-phase resume здесь не реализован. До и после —15 generated skill answers
и3 полных v2 workflows: два catalog-name variants и initially-feasible proposal.
Все3 являются производными ОДНОЙ base network; это намеренный tiny calibration.

**Результат:** **7/15→7/15**, **0/3→0/3 workflows**. Каждый workflow закончился
после24 calls с repeated validate, без edits/filled slots/выполненных simulations.
Оба gate failed. Entity L3 в conditional task усвоен, но каталог выбран неверно
в обоих исходных вопросах. Measurement fields перестали быть пустыми, однако
перегрузка и превышенный equipment budget не распознаны. Утрачена правильная
abstention для ранее не обучавшегося from_node/to_node вопроса; scalar null и
factual/counterfactual images сохранились. Снижение CE не закрывает learning gate.
Result manifest `146bbff245df9260d37fd1814de73f143bfda4592e19f00c0d6ee78672ddb25b`.

`engineering_staged_audit` заново разбирает raw completions, строит те же public
requests, воспроизводит каждое действие, feedback, tool response, counters и
terminal; сверяет actual native prompt lengths и caps. Successful completed
studies проверяются независимым constant-Z nodal reference. В текущих6 model
traces исследований НЕ БЫЛО: numerical_studies_checked=0, а не6 passes физики.
В unit-control replay проверены реальные2 studies. Fabricated actions, goals,
responses/terminal, native counters и поздние события отвергаются; generation
exceptions не получают выдуманных completions. Все6 model failures подтверждены.
Audit manifest `fe22968bb25214eda5ca1a29e0c4ed649aca83fc67a8df24f6b28c40097f2fe8`.

### Диагностика наблюдений и численного runtime

Alias rename — bijection N/L/R/D, сохраняющая topology/goal/measurements и
меняющая ожидаемый handle. Posthoc TRAIN argument probe: exact0/2→1/2; оба
line handles у physics78 следуют переименованию, но catalog grounding слабый.
Это не новая электрическая сеть и не family generalization.

Отдельный public-summary observation вычисляет только measurement/budget status
из ПУБЛИЧНОГО card; desired action и выбранный каталог не подставляются.
Measurement-label prediction tasks этот adapter не принимают, иначе labels
оказались бы во входе. Две preferred-operation проверки: original0/2 у обоих,
public summary0/2 у adapter40 и1/2 у physics78. При summary overloaded case всё
ещё получает submit. Новая подсказка не спасла planning; она меняет ObservationSpec
и не входит в primary workflow score или optimizer updates.
Probe manifest `d49d58092f9817953899a54838b670ccfb89ba0dd79edfac16a47d06c0f75b5f`.

Перед capacity arm выполнен READ-ONLY causal audit. На3 выбранных TRAIN states,
8 target positions full teacher-forced logits сравнены с усечённым идентичным
prefix, cache-prefill и train/eval branches. Maximum logit difference
**1.6689300537109375e-05**, top token одинаков на всех проверках; prefill-cache
и train/eval различия0. Text→image→text сохранил first-step logits и generated IDs
побитно. На decision token модель сама предпочитает validate: gold probabilities
около0.12(set) и0.09(simulate); это не выявленная утечка future labels или сбой
переключения image/text. Не переносить проверку на все префиксы, incremental-cache
траектории или произвольные runtimes. Audit относится к REFERENCE physics78
last2, не к parent40/last8. Gate пройден в этом ограниченном scope.
Receipt `reports/engineering-native-causal-audit-2026-11-18`, manifest
`12b22fbefcb3e168e856cdb5e06ccc9cabda88962b86bae00e655ec64e0c6950`.

### Следующий контролируемый arm: меняется только depth2→depth8

`engineering_capacity_campaign` реализован и проверен. Тот же parent40, targets,
schedule78, LR/value weight/budgets/seed и staged interface. Меняется только
lora.last_blocks:2→8. Не добавляются epochs, новые данные, annotations, hidden
teacher corrections или dev/test. Это контролируемая TRAIN capacity гипотеза,
не обещание, что capacity является единственной причиной failures.

Расширение выполняется на свежей НЕОБЁРНУТОЙ базе: старые A/B точно переносятся,
у дополнительных6 слоёв B=0. Shape/dtype/finite/inventory проверяются; nested
LoRA, partial restore или ненулевой extra B отвергаются. В native planning
**на всех15 входах побитно совпали SHA256 logits, ordinary CE и weighted loss**
между исходным parent40 last2 и expanded initialization. Модели грузятся по одной;
в proof сохраняются hashes, не большие tensors. Это функциональный initial-state
check, не generated score. Initialization повторно проверяется ДО optimizer.

План `reports/engineering-depth8-capacity-plan-2026-11-18`, manifest
`56fce5ce80bca1d7e1875a88e8d6fdb185ea468085b20d1e293e8e357dc8cff2`.
Run требует sealed passing causal receipt с правильным reference physics78
attribution и честным3-state/8-position scope; он не выдаётся за all15 causal
check. Parent40 baseline берётся ВЕРБАТИМ из sealed reference вместе с hashes,
raw traces и functional proof. Он не считается заново сгенерированным depth8.
Все after15+3 должны реально генерироваться, after episodes replayed физическим
judge, frozen-base/roundtrip/source invariants обязательны. Только один finite
arm, никаких автоматических дополнительных epochs или resource-побед.

Arm завершён: `reports/engineering-depth8-capacity-phase-2026-11-18/summary.json`.
Learning gate failed, подробности ниже. Resume в этом конечном diagnostic runner
не реализован. Depth2/depth8 сравниваются на одинаковых denominators с явным
baseline reuse. Даже15/15 и3/3 дали бы только tiny train memorization на одной сети.
Следом — больше base networks/семейств и перенос, а не сразу GRPO/industrial claim.

### Результат контролируемого сравнения и следующая очередь

Depth8 arm78/78 завершён, manifest
`3a73203919261459ac531c21cfd9cdfaf8241d3f74e03d374a999a7ad968dc92`.
Before baseline reused7/15, after8/15; full workflows0/3→0/3. Against depth2
control7/15,0/3: +1 skill answer, +0 workflows. Разница ПРОТИВ DEPTH2 — только
сохранённый from_node/to_node null на ранее не обучавшемся вопросе. Относительно
parent40 прибавился один healthy measurement assessment с заполненными нулями;
нарушенные режимы и planning по-прежнему неверны. Неверные
catalog IDs, workload/over-budget assessment и все три preferred-operation
ошибки сохранились. Все3 after episodes снова24 validate calls без model
simulations/edits. Depth8 gate failed. Frozen base/vision, exact roundtrip,
source invariants и повторная проверка15-input initialization прошли.

`engineering_capacity_audit` независимо проверил manifest/parent/proof attribution,
копии baseline byte hashes, after raw completions/actions/feedback/terminal,
native token caps, generated score, control delta и отсутствие gate promotion.
Replay receipt — `reports/engineering-depth8-capacity-replay-2026-11-18`.
Baseline reuse не превращается в повторный inference result. Никаких resource
побед или общих утверждений о бесполезности depth8: вывод относится только к
этому fixed78/13-target budget. Другие LR/budgets не проверялись.

Следующая работа уточняется результатом: сначала minimal2-state operation
calibration и чтение публичных state fields. Разделить API knowledge, catalog
numeric ranking и full planning; проверить short/full public views с одинаковыми
weights, чтобы не смешать interface assistance и обучение. Заранее зафиксировать
convergence budget/learning rate и train-only stop rule, затем возвращать остальные
roles/retention и проверять3 full workflows. Не увеличивать одновременно depth,
corpus, objective и число физических профилей. GRPO пока не имеет qualified
positive full-workflow/reward-variance pilot; новый RL update не запускался.

## R06.88 — исправление данных: от сериализации к обучаемому инженерному навыку

Статус: аудит выполнен; перечисленные ниже новые профили данных ещё не
реализованы и не обучены. Это порядок реализации, не декларация улучшения модели.
Данные — подтверждённый источник ограничений, но не доказанная единственная
причина failed learning gates. 13 targets ×6 updates не проверяют обучение на
всём корпусе; depth8 на этом бюджете не исключает проблемы оптимизации.

### Что подтверждено и что из этого следует

1. Исторические4292 targets — всего16 base networks/8 families, не4292 схемы.
   2464 вызова без аргументов (57.4%),208 точных input/action duplicates.
   64 одинаковых input имеют несколько допустимых успешных продолжений:
   это НЕ автоматически ошибки разметки. Проверка3784 argument fields не
   обнаружила потери payload в supervised tokens: переписывать токенизацию
   как установленную причину не следует.
2. Staged848 содержит624 measurement,192 operation и лишь32 conditional
   argument targets. Старые528 binding targets прямо называют нужные ID в
   directive: они учат исполнению поручения, не самостоятельному выбору.
   Разделить эти навыки и их метрики. Число строк/рендеров не заменяет разнообразие
   физических состояний и независимых семейств.
3. Новый воспроизводимый аудит32 cases/16 bases/8 families:
   `reports/engineering-data-observability-2026-11-18/summary.json`.
   На каждом healthy варианте построены два контрпримера: r_ohm_km+1e-8
   скрывается округлением, installed p_kw+0.25 не виден, потому что карточка
   показывает required p_kw. Начальные public cards равны, structure_errors
   различаются:64 constructed collisions. Это НЕ измеренная частота ошибок
   текущего корпуса и НЕ неразличимость после свежего расчёта.
4. Public max-rating control решает32/32 через настоящий solver/v2 route budget.
   Переименование copper-1/copper-2 не устраняет shortcut «больший номинал».
   На healthy вариантах simulate→submit успешен32/32 без validate. Поэтому
   teacher-preferred validate нельзя считать единственным правильным следующим
   действием. Схемы/описания tools удаляются из operation prompt, список всегда
   заканчивается validate. Возможный эффект порядка — гипотеза, не causal proof.
5. Последний last2/last8 полный workflow score0/3 на ОДНОЙ base network.
   Чтение паспортной цифры на картинке не доказывает визуальную диагностику,
   планирование или применение физики. Три варианта одной сети не три независимых
   инженерных задачи. Нового обучения в этом аудите не было.

### DQ-01 — достаточное наблюдение и единый контракт (первый блок)

- Выпустить ObservationSpec/v2 отдельно от frozen v1. Развести required и
  installed equipment: реальные P/Q/тип нагрузки, узел и наличие; параметры
  каждой фактической линии и требования маршрута. Не подписывать required
  значения как фактический паспорт. Передавать актуальные revision/freshness,
  budgets и состояние защит/аппаратов, только если они реально моделируются.
- Машиночитаемый текст хранит исходные public численные значения, единицы и
  идентификатор паспорта; изображение имеет объявленную точность отображения.
  Проверка паспортов использует явную версию каталога/ID и объявленное правило
  сравнения: дискретное соответствие или физически обоснованный допуск.
  Нельзя просто ослабить judge, чтобы скрыть ошибки. Граничные условия проверять
  по исходным значениям; если округлённое изображение не определяет ответ,
  правильное действие — запрос точных данных, а не угаданный бинарный класс.
- ToolSpec содержит смысл validate/simulate/inspect/mutate/submit, эффекты на
  revision и свежесть, схемы и ошибки. Возвращать публичный result инспекции
  и конкретную ошибку; текущий staged feedback теряет result. Поле предыдущего
  действия явно назвать last_executed_operation. Ни teacher action, ни скрытый
  диагноз, ни solver future result не попадают в candidate observation.
- Один контракт при генерации, SFT, rollout и оценке. Task-specific responses
  допускаются, но сохраняют task_type/schema_version: readiness boolean не
  смешивать с числами и action JSON. Проверять значения/типы/null, не только JSON.
- Приёмка: регрессии на обе найденные коллизии; все величины, от которых зависит
  запрошенный ответ, наблюдаемы или задача размечена insufficient_information.
  Новые inspect results действительно меняют доступную информацию. v1 sealed
  артефакты остаются историческими. Переход интерфейса оценивается matched A/B.

### DQ-02 — учебная лестница и минимальные контрфактуальные пары

Порядок навыков: (1) читать installed/required/revision/freshness; (2) понимать
эффекты API; (3) сравнивать численные значения с публичным пределом; (4) находить
нарушенную линию/узел; (5) выбирать совместимый паспорт и аргументы; (6) выполнять
план и проверять результат. Каждый уровень имеет отдельный generated score.
Порог берётся из goal, а не hardcoded100. Включить <, =, >; разные единицы,
положительные/нулевые/отрицательные допустимые значения и точность near-threshold.
Недопустимые входы не маскируются под допустимые физические состояния.

Для каждой основной развилки нужны paired examples: один значимый факт меняется
и ответ меняется; отдельно nuisance-only пары, где ответ сохраняется. Например:
validated true/false при одинаковой свежести; fresh/stale; исправный/перегруженный
режим; один и тот же ток при другом допустимом пределе; правильный/неправильный
паспорт; свежая симуляция после изменения и до него. Требуемые физические
изменения заново рассчитываются и проходят независимый reference, labels не
подменяются поверх прежнего расчёта. Синтетические логические microtasks явно
отделены от solver-completed networks и не считаются новой физической валидацией.

Менять aliases, порядок enum/JSON keys, имена каталога, направление записи
маршрута и layout независимо от ответа. Семантика и типы сохраняются.
Не использовать fault-specific framing/crop/filename как подсказку. Шаблоны
вопросов и названия файлов не должны кодировать класс. Тестировать постоянный
ответ, последний tool в списке, копирование feedback и выбор max-rating:
публиковать успех каждой эвристики, не объявлять любой простой алгоритм ошибкой.

### DQ-03 — оборудование, альтернативы и отрицательные примеры

- Каталог не должен иметь универсальный доминирующий ответ. В пределах уже
  реализованной физики строить случаи, где больший I_rated недостаточен из-за
  импеданса/просадки, и разные допустимые варианты. Каждую комбинацию реально
  пересчитать, включая отсутствие решения. Новые тепловые/топологические/
  конструктивные ограничения требуют реализации judge прежде новых labels.
  Если обе позиции одинаково допустимы, принимать обе; не придумывать экономику.
- Добавить already-feasible/no-edit, несколько дефектов, структурно корректный
  перегруз, физически допустимое превышение бюджета изменений, missing/stale
  измерения, отказ расчёта и отсутствие допустимого решения в доступном каталоге.
  «Не могу определить» и «доказано неразрешимо» — разные классы. Не выдавать
  отсутствие решения у одного teacher за доказательство infeasible.
- Хранить preferred_action и acceptable_actions отдельно. Допустимость первого
  шага определяется replay продолжения в бюджете; validate first — стиль teacher,
  а не закон физики. Бесконечно повторять допустимый validate не успешная политика.
  Отчитывать schema validity, progress, terminal constraints и cost отдельно.
- Failed student trajectories полезны как состояния: восстановление после
  ошибки, получение недостающих данных, новый расчёт после мутации. Действие
  неудачной модели НЕ становится positive SFT label. Корректирующее действие
  проверяется replay; отрицательные кандидаты идут в preference/verifier данные
  с конкретным классом нарушения и provenance, не в assistant gold ответы.

### DQ-04 — VLM учится инженерному чтению, а не только OCR

Общий публичный scene graph порождает текст и изображение с revision, единицами,
легендой и происхождением измерений. Кроме паспортов нужны одно-линейная схема,
положение выключателей, графики U/I/скорости во времени, загрузки и напряжения.
Изображать только реально рассчитанные величины; динамические задачи добавлять
через соответствующий solver profile, не рисовать выдуманные осциллограммы.

Задачи: локализовать объект, проследить соединения, найти обесточенный участок,
прочитать пик/момент события, связать график с объектом, сравнить до/после,
выбрать следующее измерение. На paired pixels менять один наблюдаемый факт;
проверять изменение ответа. На изменении темы/масштаба/раскладки ответ сохраняется.
Image-only, text-only, combined, blank/missing/occluded — отдельные профили.
Combined может законно решаться по тексту: это не доказательство использования
vision. Image-required задачи не дублируют ответ в тексте/alt/имени файла.
Кроп выбирается вопросом или публичным объектом, а не скрытым правильным диагнозом.
Частично видимые/нечитаемые данные должны вызывать локальный запрос уточнения;
не учить общему null на все сложные изображения. Processor admission проверяет
фактические pixel inputs, разрешение, truncation и supervised payload.

### DQ-05 — состав, lineage и независимая оценка

Запись связывает family/base/scenario/pair/trajectory/revision, source hashes,
solver/judge/observation/tool/render versions, public-input hash, modality,
skill/answer class, preferred/acceptable targets, evidence и replay status.
Gold/expert, student-negative, constructed counterexample и evaluation — разные
источники. Контрпримеры этого аудита автоматически НЕ являются gold SFT.

Split выполняется по семействам/базовым физическим объектам ДО генерации
траекторий, aliases, images и counterfactuals; все производные остаются вместе.
Отдельно отчитывать same-family new parameters и new-family/OOD topology.
Аудировать точные и нормализованные duplicates между splits, label conflicts,
распределение ответов/слотов/числовых диапазонов и видимые shortcut признаки.
Точный duplicate не обязательно ошибка: его повтор не увеличивает diversity.
Сэмплировать по skill × answer class × family/base с ограничением вклада одной
траектории. Null/zero/validate/submit не должны вытеснять ошибки и аргументы.
Не обещать универсальную оптимальную пропорцию; рецепт и веса фиксировать заранее.

### DQ-06 — условия допуска к следующему обучению

1. Пройти DQ-01 и processor admission: нет необъяснённых неоднозначных labels,
   скрытых необходимых величин, потерянных payload, split leakage и failed gold
   replay. Все выявленные классы недостаточности получают явное решение.
2. На маленьком train-only наборе проверить отдельно state reading, API effects,
   threshold comparison, catalogue selection и operation; generated answers и
   retention, не только падающий teacher-forced loss. Минимальные пары проверяются
   обеими сторонами. Сначала короткий и полный view с одинаковыми weights;
   менять один фактор. Budget/LR/stop rule фиксировать до запуска, не крутить
   бесконечно по dev. Добиться воспроизводимого освоения tiny set прежде масштаба.
3. Выполнить несколько целых train workflows с реальными edits/simulate/submit
   без teacher fallback, затем dev на независимых bases/families. Отчёт содержит
   все seeds/ошибки/бюджеты/отказы, число уникальных сетей и доверительные интервалы
   там, где выборка позволяет. Старые0/3 не заменять результатом другого профиля.
4. Масштабировать SFT только после этих gates; объём определять покрытием и
   learning curves. Формальная квота тысяч примеров не гарантирует обучение.
   GRPO — после положительного policy pilot и проверенной variance reward:
   обязательные физические/структурные/операционные ограничения отдельно,
   короткий JSON или частые simulate сами по себе не дают reward за решение.
5. Итог сравнивать на одинаковом heldout contract с base, простыми алгоритмами,
   SFT text и SFT VLM. Успех на допусках, отсутствие небезопасных действий,
   completion rate и вычислительная стоимость важнее одного exact preferred match.

Следующая реализация: DQ-01 → DQ-02 и DQ-03 → DQ-04/DQ-05 → DQ-06.
Никакие новые веса и claims о росте качества в R06.88 не выпущены.

## R06.89 — реализованный тракт SFT/v2 и следующая очередь качества

Этот раздел продолжает DQ-01–06. Существующие v1 generators, judge и model receipts
не переписаны. Новый профиль пока **text-only, balanced400V, constant-Z, radial,
не более8 узлов**; он не покрывает весь симулятор и не доказывает качество агента.

### Что работает в коде

- `benchmarks/engineering_sft_contract_v2.py`: ExactEpisode поверх того же
  физического owner engine/v2 route budget. Паспорта без округления в тексте;
  required/installed нагрузки раздельно, включая реальные P/Q и лишние нагрузки.
  Tool schemas и effects остаются в operation prompt. Инспекция сохраняет
  фактический public result; previous action явно last_executed_operation.
  Tool feedback расчёта имеет объявленный balanced-study-summary/v2: напряжения,
  токи, загрузки, сходимость и revision; исходный фазный ответ хранится в trace.
- `engineering_sft_release_v2.py`: replay успешных complete trajectories,
  state/structure/measurement/operation/conditional arguments как разные skills.
  validate-first и simulate-first варианты сохраняют проверенные допустимые
  альтернативы. Кандидат их не получает как подсказку. Factual labels вычисляются
  из public card и сверяются с owner judge, fresh studies — с независимыми
  NumPy nodal equations. Failed actions не являются positive SFT.
- Реальные higher-rating distractors: больший токовый номинал вместе с высоким
  импедансом нарушает минимум напряжения. Каждый вариант физически пересчитан;
  имена всех позиций перемешаны через однотипные content-derived part IDs.
  Это устраняет универсальность max-I в данном наборе, но **не доказывает**
  отсутствие других shortcuts: min-R и другие простые controls остаются нужны.
- `engineering_sft_admission_v2.py`: anchored inventory/source проверка,
  полное восстановление supervision из replay, factual conflict/boolean typing,
  family/base/input split leakage, schema и all-alternatives native pack checks.
  Неудачный/частичный допуск не может попасть в AdmittedDataset/optimizer.
- `engineering_sft_train_v2.py`: только passing admission; bounded explicit chunk,
  assistant-only uniform CE, last2 LoRA rank16/alpha32; frozen vision/base,
  AdamW/adapter/RNG в safetensors, same-phase resume, exact adapter roundtrip.
  Параметры рецепта и schedule связаны manifest/source/core hashes. Новый запуск
  начинает с закреплённой базы; не подмешивает молча прежние failed adapters.
  Частичные checkpoints не выпущены как inference policy; завершение schedule
  также не повышает closed-loop gate автоматически.
- `engineering_sft_runtime_v2.py` и `engineering_sft_eval_v2.py`: тот же public
  запрос при rollout; строгий JSON/schema, ошибочные решения расходуют бюджет,
  без teacher fallback и скрытого исправления аргументов. Generated-answer score
  принимает replayed alternatives, а успех workflow требует физического terminal.
  Отдельные denominators: skill/class/family, workflow cases/bases/families,
  фактические edits/simulations. TRAIN diagnostic не называется heldout score.

### Реально выполненный data admission

Release: `reports/engineering-exact-sft-release-2026-11-18`, manifest
`d53f90e7b0d91a5895464e62358a9161449835f02d01117c7a4f15ec8109d90c`.
3136 deduplicated inputs из3520 raw targets:768 state,768 structure,
768 measurement,704 operation,128 arguments.96 inputs имеют несколько
проверенных допустимых ответов.192 успешные полные trajectories,32 физически
проверенных higher-rating voltage failures;16 base networks/8 families.
Все записи имеют историческую TRAIN ancestry: нового независимого dev/test нет.

Native admission: `reports/engineering-exact-sft-qualified-2026-11-18`, manifest
`80e9b43d07b02ad8dda18c554f2caa80e58d5826c92ff68632a72d2c3c5003b2`.
**3232/3232 alternatives** прошли локальный Qwen3.5-0.8B processor,0 failures,
maximum prompt4548 tokens. Prefix/assistant mask/decoded values/value-bearing
payload проверены, не только сериализованный JSON. Model weights для этого
допуска не грузились. Eligible только для train-only calibration, production
training gate закрыт из-за отсутствия независимого evaluation corpus.

Первая версия с полными дублированными фазными tool results честно провалила
native admission по8192 token budget. Failed receipt сохранён отдельно в
`reports/engineering-exact-sft-admission-2026-11-18`; он не используется тренером.
Окончательный компактный профиль — отдельный release; tokenizer truncation
не используется для скрытого удаления данных. Разные версии входов не смешаны.

### Конечный исследовательский рецепт, а не обещание сходимости

Admission содержит proposed384-step ceiling:128 foundation (state/structure/
measurement),128 grounding (arguments/state/measurement),128 policy (operation/
arguments/state/structure/measurement). Sampling round-robin skill → answer
class → family, seeded cycling внутри bucket. Проверенные альтернативы циклически
выбираются при повторных экспозициях, а не размечаются как ошибки друг друга.
Это не оптимальные пропорции, не освоенный curriculum и не доказательство,
что384 updates достаточно. Ни один dev/test target не входит в schedule.

Запуск требует явного --max-new-steps. Технический smoke из одного/двух шагов
проверяет тракт и сохранение, но не годится для заявления «модель научилась».
Same-phase resume обязан сохранять admission, рецепт, cursor, AdamW, RNG,
adapter inventory и code fingerprints. Новый corpus/recipe — новая фаза,
а не подмена данных в продолжающемся оптимизаторе.

### Обязательная следующая очередь до большого SFT

1. **Чистый evaluation registry.** Зафиксировать ранее использованные family
   signatures и принадлежность split. Новые семьи выбирать с проверкой пересечений
   до генерации паспортов/картинок/траекторий. Все производные сохраняют один split.
   Не превращать текущие16 TRAIN bases в независимый test случайным разделением
   строк. Отдельно new parameters same family и новые topology families.
2. **Primitive curriculum.** Добавить самостоятельные API-effect, scalar
   threshold/comparison, ranking и necessary-information задачи. Сейчас отдельные
   state/structure/measurement навыки есть, но API quiz и near-threshold curriculum
   ещё не выпущены. Логические контрфактуалы явно отличаются от solver studies.
   Ввести paired >=/=/<= с единицами и точностью; предел берётся из goal.
3. **Минимальные пары и nuisance invariance.** Одинаковые budgets/revision/goal,
   меняется одна значимая величина; отдельно перестановки aliases/catalog order/
   JSON order/layout, не меняющие ответа. Не перемешивать единицы без пересчёта.
   Испытать constant-answer, last-tool, copied-feedback, max-I/min-R controls.
   Данные допускаются по покрытию заявленных навыков, не по числу строк.
4. **Отказы и восстановление.** Реальные visited states прошлой модели,
   недостаточные сведения, stale studies, отклонённые аргументы, no feasible
   catalogue, multiple faults, физически допустимое нарушение бюджета. Отказ
   teacher найти план не доказывает infeasible; нужна исчерпывающая проверка
   заявленного конечного action space либо более узкая метка unknown.
5. **VLM release с независимым смыслом.** Единый public scene graph с токами,
   напряжениями/загрузками, topology/switch states и computed time series в
   соответствующем solver profile. Image-only/text-only/combined отдельны;
   masking/blank/occlusion, pixel counterfactuals и localization. Ответ не должен
   дублироваться в filename/alt/тексте image-required задач. Динамические графики
   не синтезируются без расчёта. Текущий exact/v2 не претендует на это покрытие.
6. **Learning gates.** Сначала train-only tiny-set generated memorization по
   каждому primitive skill и обеим сторонам minimal pairs с retention остальных
   ответов. Зафиксировать budget/LR/stop rule и несколько seeds до запуска.
   Затем реальные edit→simulate→verify→submit workflows без guards/fallback,
   новый dev с неизменённым contract, только затем большой SFT и closed test.
   Изменение интерфейса, глубины LoRA и objective проверять раздельно.
7. **GRPO позже.** Нужна ненулевая доля полных успешных policy rollouts и
   различимый физический reward с anti-shortcut controls. Schema-valid JSON,
   частый simulate или малый loss не достаточны. Не заменять физический judge
   свободным языковым оценщиком. Reward/expert data/evaluation версии закреплены.

### Воспроизводимые команды

Команды из корня репозитория; для повторения нужны НОВЫЕ output directories.
Manifest после генерации читается через study_artifacts.verify и явно передаётся
следующему шагу. Ни GitHub, ни CI, ни внешняя отправка данных не используются.

```bash
python -m benchmarks.engineering_sft_release_v2 \
  --source reports/engineering-catalog-selection-final-2026-11-18 \
  --manifest-sha256 fc5e5950c5939db1bee86de58c320dcd5ac58936b3b995aa287535f715f7308e \
  --out reports/my-exact-sft-release

.venv-qwen35-08b/bin/python -m benchmarks.engineering_sft_admission_v2 \
  --source reports/engineering-exact-sft-release-2026-11-18 \
  --manifest-sha256 d53f90e7b0d91a5895464e62358a9161449835f02d01117c7a4f15ec8109d90c \
  --target requirements/qwen35-08b-local-target.json \
  --target-sha256 e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262 \
  --out reports/my-exact-sft-admission

.venv-qwen35-08b/bin/python -m benchmarks.engineering_sft_train_v2 \
  --target requirements/qwen35-08b-local-target.json \
  --target-sha256 e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262 \
  --admission reports/engineering-exact-sft-qualified-2026-11-18 \
  --manifest-sha256 80e9b43d07b02ad8dda18c554f2caa80e58d5826c92ff68632a72d2c3c5003b2 \
  --out reports/my-exact-sft-calibration --max-new-steps 2
```

Третий пример — bounded research calibration, не запуск production training.
Для resume добавить --resume и --resume-manifest-sha256, оставив тот же admission.
Для данного профиля нет автоматически подключённого нового endpoint в Agent Lab.

### Native smoke и точное продолжение: выполнено

`engineering-exact-sft-native-two-2026-11-18` выполнил2/384 позиции; отдельно
`engineering-exact-sft-native-one-2026-11-18` выполнил первую позицию и
`engineering-exact-sft-native-resumed-2026-11-18` восстановил AdamW/adapter/RNG
и выполнил вторую. В сумме4 optimizer executions, но только2 уникальные позиции.
Никаких generated skill/workflow scores этот smoke не измеряет.

`engineering_sft_resume_audit_v2.py` проверил manifests/ancestry/recipe/core,
сравнил30 adapter tensors,91 tensor optimizer artifact (включая RNG), отдельный
RNG и весь loss/gradient/target history: **битовое совпадение** continuous2 и
chunk1+resume1. Frozen base/vision неизменны, adapter save/restore logits/loss exact.
Proof: `reports/engineering-exact-sft-native-resume-audit-2026-11-18`.
Это two-position CPU qualification, не проверка всего384-step curriculum.
47 целевых tests прошли и в base Python, и в native Qwen environment;
web build и60 JS checks прошли. Model/core fingerprint прежний.

Для следующего большого выпуска также нужны evidence-grounded объяснения:
структурированные claim/observed value/unit/limit/revision и проверка результата
действия. Такие targets должны выводиться из публичного расчёта; выдуманные
reasoning traces не становятся gold. Сложную арифметику/исследования агент
поручает проверяемым инструментам. Отдельные метрики: правильность вывода,
достаточность evidence и успешный полный workflow. Текущий flat-payload профиль
ещё не поддерживает nested evidence schema — для неё нужен новый native admission.

## R06.90 — frozen new families, primitive data и measured learning pilot

Дата:19 ноября2026. Реализованы registry/generator/admission/finite pilot/audit.
Это продолжение DQ-02/DQ-05/DQ-06, а не завершение full-policy/VLM qualification.
Все прежние sealed receipts и исходники SFT/v2 сохранены.

### Новый реестр и lineage

`engineering_family_registry.py` проверяет manifests исторических procedural
train/evaluation releases, соответствие публичной схемы canonical rooted-tree
signature и отсутствие конфликтов split.14 известных procedural families и3
legacy families исключены. Затем назначаются4 новых train/2 dev/2 test families
и реестр sealed ДО вызова генерации новых параметров. Aliases/порядок строк не
меняют семейство. Не используется random split по строкам или переприсвоение
старых TRAIN examples в test. Новизна относительна только перечисленным anchored
источникам; неизвестные pretraining данные Qwen этим не проверяются.

Registry `reports/engineering-new-family-registry-2026-11-19`, manifest
`0a1165c836f469416b00e364c71dbd810cc18ad3fa02ce35b71232df7a83edd0`.
Параметры берутся существующим deterministic procedural factory после freezing
registry. Его внутренние provisional train tags заменяются назначениями реестра
до публикации каких-либо производных примеров. Старый factory не изменён.

### Состав primitive release

`engineering_primitive_curriculum.py` строит780 examples на15 bases/8 families:
120 чтение состояния,180 API-effect assessments,480 decimal/unit comparisons.
Train416 на4 families/8 bases; dev208 на2/4; test156 на2/3.
Один из16 запрошенных base variants помещён в карантин: недостаточная разница
токов для unique one-line overload. Resampling/подмена отсутствуют, все8 families
покрыты. Это явно ограниченная conditional task distribution, не полный набор
возможных аварий или доказательство отсутствия неоднозначных режимов вообще.

- **State reading:** настоящие revision/validated/freshness до validate, после
  него, после simulate и после мутации. Compact публичный microtask view включает
  источник/число узлов, без task IDs или скрытого желаемого ответа. Все состояния
  одной базы присутствуют: источник не кодирует класс ответа.
- **API effects:** реально выполненные validate/simulate/inspect/mutation/rejected
  mutation. Заданный action, schema, installed handles и before state публичны;
  labels вычислены из engine transition. Это прогноз API при явном предположении
  успешного выполнения schema-valid study, НЕ прогноз физической сходимости.
  Отказ invalid catalogue сохраняет freshness, mutation её сбрасывает.
- **Comparison:** Decimal, строгое above/below, равенство допустимо, missing→
  unknown/null; A/kA, V/kV, pu/%voltage, отдельный %loading dimension. Это analytic
  microtasks, НЕ заменённые solver результаты. Near-threshold примеры имеют точную
  decimal representation; unsupported units/nonfinite values отвергаются.
- **Pairs:**390 key-order nuisance pairs с неизменными targets и60 semantic
  boundary groups below/equal/above/missing. Метаданные member/pair_group/evidence
  принадлежат owner; candidate получает только messages. Порядок ключей не должен
  влиять на смысл. Это не доказательство всех permutation/layout invariances.

Release `reports/engineering-primitive-release-2026-11-19`, manifest
`8fd1e0e82aae17bdfb1d060fb7ca4d49e498c6c6ccef2a2c626c17fc249f3445`.
Сохраняются train.jsonl, owner-evaluation.jsonl и отдельные dev/test-prompts.jsonl.
Prompt projection содержит только id/split/skill/messages, без answer/member/
pair-group/evidence. Разделение файлов — data-flow boundary, не ОС sandbox;
внешнему оцениваемому агенту нельзя давать доступ к evaluator filesystem.

### Admission и учебный доступ

`engineering_primitive_admission.py` заново воспроизводит все API transitions и
row IDs, проверяет соответствие registry, семейств/баз/inputs, factual labels,
комплектность пар и всех объявленных семейств. Нельзя сохранить оба неверных
nuisance labels и выдать consistency за правильность: engine replay обнаруживает
подмену. Невозможность принять конкретную базу не превращается в gold infeasible.

Native Qwen3.5-0.8B:416/416 TRAIN labels/payloads прошли assistant-only pack;
364/364 dev/test prompts проверены БЕЗ добавления их ответов в SFT. Max prompt352.
Это отдельный microtask profile; снижение длины относительно exact full-card/v2
не является сравнением ресурсов на одинаковой инженерной задаче.
`PrimitiveDataset` хранит и отдаёт optimizer только TRAIN rows; отклоняет
heldout injection, negative/bool indices, failed/partial admission и code drift.

Admission `reports/engineering-primitive-native-admission-2026-11-19`, manifest
`ef9358fe2b0895adbe316dbd15fcf4d3cbd89fecd5ce0a2072662d6fe5a4c1f5`.

### Реальный контролируемый learning pilot: 1/20 → 12/20

`engineering_primitive_pilot.py`:10 заранее выбранных TRAIN originals одной базы,
каждый показан3 раза, итого30 updates. Fresh base, last2 LoRA rank16/alpha32,
LR1e-4, uniform assistant CE, fixed seed, без warm-start/teacher fallback/
automatic extension.10 reordered-key probes не входят в optimization schedule.
План записывается до inference. Это tiny TRAIN memorization/retention test,
не обучение на всех416 rows и не OOD topology evaluation.

До: originals1/10 и reorder0/10. После:6/10 и6/10, суммарно12/20.
По skills после: state4/4, API4/8, threshold4/8. До было8 protocol errors,
3 field-schema errors,8 wrong-value/type errors и1 правильный ответ. После
формат/schema корректны20/20, но8 wrong-value/type errors остались.

Четыре неосвоенных original cases (и те же четыре reorder probes):
1. simulate: модель оставляет fresh=false/simulations_spent=0 вместо true/1;
2. rejected mutation: wrongly сбрасывает свежесть прежнего расчёта;
3. above-limit: выдаёт below и отсутствие нарушения;
4. missing measurement: computable=false/unknown правильны, но violation=false
   вместо null. Это ошибка типа/смысла, не физическое доказательство безопасности.

Frozen base/vision unchanged; adapter roundtrip logits/loss exact.
**Primitive learning gate FAILED (требовались20/20).** Full workflows и новые
heldout model scores не выполнялись; test не использовался для выбора рецепта.
Нельзя сравнивать12/20 с прежними7/15 или8/15 как одну метрику: tasks/interfaces
различаются. Этот checkpoint не выпущен в Agent Lab и не inference release.

Pilot `reports/engineering-primitive-native-pilot-2026-11-19`, manifest
`07a8c7cdeebd4c1dc0facdb5b4a7317646f922f1983a640686ae5b61af2fcedc`.
`engineering_primitive_pilot_audit.py` пересчитал scores/группы/типовые ошибки,
проверил TRAIN attribution, fixed schedule30 и отсутствие gate promotion.
Audit `reports/engineering-primitive-pilot-audit-2026-11-19` passed; это audit
записанных outputs/lineage, не независимая аттестация выполнения model kernels.

### Следующий корректирующий эксперимент

1. Разложить API-effect ответ на one-field microtasks: fresh_after отдельно,
   simulations_spent отдельно, revision_delta отдельно. Все labels брать из того
   же executed transition; затем вернуть совместный ответ с проверкой retention.
   Представления сравнивать при одинаковых weights, не одновременно с depth/LR.
2. Null curriculum: неизвестно/ложь/ноль — разные ответы. Сначала отдельное поле
   violation для known-safe/known-unsafe/missing; затем исходный3-field формат.
   Missing scalar не выдавать за общее умение воздерживаться в workflow/VLM.
3. Численные сравнения: сначала целые/простые десятичные, затем units и
   near-threshold precision. Сохранять строгое правило равенства и эталон Decimal.
   Не обучать округлением, меняющим правильный класс. Для сложных инженерных
   расчётов учить вызову проверяемого инструмента, а не доверять mental arithmetic.
4. Зафиксировать новый finite budget/seed/stop rule до запуска. Старые30 updates
   не продолжать с подменой dataset. Не менять одновременно длину prompt, loss,
   адаптируемые слои и schedule. Новые targeted rows пройти всем тем же admission.
5. Только после TRAIN gate возвращать multi-base curriculum и полные
   edit→simulate→check→submit episodes. Dev использовать по объявленному плану;
   test открыть для фиксированного кандидата после выбора рецепта, не для retries.
6. Дальше — recovery/infeasible tasks и VLM engineering scenes; registry
   assignments применяются ко всем images/trajectories/counterfactuals. Преимущество
   VLM измерять image-required controls, не ростом text-only accuracy.

Ни GitHub, ни CI, ни внешняя отправка данных не использовались.

Проверки R06.90:72 целевых Python tests в base и native окружениях,60 JS checks,
web build и whitespace checks — passed. Core fingerprint и исходники прежнего
qualified SFT/v2 неизменны. Итоговый audit manifest:
`7f3bbfcdee864089aa806c974b26db1dfe9a0048427be4e8f84985b2813a50b7`.

Воспроизведение finite pilot на уже проверенных источниках (нужен новый --out):

```bash
.venv-qwen35-08b/bin/python -m benchmarks.engineering_primitive_pilot \
  --admission reports/engineering-primitive-native-admission-2026-11-19 \
  --manifest-sha256 ef9358fe2b0895adbe316dbd15fcf4d3cbd89fecd5ce0a2072662d6fe5a4c1f5 \
  --target requirements/qwen35-08b-local-target.json \
  --target-sha256 e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262 \
  --out reports/my-primitive-pilot
```

Это повторение фиксированного исследования, не следующий корректирующий рецепт.
Для новой генерации: family_registry.build(history,out) → verify(registry) →
primitive_curriculum.build(registry,anchor,out) → verify(release) →
primitive_admission.qualify(release,anchor,target,target_sha,out). Каждый шаг
требует свежую директорию; изменённые sources/registry требуют нового admission.

## R06.91 — focused fields, measured regression и coverage gate

Новые модули изолированы от source-pinned R06.89/R06.90. GitHub/CI и внешняя
отправка не используются. Ни один из новых adapters не подключён как Agent Lab
inference endpoint. Новая работа относится только к TRAIN primitives.

### Выпущенные данные и фактически выполненная фаза

`engineering_focused_curriculum` берёт только TRAIN rows из passing native
PrimitiveDataset, выделяет отдельные API/comparison fields без изменения меток,
добавляет простые decimal comparisons в одинаковых единицах. Вопрос указывает
requested_field, но не содержит teacher answer. Missing/false/true/null сохранены
с точным типом. Учебная арифметика не называется новым physical study.
Все производные сохраняют family/base/source_row lineage и nuisance pairs.

1504 records:480 api_field,768 comparison_field,256 coarse_threshold на8 TRAIN
bases. Все1504 native packs прошли prefix/assistant-only/payload roundtrip,
max prompt343 tokens. Release:
`reports/engineering-focused-native-release-2026-11-19`, manifest
`4ba73bdce78beabd776cced5c0d93601cd2bae4b395807ca1fa5b173072a163b`.
Dataset loader заново выводит targets из admitted parent, проверяет source pins
и полный native inventory; resealed false-vs-null подмена обнаруживается.

`engineering_focused_phase` провёл заранее ограниченные60 updates:
10 новых focused +10 прежних retention originals, каждый показан3 раза.
Fresh AdamW поверх complete primitive30 adapter, last2 LoRA rank16/alpha32,
LR1e-4, прежний uniform assistant CE. Это НОВАЯ фаза, не optimizer resume.
Parent lineage/source/target/complete recipe проверены до weights; до updates
20 прежних raw completions реально воспроизведены побитно. Дополнительные20
focused probes имеют собственные знаменатели;10 из них — untrained key order.

Measured outcome:
- Focused1/20→14/20:13 новых правильных probes,0 регрессий в этой группе.
- Retention12/20→8/20:4 индивидуальных регрессии,0 новых правильных.
- Потеряны full API mutation answer и equality comparison вместе с их key-order
  представлениями. Прочие старые simulate/rejection/above/missing ошибки остались.
- После40/40 outputs имеют правильный JSON/набор полей, однако wrong value/type
  errors12/20 retention и6/20 focused. Нет оснований объявлять их все format bugs.
- Frozen base/vision unchanged, adapter logits/loss roundtrip exact.
- **Focused+retention gate FAILED.** Нет full workflows, новых dev/test model
  scores, inference release или доказанной причинности конкретного изменения.
  Одновременно менялись учебный состав/презентация/число обновлений относительно
  исходного primitive pilot: это correction bundle, не one-factor ablation.

Phase `reports/engineering-focused-native-phase-2026-11-19`, manifest
`28c555de520ebfa514a579ec328df51711a69aa16d5e01f273e36b3fce9b862f`.
`engineering_focused_audit` заново проверил selection/schedule60, исходные target
values/types, raw outputs, parent exact replay, агрегаты и КАЖДУЮ регрессию.
Audit `reports/engineering-focused-phase-audit-2026-11-19`, manifest
`19441ce0098afab7adeef11d86330a489208d4b67d013fc25d3629f5b59587f1`.
Audit passed означает корректность attribution/пересчёта, не passing learning
или независимую аттестацию исполнения model kernels.

### Найденный дефект выбора задач, который не ловит all-corpus admission

В полном1504-row корпусе нужные противоположные классы есть. Однако выбранные
10 focus originals из error-mining не покрывают4 learning heads:

1. fresh_after: есть true, отсутствует false в one-field format;
2. simulations_spent: есть1, отсутствует0 в one-field format;
3. relation: есть above, отсутствуют below/equal/unknown в one-field format;
4. violation: есть true/null, отсутствует false в one-field format.

Присутствие false в ДРУГОМ multi-field retention format не означает наличия
контраста в изучаемом one-field формате. Ошибки equality→null и частичный
constant-answer behavior согласуются с этой проблемой, но причинность всех
регрессий ей не доказана. Малый бюджет/оптимизация/адаптация остаются гипотезами.

`engineering_focused_balance.coverage` строит typed answer classes по task
head/field (и direction для joint coarse comparisons). Проверяется именно
selected optimization set, не только размер доступного корпуса. Булево false,
нулевое число и null не сливаются. `require_coverage` отклоняет исходную выборку.
Граница критерия — наблюдаемые классы текущего корпуса: он не доказывает полноту
всех API, physics modes или систематическую генерализацию. В частности, такие
API exercises не покрывают успешное submit/true finished и не квалифицируют это.

### Исправленный план подготовлен и проверен, но НЕ обучен

Новая deterministic selection сохраняет исходные10 focus originals и добавляет
6 contrasts: fresh_after=false, simulations_spent=0, relation=below/equal/unknown,
violation=false. Получается16 originals/16 key-order probes. Нужные labels уже
подтверждены parent replay/Decimal; новые PF studies не придумываются.

Plan `reports/engineering-focused-balanced-plan-2026-11-19`, manifest
`54532da3d8626c358caaba1b1b97ffe8345c5232c3b34fec55a90a5f0ccaff7d`.
Все32 proposed native probes повторно упакованы. Coverage4 deficient heads→0.
Бюджет остаётся60 (30 focus/30 retention), все16 contrasts экспонируются хотя бы
один раз, остальные экспозиции фиксированы seed. Старые10 retention originals
сохраняют по3 экспозиции. Sample frequencies и порядок отличаются от failed
phase: это не причинный контроль только одной переменной.

Parent для нового плана — исходный primitive30 adapter с12/20, не регрессировавший
focused60 с8/20 retention. Сравнение обязано сохранять общие20 focused/20 retention
probes;12 добавленных contrast probes отчитываются отдельно. Нельзя заменять
старый знаменатель40 новым52 и объявлять повышение accuracy без paired сравнения.

`load_plan` проверяет focused release и target ancestry, parent receipt, source
pins, точный selected set, coverage, deterministic schedule и complete32 native
inventory. **Новый trainer-потребитель этого validated plan ещё не реализован;
веса по исправленному плану не обучались.** Старый frozen focused runner остаётся
для воспроизведения failed phase и не должен выдаваться за новый consumer.

Следующий блок: реализовать plan-consuming bounded runner с теми же weight/target/
optimizer checks и обязательным coverage gate ДО model load; выполнить заранее
заявленный balanced60 без продолжения regressed weights, сравнить common probes,
каждую retention regression, все typed fields и дополнительные contrasts.
Если tiny TRAIN gate не пройден — вывод остаётся failed, без auto extra epochs,
heldout tuning или выпуска в production. После освоения primitives вернуться к
multi-base/full workflows, затем unseen-family dev/test и VLM engineering data.

R06.91 validation:87 целевых Python tests в каждом из base/native окружений,
60 JS checks, web build, YAML, whitespace и status mirror passed. Physics core
fingerprint и source pins прежних admitted releases неизменны.

## R06.92 — выяснение механизмов регрессии и constrained training

Работа локальная, TRAIN-only, без GitHub/CI. Старые source-pinned корпуса и
checkpoint receipts не переписаны. Ни один failed/diagnostic checkpoint не
подключается к Agent Lab автоматически.

### Что проверено нативно, а что остаётся гипотезой

`engineering_regression_diagnosis.py` загрузил parent30 и failed focused60,
воспроизвёл exact completions mutation/equality и измерил teacher-forced logits
на одинаковых public inputs/labels. Frozen base не менялась, optimizer updates0.

- Mutation fresh_after=false: token probability0.508514→0.416264. До обучения
  correct решение было хрупким, близким к argmax boundary. Finished=false на
  правильном teacher prefix остаётся top1 с probability0.887928 после обучения,
  хотя при свободной генерации output finished=true.
- Equality violation=false:0.713540→0.417492. Это value regression, не JSON parse
  failure. Потерянные значения представлены в supervised payload: masking их
  не удалил. Native generations совпали с сохранёнными raw completions.
- Uniform CE для mutation0.080388→0.044781, threshold0.073701→0.064190:
  средний loss улучшился одновременно с ухудшением важных значений.
- Parent gradient dot products двух old/new pairs отрицательны, cosine−0.257690
  и−0.351477. Это измеренное ЛОКАЛЬНОЕ interference под историческим uniform CE;
  оно не доказывает единственную причину всей60-step AdamW траектории.
- Значение в API full answer получает coefficient1/28, в field answer1/8
  (3.5×); в threshold full answer1/17 против1/8 (2.125×). Это коэффициенты CE,
  а не утверждение о равном масштабе реальных gradients.50/50 по examples не
  равняется балансу смысловых полей, особенно при missing negative classes.

Diagnosis artifact `reports/engineering-regression-native-diagnosis-2026-11-19`,
manifest `284c81eaf0013057ed13cfc4c287338c3522084c986b8040e1daf210dc986aec`.

`engineering_regression_prefix.py` выполнил один контролируемый prefix experiment
на FAILED checkpoint: public question неизменен, только предыдущий assistant
fresh_after переключён false→true. Для следующего finished probability false
упала0.887928→0.345659, greedy false→true. Это проверка autoregressive cascading
error в ОДНОМ случае; counterfactual assistant prefixes не являются gold SFT или
physical studies. Teacher forcing даёт правильный предыдущий token, реальный
decoder получает собственную ошибку, поэтому одной teacher loss недостаточно.
Artifact `reports/engineering-regression-prefix-proof-2026-11-19`, manifest
`a47779c393dfad9b3d1eac16b4affeb6e50adb609e523536481a4ba2c6490c73`.

Проверенные причины/факторы: выбранная смесь не содержала части contrasts,
objective давал разный per-value вес, gradients конфликтовали локально,
одна ошибка prefix вызывала следующую. Не найдено основания объяснять ЭТИ
регрессии потерей labels, сменой solver, порчей checkpoint или parser-only bug.
Не исключаются малый бюджет, narrow last2 adaptation, variance online updates,
малое число сетей и влияние формата задач. Token probabilities не являются
калиброванной вероятностью инженерной безопасности.

### Consumer balanced plan и matched TRAIN controls: выполнено

`engineering_balanced_training.py` загружает только validated/source-pinned plan,
полностью воспроизводит его coverage/selection/schedule/native inventory ДО
открытия weights и начинает от исходного primitive30.60 fixed SFT updates,
LR1e-4, last2/rank16, те же SFT inputs/order, fresh AdamW. Первоначальные52 raw
completions совпали между balanced и anchored runs побитно. Метрики:

- Balanced: common retention12/20→6/20; common focused1/20→10/20;
  additional contrasts4/12→6/12.8 прежде правильных retention replies потеряны,
  2 прежние ошибки исправлены: общий балл скрывал бы часть forgetting.
- Anchored: retention12/20→10/20; common focused1/20→8/20; additional4/12→7/12.
  Использован full-vocabulary KL только на value-bearing tokens6 корректных
  TRAIN originals, teacher probabilities от parent, weight2. Teacher frozen,
  payload target top1 проверен до обучения. Это6 дополнительных anchor forwards
  в цикле, не equal-compute сравнение с обычным SFT. Nuisance views для KL не
  использовались. Потеряны2 retention replies,0 новых retention successes.

KL — soft penalty, а не запрет переключать argmax. Parent fresh_after=false
было всего0.5085: небольшое распределительное смещение может разрушить correct
greedy output. Оба run получили failed learning gates и inference_release=false.
Сбалансированного selected class coverage оказалось недостаточно для полной
стабильности. Контрастные примеры остаются необходимой защитой данных, не
гарантией обучения. Это TRAIN controls, не независимое подтверждение OOD качества.

Artifacts:
`reports/engineering-balanced-control-2026-11-19`, manifest
`da5515aae6c7864cd6975c401ddae8ee628d6a3aa9a3577cd525a96945338f04`;
`reports/engineering-balanced-anchored-2026-11-19`, manifest
`21330c69c60212aefd8b5137ff6831102ed0c9dae5c932c1d529fa55da197605`.

### Hard retention constraints и корректный rollback

`engineering_retention_guard.py` принимает тот же balanced plan и parent30.
60 SFT proposals, до5 заранее заданных LR trials (1,.5,.25,.125,.0625) на каждое.
Для trial используется тот же clipped gradient и одно исходное AdamW состояние.
При failure возвращаются ВСЕ adapter weights и AdamW moments/step counters;
при exception rollback также обязателен. Accepted trial advances moments один
раз, не число retries. После trial base LR восстанавливается. Полностью rejected
proposal не меняет weights/optimizer. Unit tests проверяют это на настоящем AdamW.

Witness — фактически сгенерированная правильная parent completion целиком до
первого EOS, без template newline после EOS. На каждом candidate проверяется
argmax каждого causal token в12 parent-correct TRAIN replies. Это защищает всю
известную цепочку, а не только loss или отдельный teacher-forced field. Свободная
native generation после обучения оценивается отдельно: numerical/cache changes
могут нарушить строгую эквивалентность witness и другого runtime.

Guard использует и6 original, и6 key-order TRAIN views. Эти views теперь влияют
на принятие updates, поэтому их нельзя называть независимой transfer оценкой.
Проверка ограничена12 известными правильными replies; не гарантирует безопасность
новых вопросов/сетей и не мешает модели исправлять остальные ошибочные TRAIN
replies. Guard не является runtime teacher fallback и не меняет ответы модели.
Если forward/gradient не удаётся или качество не проходит gate, release не
повышается. Accepted updates и tentative trials отчитываются раздельно.

### Проверенный inference-only prefix cache

Первый full-model guard запуск прерван из-за CPU стоимости повторных witnesses.
`reports/engineering-retention-guard-native-2026-11-19` sealed как ABORTED:
частичная history и исходник runtime сохранены, финальных weights/scores нет.
Это не successful run и не resume checkpoint. Новая попытка снова начинается
от исходного parent30 с тем же plan/schedule, а не от потерянных partial weights.

`engineering_frozen_prefix_cache.py` использует forward pre-hooks только для
наблюдения native inputs последних decoder blocks. Модуль не заменяет methods
и не изменяет parameters. Frozen earlier layers/embeddings неизменны, поэтому
их hidden states и per-layer kwargs для фиксированного witness можно повторно
использовать. Replay вызывает те же последние blocks, final norm и LM head.

Кэш разрешён только для eval/inference, stateless text batches и trainable tail;
trainable prefix/norm/head, изменённый input или active grad mode отвергаются.
Hooks снимаются после capture. На КАЖДОМ из12 witnesses перед updates требуется
bit-exact сравнение всех selected logits с native full forward. После updates
повторяются те же12 full/native comparisons. Это квалификация text/last2 profile,
не общий VLM/EMT cache и не утверждение о времени/памяти без matched измерений.

Финальные результаты cached guard и cross-arm audit добавляются после завершения
native generation и проверок целостности. Полный52-probe gate и full-workflow gate
отчитываются отдельно; защита от известных regressions не равна освоению среды.

### Финальный guarded результат и cross-arm audit: выполнено

`reports/engineering-retention-guard-cached-native-2026-11-19`, manifest
`f124789a40318d00bd4587141cf12301961e51a133a833d2989da5936e3e67ee`:
common retention12/20→12/20,**0 потерянных правильных replies**;
common focused1/20→5/20; additional contrasts4/12→7/12.
22 accepted optimizer updates из60 proposals,38 полностью rejected;
243 tentative optimizer steps,1992 witness tail forwards. Это НЕ60 применённых
updates. Первые16 recorded gradients/losses/decisions/history совпали побитно
с aborted full-model implementation: prefix cache не изменил уже наблюдавшуюся
траекторию. Initial и final cache/native logits равны побитно на всех12 witnesses,
max difference0. Reuse охватывает22 frozen decoder blocks для text/last2 profile.

Final free greedy generation с обычным NativePolicy (не cached witness path)
подтвердила сохранение всех12 прежде правильных replies. Новые правильные ответы
появились, поэтому результат не сводится к возврату нетронутого parent. При этом
full52-probe gate FAILED:24/52 correct, не квалифицированный инженерный агент.
Full workflow/independent dev-test/VLM scores отсутствуют, inference_release=false.

`engineering_regression_suite.py` воспроизвёл source/parent/plan attribution,
общие initial52 generations, raw scores, individual losses, schedule targets,
backtracking trial scales и cache proofs. Balanced/KL/guarded имеют одинаковый
основной SFT schedule, но guard принимает только часть updates и использует
дополнительные TRAIN views как constraints; это не equal-compute benchmark и
не доказательство переносимости. Cross-arm artifact:
`reports/engineering-regression-repair-comparison-2026-11-19`.

### Что именно исправлено и что ещё требует работы

- Проверка selected curriculum отклоняет отсутствие typed contrasts, даже если
  весь исходный корпус прошёл processor admission. Эту защиту сохраняем, хотя
  эксперименты показали, что сама по себе она не устраняет forgetting.
- Реализован consumer проверенного плана; нет незаметного продолжения failed
  weights. Native parent reproduction/target/source checks обязательны.
- Добавлена проверка каждого известного правильного TRAIN reply и transactional
  rollback параметров ВМЕСТЕ с AdamW. Средний score/loss не может скрыть потерю
  конкретного защищаемого ответа. Guard constraints и финальное generation
  оцениваются отдельно, чтобы не скрыть teacher-forcing/cache mismatch.
- Учебные outputs/weights, diagnostic counterfactual prefixes и physical results
  не смешиваются. Prefix experiments с неверным fresh_after не попадают в SFT.
- Высокое качество новых задач ещё не достигнуто: guard защищает старые знания,
  но отбрасывает38 конфликтующих proposals и освоение новых targets медленное.
  Нельзя заявлять устранение всех причин, общий convergence result или преимущество
  над другими агентами по одному tiny TRAIN случаю.

Следующий controlled recipe должен менять sampling/accumulation по semantic
field/class и контролировать нормировки loss. Нужны coherent mixed microbatches,
а не только50/50 online examples с разным label length; сравнение при заранее
зафиксированных budgets и явных gradient/margin diagnostics. Raw token
probabilities остаются некалиброванными diagnostic quantities, не инженерной
confidence оценкой. Затем расширять8 TRAIN bases и выполнять полные реальные
measure→edit→simulate→check workflows, где revision/freshness/terminal принадлежат
engine и наблюдаются агентом. API-effect forecasting — диагностический навык,
он не заменяет task completion. Старый52-probe gate остаётся failed; новый
workflow profile требует собственного объявленного admission/evaluation.

Новые dev/test families остаются закрыты для настройки модели. Guard key-order
views, использованные при выборе updates, нельзя повторно назвать независимым
transfer. Для VLM нужны отдельные image-required tasks/controls, не текстовая
accuracy под другим названием. Ни один guarded training результат не является
общей гарантией безопасности реальной сети.

Cross-arm audit manifest:
`1fbb114fdb238be8cfd2988db5030406f7ff41c4114f81d9a2f4ab114a5c15c2`.
101 целевой Python test в каждом base/native окружении и60 JS checks passed;
web build, YAML, whitespace и source/physics identity checks прошли.
Guarded runner — finite research profile без same-phase resume; transactional
rollback внутри процесса не надо выдавать за durable optimizer checkpointing.

Воспроизведение guarded профиля (новая output directory обязательна):

```bash
.venv-qwen35-08b/bin/python -m benchmarks.engineering_retention_guard \
  --plan reports/engineering-focused-balanced-plan-2026-11-19 \
  --manifest-sha256 54532da3d8626c358caaba1b1b97ffe8345c5232c3b34fec55a90a5f0ccaff7d \
  --target requirements/qwen35-08b-local-target.json \
  --target-sha256 e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262 \
  --out reports/my-guarded-research-run
```

Для controls используется `engineering_balanced_training` с теми же --plan,
--manifest-sha256/--target/--target-sha256 и --arm balanced либо --arm anchored.
Эти команды воспроизводят fixed TRAIN исследования, не запускают промышленную
поставку, внешний CI или автоматическое продолжение failed learning recipe.
