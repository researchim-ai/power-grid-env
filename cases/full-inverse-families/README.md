# Полные матрицы Very Inverse / Extremely Inverse и проверяемая коррекция уставки

Продолжение [полного SI-профиля](../full-protection-envelope/README.md).
Нормальные требования одинаковы: пуск4с, повторные провалы4с, выбег/самозапуск4с;
minimum_final_speed=.9 и исходные ограничения токов/нагрева/напряжения.
Три аварийных опыта сохраняют apply=.1, clear=.8, duration=1с.
Для каждого семейства и метода выполняются nominal и все16 углов ±5% pickup /
±10% TMS основной/резервной ступени; на каждом варианте шесть случаев и три сетки.
По612 grid runs на семейство (Radau+network_dae).

## Найденный отрицательный пример EI

Исходный EI имеет primary TMS=.01, backup TMS=.08. У adverse corner0011
(primary pickup/time ниже, backup pickup/time выше) запас backup оказывается
недостаточным: при реальной потере измерения основной защиты backup не успевает
сработать до снятия заданного КЗ в.8с. Оба решателя и все три сетки воспроизводят
отказ. Motor/relay-clock audits проходят; max backup progress≈.88273<1.
Это инженерный отказ модели с численно сошедшимся решением, а не solver failure.
Не проверялись все16 углов исходной EI: одного воспроизводимого нарушения
достаточно, чтобы отклонить утверждение о прохождении всех углов.

Сохранены отдельно:

- `extremely_inverse-original-*.json`: исходные envelopes с backup TMS=.08.
- `extremely_inverse-original-corner0011-*.json`: точные effective plans
  отрицательного контроля, по18 реальных интегрирований на решатель.
- `extremely_inverse-coordinated-*.json`: новый вариант с backup TMS=.06.

Новый вариант не изменяет оборудование, допуски, события, время снятия КЗ
или критерии. Изменены только проектная backup TMS и label envelope.
`protection_family_revision.py` проверяет это строгим сравнением
канонических данных с исключением ровно этих двух полей и сохраняет общий hash.
Ревизия — authored design choice, не автоматический оптимизатор и не доказательство
оптимальности. Это уставка математической модели, не рекомендация для серийного реле.

## Запуски

Very Inverse без изменения прежней TMS=.07:

```sh
python -m power_grid_env.cli protection-envelope-store --input cases/full-inverse-families/very_inverse-radau.json --out /tmp/full-vi-radau
python -m power_grid_env.cli protection-envelope-store --input cases/full-inverse-families/very_inverse-network_dae.json --out /tmp/full-vi-dae
python -m benchmarks.full_protection_envelope_reference --radau /tmp/full-vi-radau --dae /tmp/full-vi-dae --owner-radau cases/full-inverse-families/very_inverse-radau.json --owner-dae cases/full-inverse-families/very_inverse-network_dae.json --out /tmp/full-vi-reference
```

Согласованный вариант EI:

```sh
python -m power_grid_env.cli protection-envelope-store --input cases/full-inverse-families/extremely_inverse-coordinated-radau.json --out /tmp/full-ei-radau
python -m power_grid_env.cli protection-envelope-store --input cases/full-inverse-families/extremely_inverse-coordinated-network_dae.json --out /tmp/full-ei-dae
python -m benchmarks.full_protection_envelope_reference --radau /tmp/full-ei-radau --dae /tmp/full-ei-dae --owner-radau cases/full-inverse-families/extremely_inverse-coordinated-radau.json --owner-dae cases/full-inverse-families/extremely_inverse-coordinated-network_dae.json --out /tmp/full-ei-reference
```

`verify-protection-envelope-store DIRECTORY --input OWNER` пересчитывает
весь store. Аналогично [full protection envelope](../full-protection-envelope/README.md)
выполняется `benchmarks.full_protection_duty` с соответствующими двумя owners:
306 аварийных grid traces на семейство, actual opening time/I²t/pre-opening RMS.
Парный benchmark сравнивает306 пар на семейство и SI-алгебру обеих траекторий.
SI в названии oracle означает физические единицы V/A, а не ограничение
семейством Standard Inverse. Это oracle на сохранённых EMF, не независимая ODE.

Исходный отрицательный контроль исполняется обычным `closed-loop-protection`
на двух original-corner0011 файлах; ожидается exit2 при complete=true и
numerically_qualified=true. Verifier может подтвердить корректность отрицательного
пакета, сохраняя passes_declared_profile=false. Отрицательный результат не
переименовывается в успешный результат нового варианта.

## Общий протокол

`acceptance-layout.json` фиксирует расположение артефактов этого исследования,
owner files, pinned producers и явное повторное использование ранее проверенного
SI-профиля с тем же package fingerprint. `three_family_envelope_acceptance.py`
проверяет все шесть stores, связи references/duty, общие инженерные требования,
исходный EI rejection и точную коррекцию .08→.06:

```sh
python -m benchmarks.three_family_envelope_acceptance --input cases/full-inverse-families/acceptance-layout.json --out /tmp/three-family-acceptance
```

Для нового воспроизведения измените layout на свои выходные пути. Если SI
пересчитывается заново, укажите текущий reference producer и
reused_previous_current_code=false. Пример layout для выполненного исследования
ссылается на сохранённый ранее producer из SI delivery: его bytes сверяются
с hash старого reference. Это явная provenance-зависимость, не файл внутри wheel.
Повторная проверка или копирование старого SI не считается новым расчётом.

Итого положительная база трёх семейств:1836 grid runs (612 прежних SI +1224
новых VI/EI). Дополнительно36 расчётов исходного отрицательного EI контроля.
Не следует говорить, что исходная EI TMS=.08 прошла полную матрицу.

## Границы

Один authored75кВА/400В motor, радиальная двухузловая сеть, холодная
инициализация, одно downstream резистивное КЗ, фиксированные R/X/reset/dropout
и delays. Не доказаны внутренние точки диапазона параметров, cold/hot envelope,
полная machine fault duty, CT/instantaneous/reclose модели, OEM-стойкость или
произвольные схемы/масштабы. Неподтверждённые данные оборудования сохраняют
статус assumptions. Полный DES-04 открыт; normative_approval=false и reward=null.
