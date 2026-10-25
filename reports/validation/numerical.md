# Численный отчёт

Версия 0.1.0.

- conventions_power_and_bases: пройдено. S, базы и прямая последовательность
- two_bus_pq_closed_form: пройдено. dV=2.668e-07 pu
- unbalanced_constant_z_mna: пройдено. max dV=1.693e-06 pu
- kcl_and_balance: пройдено. KCL 1.544e-12 pu, баланс -1.175e-03 Вт
- zip_formula: пройдено. ZIP совпал с определением
- dyn11_phase_shift: пройдено. Dyn11: НН опережает ВН на 30°
- delta_blocks_zero_sequence: пройдено. сумма линейных токов треугольника ~ 0
- open_phase: пройдено. фаза C на приёмном конце отсутствует
- island_without_source: пройдено. deenergized_no_source
- false_convergence_is_not_collapse: пройдено. numerically_not_converged, collapse=false
- three_phase_fault_thevenin: пройдено. dV=2.087e-11 pu
- storage_soc_and_inverter_limit: пройдено. предел S, SoC и cold-load
- kron_matches_explicit_neutral: пройдено. Крон совпал с явной заземлённой нейтралью в пределах 5e-4 pu

Совпадение с positive-sequence не использовалось как доказательство несимметрии.
Несходимость PQ не названа коллапсом.