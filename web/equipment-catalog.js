// Coverage is deliberately about THIS editor profile, not a global solver claim.
export const equipmentCatalog=[
 {id:'node',name:'Шина / узел',group:'Сеть',symbol:'⊥',action:'add-node',scope:'Трёхфазный узел связности. Одно напряжение в текущем исследовании.',anchor:'ConnectivityNode'},
 {id:'source',name:'Внешняя сеть',group:'Сеть',symbol:'∿',action:'source',scope:'Источник Тевенина: U,R,X. Z трансформатора можно привести к этой стороне.',anchor:'VoltageSource'},
 {id:'line',name:'Кабель / линия',group:'Сеть',symbol:'⌁',action:'connect',scope:'Симметричный R+jX,длина и длительный допустимый ток. Соедините две шины.',anchor:'Line'},
 {id:'load',name:'Нагрузка Z / PQ / I / ZIP',group:'Нагрузки',symbol:'▽',action:'add-load',scope:'Пассивная постоянная проводимость. Задайте номинальные P/Q,фактическая мощность меняется с U².',anchor:'Load'},
 {id:'motor',profiles:['lv_motor_start','multivoltage_motor_start'],name:'Асинхронный двигатель',group:'Машины',symbol:'M',action:'add-motor',scope:'Одна/две клетки, вентиляторный момент, электрические pu-параметры и H. Выберите шину.',anchor:'InductionMotor'},
 {id:'thermal',profiles:['lv_motor_start','multivoltage_motor_start'],name:'Тепловая защита мотора',group:'Автоматика',symbol:'θ',action:'thermal',scope:'I²-реплика с защёлкой отключения,τ и порогом. Не температура обмотки и не IEC-кривая.',anchor:'MotorThermalProtection'},
 {id:'transformer',profiles:['multivoltage_motor_start','passive_power_flow','passive_3ph_fault'],name:'Трансформатор',group:'Сеть',symbol:'◎',action:'add-transformer',scope:'Две обмотки, фиксированные ответвления,Yy0/Dyn11/Dyn1/Dd0. Выберите многовольтный RMS-профиль и две шины. Нет намагничивания, потерь стали или динамического РПН.',anchor:'Transformer'},
 {id:'switch',profiles:['passive_power_flow','passive_3ph_fault'],name:'Выключатель',group:'Автоматика',symbol:'⏛',action:'add-switch',scope:'Статическое положение аппарата с конечным R+jX в профилях PF/КЗ. Размыкание отделяет обесточенные острова. Нет автоматических отключений или селективности.',anchor:'Switch'},
 {id:'generator',profiles:['ac_power_flow'],action:'add-generator',name:'Генератор PQ/PV',group:'Машины',symbol:'G',scope:'В AC PF доступны статические PQ/PV с пределами Q. Синхронная динамика ротора и регуляторов остаётся отдельным coupled-RMS профилем.',anchor:'Generator'},
 {id:'inverter',name:'Инвертор / DER',group:'Преобразователи',symbol:'≋',scope:'В ядре есть модели инверторов; статические и динамические возможности зависят от адаптера. В текущий motor-start профиль не входит.',anchor:'Inverter'},
 {id:'storage',name:'Накопитель',group:'Преобразователи',symbol:'▥',scope:'Паспорт накопителя в формате сети. Интерактивное управление и длительные исследования требуют другого профиля.',anchor:'Storage'},
 {id:'shunt',name:'Шунт / батарея',group:'Сеть',symbol:'⊣',scope:'Шунтирующий элемент есть в формате и сетевых расчётах. Не поддерживается текущим адаптером motor-start.',anchor:'Shunt'},
 {id:'ground',name:'Заземление нейтрали',group:'Сеть',symbol:'⏚',scope:'Для фазных и несимметричных исследований; не входит в симметричный профиль редактора.',anchor:'NeutralGround'},
 {id:'meter',name:'Измерительный канал',group:'Измерения',symbol:'◉',scope:'В формате есть измерители. Сейчас U/I/P/Q выдаёт расчёт; размещение и погрешности приборов ещё не подключены.',anchor:'Meter'},
 {id:'fault',profiles:['passive_3ph_fault'],name:'Короткое замыкание',group:'Возмущения',symbol:'ϟ',action:'fault-settings',scope:'Доступен отдельный пассивный профиль 3ф КЗ через конечный Z. Выберите шину в параметрах КЗ. Нет вклада моторов, пикового тока или поправок IEC60909.',anchor:'StudyFault'},
 {id:'harmonic',name:'Гармонический источник',group:'Возмущения',symbol:'∿ⁿ',scope:'Формат гармонических компонент; отдельное частотное исследование. Не эквивалент EMT или произвольной гармонической динамики.',anchor:'HarmonicComponent'},
];
export function searchEquipment(query='',group='all') {const q=query.toLocaleLowerCase('ru');return equipmentCatalog.filter(e=>(group==='all'||e.group===group)&&`${e.name} ${e.group} ${e.scope}`.toLocaleLowerCase('ru').includes(q));}
export const learningTasks=[
 {name:'Прочитать схему и измерения',state:'SFT-корпус подготовлен',detail:'Топология, паспорта, осциллограммы → подключение и состояние каждого мотора. Нужны также отказы отвечать, когда данных недостаточно.'},
 {name:'Проверить гипотезу расчётом',state:'Следующая задача агента',detail:'Замечать провал/неразгон, выбирать измерения, запускать сценарий и объяснять вывод по фактическим каналам.'},
 {name:'Изменить проект',state:'Требуется tool-policy',detail:'Предлагать кабель, параметры, очередность пуска. Принимать решение по всем обязательным режимам и ограничениям, не по одному красивому графику.'},
 {name:'Обучение GRPO',state:'Замкнутый rollout ещё не реализован',detail:'Группы действий → реальные расчёты → reward по корректности, ограничениям и воспроизводимости. Ошибка/неполный расчёт не получает награду за успех.'},
];
