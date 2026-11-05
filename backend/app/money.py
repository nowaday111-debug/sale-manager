"""Tiện ích Decimal cho tiền tệ / khối lượng. Không bao giờ dùng float."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")
ONE = Decimal("1")
_MONEY_Q = Decimal("1")        # tiền VNĐ làm tròn tới đồng
_KG_Q = Decimal("0.001")       # khối lượng tới gam
_PRICE_Q = Decimal("0.0001")   # đơn giá giữ 4 số lẻ


def D(value: Any) -> Decimal:
    """Chuyển về Decimal an toàn. float được chuyển qua str để tránh sai số nhị phân."""
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("bool không phải số")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    text = str(value).strip().replace(" ", "")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Không đọc được số: {value!r}") from exc


def round_money(value: Any) -> Decimal:
    return D(value).quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def round_kg(value: Any) -> Decimal:
    return D(value).quantize(_KG_Q, rounding=ROUND_HALF_UP)


def round_price(value: Any) -> Decimal:
    return D(value).quantize(_PRICE_Q, rounding=ROUND_HALF_UP)


def dsum(values) -> Decimal:
    total = ZERO
    for v in values:
        total += D(v)
    return total


def normalize(value: Decimal) -> Decimal:
    """Bỏ số 0 thừa sau dấu phẩy (47500.0000 -> 47500) nhưng giữ dạng số nguyên không mũ."""
    value = D(value)
    if value == value.to_integral_value():
        return value.quantize(ONE)
    return value.normalize()


def split_vat(total_incl_vat: Any, vat_rate: Any) -> tuple[Decimal, Decimal]:
    """Tách tiền đã gồm VAT -> (tiền trước thuế, tiền thuế). vat_rate dạng 0.08."""
    total = D(total_incl_vat)
    rate = D(vat_rate)
    before = round_money(total / (ONE + rate))
    return before, total - before
