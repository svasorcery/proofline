"""Structured blocking issues and source-bound evidence; never an approval decision."""

from dataclasses import dataclass
from typing import Literal

type FieldPath = tuple[str | int, ...]
type IssueCode = Literal[
    "case_id_mismatch",
    "fact_keys_mismatch",
    "invalid_fact_value",
    "known_fact_without_evidence",
    "source_not_visible",
    "empty_fragment",
    "fragment_not_literal",
    "ambiguous_fragment",
    "wrong_time_role",
    "invalid_or_naive_time",
    "time_not_in_fragments",
    "timeline_not_sorted",
    "duplicate_events",
    "duplicate_requirements",
    "unexpected_followup_payload",
    "empty_requirements",
    "ask_user_has_locator",
    "locator_not_authorized",
    "wrong_lookup_kind",
    "repeated_snapshot_lookup",
    "premature_readiness",
    "missing_followup",
    "acceptance_fact_event_conflict",
]

_MESSAGES: dict[IssueCode, str] = {
    "case_id_mismatch": "Разбор относится к другому обращению.",
    "fact_keys_mismatch": (
        "Укажите все запрошенные факты ровно один раз, без дополнительных фактов."
    ),
    "invalid_fact_value": "Это значение недопустимо для выбранного факта.",
    "known_fact_without_evidence": "Добавьте доказательства установленного факта.",
    "source_not_visible": "Источник не включён в этот снимок анализа.",
    "empty_fragment": "Выберите непустой фрагмент источника.",
    "fragment_not_literal": "Фрагмент не найден в этой версии источника.",
    "ambiguous_fragment": "Фрагмент встречается несколько раз; выберите более длинную цитату.",
    "wrong_time_role": "Роль времени не соответствует типу события.",
    "invalid_or_naive_time": "Укажите корректное время ISO8601 с часовым смещением.",
    "time_not_in_fragments": "Время события должно дословно присутствовать в его доказательствах.",
    "timeline_not_sorted": "Расположите события по времени с учётом часовых смещений.",
    "duplicate_events": "Удалите повтор одного события с тем же временем и ролью.",
    "duplicate_requirements": "Укажите каждое требование к подтверждениям один раз.",
    "unexpected_followup_payload": (
        "При отсутствии следующего действия ссылка и требования должны быть пустыми."
    ),
    "empty_requirements": "Укажите конкретные недостающие подтверждения.",
    "ask_user_has_locator": "Вопрос пользователю не должен содержать ссылку для чтения источника.",
    "locator_not_authorized": "Выберите доступную ссылку из каталога этого обращения.",
    "wrong_lookup_kind": "Выбранный способ получения не соответствует типу источника.",
    "repeated_snapshot_lookup": "Повторное чтение этого снимка не даст нового подтверждения.",
    "premature_readiness": (
        "Неподтверждённые факты или нерешённые условия исключают готовность данных."
    ),
    "missing_followup": "Для запроса доказательств нужен конкретный следующий шаг.",
    "acceptance_fact_event_conflict": (
        "Принятие альтернативы требует подтверждённого безусловного согласия."
    ),
}


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: IssueCode
    path: FieldPath

    @property
    def message(self) -> str:
        return _MESSAGES[self.code]


@dataclass(frozen=True, slots=True)
class ResolvedFragment:
    source_id: str
    source_version_id: str
    source_sha256: str
    start_cp: int
    end_cp: int
    fragment: str
    path: FieldPath


@dataclass(frozen=True, slots=True)
class ValidationResult:
    issues: tuple[ValidationIssue, ...] = ()
    fragments: tuple[ResolvedFragment, ...] = ()

    @property
    def state(self) -> Literal["blocked_for_approval", "needs_operator_review"]:
        return "blocked_for_approval" if self.issues else "needs_operator_review"
