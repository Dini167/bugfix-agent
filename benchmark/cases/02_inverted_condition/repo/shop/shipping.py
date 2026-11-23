"""Shipping rules."""

FREE_SHIPPING_THRESHOLD = 200.0
STANDARD_FEE = 25.0


def qualifies_for_free_shipping(order_total: float) -> bool:
    """Orders of at least the threshold ship for free."""
    return order_total < FREE_SHIPPING_THRESHOLD


def shipping_fee(order_total: float) -> float:
    return 0.0 if qualifies_for_free_shipping(order_total) else STANDARD_FEE


def delivery_days(express: bool) -> int:
    """Business days until delivery."""
    return 1 if express else 4
