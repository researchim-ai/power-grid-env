# DES-04: обратнозависимые часы реле и конечная матрица допусков

Развивает [closed-loop protection](../closed-loop-protection/README.md):
вместо фиксированного времени trip используется накопление по фактическому
меняющемуся RMS-току. Три семейства: Standard Inverse, Very Inverse,
Extremely Inverse. Измерения связаны с двигателем, сетью и действиями аппаратов.

## Номинальные исследования

```sh
python -m power_grid_env.cli closed-loop-protection --input cases/inverse-protection/standard_inverse-radau.json --out /tmp/inverse-radau
python -m power_grid_env.cli verify-closed-loop-protection /tmp/inverse-radau --input cases/inverse-protection/standard_inverse-radau.json
python -m power_grid_env.cli closed-loop-protection --input cases/inverse-protection/standard_inverse-network_dae.json --out /tmp/inverse-dae
python -m benchmarks.closed_loop_protection_reference --radau /tmp/inverse-radau --dae /tmp/inverse-dae --out /tmp/inverse-reference
```

Аналогичные файлы есть для very_inverse/extremely_inverse. Полная матрица:

```sh
python -m benchmarks.inverse_protection_acceptance --cases cases/inverse-protection --out /tmp/inverse-families
```

Три семейства ×два решателя ×шесть сценариев ×три сетки =108 интегрирований.
Сохраняются исходные требования полного пуска, повторных провалов и самозапуска,
а также три аварийных сценария: основная защита, отказ её выключателя с
резервированием и действительная потеря измерения основной ступени.
Положительный результат не получается простым пересчётом предыдущего fixed-time
trip по одному начальному току КЗ.

## Независимое восстановление часов

План явно выбирает `clock_policy=sampled_integral`. На каждом сохранённом
интервале с активным реле восстанавливается интеграл
`dP/dt=max((I/Is)^alpha−1,0)/(TMS*k)`. Для definite это `1/T`, когда I>Is.
В неактивном состоянии применяется точный экспоненциальный возврат
`Pnext=P*exp(−dt/reset_s)`. После latch прогресс сохраняется.
Коэффициенты обратнозависимых кривых опубликованы, например, в
[официальном руководстве ABB REX610](https://techdoc.relays.protection-control.abb/r/REX610-Technical-Manual/1.1/en-US/Standard-inverse-time-characteristics).

Реализация `relay_clock_audit.py` не вызывает нативный iec_operate_time.
Использует трёхточечную гауссову квадратуру линейного интерполянта тока,
разделяет интервал при пересечении pickup и не интегрирует через скачки
pre/post. Дискретные изменения active/latched/measurement_valid требуют
одноимённых отсчётов с нулевым временным интервалом. У trip проверяются
прогресс≈1 и согласие события с изменением latch; далее отдельно проверяются
задержки и реальные действия выключателей. Подмена только журнала событий
или progress не заменяет эту проверку. Допуск ошибки прогресса2e-4 задан
до расчётов и не настраивается агентом через план.

Это восстановление по дискретным измерениям, не независимая дифференциальная
траектория и не доказательство отсутствия экстремумов между отсчётами.
Независимые SI-уравнения дополнительно проверяют сами напряжения и токи,
используя сохранённые EMF-состояния двигателя как входы. Нативный stamping
не используется; production topology нужен только для идентификации каналов.
Radau/DAE сопоставляются отдельно по сторонам событий с пределом ошибки
времени1e-7с; интерполяции через изменение схемы нет.

Reset/dropout — идеализированные authored-модели из сохранённых native relay
passports (`reset_s=.05`, `dropout_ratio=.95` в примере), а не паспортная
модель возврата конкретного производителя. Source-записи уставок унаследованы
от предварительного screen и не являются OEM-данными этих динамических моделей.

## Полный набор крайних сочетаний четырёх параметров

```sh
python -m power_grid_env.cli protection-envelope --input cases/inverse-protection/envelope.json --out /tmp/inverse-envelope
python -m power_grid_env.cli verify-protection-envelope /tmp/inverse-envelope --input cases/inverse-protection/envelope.json
python -m benchmarks.inverse_protection_verification --envelope /tmp/inverse-envelope --input cases/inverse-protection/envelope.json --out /tmp/inverse-envelope-reference
```

Четыре координаты: pickup основной, TMS основной, pickup резервной, TMS
резервной. Для каждой выбраны два края заданного допуска: ±5% pickup, ±10% TMS.
Выполняются **все16 декартовых сочетаний плюс номинал**, а не две произвольно
названные worst-case точки. Рецепт каждого варианта содержит исходные знаки,
точные effective settings; применённые допуски обнуляются в effective plan,
чтобы не создавать неисполненную вторую неопределённость. Кандидат не может
менять физику, критерии и паспорт оборудования через этот рецепт.

Матрица относится к отдельному явно записанному раннему пусковому/аварийному
brief: пусковая часть0.2с с minimum_final_speed=0, три аварийных случая0.6с,
КЗ с0.1 до0.5с. Это17×4×3=204 интегрирования на Radau.
**Полный разгон и повторные восстановления на всех16 углах не проверяются**;
они остаются в номинальной108-run матрице. Исходные длинные требования не
редактируются: у короткого brief свои данные, id и hashes.

Verifier проверяет все17 планов с owner anchors, рецепты, motor audits,
восстановленные часы, действия и итог. Negative corner не исчезает из результата;
прерванный расчёт не может пройти. Внешняя SI-проверка всех204 сохранённых
траекторий выполняется отдельной командой выше. При необходимости независимую
проверку пакета следует повторить вместе с основным verifier.

## Границы

Конечные углы не доказывают пригодность всех внутренних значений интервала.
Фиксированы задержки выключателей, reset/dropout, R/X, схема и модель измерения.
Не проверены холодные/горячие границы КЗ, CT saturation, instantaneous stage,
случайный шум/задержки связи, все положения/виды КЗ и произвольные масштабы.
Generic IEC curves не являются моделью серийного реле с OEM временными полами.
Неподтверждённые параметры каталога оборудования сохраняют свой статус.

`reward=null`, `normative_approval=false`; полное закрытие DES-04 и независимая
валидация всей дифференциальной траектории этим профилем не заявляются.
Большие family/envelope/reference пакеты хранятся раздельно с manifest anchors;
не следует интерпретировать повторную проверку или копирование как новый расчёт.
