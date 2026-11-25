# Groq strict decoding: Responses и Chat Completions

17 ноября 2026 года. Разбор и предложение, **новых модельных вызовов — 0**.
Читались официальная документация, исходники SDK и журнал уже выполненных
запросов. Два запроса SDK выполнены только через HTTP transport в памяти.
Активный код, конфигурация, контейнеры, БД и решения оператора не менялись.
Результаты [первого](live-acceptance-2026-11-16.md) и
[второго](live-acceptance-v3.2-2026-11-16.md) прогонов сохранены.

## Вывод

**Наш Responses-запрос уже содержит правильный `text.format.strict=true`.**
Официальный Groq API Reference также описывает это поле. Добавлять ещё один
флаг или повторно менять инструкцию по этой ошибке оснований нет.

Отказ произошёл на стороне провайдера после генерации; SDK не потерял `strict`.
Доступных данных недостаточно, чтобы установить, почему сервер отклонил результат:
неизвестны сгенерированный JSON, нарушенное ограничение и фактический режим
декодера. Нельзя объявлять доказанным, что Responses игнорирует strict, или что
виновато конкретное поле нашей схемы.

Предлагаю следующим отдельным патчем перевести **только Groq** на Chat Completions
с явным strict JSON Schema. Для этой связки документация прямо описывает
constrained decoding и поддержку `openai/gpt-oss-120b`. Это обоснованный выбор
протокола для следующей проверки, а не уже доказанное исправление серверной ошибки.

## Что установлено по обоим прогонам

| Наблюдение | v1, V05 | v3.2, V02 |
| --- | --- | --- |
| HTTP / код Groq | 400 / `json_validate_failed` | 400 / `json_validate_failed` |
| Суффикс request ID в Console | `ap6m` | `2hj0` |
| Время 16 ноября, Europe/Moscow | 20:57:54 | 23:47:02 |
| Input / output tokens из Console | 2460 / 2383 | 2639 / 1942 |
| Запрошенный output limit | 4096 | 4096 |
| API usage в сохранённом run | `null` | `null` |

[Новое наблюдение Console](../evals/diagnostics/groq-strict-2026-11-17/provider-console-observation.json)
хранится отдельно. Совпадают суффиксы request ID, модель и время локальных runs.
Старые `usage=null` и классификация ошибки v1 не переписывались.
Оба известных output count ниже 4096: свидетельств достижения этого лимита нет.
Это не раскрывает конкретную причину schema-отказа и не доказывает её отсутствие
в любой другой конфигурации. Увеличивать лимит на основании этих ошибок не предлагаю.

## Что действительно обещает документация

| | Groq Responses | Groq Chat Completions |
| --- | --- | --- |
| Endpoint | `/openai/v1/responses` | `/openai/v1/chat/completions` |
| Место strict | `text.format.strict` | `response_format.json_schema.strict` |
| Системная инструкция | `instructions` | `messages`: system |
| Входной snapshot | `input` | `messages`: user |
| Лимит генерации | `max_output_tokens` | `max_completion_tokens` |
| Результат | `output` + `status` | `choices[].message` + `finish_reason` |
| Поля usage | `input_tokens`, `output_tokens` | `prompt_tokens`, `completion_tokens` |

