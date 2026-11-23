import pytest

from metrics.series import mean, spread


def test_mean():
    assert mean([2.0, 4.0, 6.0]) == pytest.approx(4.0)


def test_mean_empty_raises_value_error():
    with pytest.raises(ValueError, match="empty series"):
        mean([])


def test_spread():
    assert spread([3.0, 9.0, 4.0]) == pytest.approx(6.0)


def test_spread_empty():
    with pytest.raises(ValueError, match="empty series"):
        spread([])
