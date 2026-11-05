"""Tái tạo đúng sheet T10.2026 của Stargate từ dữ liệu nghiệp vụ."""
from decimal import Decimal

from app.demo import stargate_t10 as demo_data
from app.models import StatementStatus
from app.services import statements


def test_site_totals_and_grand_total(session, demo):
    st = statements.build_statement(session, demo["partner_id"], "2026-10")
    got = {b["site_name"]: b["totals"]["amount"] for b in st["blocks"]}
    assert got == {k: Decimal(v) for k, v in demo_data.EXPECTED_SITE_TOTALS.items()}
    assert [b["site_name"] for b in st["blocks"]] == [s[0] for s in demo_data.SITES]
    assert st["grand_total"]["amount"] == demo_data.EXPECTED_MONTH_TOTAL
    assert st["cylinder_columns"] == ["PC48"]


def test_row_formula_matches_excel(session, demo):
    st = statements.build_statement(session, demo["partner_id"], "2026-10")
    thuong_tin = next(b for b in st["blocks"] if b["site_name"].startswith("Bếp Thường Tín"))
    first = thuong_tin["rows"][0]
    # Excel: G = 24*48 - 42.4 = 1109.6 ; I = 1109.6 * 47500 = 52.706.000
    assert first["net_kg"] == Decimal("1109.6")
    assert first["amount"] == Decimal("52706000")
    assert first["vat_invoice_no"] == "4638"
    assert first["note"] == "Mượn 08 vỏ pc48"
    notes = [r["text"] for r in thuong_tin["rows"] if r["kind"] == "note"]
    assert notes == ["Cty đầu tư cho bếp 4 bình cứu hỏa 4kg + 4 bình cứu hỏa 8kg", "Cty Đầu tư súng mồi 01 cái"]


def test_debt_section_and_words(session, demo):
    st = statements.build_statement(session, demo["partner_id"], "2026-10")
    assert [(r["label"], r["amount"]) for r in st["prior_debts"]] == [
        ("Công nợ tháng 07/2026 chưa thanh toán", Decimal("224283700")),
        ("Công nợ tháng 08/2026 chưa thanh toán", Decimal("675678850")),
        ("Công nợ tháng 09/2026 chưa thanh toán", Decimal("783130800")),
    ]
    assert st["total_debt"] == Decimal("2879010350")
    assert st["total_paid"] == Decimal("1049962550")
    assert st["closing_debt"] == demo_data.EXPECTED_CLOSING
    assert st["balanced"] is True
    notes = [p["note"] for p in st["payments"]]
    assert notes == [None, "TT xong CN tháng 07/2026", None, None, "TT xong CN tháng 08/2026",
                     "TT 1 phần CN tháng 09/2026"]
    assert st["payments"][0]["label"] == "Ngày 5/10/2026 thanh toán"
    assert st["amount_in_words"] == "Một tỷ, tám trăm hai mươi chín triệu, bốn mươi bảy ngàn, tám trăm đồng chẵn."
    assert st["period_text"] == "Từ ngày 01/10/2026 đến hết ngày 31/10/2026"
    assert st["sign_text"] == "Hà Nội, ngày 31 tháng 10 năm 2026"
    assert st["sheet_name"] == "T10.2026"


def test_loans_text(session, demo):
    st = statements.build_statement(session, demo["partner_id"], "2026-10")
    assert st["opening_loans_text"] == "Tổng mượn 157 vỏ PC48 + 13 bếp CN"
    assert st["closing_loans_text"] == "Tổng mượn 184 vỏ PC48 + 13 bếp CN"
    tuan_chau = next(b for b in st["blocks"] if "Tuần Châu" in b["site_name"])
    assert tuan_chau["site_label"] == "Bếp Tuần Châu - Quảng Ninh\nMượn 30 vỏ PC48 - ko cược + 13 bếp CN"
    hung_yen = next(b for b in st["blocks"] if "Hưng Yên" in b["site_name"])
    assert hung_yen["rows"] == [] and hung_yen["loan_text"] == "Mượn 02 vỏ PC48 - ko cược"


def test_next_month_carries_debt(session, demo):
    st = statements.build_statement(session, demo["partner_id"], "2026-11")
    assert [(r["period"], r["amount"]) for r in st["prior_debts"]] == [
        ("2026-09", Decimal("633130800")), ("2026-10", Decimal("1195917000"))]
    assert st["closing_debt"] == demo_data.EXPECTED_CLOSING
    assert st["opening_loans_text"] == "Tổng mượn 184 vỏ PC48 + 13 bếp CN"


def test_save_confirm_lock(session, demo):
    from datetime import date

    import pytest

    from app.errors import BusinessError, PeriodLockedError
    from app.services import sales

    saved = statements.save_statement(session, demo["partner_id"], "2026-10")
    assert saved.closing_debt == demo_data.EXPECTED_CLOSING
    statements.set_status(session, saved.id, StatementStatus.CONFIRMED)
    with pytest.raises(PeriodLockedError):
        sales.create_slip(session, sales.SlipInput(partner_id=demo["partner_id"], slip_date=date(2026, 10, 31),
                                                   items=[sales.ItemInput(sku="PC48", qty=1)]))
    with pytest.raises(BusinessError):
        statements.save_statement(session, demo["partner_id"], "2026-10")
    # tháng 11 vẫn mở
    res = sales.create_slip(session, sales.SlipInput(partner_id=demo["partner_id"], slip_date=date(2026, 11, 2),
                                                     items=[sales.ItemInput(sku="PC48", qty=1)]))
    assert res.slip.total == Decimal("2280000")
    statements.set_status(session, saved.id, StatementStatus.SENT)
    sales.create_slip(session, sales.SlipInput(partner_id=demo["partner_id"], slip_date=date(2026, 10, 31),
                                               items=[sales.ItemInput(sku="PC48", qty=1)]))
