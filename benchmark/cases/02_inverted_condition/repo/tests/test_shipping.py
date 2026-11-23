from shop.shipping import (
    STANDARD_FEE,
    delivery_days,
    qualifies_for_free_shipping,
    shipping_fee,
)


def test_large_order_ships_free():
    assert qualifies_for_free_shipping(350.0)


def test_threshold_is_inclusive():
    assert qualifies_for_free_shipping(200.0)


def test_small_order_pays_fee():
    assert shipping_fee(50.0) == STANDARD_FEE


def test_large_order_fee_is_zero():
    assert shipping_fee(500.0) == 0.0


def test_delivery_days():
    assert delivery_days(express=True) == 1
    assert delivery_days(express=False) == 4
