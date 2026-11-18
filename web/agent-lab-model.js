export const taskFamilies=[
 ['assemble','Сборка сети по ТЗ','bounded','Разрешённые маршруты,каталог,фиксированные нагрузки и проверка PF.'],
 ['drawing','Восстановление по изображению','planned','OCR,разбор соединений,паспортов и неоднозначных обозначений.'],
 ['review','Проверка проекта','partial','Есть проверки редактора;общий агентский контракт ревью ещё открыт.'],
 ['study','Подготовка исследования','planned','Выбор профиля,сценариев,критериев и полноты расчёта.'],
 ['observe','Чтение схем и осциллограмм','dataset','Есть ограниченный SFT-корпус;это не пройденный критерий обученного агента.'],
 ['diagnose','Диагностика проблемы','bounded','Три внесённых типа проблемы в малой пассивной PF-сети.'],
 ['measure','Выбор следующего измерения','planned','Частичные наблюдения,неоднозначные гипотезы и бюджет измерений.'],
 ['repair','Исправление проекта','bounded','Каталог/маршруты,неизменяемые требования и свежий PF после правки.'],
 ['sizing','Подбор оборудования','partial','Есть ограниченные Python design-пакеты;общий эпизод ещё не объединён.'],
 ['restart','Пуск и самозапуск','partial','Есть физика и проверенные профили;нужен общий tool-policy эпизод.'],
 ['operating','Управление рабочим режимом','partial','AC PF с PQ/PV и Q-пределами;эпизод управления впереди.'],
 ['protection','КЗ и защиты','partial','Есть пассивный3ф fault GUI и отдельные Python-профили;не общая координация.'],
 ['restore','Переключения и восстановление','partial','Есть прежние отдельные restoration-инструменты;новый эпизод не объединён.'],
 ['robust','Робастность и отказы','planned','Семейства N−1,разброс параметров,полный набор обязательных режимов.'],
 ['identify','Идентификация параметров','partial','Есть Python-модели;неуникальность параметров должна учитываться в критерии.'],
 ['adversarial','Поиск тяжёлых случаев','planned','Численные контроли,сходимость,балансы и независимые эталоны.'],
 ['report','Инженерное заключение','planned','Проверяемые ссылки на расчёты и полнота выводов,а не только качество текста.'],
].map(([id,title,status,scope])=>({id,title,status,scope}));
export const toolExamples={inspect_project:{},inspect_equipment:{equipment_id:'line1'},validate:{},simulate:{},add_line:{route_id:'route1',equipment_id:'line1',catalog_id:'copper-1'},add_load:{load_id:'load1'},set_line_catalog:{equipment_id:'line1',catalog_id:'copper-1'},remove_line:{equipment_id:'line1'},submit:{},export:{}};
export function parseArguments(text){if(text.length>8192)throw new Error('Предел аргументов8KB');const value=JSON.parse(text);if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Нужен JSON-объект аргументов');return value;}
