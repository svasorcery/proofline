# Библиотека проверки разбора

Библиотека принимает типизированный ответ и подготовленный сервером контекст источников. Она проверяет форму, дословность и согласованность и возвращает замечания и найденные фрагменты. Полнота смысла остаётся за оператором; сохранение решений и актуальность относительно БД реализованы отдельно в [транзакционном API](api.md).

## Пример

После `uv sync --locked` этот пример можно выполнить через `uv run python`:

```python
from proofline.domain.assembly import AssemblyOutput, Evidence, Fact, FollowUp
from proofline.domain.sources import SourceReference, SourceSnapshot, ValidationContext
from proofline.validation import validate_assembly

source = SourceReference(
    case_id="demo", source_id="notice", kind="document", locator="document:notice"
)
snapshot = SourceSnapshot.from_text(
    source, version_id="notice:v1", text="Cancellation confirmed."
)
context = ValidationContext(
    case_id="demo",
    requested_fact_keys=("cancellation_status",),
    sources=(snapshot,),
    catalog=(source,),
)
output = AssemblyOutput(
    case_id="demo",
    facts=[Fact(
        key="cancellation_status",
        value="confirmed",
        evidence=[Evidence(source_id="notice", fragments=["Cancellation confirmed."])],
    )],
    timeline=[],
    readiness="ready_for_rules",
    follow_up=FollowUp(action="none", locator=None, requirements=[]),
)
result = validate_assembly(output, context)
assert result.state == "needs_operator_review"
assert result.issues == ()
assert result.fragments[0].source_version_id == "notice:v1"
```

JSON от модели разбирается через `AssemblyOutput.model_validate_json(raw_json)`. Неизвестные поля, значения вне словаря и неявное преобразование типов отклоняются. Неверная форма вызывает `pydantic.ValidationError`; проверка согласованности возвращает `ValidationResult` с состоянием `blocked_for_approval` либо `needs_operator_review`. Публичный валидатор повторно проверяет переданные модели, поэтому изменение вложенного списка или обход конструктора не превращает некорректный объект в доверенный.

## Контекст и доказательства

`SourceReference` описывает разрешённый источник текущего обращения. `catalog` — видимые разрешённые ссылки, включая пока не полученные документы. `sources` — только выбранные версии с текстом. Модель не выбирает `version_id`, SHA256 или границы цитат. Сервер не должен включать произвольную ссылку из текста в каталог без проверки принадлежности источника обращению.

`SourceSnapshot.from_text` один раз нормализует CRLF/CR в LF и вычисляет SHA256 UTF-8 текста. Остальные пробелы и Unicode сохраняются. Уже сохранённый снимок с неправильным хешем или ненормализованными переносами строк отклоняется. Контекст проверяет case_id, уникальность источников и ссылок и соответствие метаданных выбранных источников каталогу. Сам объект контекста и снимки неизменяемы.

Каждый `ResolvedFragment` содержит `source_id`, `source_version_id`, `source_sha256`, исходный `fragment`, `[start_cp, end_cp)` в Unicode code points и путь поля `path`. Несмежные фрагменты остаются отдельными. Неоднозначная цитата блокируется; оператору нужно выбрать больше контекста. При отображении в JavaScript потребуется преобразование code points в UTF-16 offsets.

## Время и следующий шаг

Поддерживается расширенная календарная форма ISO8601: `YYYY-MM-DDTHH:MM[:SS[.fraction]]` с обязательным `Z`, `±HH`, `±HHMM` или `±HH:MM`. Десятичный разделитель долей секунд — точка или запятая, точность — до шести знаков. Формат, диапазоны компонентов и представимость UTC проверяются явно; лишняя точность отклоняется, не обрезается. Исходная строка и её смещение не переписываются.

Время должно целиком входить в дословный фрагмент и не обрывать временной токен в полном исходном тексте. Например, `+03` из источника с `+03:30` не подтверждает другое смещение. Порядок и дубликаты проверяются по UTC instant, типу события и роли.

Следующий шаг проверяется по каталогу и типу источника. Повторное чтение уже выбранного снимка не считается новым подтверждением. Для `ask_user` нужны конкретные требования, для `none` ссылка и требования пусты. `render_requirements` формирует текст только выбранных требований; пропущенные условия автоматически не добавляются. `format_usd_minor` отображает целое число центов USD с scale=2 без float-арифметики и без обращения к модели.

## Замечания и ограничения

У каждого `ValidationIssue` есть стабильный машинный `code`, путь поля `path` и русское сообщение `message`. Примеры: `fragment_not_literal`, `ambiguous_fragment`, `time_not_in_fragments`, `repeated_snapshot_lookup`, `acceptance_fact_event_conflict`. Полный словарь расположен в [result.py](../src/proofline/validation/result.py).

Библиотека не доказывает смысл цитаты, полноту событий, достаточность условий или действительность документа. V02 проходит runtime-проверки с пропущенным событием; это остаётся предметом eval и операторского review. V04 блокируется по явному противоречию и повторному чтению снимка. Эталоны не используются для заполнения ответа.

Серверный слой добавляет транзакции, определение stale по текущему входу, историю версий и операторские решения. Получение `needs_operator_review` не является ни одобрением, ни разрешением выполнить предложенное действие. Оно лишь позволяет оператору перейти к явной команде review через API.
