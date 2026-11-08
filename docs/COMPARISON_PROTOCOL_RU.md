# R01.1: единый локальный протокол внешних сравнений

Реализованы регистрация физической постановки, запуск закреплённых providers
в отдельных процессах, проверка полного набора каналов и переносимый data-only
результат. Это первый конечный профиль сравнений, не рейтинг всех моделлеров.

## Зарегистрированный корпус

`cases/comparison/registered-baseline.json` задаёт 10 позиций:

- Два balanced AC power flow против **pandapower 3.5.6**: радиальная схема
  с tap/phase branch и кольцевая схема с PV-узлом. Сверяются модули и углы
  напряжения всех трёх узлов, включая нетривиальные ID 10/20/30. Branch flows,
  Q-limit transitions и полный IEC short-circuit этим subset не проверяются.
- Четыре RMS cold-start запуска против **ANDES 2.0.0 Motor3**: nominal Radau,
  тот же nominal DAE, другая frequency/base постановка, более слабый фидер.
  Две nominal реализации — варианты одного физического случая, а не две
  независимые топологии. Сверяются скорость, комплексные E, ток, напряжение,
  момент и P/Q. Horizon и физические оси фиксируются до запуска.
- Две full-winding RL-сети против **motulator 0.19.0** и независимо собранной
  SI-сети: радиальная и кольцевая схемы с двумя насыщенными single-cage
  двигателями. Сверяется полный зарегистрированный инвентарь узловых V,
  токов, потоков, скорости, момента и медных потерь. Здесь окно 60 ms:
  это начальный переходный процесс, не весь разгон до рабочего режима.
- Qwen motor VLM workflow — `not_performed`, пока multimodal profile не готов.
- Общий произвольный EMT — `not_applicable` к этому конечному subset.

Итого: 8 обязательных пар, 1 невыполненная и 1 неприменимая позиция.
Даже при 8/8 `required_comparisons_passed=true` поле
`full_declared_inventory_executed=false`. Численное сравнение с другими
неперечисленными продуктами этим протоколом не заявляется.

## Что фиксируется до решения

В каждой позиции: исходные параметры, provider, track, версия reference,
тип независимости, физические преобразования, единицы каналов, абсолютные
допуски, полные ожидаемые оси primary/reference и область отсчётов.
Регистрация включает конечный горизонт: два одинаково обрезанных результата
не получают pass. Выбранный runner принимает только точный зарегистрированный
документ; исключить неудачный обязательный случай или увеличить его допуск
через `--input` нельзя. Другая постановка требует отдельного профиля.

Все source .py benchmarks хешируются до запуска и перепроверяются после
каждого worker; Python-пакет имеет отдельный fingerprint. Версии сторонних
решателей и исходники участвующих модулей записываются вместе с трассами.
У motulator дополнительно сверяется исходный installed source с закреплённым
wheel. Нужны локальные зависимости; автоматических скачиваний нет.

Provider failure, отсутствие зависимости, ошибка версии и timeout дают
`execution_failed` обязательного случая. Они не превращаются в `not_applicable`.
Для declared exclusion заранее требуется непустая причина; незаявленные
запуски в исключённой позиции отвергаются.

## Физические преобразования

AC-case нашего polar Newton использует MW/Mvar, MVA base и **pu-admittance**
bus shunts. В MATPOWER/pandapower GS/BS — MW/Mvar при 1 pu, поэтому они
умножаются на baseMVA. Углы приведены к рад/одному slack; Q-limit policy
выключена с обеих сторон. Tap/phase ветвь имеет нулевой shunt; charging
сохраняется у обычных линий. Capacitive excitation трансформатора через
неэквивалентный converter не объявляется представленной физикой.

ANDES: явно задаются slip=1, E=0 и native G=0 при фиксированном X.
Rotor d/q, Id/Iq, motor/system MVA bases и фактический native polynomial
нагрузки преобразуются явно. Используется исходный native implicit trapezoid
с шагом 0.125 ms. Интерполяция разрешена только для event-free траекторий,
при полном совпадении концов горизонта и ожидаемого native sampling.
Этот профиль использует один выбранный native шаг; не является новым
трёхшаговым convergence-исследованием каждой reference-постановки.

Full windings: primary использует токовые состояния, original motulator —
потокосцепления; независимая сеть не использует production stamps/RHS.
Каналы αβ имеют мгновенные SI V/A/Vs, не скользящее RMS. Сравниваются правые
значения на общей сетке. Предсобытийные пределы и непрерывные пики не входят
в этот выбор каналов. Механика/вариация насыщения — явно авторские входы,
а не дополнительный измеренный паспорт.

## Результат и проверка

