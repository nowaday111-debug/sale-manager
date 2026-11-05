"""Đọc số tiền VNĐ thành chữ tiếng Việt.

style="excel"    : giống hệt công thức trong file Bảng kê hiện tại
                   ("Một tỷ, tám trăm hai mươi chín triệu, bốn mươi bảy ngàn, tám trăm đồng chẵn.")
                   - không đọc "không trăm" cho nhóm có hàng trăm = 0
                   - đọc "lẻ" khi hàng trăm ≠ 0, hàng chục = 0, hàng đơn vị ≠ 0
                   - dùng "ngàn", có dấu phẩy sau tỷ/triệu/ngàn khi phía sau còn số
style="standard" : cách đọc chuẩn kế toán
                   ("Một tỷ tám trăm hai mươi chín triệu không trăm bốn mươi bảy nghìn tám trăm đồng chẵn")
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]


def _read_group(a: int, b: int, c: int, *, full: bool, style: str) -> list[str]:
    """Đọc nhóm 3 chữ số abc. full=True: nhóm không đứng đầu (đọc 'không trăm' ở style chuẩn)."""
    words: list[str] = []
    if a == 0 and b == 0 and c == 0:
        return words
    # hàng trăm
    if a != 0:
        words += [DIGITS[a], "trăm"]
    elif full and style == "standard":
        words += ["không", "trăm"]
    # hàng chục
    if b == 0:
        if c != 0 and (a != 0 or (full and style == "standard")):
            words.append("lẻ")
    elif b == 1:
        words.append("mười")
    else:
        words += [DIGITS[b], "mươi"]
    # hàng đơn vị
    if c != 0:
        if c == 1:
            words.append("mốt" if b > 1 else "một")
        elif c == 5:
            words.append("lăm" if b != 0 else "năm")
        else:
            words.append(DIGITS[c])
    return words


def so_thanh_chu(amount, style: str = "excel") -> str:
    value = Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    negative = value < 0
    n = int(abs(value))
    if n == 0:
        text = "không đồng." if style == "excel" else "không đồng"
        return text[0].upper() + text[1:]

    # Tách nhóm 3 số từ phải sang: đơn vị, ngàn, triệu, tỷ, ngàn tỷ, triệu tỷ...
    groups: list[int] = []
    while n > 0:
        groups.append(n % 1000)
        n //= 1000
    thousand = "ngàn" if style == "excel" else "nghìn"
    base_units = ["", thousand, "triệu"]

    def unit_for(idx: int) -> str:
        # idx 3 = tỷ, 4 = ngàn tỷ, 5 = triệu tỷ, 6 = tỷ tỷ ...
        if idx < 3:
            return base_units[idx]
        rest = idx % 3
        prefix = base_units[rest]
        return (prefix + " tỷ").strip() if rest else " ".join(["tỷ"] * (idx // 3))

    parts: list[str] = []
    top = len(groups) - 1
    for idx in range(top, -1, -1):
        g = groups[idx]
        if g == 0:
            continue
        a, b, c = g // 100, (g // 10) % 10, g % 10
        words = _read_group(a, b, c, full=idx != top, style=style)
        unit = unit_for(idx)
        if unit:
            lower_nonzero = any(groups[j] for j in range(idx))
            words.append(unit + ("," if style == "excel" and lower_nonzero else ""))
        parts.append(" ".join(words))
    text = " ".join(parts) + (" đồng chẵn." if style == "excel" else " đồng chẵn")
    if negative:
        text = "âm " + text
    text = " ".join(text.split())
    return text[0].upper() + text[1:]
