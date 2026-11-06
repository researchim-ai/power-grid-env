# EX-04: воспроизводимый кандидат поставки

> Исторический протокол. По указанию пользователя от 6 ноября 2026 года внешний EX-04 и CI не требуются; агент не обращается к GitHub. Действующий критерий — локальная поставка B08.4 в `plans/CURRENT_STATUS.md`. Этот документ не является разрешением запускать внешние действия.

Профиль `balanced-dol-linux-x86_64-cpython312-candidate-v1` предназначен для
прямого пуска и самозапуска двигателей в симметричной сети. Это кандидат
поставки для Linux x86_64 / CPython 3.12. Он не означает квалификацию всех
реализованных моделей, произвольных масштабов, EMT или конкретного предприятия.
Собственная лаборатория не требуется: используются открытые модели,
зафиксированные параметры, независимые расчёты SI/MNA и опубликованные контракты
сверки. Натурные измерения могут дополнять доказательства, но не являются
обязательным условием открытого профиля.

## Что входит

`benchmarks.release_candidate` сохраняет точный снимок исходников, тестов,
кейсов, документации, планов, lock-файлов и CI. Секреты, `.git`, виртуальные
окружения, кеши и каталог `reports` не входят в исходный снимок. Симлинки
запрещены. Большие EX-03 кейсы воспроизводятся закреплённым генератором;
их хеши находятся в `requirements/ex03-case-manifest.json`.

Кандидат содержит дважды собранный одинаковый wheel, полный runtime wheelhouse,
хешированный install.lock, квитанции двух чистых офлайн-установок, OpenDSS smoke
и повтор синтетического моторного эталона. Все build inputs и код wheel
сверяются с текущими исходниками. Старая квитанция после изменения README,
ресурсов, pyproject или производственного кода отвергается.

Для каждого wheel сохраняются METADATA и доступные LICENSE/NOTICE/COPYING,
включая вложенные notices. В wheel cloudpickle 3.1.1 и PyArrow 23.0.0
отдельные тексты отсутствуют: добавлены LICENSE/NOTICE из соответствующих
upstream-релизов, с URL и SHA-256 в
`requirements/release-license-supplements.json`. Комплект не собирается, если
осталась только METADATA без текста лицензии. Это инвентаризация, а не
юридическое заключение.
Лицензии OpenIPSL/Dynawo и рецепты закреплённых внешних источников остаются
в исходном снимке. Скомпилированные Modelica/FMU артефакты не включаются в этот
runtime-кандидат: их отдельный native-online workflow сохраняет собственные
исходники, версии и лицензии. Этот workflow не подменяется моторным smoke.

## Локальное воспроизведение

В CPython 3.12 установите build tools и runtime, подготовьте wheelhouse:

```bash
python -m pip install setuptools==79.0.1 wheel==0.45.1
python -m pip install --require-hashes -r requirements/pilot-linux-py312.lock
python -m pip download --only-binary=:all: --require-hashes \
  -r requirements/pilot-linux-py312.lock --dest /tmp/pge-release-wheels
python -m benchmarks.pilot_installation --work /tmp/pge-release-work \
  --out /tmp/pge-release-installation --wheelhouse /tmp/pge-release-wheels
python -m benchmarks.release_candidate --installation /tmp/pge-release-installation \
  --wheelhouse /tmp/pge-release-wheels --out /tmp/pge-release-candidate
```

Используйте новые каталоги для каждого прогона. Подготовка зависимостей может
требовать сеть; две проверяемые установки используют `--no-index` и
`--require-hashes`, без editable install, PYTHONPATH и system-site-packages.

Тестовый инструмент устанавливается отдельно от поставляемого runtime:

```bash
python -m pip install --require-hashes -r requirements/ex02-andes-linux-py312.lock
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
python -m pytest tests -q -p benchmarks.release_inventory \
  --release-profile requirements/release-balanced-dol-linux-py312.json \
  --release-inventory /tmp/pge-release-evidence/test-inventory.json
python -m benchmarks.release_receipt --candidate /tmp/pge-release-candidate \
  --inventory /tmp/pge-release-evidence/test-inventory.json --source . \
  --out /tmp/pge-release-evidence/execution.json
python -I /tmp/pge-release-candidate/verify.py /tmp/pge-release-candidate
```

Плагин сначала собирает весь каталог `tests`, затем выполняет точный список
файлов профиля. Каждый оставшийся nodeid получает `outside_profile`, а не
«пройдено». Пропуски при collection считаются ошибкой. Для обязательного теста
нужны успешные setup/call/teardown без skip/xfail; фильтр `-k` не может скрыть
невыполненный обязательный тест. Частичная команда `pytest tests/test_x.py`
не допускается. Поля полного числа тестов и `all_project_tests_executed`
показывают фактическую границу проверки. Снимок исходников проверяется до и
после тестов и при связывании с кандидатом.

Проверяйте запечатанный кандидат без запуска внутри него: Python/pytest
создают кеши, а лишние файлы нарушают точный манифест. Для нового исполнения
скопируйте `source` в отдельный рабочий каталог. Отчёты храните снаружи.

## Где проходит граница доказательства

`candidate_id` адресует содержимое, включая весь манифест файлов. Проверяющий
скрипт использует только стандартную библиотеку, ограничивает размер и число
файлов, запрещает небезопасные пути, дубликаты JSON, NaN и симлинки. Переданный
`--expected-id` должен поступить из отдельно доверенного канала. Хеши не
аутентифицируют автора или машину; злоумышленник может переписать и файл, и
манифест. Для враждебного комплекта используйте отдельно доверенную копию
проверяющего скрипта, а не исполняемый файл из самого комплекта.

