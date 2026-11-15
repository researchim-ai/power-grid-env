# Полная конечная матрица сетевых условий и независимая динамика

## Профиль R06.34, 15 ноября 2026

Исходная VI-ревизия R06.33 сохранена: primary TMS0.009, backup TMS0.0155,
pickup1000/1200A, допуски pickup±5% и TMS±10%. Три нормальных режима
(пуск, повторные провалы, выбег/повторный пуск) и три аварии после3с разгона
(primary, отказ primary-выключателя, потеря измерения primary) проверены
без изменения исходных критериев.

Полностью выполнено **2448** native studies:

`20/90°C × 0.95/1.05U × (nominal +16 relay corners) ×6 modes ×2 solvers ×3 output meshes`.

Это все заявленные **конечные комбинации**, а не доказательство для каждого
вещественного значения между20 и90°C или0.95 и1.05U. Сетки вывода2/1/0.5мс
не объявляются самостоятельным доказательством сходимости интегратора.
В электрической модели меняется постоянное сопротивление **кабеля**, не
температура обмоток мотора и не Rт. Нагрев во время опыта и согласованное
тепловое равновесие кабеля не рассчитываются.

`protocol.json` содержит исходный паспортный каталог, brief, selection и
защитный envelope. Компилятор сверяет их точное соответствие; сопротивление
выводится из Rdc20, коэффициента меди, AC/DC-factor и длины. Нет скрытых
табличных сопротивлений. Исходные ограничения рабочей температуры, единицы
и происхождение фактов сохраняются. В этой серии motor/mechanism и часть
аппаратных/монтажных параметров остаются авторскими допущениями.

Аварийные критерии: мотор подключён перед КЗ, скорость≥0.9pu; отключение
сетевой подпитки и sampled-погасание полного тока КЗ≤0.1A не позже150мс;
наблюдение погасания≥20мс до внешнего удаления КЗ в3.3с. UV-контактор
0.3pu/90ms, автоматический рестарт запрещён. Отключение сети не подменяет
погасание подпитки от ротора. Нормальные speed/current/voltage/thermal
criteria передаются исходными, без подстройки под результаты.

## Внутренние точки

`interior.json` задаёт центр и8 стратифицированных точек с seed20261115
в шести координатах: pickup/TMS двух реле, температура кабеля, напряжение
источника. По каждой координате8 проб покрывают8 различных bins; это
детерминированная Latin-strata выборка, не оптимизация экстремумов.
Каждая точка имеет все6 режимов, оба решателя и3 сетки: ещё **324** native
studies. Tolerances применяются к конкретным параметрам и обнуляются,
чтобы не начислять второй слой неопределённости.

Итого2772 новых native studies,1386 пар Radau/DAE,1386 аварийных траекторий
для I²t и462 fine-pair проверки I²t. Артефакты сохраняют результаты отдельных
аппаратов, двигателя, кабеля и места КЗ. Квадрат полного тока КЗ не назначается
кабелю автоматически, скачки событий не размазываются интерполяцией.

## Независимый дифференциальный эталон нормальных режимов

`benchmarks.running_motor_winding_reference` решает три примитивных SI
уравнения статора/ротора/KCL и интегрирует физическое потокосцепление ротора,
скорость и тепловую реплику через DOP853. Начальные поток/скорость нулевые,
тепловое состояние паспортное. ЭДС native trajectory не используется для
продвижения reference; не вызываются production derivatives, Norton stamps,
constants или initialization. Source voltage берётся в физических вольтах.

Проверены15 самостоятельных траекторий: все3 нормальных режима на четырёх
endpoint conditions и в центре. Каждая сравнена с native Radau и DAE finest
output mesh —30 сравнений. Сравниваются скорость, комплексная ЭДС, ток,
комплексные напряжения обеих шин и thermal replica. Pre/post-event samples
проверяются раздельно; поток, скорость и тепло непрерывны на ручных командах.
Reference guards явно отклоняют выход в активную OC/UV/thermal protection,
которая в этом независимом normal-mode эталоне не реализована.

Максимальные ошибки на30 сравнениях: speed≈1.63e-9pu, EMF≈5.87e-8pu,
current≈2.26e-7pu, bus voltage≈7.18e-9pu, thermal replica≈4.58e-10.
Это согласие двух формулировок одной модели, не точность относительно
натурного оборудования. SciPy и host общие. Независимая дифференциальная
валидация **аварий и защит** всё ещё открыта; там остаются условный SI oracle,
replay часов и согласие native Radau/DAE.

