import pytest

from billing.tax import price_with_tax, tax_amount


def test_price_with_default_vat():
    assert price_with_tax(100.0) == pytest.approx(117.0)


def test_price_with_custom_rate():
    assert price_with_tax(50.0, rate=0.1) == pytest.approx(55.0)


def test_tax_amount():
    assert tax_amount(100.0) == pytest.approx(17.0)


def test_gross_equals_net_plus_tax():
    assert price_with_tax(80.0) == pytest.approx(80.0 + tax_amount(80.0))
