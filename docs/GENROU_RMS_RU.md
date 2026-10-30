# GENROU: электромагнитные состояния ротора в общем RMS

В `rms/andes` добавлен явный вариант машины **GENROU** наряду с GENCLS.
Network→ANDES adapter, mixed models, события, регуляторы и worker используют
один StudySpec. GENCLS остаётся отдельной упрощённой моделью, а не меняет
смысл при появлении новых параметров.

## Что моделируется

GENROU содержит шесть динамических состояний: угол, скорость и четыре
переходных/сверхпереходных состояния ротора. Реализованы сопротивление статора,
leakage, различные d/q reactances, постоянные времени и квадратичное насыщение.

Stator здесь **алгебраический** (ANDES Flux0). Мгновенные токи, switching
waveforms, статорные электромагнитные переходы и полный EMT не заявлены.
GENSAL и другие типы машин пока не добавлены.

## Паспорт машины

Для GENROU задаются:

- `model: GENROU`, generator_id, rated_va, inertia_s, damping_pu;
- `x_d_pu`, `x_q_pu`, `x_d_prime_pu`, `x_q_prime_pu`;
- `x_d_subtransient_pu`, `x_q_subtransient_pu`, `leakage_pu`, `armature_r_pu`;
- `td_prime_s`, `td_subtransient_s`, `tq_prime_s`, `tq_subtransient_s`;
- явный режим `saturation` и, если нужен, saturation_s10/saturation_s12;
- опциональные TGOV1, SEXS и IEEEST passports.

Все R/X заданы в pu на **собственной MVA/voltage base машины**. Постоянные
времени соответствуют open-circuit T′d0/T″d0/T′q0/T″q0 в секундах.
Mechanical inertia H задана отдельно; ANDES получает M=2H и свою базу машины.
Условия входа проверяются до построения backend:

- X > X′ > X″ > Xleakage для обеих осей;
- T′ > T″ > 0;
- X″d = X″q — ограничение выбранной ANDES GENROU formulation;
- Ra ≥ 0;
- конфликт с явно заданными H/Xd в canonical Generator отклоняется.

В режиме `disabled` saturation factors не задаются. Adapter использует
конвенцию отключения upstream (S10=0, S12=1), при которой Se=0; это не
подстановка физической saturation curve. В режиме `quadratic` необходимы
явные положительные S10 < S12. Противоречащие режиму значения отклоняются.

Примеры содержат синтетические declared parameters, не паспорт реального
агрегата и не результат идентификации измерений.

## Запуск

```bash
python -m power_grid_env.cli run-study cases/studies/rms_genrou.json \
  --out /tmp/genrou.json --isolate --wall-timeout 30

python -m power_grid_env.cli run-study cases/studies/rms_genrou_saturated.json \
  --out /tmp/genrou-saturated.json --isolate --wall-timeout 30

python -m power_grid_env.cli run-study cases/studies/rms_genrou_regulated.json \
  --out /tmp/genrou-regulated.json --isolate --wall-timeout 30
```

Нужен установленный RMS extra/ANDES. Дедлайн, отмена и worker budgets
описаны в [руководстве исполнителя](REGULATED_RMS_AND_FAULTS_RU.md).

Отчёт содержит `mapping.machine_models` и stable canonical IDs. Для каждой
машины выбирается собственный ANDES model/index; смешанная сеть не читает
все каналы через индекс GENCLS. `generator_trip` и `mechanical_step` также
адресуют правильную модель.

Дополнительные каналы: e1q/e1d/e2d/e2q, psi2d/psi2q и Se, с отдельной картой
`machine_state_units`. Это внутренние состояния, не измеренные напряжения
терминалов. P/Q остаются в W/var, моменты — на системной MVA-base.

## Независимая проверка

`benchmarks/genrou_reference.py` реализует отдельную шестимерную ODE:

1. Рабочая точка определяется из аналитического terminal PF и независимого
   d/q equilibrium solve. Начальные состояния не берутся из ANDES trajectory.
2. Восстанавливаются transient/subtransient fluxes и ток через собственное
   узловое уравнение генератор–сеть–КЗ.
3. Уравнения качаний и четыре rotor equations интегрируются SciPy RK45.
4. Квадратичная saturation curve вычисляется отдельно из S10/S12;
   контрольные точки Se(1)=S10 и Se(1.2)=S12 проверяются тестом.
5. Для Ra>0 механическая мощность равна terminal P плюс copper loss;
   начальный момент не ошибочно приравнивается только к terminal P.

```bash
python -m benchmarks.genrou_reference cases/studies/rms_genrou.json \
  --out /tmp/genrou-reference
python -m benchmarks.genrou_reference cases/studies/rms_genrou_saturated.json \
  --out /tmp/genrou-saturation-reference
```

Reference специализирован для объявленной SMIB-схемы с lossless внешней
сетью; Ra самой машины может быть ненулевым. Уставка механического момента,
КЗ и очистка имеют общие physical inputs, но reference не вызывает ANDES
или project machine derivative для своей интеграции.

При шаге 0.5 ms максимальная ошибка угла около 5.7e-8 rad без насыщения и
5.3e-8 rad с насыщением; наихудшая ошибка из четырёх rotor states — около
1.2e-5 pu. Проверены оба шага 1/0.5 ms и сохранены полные trajectories.
[Численные результаты](../reports/genrou-2026-10-30/).

Дополнительно проверены quiet equilibrium, смена системной MVA-base,
совместная сеть GENCLS+GENROU, отключение правильной машины, контроллеры и
тождество direct/isolated worker результатов. Общий прогон — 298 тестов.

## Статус и следующие задачи

GENROU на объявленных кейсах численно сверён, но industrial/field profiles
остаются закрытыми. Управляемый GENROU проверен на подключение блоков и worker;
независимый полный controller+network ODE предыдущего этапа относится к GENCLS,
а новый GENROU reference — к машине без controllers. Эти доказательства
не смешиваются в утверждение о квалификации любого агрегата.

Далее: GENSAL/другие model families, motor groups и drives, converter dynamics,
external dynamic corpus, parameter provenance/identification, mixed industrial
packs и более широкие ресурсные измерения. Поддержка одного типа машины не
означает произвольную физику или произвольный масштаб.
