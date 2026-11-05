from datetime import date
from decimal import Decimal

import pytest

from app.errors import BusinessError
from app.models import DebtSide, FundType, VoucherType
from app.services import cashbook, debts, reports, sales


def test_fifo_allocation_matches_customer_notes(session, demo):
    allocs = debts.allocations_by_period(session, demo["partner_id"], DebtSide.RECEIVABLE)
    assert allocs == {"2026-07": Decimal("224283700"), "2026-08": Decimal("675678850"),
                      "2026-09": Decimal("150000000")}
    out = debts.outstanding_by_period(session, demo["partner_id"], DebtSide.RECEIVABLE, as_of=date(2026, 10, 31))
    assert out == {"2026-09": Decimal("633130800"), "2026-10": Decimal("1195917000")}


def test_manual_reallocation_and_validation(session, demo):
    v = cashbook.create_voucher(session, voucher_type=VoucherType.RECEIPT, voucher_date=date(2026, 11, 3),
                                amount=100000000, partner_id=demo["partner_id"],
                                allocations=[("2026-10", 100000000)])
    out = debts.outstanding_by_period(session, demo["partner_id"], DebtSide.RECEIVABLE)
    assert out["2026-10"] == Decimal("1095917000")
    with pytest.raises(BusinessError):
        cashbook.reallocate_voucher(session, v.id, [("2026-09", 1)])
    cashbook.reallocate_voucher(session, v.id, [("2026-09", 60000000), ("2026-10", 40000000)])
    out = debts.outstanding_by_period(session, demo["partner_id"], DebtSide.RECEIVABLE)
    assert out["2026-09"] == Decimal("573130800")
    cashbook.cancel_voucher(session, v.id, "nhập trùng")
    assert debts.balance(session, demo["partner_id"]) == Decimal("1829047800")


def test_refund_increases_debt(session, demo):
    cashbook.create_voucher(session, voucher_type=VoucherType.PAYMENT, voucher_date=date(2026, 11, 5),
                            amount=1000000, partner_id=demo["partner_id"], description="Hoàn tiền thừa")
    assert debts.balance(session, demo["partner_id"]) == Decimal("1830047800")


def test_cash_book(session, demo):
    cashbook.set_fund_opening(session, FundType.BANK, date(2026, 10, 1), 50000000)
    cashbook.create_voucher(session, voucher_type=VoucherType.PAYMENT, voucher_date=date(2026, 10, 30),
                            amount=20000000, fund_type=FundType.BANK, category="Chi phí vận chuyển",
                            description="Thuê xe tháng 10")
    book = cashbook.cash_book(session, FundType.BANK, date(2026, 10, 1), date(2026, 10, 31))
    assert book["opening"] == Decimal("50000000")
    assert book["total_receipt"] == Decimal("1049962550")
    assert book["total_payment"] == Decimal("20000000")
    assert book["closing"] == Decimal("1079962550")


def test_pay_on_slip_creates_receipt(session, demo):
    res = sales.create_slip(session, sales.SlipInput(
        partner_id=demo["partner_id"], slip_date=date(2026, 11, 2), items=[sales.ItemInput(sku="PC48", qty=2)],
        gas_residual_kg=6, paid_amount=4275000, paid_fund=FundType.CASH))
    assert res.slip.total == Decimal("4275000")          # (96 - 6) * 47.500
    assert res.voucher_id is not None
    out = debts.outstanding_by_period(session, demo["partner_id"], DebtSide.RECEIVABLE)
    assert "2026-11" not in out


def test_summary_detail_overview(session, demo):
    rows = debts.debt_summary(session, DebtSide.RECEIVABLE, date(2026, 10, 1), date(2026, 10, 31))
    assert len(rows) == 1
    r = rows[0]
    assert (r.opening, r.increase, r.decrease, r.closing) == (
        Decimal("1683093350"), Decimal("1195917000"), Decimal("1049962550"), Decimal("1829047800"))
    detail = debts.debt_detail(session, demo["partner_id"], DebtSide.RECEIVABLE, date(2026, 10, 1), date(2026, 10, 31))
    assert detail["closing"] == Decimal("1829047800")
    ov = reports.customer_overview(session, demo["partner_id"], period="2026-10", months=3)
    assert [m["period"] for m in ov["monthly"]] == ["2026-08", "2026-09", "2026-10"]
    assert ov["monthly"][-1]["kg"] == Decimal("25177.2")
    assert ov["monthly"][-1]["closing_debt"] == Decimal("1829047800")
    assert ov["total_loans"] == "Mượn 184 vỏ PC48 + 13 bếp CN"
    dash = reports.dashboard(session, date(2026, 10, 1), date(2026, 10, 31))
    assert dash["revenue"] == Decimal("1195917000")
    assert dash["bank_received"] == Decimal("1049962550")
