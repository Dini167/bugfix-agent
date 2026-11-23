from invoicing.invoice import invoice_total, line_total


def test_line_total_rounds_half_up():
    assert line_total(0.535, 5) == 2.68  # 2.675


def test_invoice_total():
    assert invoice_total([(1.0, 3), (2.5, 2)]) == 8.0