В развёрнутом [API Reference](https://console.groq.com/docs/api-reference#responses-create-request-body)
путь **Responses → text → format → JSON schema → properties** содержит `strict`:
необязательный boolean/null, default false, включает строгое соблюдение схемы.
Наше true соответствует этому интерфейсу. Поле не видно в свёрнутом справочнике.

[Responses guide](https://console.groq.com/docs/responses-api) помечает API как beta;
пример JSON Schema не задаёт strict. Пропуск в примере **не означает отсутствия
поддержки**, раз поле описано в reference. Утверждение «у Responses нет strict»
было бы неверным. При этом публичный контракт не показывает фактическую
реализацию декодера для наших двух запросов.

[Structured Outputs guide](https://console.groq.com/docs/structured-outputs)
явно связывает strict mode с ограничением допустимых токенов при генерации;
примеры используют Chat Completions. `gpt-oss-120b` включён в список поддержки.
Требуются все properties в required и закрытые объекты. Это сильнее, чем
генерация с последующей проверкой JSON, хотя обещание документации ещё нужно
проверить на нашей полной схеме. Примеры retry из best-effort раздела не
переносятся в Proofline: один run по-прежнему означает один запрос.

Зафиксированные источники и способ раскрытия reference:
[наблюдение документации](../evals/diagnostics/groq-strict-2026-11-17/provider-documentation-observation.json).

## Что проверено без провайдера

[Wire/schema audit](../evals/diagnostics/groq-strict-2026-11-17/offline-wire-and-schema-audit.json)
использует установленный OpenAI SDK 2.54.0 и поддельный ответ HTTP в памяти.
Реальный API-ключ не нужен и не читался. Проверено:

- Текущий Responses request сериализует `text.format.strict=true`.
- Тот же SDK сериализует Chat request с `response_format.json_schema.strict=true`.
  Для этого не требуется новая библиотека.
- В обоих телах одинаковы текст инструкции, snapshot и каноническая схема:
  SHA256 `9d031d5c4bb85493f85fc50e97990dc7b0a34e6853154aac59636b9275b54eaa`.
- Все 6 object schemas закрыты, все их properties перечислены в required;
  13 `$ref` внутренние, внешних ссылок нет.
- `Evidence.fragments.minItems=1` сохранён.

Это проверка формирования запроса, а не новая генерация и не доказательство
работы constrained decoding. В пробном Chat-теле указан `reasoning_effort=medium`;
в прежнем Responses-теле effort не фиксировался. Нельзя объявлять это измерением
равного эффективного reasoning. Будущий профиль должен явно документировать
выбор effort; изменение его значения не следует смешивать с настройкой качества.

Groq описывает `anyOf`, `$defs` и `$ref`, но найденная страница не устанавливает
полное покрытие ключевых слов, в частности `minItems`. Это **не доказательство
неподдержки**. Ослаблять контракт или удалять обязательность фрагментов по этой
неопределённости нельзя. Если Chat отвергнет неизменённую схему, нужна остановка
и отдельный разбор совместимости.

## Предлагаемый ограниченный патч

1. Добавить Groq Chat Completions adapter на существующем SDK. Factory выбирает
   его по `LLM_PROVIDER=groq`; `LLM_MODEL` и `LLM_API_KEY` сохраняют смысл.
   Новый выбор протокола в настройках пользователю не нужен. OpenAI/DeepSeek
   остаются на Responses, Anthropic — на Messages. Не вводить универсальную
   платформу провайдеров.
2. Перенести тот же INSTRUCTIONS в одно system message, канонический snapshot
   в одно user message, схему — в `response_format.json_schema` с `strict=true`.
   Policy v3.2 и schema v1 не менять. Установить `n=1`, `stream=false`, лимит
   completion 4096, прежние deadline 90 s и `max_retries=0`. Инструменты отключены.
   Параметры Responses `background`/`store`/`truncation` в Chat не передавать.
3. Явно записать `api_protocol=chat_completions` и новую версию профиля запроса.
   Сохранять request/schema/prompt hashes и выбранные параметры. Замена endpoint
   меняет wire request и требует новой записи происхождения, даже с тем же текстом.
4. Разбирать ровно один choice. Успешным кандидатом считать непустой строковый
   `message.content` с нормальным завершением; отдельно обработать refusal,
   `finish_reason=length`, content filter, tool calls и неизвестные формы.
   Не добавлять reasoning в JSON карточки. Перед permissive SDK parsing сохранять
   raw envelope и request ID, как уже сделано для Responses.
5. Сохранять native Chat usage. Если отчёту нужны общие input/output totals,
   явно отображать prompt/completion в них; неизвестные значения оставлять null.
   Reasoning tokens, если сообщены отдельно, не прибавлять повторно к completion.
   Поля подтверждены [типами Groq SDK](https://github.com/groq/groq-python/blob/main/src/groq/types/completion_usage.py).
6. Сохранить безопасную классификацию `json_validate_failed`, расширив закрытый
   allowlist параметров только на известные Chat paths. Не сохранять произвольные
   `message`, error body, `failed_generation` или отражённые значения параметров.
7. Адаптировать будущий archive verifier к протоколу: тело Chat восстанавливать
   через messages/response_format, старые Responses — по их сохранённым шаблонам.
   Архивы v1/v2, их manifests, usage и старые профили не мигрировать.
8. Сохранить локальную полную валидацию JSON/schema, цитат и времени, человеческое
   решение, идемпотентность и один вызов. Никаких fallback на Responses/другую
   модель, автоматического repair или повторной генерации при 400.

Strict ограничивает форму ответа. Он не доказывает правильность отказа клиента,
даты получения, достаточность цитаты или готовность к правилам. Поэтому семантические
ошибки первого прогона и качество v3.2 остаются самостоятельным предметом приёмки.

## Проверки и следующий live-gate

Будущий патч сначала проверяется без inference: wire body/endpoint, неизменность
инструкции и схемы, finish reasons/refusal/некорректный envelope, safe errors с
подставными секретами, usage, один запрос, PostgreSQL failed run/повтор ключа и
protocol-aware archives. Затем обязательные проверки репозитория и отдельного
Compose-экземпляра. Offline не может подтвердить декодер Groq.

Для следующей live-проверки нужно отдельное разрешение и бюджет: новая версия
профиля и каталог результатов, сначала V02/V04, остановка на технической ошибке.
Остатки лимитов остановленных прогонов не используются автоматически. Сейчас
ни патч, ни новая live-попытка не выполнялись.

## Сохранность и границы проверки

Свежая проверка `scripts/verify_live_archive.py` прошла для v1 (25 artifact hashes,
5 попыток) и v2 (8 artifact hashes, 1 попытка). Включая manifests, сохранены 26 и
9 файлов соответственно. Source drift v1 ожидаемо отражает уже выполненный раньше
переход на v3.2; у v2 drift отсутствует. Frozen baseline verifier также прошёл.
Все 21 baseline-тест прошли. В песочнице два теста переноса получили `WinError 5`
на временных каталогах; та же команда с разрешённым файловым доступом прошла без
изменения тестов. [Результаты проверок](../evals/diagnostics/groq-strict-2026-11-17/verification.json).

В этом разборе изменены только документация и новый диагностический каталог.
Команды изменения контейнеров, БД и истории не выполнялись; секрет не читался.
Полная проверка приложения заново не заявляется: runtime-код не менялся.