На каждом канале вычисляются max absolute error и RMSE. Требуется точное
покрытие заявленных имён и всех отсчётов, конечные числа и завершение обоих
решателей. Совпадение одинакового backend допускается только как
`shared_upstream`, не увеличивая `independent_passes`.

Сохраняются полный input, benchmark-source inventory, environment, raw и
normalized primary/reference, worker logs, outcome, summary и SHA256 manifest.
`verify-comparison` перепроверяет целостность и пересчитывает метрики/coverage
из normalized provider outputs. Это не повторное выполнение чужого solver и
не аутентификация автора. Raw provider outputs и исходники нужны для аудита
самого преобразования данных; generic verifier не исполняет произвольный код
из артефакта. Повтор реальных программ выполняется исходным registered runner.

Ложные summary-флаги, несовпадающие метрики, скрытые неуспехи, неполные оси,
пропущенные/добавленные каналы, неизвестные версии и NaN отвергаются.
Манифест устанавливает целостность артефакта, не авторство.

## Запуск

Генерация выполняется из репозитория с уже подготовленными local references:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLCONFIGDIR=/tmp/pge-comparison-mpl \
PYTHONPATH=. python -m benchmarks.comparison_suite \
  --input cases/comparison/registered-baseline.json \
  --reference-dir /absolute/path/to/installed-reference-packages \
  --wheel /absolute/path/to/motulator-0.19.0-py3-none-any.whl \
  --out reports/my-comparison
power-grid verify-comparison reports/my-comparison
```

Reference-dir необязателен, если ANDES/motulator уже установлены в interpreter.
Это явный import overlay доверенного host, не OS sandbox. Версии:
pandapower 3.5.6, ANDES 2.0.0, motulator 0.19.0; закреплённые требования
лежат в `requirements/ex02-pandapower-linux-py312.lock`,
`requirements/ex02-andes-linux-py312.lock`, `requirements/motulator-reference.lock`.
Проверка готового ZIP/каталога требует только основного установленного пакета,
не сторонних решателей и не модуля benchmarks из репозитория.

Каждый worker имеет deadline и лимиты stdout/result; предусмотрен общий
deadline. Существующий каталог не перезаписывается. Ошибка worker остаётся
в sealed отчёте, если parent смог завершить протокол; прерванный/незапечатанный
запуск не считается квалифицированным.

## Почему нет скоростного рейтинга

Измеренные wall times имеют `validation_only_uncontrolled`. Native ANDES
использует более мелкий fixed step; сторонний reference может включать
конвертацию, а primary — только solve. Разные методы/допуски/initialization
делают отношение этих времён непригодным для заявления об ускорении.
Поэтому `timing_comparable`, `speedup_claimed`, `performance_ranking_available`
и `competitive_superiority_proven` всегда false.
Контролируемый performance benchmark — отдельный R03.

## Найденное улучшение моделера

Регистрация целочисленных P/Q/начальных V выявила ошибку `balanced_ac`:
NumPy создавал integer arrays; pu-нормализация могла падать, а запись
дробной slack-мощности — обрезаться. Теперь V/Pg/Qg/Pset/Qset создаются как
float независимо от формы JSON-чисел. Есть отдельная регрессия integer/float
эквивалентности и внешние pandapower-проверки tap/shunt/PV преобразований.

R01.1 не закрывает промышленный охват, все reference-пакеты, runtime ranking
или Qwen. Следующий физический блок — R02, единый motor/network workflow.

## Зафиксированная приёмка 9 ноября 2026

Все 8 обязательных пар прошли; зарегистрированный корпус содержит также
1 not_performed и 1 not_applicable. Максимальные AC-расхождения:
voltage 6.08e-12 pu, angle 1.49e-12 rad. Для ANDES максимальная ошибка speed
среди четырёх запусков — 1.30e-6 pu при исходном лимите 1e-5 pu.
Две flux-сети прошли полный зарегистрированный инвентарь каналов.
Разные единицы/метрики не объединяются в общий балл качества моделера.

Обязательная регрессия: **1638 passed, 0 failed/skipped**, 2082 вне профиля
не выполнялись; включены 48 новых тестов. Установленный wheel вне source tree
проверил корпус и его ZIP-перенос с import blocker для pandapower/ANDES/
motulator/benchmarks. Core-зависимости взяты с текущего host, clean OS и
независимый физический host не заявляются. Историческая диагностика неверного
shunt mapping сохранена отдельно, не считается проигрышем чужого решателя.

Производитель:
`c343d0cd10b089e28e8da6028616a6e6f2597266ad73e4f13367c07d3f671712`.
Точные inputs, raw/normalized traces, sources, wheel, logs и JUnit:
`reports/comparison-delivery-2026-11-09/delivery.json`.
Тестовый source snapshot сохранён до итоговых заметок; Python после регрессии
не менялся. GitHub/CI, загрузки моделей и обучение Qwen не выполнялись.
