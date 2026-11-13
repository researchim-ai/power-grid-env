# DES-01: проверяемый агент-проектировщик

Первый профиль — выбор **полного эквивалентного импеданса питающей ветви** из
каталога владельца. Одна выбранная позиция применяется ко всем обязательным
режимам. Пуск, два провала с восстановлением и выбег/повторное включение
рассчитываются на трёх сетках с аудитом энергии вала, алгебры, I²t и тепловой
реплики. Это ещё не подбор физического кабеля, трансформатора или защиты.

## Проверка конкретного предложения

```bash
python -m power_grid_env.cli feeder-design --input cases/design-curriculum/design-radial-standard.json --proposal cases/design-curriculum/opt-a.json --out reports/my-design
```

В этом корпусе ID каталога — `opt-a`/`opt-b`; готовые файлы предложений лежат
рядом. Старый
`cases/design/stiff.json` использует другой каталог и потому для нового ТЗ
должен быть заменён таким файлом. Для исходного примера используйте вместе
`cases/design/industrial-feeder.json` и `cases/design/stiff.json`.

```bash
python -m power_grid_env.cli verify-feeder-design reports/my-design --input cases/design-curriculum/design-radial-standard.json --proposal cases/design-curriculum/opt-a.json
```

Каталог результата должен быть новым. Проверка с `--input` и `--proposal`
привязывает evidence к доверенному ТЗ и фактически поданному предложению;
без этих anchors проверяется только внутренняя согласованность пакета.
Код возврата 0 означает принятие по объявленным динамическим требованиям, 2 —
отклонение, недостаточную численную проверку либо ошибку. Окончательный статус
и причина сохраняются в JSON.

## Вердикт и численная проверка

- `accepted`: каждый режим численно квалифицирован и выполняет ТЗ; reward1.
- `rejected`: расчёты квалифицированы, хотя бы одно требование нарушено; reward0.
- `numerically_unqualified`: сетки/аудиты/решения не согласованы; rewardnull.
- `incomplete`: таймаут, отмена, отказ worker или другая неполнота; rewardnull.

`solved=true` после инициализации не заменяет `completed=true`. Ни одного
режима или шага нельзя исключить, чтобы получить положительный вердикт.

Базовый шаг делится пополам до `h <= 1/(8*f)`, затем считаются h, h/2 и h/4. Это
сохраняет исходную сетку владельца как подмножество. Ограничения ТЗ и прежние
численные допуски не меняются; пределы worker time/output и общего размера
артефактов сохраняются. Успех трёх сеток не является строгой верхней границей
непрерывной ошибки или сертификатом мгновенных EMT-пиков.

Первый exploratory run с h10ms сохранил rewardnull из-за thermal audit и
выявил физическое нарушение исходного лимита нагрева3. Этот ТЗ не был принят
путём ослабления критериев. В текущем корпусе стандартные (лимит8) и строгие
(лимит3) ТЗ — разные явно заданные задачи. Строгие случаи остаются без
допустимой позиции в объявленном каталоге, что подтверждается проверкой обеих
позиций на трёх сетках. Это не доказательство невозможности других проектов
или оборудования за пределами каталога.
Лимиты3/8 относятся к безразмерной stator-current I²-replica, не к температуре
обмотки в °C; rotor heating и hotspot temperature этим профилем не оцениваются.

## Корпус и разделение данных

Шесть топологий: radial/chain в train, fork в dev, loop/two_paths/ladder в test.
Точная каноническая сигнатура малого неразмеченного графа не зависит от имён
узлов или порядка ветвей; одинаковые структуры не попадают в разные семейства.
В каждом ТЗ один синтетический двигатель1MVA/10kV, три режима и две позиции.
Всего12 квалифицированных решений и108 изолированных физических запусков:
четыре принятых выбора и восемь отклонённых; два ТЗ не имеют допустимой позиции.

```bash
python -m power_grid_env.cli feeder-corpus generate --out reports/designer-corpus
python -m power_grid_env.cli feeder-corpus verify reports/designer-corpus
python -m power_grid_env.cli feeder-corpus export reports/designer-corpus --out train-prompts.jsonl
python -m power_grid_env.cli feeder-corpus export reports/designer-corpus --kind sft --out train-sft.jsonl
python -m power_grid_env.cli feeder-corpus export reports/designer-corpus --split test --out test-prompts.jsonl
```

Prompt exports содержат ТЗ и каталог, без private verdict/reward/raw evidence.
SFT exports разрешены только для train и содержат **два демонстрационных
decision-only диалога** — допустимый выбор и обоснованный отказ. Это не готовая
выборка для обучения Qwen, не демонстрации вызовов инструментов и не VLM-data.
Семейства и параметры детерминированы и доступны владельцу на файловой системе;
это held-out относительно экспорта train, а не секретный или защищённый OS
benchmark. Названия задач описательные. Исследовательская оценка качества
модели требует большего независимого корпуса и исключения shortcuts; EVAL-01
остаётся открытым.

## Решение агента и оценка

Для корпуса принимается один ответ на каждое задание выбранного split:

```json
[
  {"task_id":"design-loop-standard","proposal":{"option_id":"opt-b"}},
  {"task_id":"design-two_paths-strict","proposal":{"no_feasible_option":true}},
  {"task_id":"design-ladder-standard","proposal":{"option_id":"opt-b"}}
]
```

Это пример формата/контрольной политики, а не результат обученной модели.
Ответ «нет допустимого варианта» получает1 только после квалифицированного
отклонения всего каталога. Пропуски, повторные task IDs, train ID в test,
неизвестные позиции и попытки передать собственный reward запрещены.

```bash
python -m power_grid_env.cli feeder-corpus evaluate reports/designer-corpus --predictions predictions.json --split test --out reports/designer-evaluation
```

Перед оценкой пересчитываются физические аудиты всего frozen corpus, owner
anchors, source fingerprint и manifests. Награда берётся из повторно
проверенного evidence ровно для этих ТЗ/позиции; новая симуляция при каждом
score не запускается. Это позволяет использовать проверенный конечный каталог
для последующего GRPO. Собственный trainer/rollout adapter и качество агента
этим блоком не квалифицированы.

Допуски по кабельному току/нагреву, installation derating, КЗ, селективность и
нормативное соответствие остаются в DES-02/DES-04. Поэтому поле
`qualified_electrical_design` всегда false даже при `accepted`.
