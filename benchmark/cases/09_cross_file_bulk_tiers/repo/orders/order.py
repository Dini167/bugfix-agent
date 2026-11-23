"""Order totals."""

from dataclasses import dataclass, field

from orders.pricing import discounted_unit_price


@dataclass(frozen=True)
class Line:
    sku: str
    unit_price: float
    quantity: int

    @property
    def total(self) -> float:
        return round(discounted_unit_price(self.unit_price, self.quantity) * self.quantity, 2)


@dataclass
class Order:
    lines: list[Line] = field(default_factory=list)

    def add(self, line: Line) -> None:
        self.lines.append(line)

    @property
    def total(self) -> float:
        return round(sum(line.total for line in self.lines), 2)
