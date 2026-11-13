# Этап 4: локальная проверка и открытая live-приёмка

Проверено 13 ноября 2026 года. Ветка `codex/milestone-4-live-provider` создана
после fetch непосредственно от `origin/master`
`a956921552ab01de031cc201eb702af2b3241c6a`.

Код адаптера, интеграция в workflow и UI реализованы. **Этап 4 ещё не принят на
реальном API:** доступ, модель, совместимость схемы и качество новой инструкции
требуют отдельного согласованного прогона. Модельных вызовов в этой работе — 0.

## Выполненные проверки

Команды воспроизводятся из корня по [development.md](development.md).
На Windows использован Python 3.12.16 и locked uv environment; Docker собирает
Python 3.12.16 и UI на зафиксированном Node 24.22.3. UI unit/static checks также
выполнены в Node 24.22.3 из стадии `ui-builder`, без сети.

| Проверка | Результат |
| --- | --- |
| `uv sync --locked` | Успешно |
| `pytest -m "not postgres"` | 172 passed |
| `pytest -m postgres` | 60 passed, настоящий PostgreSQL 17.11 |
| Ruff check и format check | Успешно |
| `mypy src` | Успешно, 44 source files |
| Экспорт схемы против `contracts/assembly-output.schema.json` | Совпадает |
| `uv build` | Wheel и sdist собраны |
| Frozen baseline unittest | 21 passed |
| Frozen baseline verifier | 55 artifact hashes, 10 measured input hashes, 3 post-run hashes; 7/9, 72 fragments, 13 fact values; v3.1 unmeasured |
| `npm ci`, format, lint, generated API types, TypeScript/Vite build | Успешно |
| UI unit/component tests | 43 passed |
| Chromium e2e на Compose | 11 passed |
| HTTP replay walkthrough | Исходный V04 заблокирован; новая правка и явное решение сохранены; 0 модельных вызовов |
| Просмотр UI | 1440 и 390 px, без горизонтального overflow |
| `git diff --check` и ancestry | Успешно |

PostgreSQL-тесты использовали отдельный `proofline_test` на свободном 15433;
каждый тест владел своей UUID-схемой. Compose/Chromium/walkthrough работали в
отдельном проекте `proofline-m4-e2e` на 8001 с `LIVE_ENABLED=false`. Основное демо
на 8000 и его операторская история не изменялись. После финальной правки общей
метки демо повторно собраны контейнеры, пройдены все 43 UI-теста и static checks,
браузерная проверка capabilities и HTTP walkthrough.
Созданные для проверки контейнеры, тестовый том и image-теги удалены. В финале
основной Compose-проект `proofline` работает, `/api/v1/ready` на 8000 — `ready`.

SDK-тесты используют in-memory HTTP transport, а не реальный OpenAI endpoint.
Они проверяют тело единственного запроса, strict schema, отсутствие tools,
неполные ответы/отказы, access/rate/server/network errors, общий deadline,
известные usage и request IDs, неизвестную стоимость и отсутствие секретов
в диагностике. PostgreSQL/HTTP-тесты проверяют успешный/неуспешный Live,
повтор ключа, running-конфликт, поздний ответ при правке/решении, восстановление
владельца и заблокированный семантически противоречивый результат.

Саморевью обнаружило NUL-output, который мог обрывать сохранение. Теперь тело
отклоняется до БД, а недопустимый для PostgreSQL raw сохраняется обратимо с
меткой кодировки. Независимое review обнаружило `null`-поля в permissive SDK
response: исправление подтверждено четырьмя транспортными регрессиями и
проверкой сохранения IDs/usage/latency/raw envelope в настоящем PostgreSQL.
Других actionable findings review не выявило.

## Что эти проверки не доказывают

Реальный API-доступ, доступность выбранной модели, принятие схемы провайдером,
качество фактов/хронологии, устойчивость к инструкциям внутри документов и
фактические latency/usage/cost остаются предметом [live-приёмки](live-provider.md#отдельная-live-приёмка).
Стоимость всегда `null`, поскольку датированный тариф не настроен.
Измерение v3.1 не выполнялось; frozen artifacts и записанные ответы не изменялись.
