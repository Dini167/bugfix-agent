import pytest

from settings.env import get_bool, get_int


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on"])
def test_truthy_values(raw):
    assert get_bool({"DEBUG": raw}, "DEBUG", default=False) is True


@pytest.mark.parametrize("raw", ["0", "false", "False", "no", " off"])
def test_falsy_values(raw):
    assert get_bool({"DEBUG": raw}, "DEBUG", default=True) is False


def test_missing_uses_default():
    assert get_bool({}, "DEBUG", default=True) is True


def test_invalid_value_raises():
    with pytest.raises(ValueError):
        get_bool({"DEBUG": "maybe"}, "DEBUG", default=False)


def test_get_int():
    assert get_int({"PORT": "8080"}, "PORT", default=80) == 8080
