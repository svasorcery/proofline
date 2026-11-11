"""Deterministic display values; no inferred requirements or model-generated sums."""

from collections.abc import Sequence
from dataclasses import dataclass

from proofline.domain.assembly import RequirementKey

_REQUIREMENT_TEXT: dict[RequirementKey, str] = {
    "original_customer_message": (
        "Нужен оригинал сообщения с недвусмысленным отказом клиента от бронирования."
    ),
    "delivery_receipt": (
        "Нужна квитанция или заголовок получения этого сообщения поставщиком либо сервисом "
        "с датой и временем."
    ),
    "quiet_room": "Подтвердите у отеля, что именно предложенный номер будет тихим.",
    "courtyard_windows": (
        "Подтвердите у отеля, что окна именно предложенного номера выходят во двор."
    ),
    "original_acceptance_reply": (
        "Нужен оригинал ответа клиента на конкретное предложение альтернативы, "
        "чтобы проверить содержание согласия."
    ),
    "bank_settlement_document": (
        "Нужен банковский документ о фактическом зачислении возврата, "
        "связанный с исходной оплатой или возвратной трассой."
    ),
}


@dataclass(frozen=True, slots=True)
class RequirementPrompt:
    key: RequirementKey
    text: str


def render_requirements(keys: Sequence[RequirementKey]) -> tuple[RequirementPrompt, ...]:
    return tuple(RequirementPrompt(key=key, text=_REQUIREMENT_TEXT[key]) for key in keys)


def format_usd_minor(amount_minor: int) -> str:
    """Format integer USD cents exactly, including amounts beyond float precision."""
    if type(amount_minor) is not int:
        raise TypeError("USD minor units must be an integer")
    whole, cents = divmod(abs(amount_minor), 100)
    sign = "-" if amount_minor < 0 else ""
    return f"{sign}{whole}.{cents:02d} USD"
