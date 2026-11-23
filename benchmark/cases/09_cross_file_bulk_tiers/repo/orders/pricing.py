"""Volume pricing rules."""

# (minimum quantity, discount percent), highest tier first.
BULK_TIERS: list[tuple[int, int]] = [(100, 15), (50, 10), (10, 5)]


def bulk_discount_percent(quantity: int) -> int:
    """Discount for buying at least a tier's minimum quantity."""
    for minimum, percent in BULK_TIERS:
        if quantity > minimum:
            return percent
    return 0


def discounted_unit_price(unit_price: float, quantity: int) -> float:
    return unit_price * (100 - bulk_discount_percent(quantity)) / 100
