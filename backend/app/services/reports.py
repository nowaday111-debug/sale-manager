"""Dashboard tổng quan và trang tổng quan từng khách hàng."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CashVoucher,
    DebtSide,
    DeliverySlip,
    DocStatus,
    FundType,
    PartnerType,
    Partner,
    ProductKind,
    VoucherType,
)
from ..money import ZERO, D
from . import assets as asset_svc
from . import debts, inventory, masterdata, sales
from .statements import period_bounds


def _shift_period(period: str, months: int) -> str:
    y, m = (int(x) for x in period.split("-"))
    idx = y * 12 + (m - 1) + months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _slip_kg(slip: DeliverySlip) -> Decimal:
    gross = sum((D(i.qty) * D(i.capacity_kg) for i in slip.items
                 if i.product.kind == ProductKind.GAS_CYLINDER and i.capacity_kg), ZERO)
    return slip.sign * (gross - D(slip.gas_residual_kg))


def dashboard(session: Session, date_from: date, date_to: date) -> dict:
    """4 chỉ số: Doanh thu, Lãi gộp, Thực thu (TM + CK), Công nợ phát sinh; cảnh báo hàng sắp hết."""
    profit = sales.gross_profit(session, date_from=date_from, date_to=date_to)
    receipts = {FundType.CASH: ZERO, FundType.BANK: ZERO}
    for v in session.scalars(select(CashVoucher).where(
            CashVoucher.voucher_type == VoucherType.RECEIPT, CashVoucher.status == DocStatus.POSTED,
            CashVoucher.voucher_date >= date_from, CashVoucher.voucher_date <= date_to)):
        receipts[v.fund_type] += D(v.amount)
    customer_receipts = ZERO
    for v in session.scalars(select(CashVoucher).where(
            CashVoucher.voucher_type == VoucherType.RECEIPT, CashVoucher.status == DocStatus.POSTED,
            CashVoucher.partner_id.is_not(None),
            CashVoucher.voucher_date >= date_from, CashVoucher.voucher_date <= date_to)):
        customer_receipts += sum((D(a.amount) for a in v.allocations if a.side == DebtSide.RECEIVABLE), ZERO)
    top = defaultdict(lambda: ZERO)
    for s in sales.list_slips(session, date_from=date_from, date_to=date_to, status=DocStatus.POSTED):
        top[s.partner_id] += s.sign * D(s.total)
    top_customers = []
    for pid, amt in sorted(top.items(), key=lambda kv: kv[1], reverse=True)[:10]:
        p = session.get(Partner, pid)
        top_customers.append({"partner_id": pid, "name": p.name, "amount": amt,
                              "balance": debts.balance(session, pid, DebtSide.RECEIVABLE, as_of=date_to)})
    return {
        "date_from": date_from, "date_to": date_to,
        "revenue": profit["revenue"], "revenue_before_tax": profit["revenue_before_tax"],
        "cost": profit["cost"], "gross_profit": profit["gross_profit"],
        "cash_received": receipts[FundType.CASH], "bank_received": receipts[FundType.BANK],
        "total_received": receipts[FundType.CASH] + receipts[FundType.BANK],
        "debt_increase": profit["revenue"] - customer_receipts,
        "low_stock": [{"sku": p.sku, "name": p.name, "current_stock": D(p.current_stock), "min_stock": D(p.min_stock)}
                      for p in inventory.low_stock(session)],
        "top_customers": top_customers,
    }


def customer_overview(session: Session, partner_id: int, *, period: str, months: int = 12) -> dict:
    """Tổng quan 1 khách: sản lượng, doanh thu, đã trả, dư nợ theo tháng; từng bếp; vỏ đang mượn."""
    partner = masterdata.get_partner(session, partner_id)
    first_period = _shift_period(period, -(months - 1))
    start, _ = period_bounds(first_period)
    _, end = period_bounds(period)

    slips = sales.list_slips(session, partner_id=partner_id, date_from=start, date_to=end, status=DocStatus.POSTED)
    by_month = defaultdict(lambda: {"kg": ZERO, "amount": ZERO, "slips": 0})
    by_site = defaultdict(lambda: {"kg": ZERO, "amount": ZERO, "slips": 0, "last_date": None})
    for s in slips:
        kg = _slip_kg(s)
        m = by_month[s.period]
        m["kg"] += kg
        m["amount"] += s.sign * D(s.total)
        m["slips"] += 1
        st = by_site[s.site_id]
        st["kg"] += kg
        st["amount"] += s.sign * D(s.total)
        st["slips"] += 1
        st["last_date"] = max(st["last_date"], s.slip_date) if st["last_date"] else s.slip_date

    paid_by_month = defaultdict(lambda: ZERO)
    for v in session.scalars(select(CashVoucher).where(
            CashVoucher.partner_id == partner_id, CashVoucher.status == DocStatus.POSTED,
            CashVoucher.voucher_date >= start, CashVoucher.voucher_date <= end)):
        paid_by_month[f"{v.voucher_date.year:04d}-{v.voucher_date.month:02d}"] += sum(
            (D(a.amount) for a in v.allocations if a.side == DebtSide.RECEIVABLE), ZERO)

    series = []
    p = first_period
    for _ in range(months):
        _, p_end = period_bounds(p)
        series.append({"period": p, "kg": by_month[p]["kg"], "amount": by_month[p]["amount"],
                       "slips": by_month[p]["slips"], "paid": paid_by_month[p],
                       "closing_debt": debts.balance(session, partner_id, DebtSide.RECEIVABLE, as_of=p_end)})
        p = _shift_period(p, 1)

    loans = asset_svc.loan_balances(session, partner_id, as_of=end)
    sites = []
    for site in partner.sites:
        info = by_site.get(site.id, {"kg": ZERO, "amount": ZERO, "slips": 0, "last_date": None})
        sites.append({"site_id": site.id, "name": site.name, "kg": info["kg"], "amount": info["amount"],
                      "slips": info["slips"], "last_delivery": info["last_date"],
                      "loans": asset_svc.loan_text(loans.get(site.id, {})), "is_active": site.is_active})
    outstanding = debts.outstanding_by_period(session, partner_id, DebtSide.RECEIVABLE, as_of=end)
    return {
        "partner": {"id": partner.id, "code": partner.code, "name": partner.name, "tax_code": partner.tax_code,
                    "address": partner.address, "phone": partner.phone, "contact_name": partner.contact_name,
                    "credit_limit": partner.credit_limit,
                    "is_customer": partner.partner_type != PartnerType.SUPPLIER},
        "as_of": end,
        "balance": debts.balance(session, partner_id, DebtSide.RECEIVABLE, as_of=end),
        "outstanding_by_period": [{"period": k, "amount": v} for k, v in sorted(outstanding.items())],
        "monthly": series,
        "sites": sites,
        "total_loans": asset_svc.loan_text(asset_svc.merge_balances(loans), with_deposit_note=False),
    }
