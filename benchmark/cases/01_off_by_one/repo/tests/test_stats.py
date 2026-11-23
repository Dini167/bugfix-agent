import pytest

from gradebook.stats import average, highest


def test_average_of_several_grades():
    assert average([90, 80, 85]) == pytest.approx(85.0)


def test_average_of_single_grade():
    assert average([72]) == pytest.approx(72.0)


def test_average_empty_raises():
    with pytest.raises(ValueError):
        average([])


def test_highest():
    assert highest([60, 99, 75]) == 99