## Воспроизведение

Из корня репозитория, выходные каталоги должны отсутствовать:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python -m benchmarks.running_fault_cartesian \
  --protocol cases/running-fault-cartesian/protocol.json \
  --out reports/my-cartesian --workers 8

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python -m benchmarks.running_fault_interior \
  --protocol cases/running-fault-cartesian/interior.json \
  --out reports/my-interior --workers 8

python -m benchmarks.running_motor_winding_reference \
  --store reports/my-cartesian --interior reports/my-interior \
  --out reports/my-winding-reference

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python -m benchmarks.running_fault_cartesian_acceptance \
  --protocol cases/running-fault-cartesian/protocol.json \
  --store reports/my-cartesian \
  --interior-protocol cases/running-fault-cartesian/interior.json \
  --interior reports/my-interior \
  --winding-reference reports/my-winding-reference \
  --out reports/my-acceptance --workers 8
```

`--verify` вместо генерации пересчитывает опубликованный store; отсутствующее
исследование при проверке не интегрируется и не чинится. `--resume` для
незавершённой генерации использует только запечатанные owner/producer-matched
пакеты без повторного решения. Повреждённые незапечатанные данные не затираются.
Изначальный raw native trace сохраняется даже при последующей ошибке аудита;
численно незавершённый запуск получает отрицательный result, не положительный
label. Продолжать такую серию как принятую нельзя.

Workers1..16. Явный `wall_budget_s` проверяется при отправке/ожидании заданий;
после истечения новые задания отменяются и complete index не публикуется.
Это мягкий deadline: уже выполняющиеся задания завершаются/исчерпывают свои
runtime_limits, после чего pool закрывается. Жёсткий supervisor/OS sandbox
не заявляется. `maximum_studies` ограничивает размер декартовой серии до запуска.

## Целостность и область доказательства

Паспорта и producer закреплены в `index/`; каждая condition/corner-группа и
каждое исследование имеют отдельные seals. Общие64MB/file,256MB/artifact,
4096files/artifact лимиты не увеличивались. Составной корень **не** является
одним generic artifact: используйте соответствующий store verifier.
Финальный gate восстанавливает все owner recipes, criteria, clocks,
условные SI network equations, solver pairs и I²t, сверяет reference bindings
и повторно проверяет hashes. Публикация/проверка предполагает отсутствие
параллельного автора; hashes не заменяют аутентичность производителя.

311 целевых тестов прошли без ошибок/пропусков, включая34 новых этого этапа:
покрытие Cartesian/strata, паспортная R(T), owner substitution, incomplete
traces, истёкший budget, worker caps, immutable cache, отсутствие скрытой
интеграции при verify, CT corruption и независимость winding reference от
production derivatives/искажённой native EMF. Полный repository suite и
недоступные ANDES regressions не заявляются.

Данные: `reports/running-fault-cartesian-2026-11-15`,
`reports/running-fault-interior-2026-11-15`,
`reports/running-winding-qualified-2026-11-15`,
`reports/running-cartesian-accepted-2026-11-15`.
Runtime не менялся; прежние отрицательные контроли R06.33 сохранены,
нормативное/OEM одобрение и training reward не выдаются.

Следующая физическая очередь: согласованная нагруженная motor-only
инициализация и независимые дифференциальные эталоны аварий/защит, затем
другие машины, топологии, температурная обратная связь и несимметричные КЗ.
Более плотные uncertainty probes нужны отдельно; конечная сетка и8 точек
не доказывают отсутствие узкого непроверенного режима между ними.

Измеренный итог: худшее сетевое отключение/полное погасание145.857мс,
минимальный запас4.143мс до150мс; sampled motor-fed ток после отключения
сети до164.25A. Max fine-pair I²t relative change≈7.13e-5 при лимите0.002.

Локальная поставка `reports/running-cartesian-delivery-2026-11-15` включает
неизменённый pinned wheel, исходники протокола,311-test XML, aggregate gate,
все15 независимых winding trajectories и6 повторно интегрированных installed
случаев (5 positive/1 прежний negative). Physical arrays/events совпали точно.
Host и библиотеки общие, reference imports запрещены; это не OS sandbox.
2772 native traces не продублированы: закреплены manifest-хешами sibling stores.
Installed replay имеет аргументы `--installed-dir` и `--data-dir`; входы и
скрипт включены в поставку. Запуск требует тех же зависимостей инового
каталога для generated native packets.
