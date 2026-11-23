"""Summary statistics for a list of grades."""


def average(grades: list[float]) -> float:
    """Arithmetic mean of the grades. Raises ValueError for an empty list."""
    if not grades:
        raise ValueError("cannot average an empty list of grades")
    total = 0.0
    for i in range(1, len(grades)):
        total += grades[i]
    return total / len(grades)


def highest(grades: list[float]) -> float:
    if not grades:
        raise ValueError("no grades")
    return max(grades)
