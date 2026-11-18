# Пайплайн обучения инженерного агента Power Grid

Дата: 18 ноября 2026. Статус: план, не отчёт об исполнении будущих этапов.
Цель: Qwen3.5-0.8B решает инженерные задачи через инструменты и проверяемый
физический расчёт, затем работает со схемами/графиками как VLM.
Экономика исключена. GitHub, CI и отправка данных внешним сервисам не требуются.

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
