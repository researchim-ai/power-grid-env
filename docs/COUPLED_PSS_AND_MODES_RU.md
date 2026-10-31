# IEEEST и локальные моды совместной системы

В `coupled_rms/scipy` добавлен IEEEST MODE=1 с семью состояниями.
PSS, TGOV1, SEXS, GENCLS/GENROU и асинхронные двигатели входят в один RHS
с общим sparse-сетевым решением. Новый `coupled_small_signal/scipy`
анализирует локальные моды их совместного равновесия. Это дополняет
[GENROU](GENROU_COUPLED_RMS_RU.md) и [конечные КЗ](COUPLED_FAULTS_AND_CLEARING_RU.md),
не заменяя transient-проверку модальным расчётом.

## Паспорт и передаточная функция

В паспорте машины `stabilizer` принимает существующий `IEEESTPassport`.
Обязателен SEXS. Поддерживается только MODE=1: отклонение скорости ротора
`u=omega-1`, а не частота шины или мощность. Remote voltage и MODE=2…6
не реализованы. Все коэффициенты заданы явно, без подгонки или скрытого tuning.

Полная линейная цепь до ограничителей:

```text
             1                  1+A5*s+A6*s²
H(s) = ---------------- * ----------------------
        1+A1*s+A2*s²        1+A3*s+A4*s²

         1+T1*s     1+T3*s       KS*T5*s
       * -------- * -------- * ---------
         1+T2*s     1+T4*s       1+T6*s
```

A1/A2/A3/A4, T2/T4/T5/T6 положительны; A5/A6/T1/T3 и KS неотрицательны.
Точные ограничения определены паспортом. Реализация — каскад семи состояний:
`F1.rate`, `F1.output`, `F2.rate`, `F2.state`, `LL1.state`, `LL2.state`, `WO.state`.
F1 — второй порядок low-pass, F2 — второй порядок lead-lag, затем два
lead-lag и washout. В steady MODE=1 все семь состояний и выход равны нулю.

`raw_pu` — выход washout; `limited_pu=clip(raw_pu,LSMIN,LSMAX)`.
`voltage_gate=1` строго при `VCL<|Vgen|<VCU`, иначе ноль, включая равенство.
`output_pu=voltage_gate*limited_pu` добавляется к ошибке SEXS:
`error=vref-|Vgen|+output_pu`. Фильтры продолжают эволюцию при закрытом gate;
блокировка не обнуляет и не замораживает их состояния. Raw PSS может превышать
выходные пределы — это нормальное действие выходного ограничителя, не
численный overshoot клапана/поля. PSS-диагностика хранится в `pss_limits`
отдельно от guard неограниченных состояний TGOV1/SEXS.

Приложение/снятие КЗ сохраняет точные pre/post границы общего интегратора.
Непрерывные пересечения PSS voltage gate внутри сегмента обрабатываются
адаптивным Radau; отдельный relay event/time certificate для них не выдаётся.
Выходные каналы gate описывают сохранённые отсчёты. Гистерезиса нет.

## Инициализация и смешанные группы

PSS инициализируется одновременно с машиной, мотором и регуляторами.
Проверяется исходный динамический residual, а локальный Jacobian включает
семь PSS-состояний. Для нагруженного равновесия начальная точка у PSS gate
или output limiter в пределах 1e-7 отклоняется как
`controller_boundary_operating_point`: гладкая локальная устойчивость там
не заявляется. Нет автоматического переноса VCL/VCU или LSMIN/LSMAX.

Разрешены смеси GENCLS/GENROU с governor/AVR/PSS и без них, с произвольным
порядком паспортов относительно канонических генераторов. ID сохраняются;
отсутствующий PSS не создаёт фантомных каналов. Временные границы и баланс
мощности прежние. Бюджет trace учитывает 12 дополнительных PSS-каналов на машину.

## Совместный модальный расчёт

[Пример](../cases/studies/coupled_modal_pss.json) использует тот же `coupled`
паспорт, но `study=coupled_small_signal`, `backend=scipy`.
Нужен `operating_point`; события и КЗ не допускаются. Параметры thermal state
должны давать нулевую тепловую производную, иначе возвращается
`nonstationary_thermal_state`. Пример без thermal удовлетворяет этому условию.

Рассматриваются состояния подключённых моторов, генераторов и регуляторов.
Состояния отключённых моторов исключены. Односторонняя тепловая динамика
исключена из модального пространства; это не анализ порогов thermal trip
или устойчивости автомата контакторов. Нужны внешний slack и фиксированная
топология. Предел dense-разложения — 128 рассматриваемых состояний,
проверяется до поиска начального режима.

