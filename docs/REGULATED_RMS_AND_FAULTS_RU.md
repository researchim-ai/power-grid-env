# Регулируемый RMS, аварии и ограниченное исполнение

> Следующий этап уже добавил [GENROU](GENROU_RMS_RU.md); ниже описаны регуляторы и эталон предыдущего GENCLS subset.

Общий `rms/andes` расширен регуляторами и аварийными событиями из сохранённого
StudySpec. Машина остаётся **reduced-order GENCLS**: электромагнитные состояния
ротора GENROU/GENSAL и полная модель заводского агрегата этим не заявлены.

## Паспорта

У каждого `rms.machines[]` теперь могут быть:

- `governor`: TGOV1, явные R, T1/T2/T3, VMIN/VMAX, Dt;
- `exciter`: SEXS, явные TATB, TB, K, TE, EMIN/EMAX;
- `stabilizer`: IEEEST MODE=1 (вход по скорости), явные A1…A6,
  T1…T6, KS, LSMIN/LSMAX и VCL/VCU.

Не заданные контроллеры не создаются. PSS требует exciter. Рабочие параметры
не подбираются автоматически: пример содержит синтетические настройки,
которые нельзя выдавать за паспорта конкретного оборудования.
Постоянные времени заданы в секундах, MVA/voltage bases машины — отдельно.
Пределы и droop TGOV1 заданы на базе мощности машины; turbine rating равен
явному rated_va соответствующей машины.

Добавлены события `governor_step` и `voltage_reference_step`.
Первое добавляет уставку мощности на **машинной** pu-базе с переводом для ANDES.
Второе добавляет pu voltage reference. Прежний `mechanical_step` разрешён
только без governor, чтобы прямой Alter не конфликтовал с его выходом.

Отчёт включает внутренние и выходные каналы регуляторов, отдельную карту
`controller_units`, observed min/max и число samples на ограничителе.
Нарушение реализованного valve/field/PSS limiter более 1e-7 pu закрывает
successful completion; операционные пределы всего агрегата этим не заменяются.
Предел клапана проверяется по LAG_y, а не ошибочно по lead-lag turbine output.
При generator trip ограничения проверяются только для активной машины.

## КЗ и очистка

`rms.faults[]` задаёт `node_id`, `apply_s`, `clear_s`, `r_ohm`, `x_ohm`.
Это симметричное трёхфазное КЗ с конечным **пофазным** импедансом в Ohm.
Adapter переводит его по voltage/MVA base именно faulted bus, включая
многовольтную сеть, и создаёт ANDES Fault с явной очисткой.

Пустой импеданс (R=X=0), неизвестный node, перекрывающиеся fault windows
и clearance за пределами горизонта отклоняются. Глухое КЗ не подменяется
тайным малым X. Несимметричные повреждения, дуга, IEC duty и мгновенная
EMT осциллограмма не являются этим исследованием.

События включены в общий switch schedule с упорядоченными timestamps.
Сэмпл непосредственно на времени переключения может соответствовать левой
границе; для сравнения before/during/after используются интервалы строго
между event timestamps. Состояния не сбрасываются вручную к желаемому исходу.

## Killable worker

```bash
python -m power_grid_env.cli run-study cases/studies/rms_regulated_fault.json \
  --out /tmp/regulated-rms.json --isolate --wall-timeout 30
```

RMS запускается в отдельном доверенном процессе. Истечение wall deadline,
отмена или превышение result/log budget приводят к kill process group и reap.
Следующий запуск получает новый процесс. Deadline/cancel не являются
физической неустойчивостью и возвращают отдельные status с `completed=false`.

`--wall-timeout` сам включает process mode. `--cancel-file /tmp/rms.cancel`
для RMS также включает его; при уже существующем файле worker не запускается.
В Python API доступны `isolate=True`, `wall_timeout_s=…`, `cancelled=callback`.

Результат проверяется по source fingerprint, network hash и settings.
Логи ограничены 1 MB каждый, result — 64 MB; Python API worker позволяет
задать другие бюджеты. Проверяется и временный result-файл до atomic rename.
Контроль выполняется с polling 10 ms и временем завершения процесса ОС;
это не hard real-time guarantee. Парсинг результата выполняется в родителе.

Это **не OS sandbox**: память, сеть и прочие файловые записи сторонней
библиотеки не ограничены этим механизмом. Runtime предназначен для доверенных
моделей, а не исполнения чужого Python-кода. ANDES cache остаётся локальным.

## Независимые эталоны

Помимо проверки initialization и конечного solve выполнены:

1. Замкнутый TGOV1 + классическая машина — отдельная четырёхмерная RK45 ODE.
2. SEXS — независимый lead-lag + first-order transfer-function replay.
3. IEEEST — полная цепочка second-order filters/lead-lag/washout через SciPy,
   включая сравнение prelimit output. Это block replay на измеренном входе.
4. Fault/clearing — независимая узловая схема с fault admittance и ODE качаний.
5. Вся связанная система — отдельная ODE сети, машины, TGOV1, SEXS и IEEEST,
   включая КЗ и очистку. Reference не вызывает ANDES или project controller blocks.
6. Насыщение, выход из насыщения, отключение агрегата, смена системной MVA-базы.
7. Actual subprocess: совпадение direct/worker, timeout, cancellation,
   output limit, reap и успешный следующий запуск.

```bash
python -m benchmarks.regulated_rms cases/studies/rms_regulated_fault.json \
  --out /tmp/regulated-reference
```

Эталон специализирован для сохранённой lossless SMIB-схемы. На шаге 1 ms
получены максимальные ошибки около 4.6e-7 rad по углу, 6.6e-8 pu по скорости,
6.3e-5 pu по возбуждению и 3.9e-7 pu по клапану. На 2 ms ошибки больше;
сырые траектории и source hashes сохранены в
[отчёте](../reports/regulated-rms-2026-10-30/summary.json).

Динамика произвольной площадки с неизвестными параметрами не становится
квалифицированной из-за совпадения этого эталона. Industrial/field profiles
остаются закрытыми.

## Следующая физика

GENROU/GENSAL и saturation/rotor states с отдельными паспортами, motor groups,
converter current limits и islands, независимый внешний dynamic corpus,
трёхобмоточные трансформаторы и полноценный event/control industrial pack.
Для сервиса дополнительно нужны memory/disk quotas, job API, dependency lock
и отдельная изоляция недоверенного кода.
