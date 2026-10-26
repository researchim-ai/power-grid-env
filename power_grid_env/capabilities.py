"""Паспорт возможностей. validated_for_declared_scope не выдан: нет натурных измерений."""

from __future__ import annotations

MATRIX_VERSION = "0.1.0"

CAPABILITY_MATRIX = {
    "version": MATRIX_VERSION,
    "evidence_limit": "Численная и перекрёстная проверка на авторских схемах. Натурных осциллограмм нет, поэтому validated_for_declared_scope не присвоен.",
    "features": [
        {"id": "phase_domain_power_flow", "status": "numerically_verified", "scope": "OpenDSSDirect 0.9 / dss-python: линии ABC/ABCN, YNyn0, Dyn11, YNd11, ZIP, PQ, постоянный Z, шунт, КЗ через импеданс, острова"},
        {"id": "dss_transformer_import", "status": "implemented", "scope": "две обмотки YNyn0 и Dyn11 из DSS, если sidecar задаёт группу и заземление"},
        {"id": "dss_linecode", "status": "implemented", "scope": "трёхфазный LineCode: полные омы равны r1/x1 на км или м, умноженным на длину. Взаимные нули только при diagonal_from_positive_sequence"},
        {"id": "dss_devices", "status": "implemented", "scope": "Capacitor, Generator PQ/PV, Storage, PVSystem pmpp, второй Vsource, LoadShape в секундах, RegControl при явных пределах, Redirect с циклом и hash"},
        {"id": "dss_phase_matrix", "status": "implemented", "scope": "rmatrix/xmatrix/cmatrix 1, 3 или 4 фазы, длина умножается. explicit_phase_matrix"},
        {"id": "positive_sequence_ladder", "status": "numerically_verified", "scope": "главная цепочка 110/10/0.4 против OpenDSS в объявленном допуске. Не доказывает несимметрию"},
        {"id": "engineering_viewer", "status": "implemented", "scope": "локальная HTML однолинейная схема: шины по классу напряжения, выключатель, разъединитель, трансформатор, нагрузка, ввод, конденсатор, накопитель, ФЭС и измерение"},
        {"id": "rms_smib_controls", "status": "numerically_verified", "scope": "SMIB с ЭДС первого порядка и статизмом. Не ANDES и не промышленный AVR"},
        {"id": "emt_step_check", "status": "numerically_verified", "scope": "сравнение шага RLC dt и dt/2. Не полный EMT"},
        {"id": "algorithm_tasks", "status": "implemented", "scope": "каталог задач, маски Gym и схемы tools. Массовое обучение не входит"},
        {"id": "radial_timing", "status": "implemented", "scope": "замер лучевой схемы того размера, который передан в функцию. Бюджет 10 000 узлов не обещан"},
        {"id": "symmetric_newton_ac", "status": "numerically_verified", "scope": "симметричный эквивалент прямой последовательности; не заменяет фазный фидер"},
        {"id": "kron_reduction", "status": "numerically_verified", "scope": "только явно разрешённая редукция при твёрдой нейтрали и нулевой shunt-матрице"},
        {"id": "transformer_magnetizing", "status": "implemented", "scope": "учитывается только если заданы imag_pct и no_load_loss_pct; иначе нуль. ppm_antifloat=1 — численный антифлоат"},
        {"id": "qsts_restoration", "status": "implemented", "scope": "известный повреждённый участок, выключатель, разъединитель, задержки, replay, checkpoint"},
        {"id": "cold_load_pickup", "status": "implemented", "scope": "только при явных multiplier и tau"},
        {"id": "der_limits_soc", "status": "numerically_verified", "scope": "круг S, приоритет P/Q, SoC по исполненной мощности и КПД"},
        {"id": "qsts_overcurrent", "status": "numerically_verified", "scope": "IEC 60255-151 таблица A.1 SI/VI/EI и определённая выдержка. Не distance и не differential"},
        {"id": "distance_differential", "status": "planned", "scope": "нужны измерительные и transient-модели"},
        {"id": "dc_power_flow_ptdf", "status": "numerically_verified", "scope": "малые сбалансированные схемы; не подтверждает напряжения"},
        {"id": "dc_opf", "status": "numerically_verified", "scope": "линейный баланс мощности, HiGHS"},
        {"id": "ac_opf", "status": "numerically_verified", "scope": "двухшинный локальный SLSQP: Pg связан с нагрузкой и перетоком; глобальный оптимум не заявлен"},
        {"id": "dc_opf_network", "status": "numerically_verified", "scope": "сетевой DC OPF с балансами узлов и пределами ветвей; infeasible сохраняется"},
        {"id": "state_estimation_wls", "status": "numerically_verified", "scope": "линейный WLS и нормированный остаток на малой DC-модели"},
        {"id": "n1", "status": "implemented", "scope": "перебор отключений линий фазового backend, непросчитанные случаи сохраняются"},
        {"id": "rms_classical_smib", "status": "numerically_verified", "scope": "одна классическая машина, неявная трапеция, сравнение с собственной частотой"},
        {"id": "emt_rlc", "status": "numerically_verified", "scope": "RL и колебательный RLC против замкнутой формулы; отдельный switching buck"},
        {"id": "cosimulation", "status": "implemented", "scope": "учебная связка фазор–RL с задержкой шага. HELICS не подключён"},
        {"id": "full_emt", "status": "planned", "scope": "не входит в первую поставку"},
        {"id": "real_time_hil", "status": "planned", "scope": "быстрый offline-расчёт не является real-time"},
        {"id": "gymnasium", "status": "implemented", "scope": "короткий rollout, не заявлен выигрыш над baseline"},
        {"id": "llm_tools", "status": "implemented", "scope": "типизированные инструменты без скрытого состояния"},
        {"id": "vlm_pixels", "status": "implemented", "scope": "observation SVG и холст без идентификаторов; hit-test снаружи страницы"},
        {"id": "industrial_certificate", "status": "out_of_scope", "scope": "симуляция не является допуском к управлению сетью"},
    ],
}
