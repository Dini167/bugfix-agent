"""Typed access to string-valued configuration."""

from collections.abc import Mapping

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def get_int(config: Mapping[str, str], key: str, default: int) -> int:
    raw = config.get(key)
    return default if raw is None else int(raw)


def get_bool(config: Mapping[str, str], key: str, default: bool) -> bool:
    """Parse a boolean flag.

    Accepts 1/0, true/false, yes/no, on/off (case-insensitive, surrounding
    whitespace ignored). Any other value raises ValueError.
    """
    raw = config.get(key)
    if raw is None:
        return default
    return bool(raw)
