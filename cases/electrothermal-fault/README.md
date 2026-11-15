# R06.37 — горячие обмотки и температурная обратная связь при КЗ

Фазный full-winding решатель уже учитывал R(T), насыщение и тепловые состояния.
Этот этап добавляет **раздельное горячее начальное состояние статора/ротора**,
его происхождение и наблюдаемые сопротивления/тепловые потоки. Для этих функций
выполнена отдельная динамическая квалификация при конечных КЗ.

## Запуск исследования

Из корня проекта, в новый каталог результата:

```bash
python -m power_grid_env.cli phase-study run \
  --study cases/electrothermal-fault/c3-hot_rotating-repeated_ll-feedback.json \
  --out reports/my-hot-fault
```

В `case.motors[]` можно задать:

```json
{
  "initial_temperature_c": [90.0, 70.0],
  "initial_temperature_provenance": "owner://my-study/initial-winding-temperatures"
}
```

Порядок: **статор, общий тепловой узел клеток ротора**, °C. Нужен thermal
passport с ёмкостями J/K, теплопроводностями W/K, ambient/reference temperatures,
alpha и validity ceiling. Без явного initial state обе температуры, как раньше,
равны ambient. Пустое происхождение, лишняя температура, NaN, неположительное
R(T), отсутствие thermal passport и выход за validity ceiling отвергаются
при разборе до интегрирования. Неуказанные новые поля не меняют canonical JSON.

Новые каналы для thermal motor ports:

- `stator_resistance_ohm`, `rotor1_resistance_ohm` и далее по клеткам;
- `stator_temperature_c`, `rotor_temperature_c` (включая zero-core-loss port);
- `stator_net_heat_w`, `rotor_net_heat_w`, `ambient_heat_w`, `cross_heat_w`.

Физика: `R(T)=Rref·[1+alpha·(T−Tref)]`; потери `1.5·R·|i_ab|²`, core loss
поступает в статорный узел. Cross heat положителен от статора к ротору;
ambient heat положителен наружу. Net heat может быть отрицательным при
охлаждении. Полная энергия включает magnetic/shaft/thermal storage; электрические
потери не вычитаются второй раз после включения их в тепловое накопление.

## Покрытие и контроль причинности

**24 основных сценария /96 native runs /48 independent trajectories**:
одна/три клетки, холодный пуск/горячее вращение, LL/3ph/repeated LL, R(T)/alpha=0.
Для каждого — Radau и DOP853 с max step200/100µs. Независимый all-flux SI
RLC/KCL эталон интегрируется DOP853 с50/25µs и собственным refinement.
Проверяются токи/напряжения/скорость/момент/потоки, температуры, сопротивления,
тепловые потоки, энергия и KCL; есть обе стороны коммутации.

Основной alpha=0 контроль использует **R при reference temperature20°C**.
Для горячего двигателя он содержит одновременно эффект исходной температуры
и последующей обратной связи. Его нельзя выдавать за чистое влияние дальнейшего
нагрева. Поэтому добавлены **6 hot frozen-initial-R controls /24 native runs /
12 independent trajectories**: исходные Rs/Rr, токи, потокосцепления, скорости,
температуры, сеть, КЗ и тепловые уравнения совпадают; сопротивления затем остаются
на начальных горячих значениях. Наблюдавшаяся максимальная разница в токе
от дальнейшей R(T)-связи≈0.328–0.921A в этих authored случаях.

Итого **120 native runs,60 independent trajectories,240 numerical comparisons**
с учётом обоих reference refinements. Offline gate повторно выполнил все60
независимых интегрирований, проверил owner inputs, matrices/heat identities,
состав файлов, fingerprints и manifests. Это сравнение реализаций одной модели,
не погрешность относительно оборудования.

```bash
python -m benchmarks.electrothermal_fault_acceptance \
  --out reports/my-electrothermal-matrix --workers 4
python -m benchmarks.electrothermal_frozen_fault \
  --source reports/my-electrothermal-matrix \
  --out reports/my-electrothermal-frozen --workers 4
python -m benchmarks.electrothermal_fault_verify \
  --source reports/my-electrothermal-matrix \
  --frozen reports/my-electrothermal-frozen \
  --out reports/my-electrothermal-accepted --workers 4
python -m pytest tests/test_electrothermal_fault.py \
  tests/test_electrothermal_frozen_fault.py -q
```

Зарегистрированные outputs с датой2026-11-15:
`reports/electrothermal-fault-qualified-*`,
`reports/electrothermal-frozen-fault-qualified-*`,
`reports/electrothermal-fault-accepted-*`,
`reports/electrothermal-fault-delivery-*`.
**630 targeted tests прошли**,0 failures/errors/skips;4 optional ANDES cases
явно исключены из regression selection. Это не полный repository suite.
Отдельный первоначальный diagnostic matrix не прошёл сериализацию numpy bool;
он сохранён и не считается принятой квалификацией.

Новый wheel проверен вне checkout с Python-I/import guard:4 новых исследования,
2 прежних фазных случая и2 прежних RMS protection случая. Прежние фазные traces
получены старым wheel; прежние RMS traces сохранены ранее. Оси/события совпали,
общие каналы проверены с absolute+relative1e-12. Новый wheel меняет ровно3
runtime source files:phase_network.py,phase_core_motor.py,vf_drive.py. Предыдущие
sealed результаты остаются привязаны к старому wheel; не переименовываются под
новый fingerprint. Host/dependencies общие; import guard не OS sandbox и не
проверка на другом host. Hashes означают integrity, не authenticity.

## Границы

Это **фазный EMT/full-winding профиль**, не температурное расширение обычного
RMS/DAE. В RMS по умолчанию сохраняется I² replica с фиксированными motor Rs/Rr.
Отдельный opt-in single-cage R(T) теперь проверен в [профиле R06.38](../rms-winding/README.md);
полное стационарное electrothermal equilibrium и смешанные machine families
в RMS остаются открытыми. Hot rotating state здесь authored transient,
а не доказанное стационарное loaded equilibrium.

Электрический seed взят из уже зарегистрированного Aalto motor example;
добавочные клетки, RC, начальные состояния и схема — явно authored assumptions.
Одинаковый copper alpha для статора/ротора не универсален для реальных роторов.
Core loss — constant eddy conductance, не hysteresis. КЗ — конечные резисторы
2Ω с остаточной ветвью5000Ω после снятия; это не идеальные bolted faults.
Одна фазная RLC-ветвь, isolated-star motors, ограниченное время60ms, три клетки
имеют один общий rotor temperature. Нет OEM thermal-fit, гарантии hotspot,
термозащиты/селективности, continuous extrema, широких топологий или заявленного
превосходства над всеми симуляторами.
