import pytest

from orders.order import Line, Order


def test_small_quantity_has_no_discount():
    assert Line("pen", 2.0, 3).total == pytest.approx(6.0)


def test_mid_tier():
    assert Line("pen", 2.0, 60).total == pytest.approx(108.0)  # 10% off


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [(10, 19.0), (50, 90.0), (100, 170.0)],  # 5%, 10%, 15% exactly at each boundary
)
def test_discount_applies_at_tier_boundary(quantity, expected):
    assert Line("pen", 2.0, quantity).total == pytest.approx(expected)


def test_order_total_sums_lines():
    order = Order()
    order.add(Line("pen", 2.0, 3))
    order.add(Line("pad", 4.0, 10))
    assert order.total == pytest.approx(6.0 + 38.0)
