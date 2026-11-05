import pytest

from app.utils.so_thanh_chu import so_thanh_chu

# Các kết quả style="excel" đã đối chiếu 58/58 số với công thức trong file Bảng kê (tính bằng LibreOffice).
EXCEL_CASES = [
    (0, "Không đồng."),
    (5, "Năm đồng chẵn."),
    (15, "Mười lăm đồng chẵn."),
    (21, "Hai mươi mốt đồng chẵn."),
    (105, "Một trăm lẻ năm đồng chẵn."),
    (1005, "Một ngàn, năm đồng chẵn."),
    (1005000, "Một triệu, năm ngàn đồng chẵn."),
    (1010101, "Một triệu, mười ngàn, một trăm lẻ một đồng chẵn."),
    (101000000, "Một trăm lẻ một triệu đồng chẵn."),
    (1000000001, "Một tỷ, một đồng chẵn."),
    (1829047800, "Một tỷ, tám trăm hai mươi chín triệu, bốn mươi bảy ngàn, tám trăm đồng chẵn."),
    (999999999999, "Chín trăm chín mươi chín tỷ, chín trăm chín mươi chín triệu, chín trăm chín mươi chín ngàn, "
                   "chín trăm chín mươi chín đồng chẵn."),
]


@pytest.mark.parametrize("n,text", EXCEL_CASES)
def test_excel_style(n, text):
    assert so_thanh_chu(n) == text


def test_standard_style():
    assert so_thanh_chu(15250000, style="standard") == "Mười lăm triệu hai trăm năm mươi nghìn đồng chẵn"
    assert so_thanh_chu(1005000, style="standard") == "Một triệu không trăm lẻ năm nghìn đồng chẵn"


def test_beyond_trillion_and_rounding():
    assert so_thanh_chu("1000000000000") == "Một ngàn tỷ đồng chẵn."
    assert so_thanh_chu("1500.5") == "Một ngàn, năm trăm lẻ một đồng chẵn."
