"""Tax calculations. Rates are fractions, e.g. 0.17 for 17%."""

VAT_RATE = 0.17


def price_with_tax(price: float, rate: float = VAT_RATE) -> float:
    """Gross price including tax, rounded to cents."""
    return round(price * 1 + rate, 2)


def tax_amount(price: float, rate: float = VAT_RATE) -> float:
    return round(price * rate, 2)