Квитанция исполнения связывает candidate_id, исходники, профиль, полную
инвентаризацию тестов, Python/platform и версии пакетов тестовой среды.
`GITHUB_*` и `RUNNER_*` сохраняются только как недоверенные утверждения.
Даже запуск из CI сам по себе не устанавливает `independent_host_verified`:
нужна внешняя проверка GitHub run/job, exact commit, workflow, attempt и
подлинного артефакта. Проверяющий `benchmarks.release_remote` реализует эту связь через живой API
и свежую криптографическую проверку подписанного свидетельства. Локальная
квитанция по-прежнему оставляет этот gate открытым: само наличие инструмента
не является свидетельством удалённого исполнения.

Workflow `.github/workflows/release-candidate.yml` готовит такой комплект
на Ubuntu 24.04 / Python 3.12. Реальное исполнение в GitHub и проверка
происхождения артефакта остаются отдельными критериями EX-04; наличие YAML
не считается выполнением. Текущий непубликованный снимок нельзя объявлять
проверенным на удалённом хосте по прогону старого commit.

Оставшиеся отрицательные случаи и границы перечислены машинно в профиле:
старый UEL clock policy, превышение бюджета полного mixed cold native и
непроверенные комбинации масштаба. Они не исчезают при успешной сборке wheel.


## Подписанный удалённый прогон

Release workflow теперь создаёт `release-payload.zip` с двумя каталогами:
`candidate` и `evidence`. ZIP воспроизводим при одинаковых входных байтах,
включает скрытый каталог `.github` и не зависит от того, сохраняет ли GitHub
скрытые файлы при загрузке дерева каталогов. Для `workflow_dispatch` создаётся
подписанное SLSA-свидетельство через закреплённый `actions/attest`. При ошибке
подписи qualifying artifact не публикуется; диагностика сохраняется отдельно.
PR-прогоны проверяют сборку, но не открывают gate независимой поставки.

На доверенной машине с современным GitHub CLI:

```bash
python -m benchmarks.release_remote \
  --repo researchim-ai/power-grid-env \
  --commit FULL_40_CHARACTER_COMMIT_SHA \
  --run-id RUN_ID --attempt ATTEMPT \
  --source /path/to/trusted/source \
  --out /tmp/new-remote-proof
```

`--artifact-id ID` необязателен: при отсутствии ID инструмент читает полный
ограниченный список артефактов и требует единственное совпадение имени для
этого commit/attempt. Список, превышающий поддержанный предел 100 артефактов,
не принимается как полный. Имена и ID из произвольного JSON не аутентифицируют
доказательство: CLI всегда читает API заново.

Проверяются:

- Успешный завершённый `workflow_dispatch` из того же репозитория, без fork;
  точный workflow, SHA и attempt; полный список единственного job `candidate`
  со всеми успешными шагами; время создания и SHA-256 скачанного артефакта.
- Полный Git tree без truncation; имена, размеры и Git blob hashes каждого
  исходного файла; SHA-256 замороженного источника совпадает с доверенным
  локальным снимком. Другой профиль и грязный checkout отвергаются.
- `gh attestation verify` с `--repo`, `--signer-workflow`, `--source-digest`,
  `--signer-digest`, `--deny-self-hosted-runners`. Параметры ослабления проверки
  и произвольный offline bundle не принимаются.
- Поля проверенного сертификата: GitHub OIDC issuer, `github-hosted`, immutable
  repository ID, commit, ref, signer workflow, событие и подписанный URL
  конкретного run/attempt. Утверждение в SLSA predicate или переменная окружения
  не заменяет эти поля сертификата.
- Точный payload digest, целостность candidate, wheel/build inputs,
  зафиксированный профиль, квитанция исполнения и все фазы обязательных тестов.

ZIP распаковывается с ограничениями размеров и количества файлов. Запрещены
traversal, симлинки, специальные файлы, дубли, неоднозначный регистр и коллизии
file/directory. Скачанные `verify.py`, Python-модули и shell не исполняются:
используется доверенный проверяющий код с машины проверки. Доверенный GitHub CLI
тоже является частью доверенной базы; `--gh` позволяет указать его путь.

Старый CLI, отсутствие подписи, прав доступа или поддержки attestations не
превращаются в «не требуется»: инструмент возвращает ошибку и `failure.json`
с `independent_host_verified=false`. В текущем локальном окружении установлен
CLI без команды `attestation`; реальный подписанный прогон проекта ещё не
проверен. Положительные тесты с подменённым транспортом проверяют контракт и
управление проверкой, а не настоящую криптографическую подпись.

`.github/workflows/release-provenance.yml` запускает проверку после успешной
release-сборки или вручную с SHA/run/attempt. Автоматический `workflow_run`
требует наличия этого workflow в default branch. Доверенный проверяющий и
проверяемый checkout разделены; verifier job имеет только read permissions.
Нужны GitHub CLI с требуемыми флагами и разрешённые для репозитория artifact
attestations. Если тариф/политика репозитория их не предоставляет, gate остаётся
закрытым, а не понижается до проверки одного хеша.

После действительного успешного выполнения только удалённый проверяющий может
записать `independent_host_verified=true` для данного профиля и данного снимка.
`industrial_release_qualified` остаётся false: происхождение поставки не
расширяет численно подтверждённую область моделирования.

Источники контракта: [GitHub CLI attestation verify](https://cli.github.com/manual/gh_attestation_verify),
[официальный actions/attest](https://github.com/actions/attest),
[REST artifacts](https://docs.github.com/en/rest/actions/artifacts),
[REST workflow runs](https://docs.github.com/en/rest/actions/workflow-runs).
