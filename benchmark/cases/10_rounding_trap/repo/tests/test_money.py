import pytest

from invoicing.money import format_money, round_money


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2.675, 2.68),
        (1.005, 1.01),
        (0.125, 0.13),
        (-2.675, -2.68),
        (2.6749, 2.67),
        (-0.004, -0.0),
        (10.0, 10.0),
    ],
)
def test_round_money_half_away_from_zero(value, expected):
    assert round_money(value) == expected


def test_format_money():
    assert format_money(1234.5) == "$1,234.50"
    assert format_money(-2.675) == "-$2.68"
