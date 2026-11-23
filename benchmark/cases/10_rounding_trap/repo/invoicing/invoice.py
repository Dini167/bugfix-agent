"""Invoice totals."""

from invoicing.money import round_money


def line_total(unit_price: float, quantity: float) -> float:
    return round_money(unit_price * quantity)


def invoice_total(lines: list[tuple[float, float]]) -> float:
    return round_money(sum(line_total(price, qty) for price, qty in lines))
