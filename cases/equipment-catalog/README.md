# DES-02: прослеживаемые опубликованные данные оборудования

Профиль импортирует численные выдержки из открытых первичных источников,
сохраняет единицы и происхождение каждого поля и компилирует их в совместное
статическое/динамическое исследование [equipment-design](../equipment-design/README.md).
Производитель не подтверждал этот импорт. Полные исходные документы не
распространяются; локальные hashes относятся к нашим нормализованным данным,
а не к байтам удалённого документа. Каталог работает offline, не скачивает
и не исполняет содержимое URL.

## Данные и расчётные допущения

- [Nexans U-1000 R2V 4G95, ref084193001](https://latrivenetacavi.nexans.com/en/products/residential-and-commercial/residential-commercial-Eca-class/U-1000-R2V38500/product~084193001~.html): 95 мм², медь/XLPE, Rdc20=0.193 Ом/км, ток в воздухе при30°C 298 А, U=0.6/1 кВ, максимальная рабочая температура90°C. Проверено13.11.2026.
- [Schneider Trihal, каталог2018](https://download.se.com/doc/NRJED315663EN/Trihal%20Catalog_NRJED315663EN_030418.pdf): печатная стр68, PDF page67 с нуля, таблица36 кВ C0 Ck. Колонки250/400 кВА: вторичное напряжение400 В холостого хода, uk6%, потери при120°C 4000/5700 Вт. Это исторические семейства оборудования, не паспорта поставленных серийных аппаратов.
- [Schneider EIG](https://www.electrical-installation.org/enwiki/General_method_for_cable_sizing): дискретные поправки XLPE на воздух30/40°C —1/.91. Применение общей таблицы к паспортному air-rating здесь является инженерным допущением. Подтверждения конкретного метода прокладки производителем нет.
- [EIG, реактанс](https://www.electrical-installation.org/enwiki/Calculation_of_voltage_drop_in_steady_load_conditions) и [k для I²t](https://www.electrical-installation.org/enwiki/Verification_of_the_withstand_capabilities_of_cables_under_short-circuit_conditions) задают общие ориентиры, а не измерения Nexans.

В `catalog.json` все поля имеют value/unit/document_id. Документ содержит тип
manufacturer/engineering_reference/owner_assumption, ссылку, точный locator,
дату ознакомления и условия. В отчёте `compilation.json` есть полная lineage.
Недостающие данные не получают скрытого значения по умолчанию.

Явные owner assumptions: alpha меди .00393/°C, AC/DC factor1.05,
рабочая температура90°C, стойкость трансформатора10000 А/1с. Последняя —
**не подтверждённый паспортный параметр Trihal**, а допущение примера.
Положительный результат условен относительно этих входных данных.
Реактанс кабеля .08 Ом/км также не взят из паспорта этого продукта.

Компилятор вычисляет Rработ=Rdc20·(1+alpha·(theta−20))·AC/DCfactor;
для примера получается .258399015 Ом/км. Из потерь трансформатора выводится
эффективное ur=100·Pk/(SкВА·1000), включая добавочные потери:1.6/1.425%.
Это не DC-сопротивление обмоток; используется температура опубликованных потерь.
Потери холостого хода и ветвь намагничивания в этом эквиваленте не моделируются.

## Запуск и проверка

```sh
python -m power_grid_env.cli catalog-design --catalog cases/equipment-catalog/catalog.json --input cases/equipment-catalog/brief.json --proposal cases/equipment-catalog/selection-250.json --out /tmp/catalog-250
python -m power_grid_env.cli verify-catalog-design /tmp/catalog-250 --catalog cases/equipment-catalog/catalog.json --input cases/equipment-catalog/brief.json --proposal cases/equipment-catalog/selection-250.json
```

Для400 кВА используйте `selection-400.json`. CLI exit0 означает прохождение
объявленных требований, exit2 — отказ или ошибку; результат различает причины.
Кандидат выбирает только ID кабеля/трансформатора. Переданные отдельно catalog,
brief и selection защищают от замены исходных данных при проверке.
Собственный manifest без этих anchors не устанавливает их подлинность.
`reward=1` относится к условной модели с указанными допущениями,
`reward=null` при неполноте/недостаточной численной точности; это не оценка
обученного агента и не нормативное одобрение.

Воспроизводимая матрица и независимые статические сверки:

```sh
python -m benchmarks.equipment_catalog_acceptance generate --catalog cases/equipment-catalog/catalog.json --input cases/equipment-catalog/brief.json --out /tmp/catalog-matrix
python -m benchmarks.equipment_catalog_acceptance verify --catalog cases/equipment-catalog/catalog.json --input cases/equipment-catalog/brief.json --out /tmp/catalog-matrix
python -m benchmarks.equipment_catalog_acceptance reference --catalog cases/equipment-catalog/catalog.json --input cases/equipment-catalog/brief.json --out /tmp/catalog-reference
```

Матрица:250/400 кВА при30°C;250 кВА при40°C; отрицательный случай по аппарату.
Каждый вариант: пуск, два провала, выбег/повторное включение, три сетки —
всего36 фактических интегрирований. Для40°C изменяются оба явных входа,
каталог и brief; их новые хеши сохраняются, базовое ТЗ не редактируется.
Внешний OpenDSS решает КЗ в двух точках для двух трансформаторов; это четыре
статических решения, **не внешняя валидация моторных траекторий**.

## Границы

Только50 Гц, один фидер, одна цепь на воздухе. Температуры только из таблицы,
без интерполяции. Групповая/подземная прокладка и дополнительные поправки
запрещены; несовпадение brief/catalog диагностируется до создания output.
Таблицы не дают автоматически разрешения на конкретную установку.
Частотная экстраполяция, подбор защит, ударные/земляные токи, бросок включения
трансформатора и температура кабеля от пусков остаются вне профиля.
Нужны проверенные недостающие параметры, дополнительные условия прокладки
и аппаратная координация для завершения полного DES-02.
