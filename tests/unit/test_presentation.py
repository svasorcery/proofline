import pytest

from proofline.presentation import format_usd_minor, render_requirements


@pytest.mark.parametrize(
    "minor,expected",
    [
        (12000, "120.00 USD"),
        (1, "0.01 USD"),
        (0, "0.00 USD"),
        (-123, "-1.23 USD"),
        (10**20 + 1, "1000000000000000000.01 USD"),
    ],
)
def test_money_formatting_is_exact_without_float_rounding(minor: int, expected: str) -> None:
    assert format_usd_minor(minor) == expected


@pytest.mark.parametrize("value", [True, 120.0, "12000"])
def test_money_rejects_implicit_numeric_coercion(value: object) -> None:
    with pytest.raises(TypeError):
        format_usd_minor(value)  # type: ignore[arg-type]


def test_selected_requirements_are_rendered_independently_without_filling_gaps() -> None:
    prompts = render_requirements(["quiet_room", "courtyard_windows"])
    assert [prompt.key for prompt in prompts] == ["quiet_room", "courtyard_windows"]
    assert prompts[0].text == "Подтвердите у отеля, что именно предложенный номер будет тихим."
    assert (
        prompts[1].text
        == "Подтвердите у отеля, что окна именно предложенного номера выходят во двор."
    )
    assert len(render_requirements(["quiet_room"])) == 1
    assert render_requirements([]) == ()
