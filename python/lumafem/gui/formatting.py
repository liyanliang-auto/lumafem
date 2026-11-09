"""Pure presentation of physical values; callers retain their numeric sources."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import math

__all__ = ["scientific", "full_precision"]


def _format(value: object, precision: int, unavailable: str) -> str:
    """Format a supported finite number without parsing or changing its value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return unavailable
    # Normalize actual signed zero only; tiny negative values keep their sign.
    if value == 0:
        return format(0.0, f".{precision}E")
    if isinstance(value, float):
        if not math.isfinite(value):
            return unavailable
        text = format(value, f".{precision}E")
    else:
        # Decimal preserves integer precision, including integers beyond float's
        # range. Pin rounding locally rather than depend on a session context.
        with localcontext() as context:
            context.rounding = ROUND_HALF_EVEN
            text = format(Decimal(value), f".{precision}E")
    mantissa, exponent = text.split("E")
    return f"{mantissa}E{int(exponent):+03d}"


def scientific(value: object, *, precision: int = 3, unavailable: str = "--") -> str:
    """Return finite physical int/float values in uppercase scientific notation.

    Booleans, unsupported objects and nonfinite values return ``unavailable``.
    Precision is a nonnegative number of mantissa decimals; invalid precision
    raises ValueError. This function does not classify engineering quantities.
    """
    if isinstance(precision, bool) or not isinstance(precision, int) or precision < 0:
        raise ValueError("precision must be a nonnegative integer")
    return _format(value, precision, unavailable)


def full_precision(value: object, *, unavailable: str = "--") -> str:
    """Return a physical value with 17 mantissa decimals for read-only details.

    Unsupported or nonfinite values use the caller's unavailable token. The
    returned text is presentation only and must not replace the source value.
    """
    return _format(value, 17, unavailable)
