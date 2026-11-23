"""Money helpers. Amounts are rounded half away from zero, to the cent."""


def round_money(value: float) -> float:
    """Round to 2 decimals, halves away from zero (2.675 -> 2.68, -2.675 -> -2.68)."""
    return round(value, 2)


def format_money(value: float) -> str:
    amount = round_money(value)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"
