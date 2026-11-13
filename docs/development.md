# Работа с проектом

Реализованы типизированная библиотека, PostgreSQL-backed replay API и операторский интерфейс React/TypeScript/Vite. Они сохраняют исходные версии, проверки и явные решения оператора. Live-адаптер относится к следующему этапу. Действующий контракт — [spec.md](spec.md); HTTP-процесс — [api.md](api.md); интерфейс и демонстрационные истории — [operator-ui.md](operator-ui.md); причины решений и ограничения — [research.md](research.md).

## Подготовить окружение и проверить библиотеку

Установите [uv](https://docs.astral.sh/uv/getting-started/installation/). Из корня репозитория:

```sh
uv sync --locked
uv run --locked pytest -m "not postgres" --cov=proofline --cov-report=term-missing
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src
uv run --locked python -m proofline.schema --check contracts/assembly-output.schema.json
uv build
```

`.python-version` выбирает Python 3.12; `uv.lock` фиксирует зависимости. Окружение `.venv` локально и не входит в Git. Если после установки через pip `uv` ещё не появился в PATH, эти же команды можно выполнить как `python -m uv ...`. Для Windows с кешем uv и проектом на разных дисках допустим `uv sync --locked --link-mode copy`: это способ установки тех же файлов, не изменение зависимостей.

Ruff и mypy проверяют поддерживаемый код. Замороженный исследовательский код проверяется отдельно и автоматически не форматируется. [CI](../.github/workflows/checks.yml) проверяет Python 3.12/3.14 в Linux и Windows, реальные транзакции PostgreSQL в Linux, frontend и браузерный процесс поверх Compose. Никакие модельные вызовы не входят в workflow.

Wheel содержит пакет `proofline`, `py.typed`, CLI и миграции. Replay fixtures поставляются отдельно из репозитория и явно копируются в Docker-образ. Исходный дистрибутив включает тесты, спецификацию, инфраструктуру, пример, фикстуры и исходники frontend; локальные `notes`, `node_modules`, build и browser reports в него не входят. Docker собирает UI отдельным Node-этапом; в runtime остаются Python-приложение и статические файлы. Чистая библиотека проверки работает и без fixtures; её пример — в [validation.md](validation.md).

## Frontend

Для воспроизводимой сборки используется Node 24.22.3. Из корня репозитория:

```sh
npm --prefix frontend ci
npm --prefix frontend run format:check
npm --prefix frontend run lint
npm --prefix frontend run check:api
npm --prefix frontend test
npm --prefix frontend run build
```

`build` включает строгую проверку TypeScript. `check:api` сравнивает сохранённые TypeScript-типы с OpenAPI, полученной из локального Python-приложения; перед ним нужен `uv sync --locked`. Генерация не подключается к БД и не вызывает модель. После изменения HTTP-контракта выполните `npm --prefix frontend run generate:api` и проверьте diff. Скрипт берёт Python из корневой `.venv`; для другого окружения задайте `PROOFLINE_PYTHON` с полным путём к интерпретатору.

Для разработки с hot reload запустите API на `http://127.0.0.1:8000`, затем `npm --prefix frontend run dev -- --port 5173 --strictPort`. Vite проксирует `/api` к этому API. В окружении API должен быть явно разрешён `DEV_ORIGINS='["http://127.0.0.1:5173"]'`; адрес `localhost` считается другим origin. Для Compose переменную задают перед запуском app: в PowerShell `$env:DEV_ORIGINS = '["http://127.0.0.1:5173"]'`, в POSIX `export DEV_ORIGINS='["http://127.0.0.1:5173"]'`.

Обычный Compose-путь обслуживает собранный UI и API с одного origin, без Vite. При запуске Python на хосте приложение читает `frontend/dist`; каталог можно задать через `PROOFLINE_UI_DIR`. Без сборки UI остаётся доступным API. Статические маршруты ограничены `/`, `/cases/{id}` и `/assets`: неизвестный API-путь возвращает JSON404, а не HTML приложения.

## PostgreSQL и тесты транзакций

Поднять отдельную тестовую БД:

```sh
docker compose -f infrastructure/compose.test.yml up -d --wait
```

PowerShell:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://proofline:proofline_test@127.0.0.1:15432/proofline_test'
uv run --locked pytest -m postgres
uv run --locked pytest --cov=proofline --cov-report=term-missing
```

POSIX shell:

```sh
export TEST_DATABASE_URL='postgresql+psycopg://proofline:proofline_test@127.0.0.1:15432/proofline_test'
uv run --locked pytest -m postgres
uv run --locked pytest --cov=proofline --cov-report=term-missing
```

Если 15432 занят, задайте `PROOFLINE_TEST_PORT=15433` перед `docker compose up` и укажите тот же порт в `TEST_DATABASE_URL`. Не останавливайте неизвестный сервис ради тестов. В PowerShell переменная задаётся через `$env:PROOFLINE_TEST_PORT = '15433'`, в POSIX — через `export PROOFLINE_TEST_PORT=15433`.

Тесты отказываются работать с БД, которая не называется `proofline_test`. Каждый тест создаёт собственную UUID-схему и использует только её в search_path, без fallback к public. По завершении удаляется только эта схема. PostgreSQL-тесты не подменяются SQLite и не пропускаются молча при недоступной БД.

В группе проверяются upgrade/downgrade и соответствие миграций моделям, HTTP-контракт, блокировки Case, параллельные повторы ключей, поздние ответы, восстановление после прерывания, stale и смена validation profile. Координация гонок использует события и барьеры, а не sleeps.

Для ручного запуска на хосте задайте отдельный `DATABASE_URL` и путь `PROOFLINE_FIXTURES_DIR`, затем выполните `uv run proofline migrate`, `uv run proofline seed`, `uv run proofline serve`. Для просмотра демо предпочтителен [Compose-путь](api.md#запуск), который не публикует БД на хост.

## Браузерные сценарии и Compose walkthrough

Playwright работает с собранным UI и реальным PostgreSQL. Сценарии создают анализы, правки и решения в V01, V04 и V06, поэтому используйте отдельный Compose-проект с исходным seed, сохранив основное демо на 8000. Настройте порт и origin для тестового проекта.

PowerShell:

```powershell
$env:PROOFLINE_PORT = '8001'
$env:APP_ORIGIN = 'http://127.0.0.1:8001'
```

POSIX shell:

```sh
export PROOFLINE_PORT=8001
export APP_ORIGIN=http://127.0.0.1:8001
```

Затем из корня репозитория:

```sh
docker compose -p proofline-e2e -f infrastructure/compose.yml up -d --wait db
docker compose -p proofline-e2e -f infrastructure/compose.yml run --build --rm app migrate
docker compose -p proofline-e2e -f infrastructure/compose.yml run --rm app seed
docker compose -p proofline-e2e -f infrastructure/compose.yml up -d --wait app
npm --prefix frontend ci
npm --prefix frontend exec -- playwright install chromium
npm --prefix frontend run test:e2e
python examples/replay_walkthrough.py --base-url http://127.0.0.1:8001
```

По умолчанию Playwright обращается к 8001; для другого адреса задайте `PROOFLINE_E2E_URL`. В Linux установите системные зависимости браузера через `npm --prefix frontend exec -- playwright install --with-deps chromium`. Это отдельная команда подготовки окружения, а не обход отсутствующего браузера или БД.

Том `proofline-e2e` отделён от основного `proofline`. Тесты не сбрасывают существующие данные; seed также не перезаписывает правки. Если для нового прогона нужен исходный набор, используйте новое имя Compose-проекта. Команда `docker compose -p proofline-e2e -f infrastructure/compose.yml stop` сохраняет тестовую историю. После работы уберите временные переменные порта и origin из своего shell, чтобы обычный запуск снова использовал 8000.

В CI браузерные сценарии выполняются в одноразовом Compose-проекте перед HTTP walkthrough. Полная проверка включает обе группы pytest, Ruff, mypy, schema check, build, frozen baseline, frontend-проверки и оба сквозных пути. Недоступный PostgreSQL, браузер или несовпадение replay должны завершать проверку ошибкой.

## Проверить материалы

Из корня репозитория, с Python 3.12+:

```sh
python -B -X utf8 -m unittest discover -s evals/baselines/v3 -p "test_*.py"
python -B -X utf8 evals/baselines/v3/verify.py
```

Установка зависимостей и API-ключи не нужны. Проверено на Python 3.14.6 под Windows. Первая команда запускает 21 тест: 19 исходных регрессий и две проверки самостоятельности перенесённого набора и обнаружения изменённого ответа. Для двух последних тестов создаётся временная копия каталогов проекта; после проверки она удаляется.

Вторая команда только читает файлы. Она сверяет новый manifest, 10 хешей измеренных входов и 3 артефакта, добавленных после прогона, проверяет эталоны и повторно оценивает девять сохранённых ответов. Ожидаемые результаты: `pack_pass=7`, `literal_fragments=72`, `fact_values_correct=13`, блокировка `full/V04` и восемь карточек для ручного review. Это воспроизведение оценки записанных ответов, а не новое измерение модели.

Запуск не должен зависеть от `notes/`, старого блога, Codex CLI или личной памяти агента. Новые модельные вызовы не входят в эти команды. При несовпадении хеша нужно выяснить причину; перегенерация manifest ради прохождения проверки недопустима.

## Где хранить материалы

| Каталог | Что хранить | Git |
| --- | --- | --- |
| `docs/` | Поддерживаемые требования, инструкции и объяснения решений | Да |
| `contracts/` | Экспортируемые технические контракты | Да |
| `fixtures/cases/` | Синтетические обращения и источники | Да |
| `fixtures/replay/v3/` | Записанные запросы, ответы, metadata и каталог условий | Да |
| `evals/baselines/v3/` | Эталоны, измеренный оценщик, guard, политики и происхождение | Да |
| `notes/imported/` | Полный неизменённый пакет передачи и исходные квитанции | Нет |
| `notes/migration-archive/` | Исторические проектные документы и ZIP-архивы | Нет |
| `notes/working/` | Текущие заметки, планы и результаты локальных проверок | Нет |

Локальная `notes/` необязательна для свежего клона. Полный импорт сохранён там для истории и сверки, а не как зависимость приложения. Исходники блога уже очищены после проверки переноса; перед будущим удалением локального архива нужны отдельное решение автора и проверенная резервная копия. Игнорирование Git не является резервным копированием.

Файлы, перечисленные в [manifest базы v3](../evals/baselines/v3/manifest.json), хранят исходные байты. Правила [.gitattributes](../.gitattributes) предотвращают изменение переводов строк этих файлов при checkout. Новый manifest описывает актуальные пути; исторический manifest измерения сохранён без изменений и проверяется через соответствие путей происхождения.

## Реализованные границы и следующий этап

`AssemblyOutput` на Pydantic v2 повторяет согласованную форму JSON. Сгенерированная схема сравнивается с существующей без изменения её байтов: различия `$defs`/`$ref`, названий и формы nullable-типа нормализуются, ограничения полей сохраняются. Схема и проверки согласованности остаются разными уровнями, поэтому исходный V04 разбирается как типизированный ответ и получает блокирующие замечания.

Код приложения не импортирует исследовательский оценщик и не зависит от `notes`. Replay-провайдер явно читает подготовленные `fixtures`, проверяет их хеши и связывает запись с точным входом. Библиотека валидации сама файлов не читает. V04 блокируется, остальные восемь записанных условий требуют оператора. V02 остаётся примером того, что структурные проверки не доказывают полноту хронологии.

Транзакционные команды и HTTP API теперь отделены от чистой валидации. Run сохраняется до генерации, которая идёт вне транзакции; завершение защищено compare-and-set и исходной Case.version. Исторические snapshots, body и решения не переписываются. Подробности — в [api.md](api.md).

Операторский интерфейс показывает исходные версии источников и отдельные фрагменты, замечания, новую версию после правки и явное решение. Следующая задача — этап 4 [спецификации](spec.md#порядок-реализации): один явно настроенный live-адаптер с отдельным бюджетом вызовов и проверкой качества. Затем следует упаковка портфолио.

## Измерения и внешние действия

Исторические бюджеты модельных вызовов исчерпаны. Новый live-прогон требует отдельного согласованного объёма и бюджета и сохраняется в новой версии результатов. `policy_v3_1_unmeasured.json` остаётся неизмеренным кандидатом: результат 7/9 относится к v3.

Разрешение на локальную работу не означает разрешение на commit, push, публикацию или развёртывание. Финансовых действий и отправок клиентам в этом проекте нет.