Матрица строится центральными разностями **исходного совместного RHS** после
точного исключения линейной сети. Шаг зависит от масштаба состояния,
ограничивается расстоянием до клапана/поля и уменьшается при пересечении
ветви PSS limiter/gate. Непредставимый шаг или невозможность сохранить ветвь
даёт отказ. Сравниваются запросы относительного шага 1e-6 и 5e-7;
относительное изменение матрицы infinity-norm должно быть не выше 1e-5.
Это проверка численной согласованности локальной линеаризации.

Результат содержит canonical state names, матрицу 1/s, комплексные моды,
частоты, damping ratio, spectral abscissa, невязку eigenpairs и состояние
participation. Participation рассчитывается через biorthogonal right/left
vectors и нормируется на моду; при повторных/плохо обусловленных модах
атрибуция помечается inconclusive, а не придумывается.

В отличие от transient startup gate, модальный режим принимает физически
согласованную **неустойчивую** рабочую точку и сообщает `linear_stability=unstable`.
Это не обещание успешно интегрировать её в режиме startup. `completed=true`
означает завершение анализа, не положительное заключение об устойчивости.
`time_domain_simulated=false` явно отличает его от временной симуляции.

## Воспроизведение и эталоны

```bash
python3 -m power_grid_env.cli run-study cases/studies/pss_coupled_fault.json --out /tmp/pss-fault.json --isolate --wall-timeout 30
python3 -m power_grid_env.cli run-study cases/studies/coupled_modal_pss.json --out /tmp/coupled-modes.json --isolate --wall-timeout 30
python3 -m benchmarks.coupled_pss_reference --out /tmp/pss-reference --check-andes
python3 -m pytest -q tests/test_coupled_pss.py tests/test_coupled_small_signal.py
python3 -m power_grid_env.cli schemas --out /tmp/pge-schemas
```

Для `coupled_rms`/`coupled_small_signal` нужны основные зависимости SciPy;
ANDES требуется для дополнительного `--check-andes`.

Семь синтетических траекторий: [покой](../cases/studies/pss_coupled_hold.json),
[нагрузка мотора](../cases/studies/pss_coupled_motor_load.json),
[КЗ GENROU](../cases/studies/pss_coupled_fault.json),
[насыщенный GENROU](../cases/studies/pss_coupled_saturated_fault.json),
[КЗ GENCLS](../cases/studies/pss_coupled_classical_fault.json),
[voltage gate](../cases/studies/pss_coupled_voltage_gate.json),
[output limiter](../cases/studies/pss_coupled_limits.json).

[Независимый эталон](../benchmarks/coupled_pss_reference.py) решает физическую
SI-сеть из пяти комплексных уравнений, SI-мотор и генератор. PSS реализован
через произведение полиномов H(s) и `scipy.signal.tf2ss`, с другими внутренними
координатами; общий solver — DOP853 с более строгими допусками.
Production stamps, PSS cascade, initialization и RHS не вызываются.
Механика и насыщение используют прежние независимые benchmark helpers.
Область — одна линия, источник, генератор и мотор, без трансформаторов/фоновых нагрузок.

Пороги до запуска: P/Q 100 W/var, δ/скорости 1e-5, остальные каналы 1e-4
в своих единицах. Сравниваются в том числе raw/limited/gated PSS и поле AVR.
Отдельный replay проверяет каскад по H(s), а ANDES — saturated/unsaturated
GENROU без моторного тока на 4/2/1 ms, включая raw и gated output PSS.
Этот ANDES-случай не квалифицирует время переключения voltage gate.

Для модов независимо дифференцируется SI/tf2ss RHS, затем спектры сопоставляются
по минимальной суммарной комплексной дистанции. Предел ошибки eigenvalues —
1e-4 1/s. Матрицы не сравниваются покоординатно: координаты различаются.
Малая нелинейная perturbation также проверяется против exp(A*t).
Тест большого усиления подтверждает честное сообщение о неустойчивости;
нулевой gain — эквивалентность отсутствующему PSS по физическим каналам.

[Десять задач](../plans/coupled-pss-ten-tasks.yaml).
[Сохранённая поставка и manifest](../reports/coupled-pss-modes-2026-10-31/delivery.json).

## Что остаётся

[Переключения линий теперь доступны](COUPLED_LINE_SWITCHING_RU.md) в связном subset.
Параметры синтетические; настройка PSS для предприятия не выполнена.
Нет MODE=2…6, remote input, островов и синхронизации, переключения генераторов в совместном профиле, converters или stator EMT. Независимый
многомашинный динамический корпус, квалификация масштаба и измерительная
приёмка остаются открытыми. Малосигнальные моды и sampled limiter/gate traces
не заменяют large-disturbance, relay timing или глобальную устойчивость.
Industrial qualification, field validation и сравнительное превосходство
не подтверждены.

Последующее расширение: [электрическое отключение генератора](GENERATOR_ELECTRICAL_OPEN_RU.md) с продолжающими работу ротором и регуляторами; повторное включение и острова не поддержаны.
