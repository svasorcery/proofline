# Groq Chat Completions: проверка без модельных вызовов

17 ноября 2026 года. Реализован согласованный переход **только Groq** с Responses
на Chat Completions с `response_format.json_schema.strict=true`. Новых запросов
на генерацию к провайдеру — **0**. Реальный ключ не читался. Это проверка кода и
операторского процесса, не новая live-приёмка декодера или качества.

Работа продолжена в существующей `codex/configurable-live-providers`, HEAD/base
`36f175d37aab242d8b33b8352ba323b1019d3d3c`. Ветка не переключалась, коммиты, push,
merge и deployment не выполнялись. Предшествующие незакоммиченные изменения
сохранены. [Предложение и основания перехода](groq-strict-decoding-2026-11-17.md)
остаются историческим разбором до реализации.

## Получившееся поведение

- `LLM_PROVIDER=groq` выбирает `GroqChatCompletionsProvider` на существующем OpenAI
  SDK 2.54.0. Endpoint фиксирован: `https://api.groq.com/openai/v1/chat/completions`.
  `LLM_MODEL` и `LLM_API_KEY` сохраняют прежнее назначение; правки кода пользователя
  и отдельная настройка протокола не нужны.
- OpenAI/DeepSeek продолжают использовать Responses, Anthropic — Messages.
  Автоматического fallback между ними или обратно на Responses нет.
- Текст v3.2 передаётся одним system message, канонический snapshot — одним user
  message. Policy/prompt versions и текстовые хеши сохранены. Новое происхождение
  запроса различается через `api=chat_completions`,
  `request_profile=groq-chat_completions-v1`, endpoint и request hash.
- Полная схема сохранена, включая `Evidence.fragments.minItems=1`. Один choice,
  `stream=false`, tools отключены, один запрос, `max_retries=0`, прежние deadline
  и лимит генерации через `max_completion_tokens`.
- Только непустой строковый assistant content с нормальным завершением становится
  кандидатом для полной локальной валидации. Отказ, length/content filter,
  неподдерживаемые tool/function calls и неверный envelope сохраняются как ошибка.
  Reasoning не добавляется к JSON карточки. Перенос не даёт автоматического
  одобрения и не отменяет проверки доказательств/времени.
- Native Chat usage сохраняется без переименования в run. Archive verifier
  поддерживает Chat-шаблоны отдельно от старых Responses, отображает известные
  prompt/completion counters в общие totals и явно перечисляет неизвестные поля.
  Reasoning tokens не прибавляются повторно.

| Неизменённый компонент | SHA256 |
| --- | --- |
| Текст policy v3.2 | `f1490205098564aec23a1a4a5289d5e053fcb3f8611e09348690e1782846fa14` |
| Текст инструкции с префиксом | `6519091abb6f9669b4ed7d530b1867a42cee27d30baacd5064a0528ff84e562d` |
| Каноническая генерируемая схема | `9d031d5c4bb85493f85fc50e97990dc7b0a34e6853154aac59636b9275b54eaa` |

## Проверки

Команды и подготовка приведены в [development.md](development.md). На Windows
`uv` вызван через документированный `python -m uv`; sync и сборка Python использовали
локальный кеш с `--offline`. Python HTTP transport в тестах запрещает реальные
соединения; SDK получает только ответы в памяти. PostgreSQL — реальный 17.11,
отдельная `proofline_test` на 15432, UUID-схемы. Локальный frontend проверен на Node
26.11.1; Docker собирал UI на зафиксированном Node 24.22.3 и Python 3.12.16.
«Без модельных вызовов» не означает полного отсутствия сети: Docker при сборке
получал зафиксированные зависимости из реестров.

| Проверка | Результат |
| --- | --- |
| Locked sync из кеша | Успешно |
| Исходный non-PostgreSQL baseline до изменения | 266 passed |
| Финальный полный `pytest`, с явным `TEST_DATABASE_URL` | **381 passed: 305 non-PostgreSQL + 76 PostgreSQL** |
| Ruff check / format check | Успешно, 81 файл форматирования |
| `mypy src` | Успешно, 49 source files |
| Экспорт схемы против frozen контракта | Совпадает |
| Wheel/sdist, включая Groq adapter | Собраны |
| Frozen baseline unittest / verifier | 21 passed; 55 artifact hashes, 10 input hashes, 3 post-run hashes; прежние метрики сохранены |
| Frontend format / lint / API types / TypeScript/Vite build | Успешно |
| UI unit/component tests | 43 passed |
| Chromium e2e на новом offline Compose | 11 passed |
| HTTP replay walkthrough после e2e | V04 blocked → новая правка → явное approve; 0 model calls |
| Архивы live v1 / v2 | Оба прошли: 25 / 8 artifact hashes, manifests сохранены |
| Сравнение всех защищённых файлов | 110/110 совпадают побайтно по SHA256 |
| Сравнение состояний защищённых контейнеров | 6/6 совпадают |
| `git diff --check`, ancestry | Успешно |

Переход проверен RED→GREEN: исходный Responses adapter не прошёл 20 новых
Chat assertions; после реализации профиль и диагностика прошли 103 проверки.
Три новых проверки Chat archive сначала воспроизвели несовместимость старого
verifier, затем прошли вместе с прежними архивными проверками.

Независимое review обнаружило один P2: truthiness-проверка принимала пустые
значения неверного типа в `tool_calls`/`function_call`. Девять RED-регрессий
подтвердили дефект; явная проверка nullable/list формы его устранила. Финальные
381 тест прошли после исправления. Других actionable findings и отложенных
minor-замечаний review не выявило.

## Изоляция и сохранность

Основной `proofline`, первая приёмка `proofline-live-m4-20261116` и вторая
`proofline-live-v32-20261116` не запускались, не пересоздавались и не подключались
к проверкам. Состояния их шести app/db контейнеров совпали до/после; их БД и
окружение не читались. Их тома не монтировались новыми проверочными контейнерами.

Полные каталоги `evals/live`, `evals/diagnostics`, `evals/baselines/v3`, `fixtures`,
`contracts`, исходный `live_profile.py` и утверждённый текст v3.2 включены в
110-файловый снимок SHA256. Все значения совпали. Исторические responses, ошибки,
usage, manifests и решения не переписывались; source drift текущего кода
показывается verifier отдельно и не подменяет целостность исторического архива.

Проверки выполнялись только в новых проектах:

- `proofline-groq-chat-tests-20261117`: выделенная `proofline_test`.
- `proofline-groq-chat-offline-20261117`: первый Compose-проход на 8005.
- `proofline-groq-chat-clean-20261117`: свежий Compose на 8006 с финальным кодом,
  последовательные Chromium и walkthrough.

В обоих app `LIVE_ENABLED=false`, API-ключи пустые. После проверок остановлены
только эти новые проекты. Тома Compose и их тестовая история сохранены.

Ошибки запуска проверок также учтены. Ограничения sandbox (`spawn EPERM` и доступ
к кешу) устранены повтором тех же команд с разрешённым доступом без изменения
тестов. Один общий pytest был запущен без `TEST_DATABASE_URL` (308 passed,
73 setup errors); повтор с явной отдельной БД дал 381 passed. Первый e2e пересёкся
с walkthrough на V04 (10 passed, 1 конфликт версий); свежий экземпляр и
последовательный запуск дали 11 passed и успешный walkthrough. Код приложения
ради этих ошибок запуска не менялся; первая тестовая история не сбрасывалась.

## Принятые решения и ограничения

Работа оставлена в текущей ветке: отдельный checkout от HEAD не содержал бы
утверждённых незакоммиченных изменений. Цена — patch остаётся частью этого
working tree; перед commit нужен просмотр итогового diff. Локальный ledger и
снимки проверки находятся в игнорируемом `notes/` по правилам репозитория; эта
инструкция и запуск приложения от них не зависят.

Reasoning effort/sampling в wire не добавлены, профиль отмечает provider-default.
Это сохраняет выбор других Groq-моделей без model-specific параметров. Цена —
нельзя считать фактический effort равным прежнему Responses или оценивать
изменение качества по одному только переносу протокола.

Реальную совместимость схемы, enforcement strict и качество review намеренно
не подтверждали: новых inference-запросов автор не разрешал. Offline-тесты этого
не доказывают; следующий live-прогон требует отдельного бюджета и разрешения.
Review оставило проверку состояний экземпляров и завершение всех suites
координатору; эти проверки выполнены отдельно, без чтения защищённых БД.
Этап 4 остаётся открытым.
