"""Bảng kê chi tiết công nợ tháng theo khách hàng.

build_statement() trả về cấu trúc dữ liệu đầy đủ của một sheet "T10.2026": header, các khối bếp,
tổng cộng, nợ các tháng trước, các lần thanh toán, còn phải trả, số tiền bằng chữ.
Module xuất Excel (Bước 4) chỉ việc đổ cấu trúc này vào template.
"""
from __future__ import annotations

import calendar
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import transaction
from ..errors import BusinessError, NotFoundError
from ..models import (
    AssetMovement,
    CashVoucher,
    DebtSide,
    DeliverySlip,
    DocStatus,
    PriceBasis,
    Product,
    ProductKind,
    Site,
    Statement,
    StatementStatus,
    StatementTemplate,
    VatMode,
)
from ..money import ZERO, D, normalize
from ..utils.so_thanh_chu import so_thanh_chu
from . import assets as asset_svc
from . import debts, masterdata


def period_bounds(period: str, cut_day: int | None = None) -> tuple[date, date]:
    from .locks import period_range
    return period_range(period, cut_day)


def _month_label(period: str) -> str:
    y, m = period.split("-")
    return f"{m}/{y}"


def _vn_date(d: date) -> str:
    return f"{d.day}/{d.month}/{d.year}"


def _slip_row(slip: DeliverySlip, asset_notes: list[str]) -> dict:
    sign = slip.sign
    qty_by_sku: dict[str, Decimal] = defaultdict(lambda: ZERO)
    gross_kg = ZERO
    kg_prices: set[Decimal] = set()
    unit_prices: set[Decimal] = set()
    extra: list[dict] = []
    for it in slip.items:
        p = it.product
        if p.kind == ProductKind.GAS_CYLINDER:
            qty_by_sku[p.sku] += D(it.qty)
            if it.capacity_kg:
                gross_kg += D(it.qty) * D(it.capacity_kg)
            (kg_prices if it.price_basis == PriceBasis.KG else unit_prices).add(D(it.unit_price))
        else:
            extra.append({"sku": p.sku, "description": it.description or p.name, "qty": D(it.qty),
                          "unit_price": D(it.unit_price), "amount": sign * D(it.line_total)})
    residual = D(slip.gas_residual_kg)
    if kg_prices and not unit_prices and len(kg_prices) == 1:
        unit_price, basis = next(iter(kg_prices)), PriceBasis.KG
    elif unit_prices and not kg_prices and len(unit_prices) == 1:
        unit_price, basis = next(iter(unit_prices)), PriceBasis.UNIT
    elif not qty_by_sku and len(extra) == 1:
        unit_price, basis = extra[0]["unit_price"], PriceBasis.UNIT
    else:
        unit_price, basis = None, None
    notes = [n for n in [slip.note, *asset_notes] if n]
    if slip.other_deduction:
        notes.append(f"{slip.deduction_note or 'Trừ'}: {normalize(D(slip.other_deduction)):,}".replace(",", "."))
    if slip.doc_type != "SALE":
        notes.insert(0, "Khách trả hàng")
    return {
        "kind": "slip", "slip_id": slip.id, "date": slip.slip_date, "slip_no": slip.slip_no,
        "vat_invoice_no": slip.vat_invoice_no,
        "qty_by_sku": {k: sign * v for k, v in qty_by_sku.items()},
        "description": "; ".join(e["description"] if not qty_by_sku else f'{normalize(e["qty"])} {e["description"]}'
                                 for e in extra) if extra else None,
        "gas_residual_kg": sign * residual,
        "net_kg": sign * (gross_kg - residual) if qty_by_sku else None,
        "unit_price": unit_price, "price_basis": basis,
        "amount": sign * D(slip.total),
        "vat_mode": slip.vat_mode or VatMode.INCLUDED,
        "vat_rate": D(slip.vat_rate) if slip.vat_rate is not None else None,
        "amount_before_tax": sign * D(slip.amount_before_tax) if slip.amount_before_tax is not None else None,
        "vat_amount": sign * D(slip.vat_amount) if slip.vat_amount is not None else None,
        "site_name": slip.site.name if slip.site else None,
        "other_deduction": sign * D(slip.other_deduction or ZERO),
        "extra_items": extra,
        "note": "; ".join(notes) if notes else None,
        "staff": slip.staff,
    }


def build_statement(session: Session, partner_id: int, period: str, *, sign_date: date | None = None,
                    words_style: str | None = None) -> dict:
    partner = masterdata.get_partner(session, partner_id)
    company = masterdata.get_seller_for(session, partner)
    date_from, date_to = period_bounds(period, partner.statement_cut_day)
    sign_date = sign_date or date_to
    style = words_style or settings.amount_words_style

    slips = list(session.scalars(
        select(DeliverySlip).where(DeliverySlip.partner_id == partner_id, DeliverySlip.status == DocStatus.POSTED,
                                   DeliverySlip.slip_date >= date_from, DeliverySlip.slip_date <= date_to)
        .order_by(DeliverySlip.slip_date, DeliverySlip.id)))
    movements = asset_svc.movements_in_range(session, partner_id, date_from, date_to)
    notes_by_slip: dict[int, list[str]] = defaultdict(list)
    loose_notes: dict[int | None, list[AssetMovement]] = defaultdict(list)
    for mv in movements:
        if not mv.show_on_statement:
            continue
        if mv.slip_id:
            notes_by_slip[mv.slip_id].append(mv.description)
        else:
            loose_notes[mv.site_id].append(mv)

    loans_end = asset_svc.loan_balances(session, partner_id, as_of=date_to)
    loans_start = asset_svc.loan_balances(session, partner_id, before=date_from)

    # khối bếp
    site_ids = {s.site_id for s in slips} | set(loose_notes) | {sid for sid, v in loans_end.items() if v}
    sites = {s.id: s for s in session.scalars(select(Site).where(Site.partner_id == partner_id))}
    ordered = sorted(site_ids, key=lambda sid: (sid is None, sites[sid].sort_order if sid in sites else 0, sid or 0))

    cylinder_skus: set[str] = set()
    blocks = []
    for sid in ordered:
        site = sites.get(sid)
        rows = []
        for slip in (s for s in slips if s.site_id == sid):
            row = _slip_row(slip, notes_by_slip.get(slip.id, []))
            cylinder_skus.update(row["qty_by_sku"])
            rows.append(row)
        for mv in loose_notes.get(sid, []):
            rows.append({"kind": "note", "date": mv.move_date, "text": mv.description, "asset_type": mv.asset_type,
                         "direction": mv.direction})
        rows.sort(key=lambda r: (r["date"], 0 if r["kind"] == "slip" else 1))
        totals_qty: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for r in rows:
            if r["kind"] == "slip":
                for k, v in r["qty_by_sku"].items():
                    totals_qty[k] += v
        slip_rows = [r for r in rows if r["kind"] == "slip"]
        blocks.append({
            "site_id": sid,
            "site_name": site.name if site else "(Chưa gán bếp)",
            "investment_note": site.investment_note if site else None,
            "loan_text": asset_svc.loan_text(loans_end.get(sid, {})),
            "site_label": "\n".join(x for x in [site.name if site else "(Chưa gán bếp)",
                                                asset_svc.loan_text(loans_end.get(sid, {}))] if x),
            "rows": rows,
            "totals": {
                "qty_by_sku": dict(totals_qty),
                "gas_residual_kg": sum((r["gas_residual_kg"] for r in slip_rows), ZERO),
                "net_kg": sum((r["net_kg"] or ZERO for r in slip_rows), ZERO),
                "amount": sum((r["amount"] for r in slip_rows), ZERO),
                "amount_before_tax": _sum_opt(r["amount_before_tax"] for r in slip_rows),
                "vat_amount": _sum_opt(r["vat_amount"] for r in slip_rows),
            },
        })

    products = {p.sku: p for p in session.scalars(select(Product).where(Product.sku.in_(cylinder_skus or {""})))}
    columns = sorted(cylinder_skus, key=lambda k: (-(D(products[k].capacity_kg) if k in products and products[k].capacity_kg else ZERO), k))
    grand_qty: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for b in blocks:
        for k, v in b["totals"]["qty_by_sku"].items():
            grand_qty[k] += v
    grand = {
        "qty_by_sku": dict(grand_qty),
        "gas_residual_kg": sum((b["totals"]["gas_residual_kg"] for b in blocks), ZERO),
        "net_kg": sum((b["totals"]["net_kg"] for b in blocks), ZERO),
        "amount": sum((b["totals"]["amount"] for b in blocks), ZERO),
        "amount_before_tax": _sum_opt(b["totals"]["amount_before_tax"] for b in blocks),
        "vat_amount": _sum_opt(b["totals"]["vat_amount"] for b in blocks),
    }
    vat_rates = sorted({r["vat_rate"] for b in blocks for r in b["rows"]
                        if r["kind"] == "slip" and r.get("vat_rate") is not None})

    # nợ các tháng trước (tính tới hết ngày trước kỳ)
    prior_rows = []
    outstanding = debts.outstanding_by_period(session, partner_id, DebtSide.RECEIVABLE,
                                              as_of=date_from - timedelta(days=1))
    for p, amt in sorted(outstanding.items()):
        if p >= period:
            continue
        label = (f"Công nợ tháng {_month_label(p)} chưa thanh toán" if amt > 0
                 else f"Tiền trả thừa tháng {_month_label(p)} chuyển sang")
        prior_rows.append({"period": p, "label": label, "amount": amt})
    prepaid = debts.allocations_by_period(session, partner_id, DebtSide.RECEIVABLE, before=date_from).get(period, ZERO)
    if prepaid:
        prior_rows.append({"period": period, "label": f"Đã trả trước cho tháng {_month_label(period)}",
                           "amount": -prepaid})
    total_debt = grand["amount"] + sum((r["amount"] for r in prior_rows), ZERO)

    # thanh toán trong kỳ + ghi chú "TT xong"/"TT 1 phần"
    remaining: dict[str, Decimal] = {r["period"]: r["amount"] for r in prior_rows if r["period"] != period}
    remaining[period] = grand["amount"] - prepaid
    vouchers = list(session.scalars(
        select(CashVoucher).where(CashVoucher.partner_id == partner_id, CashVoucher.status == DocStatus.POSTED,
                                  CashVoucher.voucher_date >= date_from, CashVoucher.voucher_date <= date_to)
        .order_by(CashVoucher.voucher_date, CashVoucher.id)))
    payments = []
    last_touch: dict[str, int] = {}
    for v in vouchers:
        allocs = [a for a in v.allocations if a.side == DebtSide.RECEIVABLE]
        if not allocs:
            continue
        amount = sum((D(a.amount) for a in allocs), ZERO)
        done = []
        for a in allocs:
            remaining[a.period] = remaining.get(a.period, ZERO) - D(a.amount)
            last_touch[a.period] = len(payments)
            if remaining[a.period] == 0 and D(a.amount) > 0:
                done.append(a.period)
        label = (f"Ngày {_vn_date(v.voucher_date)} thanh toán" if amount > 0
                 else f"Ngày {_vn_date(v.voucher_date)} hoàn tiền")
        payments.append({"voucher_id": v.id, "voucher_no": v.voucher_no, "date": v.voucher_date,
                         "label": label, "amount": amount, "fund_type": v.fund_type,
                         "note": "; ".join(f"TT xong CN tháng {_month_label(p)}" for p in done) or None,
                         "allocations": [{"period": a.period, "amount": D(a.amount)} for a in allocs]})
    for p, idx in last_touch.items():
        if remaining.get(p, ZERO) > 0 and p != period:
            extra = f"TT 1 phần CN tháng {_month_label(p)}"
            payments[idx]["note"] = f"{payments[idx]['note']}; {extra}" if payments[idx]["note"] else extra
    total_paid = sum((p["amount"] for p in payments), ZERO)
    closing = total_debt - total_paid
    ledger_balance = debts.balance(session, partner_id, DebtSide.RECEIVABLE, as_of=date_to)

    y, m = (int(x) for x in period.split("-"))
    return {
        "period": period,
        "template": partner.statement_template or StatementTemplate.STANDARD,
        "vat_mode": partner.vat_mode or VatMode.INCLUDED,
        "vat_rates": vat_rates,
        "cut_day": partner.statement_cut_day,
        "sheet_name": f"T{m}.{y}",
        "date_from": date_from, "date_to": date_to, "sign_date": sign_date,
        "period_text": f"Từ ngày {date_from:%d/%m/%Y} đến hết ngày {date_to:%d/%m/%Y}",
        "sign_text": f"{company.sign_city}, ngày {sign_date.day:02d} tháng {sign_date.month:02d} năm {sign_date.year}",
        "seller": {"parent_name": company.parent_name, "name": company.name, "address": company.address,
                   "tax_code": company.tax_code, "phones": company.phones, "bank_account": company.bank_account,
                   "bank_name": company.bank_name, "contact_line": company.contact_line,
                   "sign_city": company.sign_city},
        "buyer": {"partner_id": partner.id, "code": partner.code, "name": partner.name, "address": partner.address,
                  "tax_code": partner.tax_code, "phone": partner.phone, "contact_name": partner.contact_name},
        "cylinder_columns": columns,
        "opening_loans_text": _total_loans(loans_start),
        "closing_loans_text": _total_loans(loans_end),
        "blocks": blocks,
        "grand_total": grand,
        "prior_debts": prior_rows,
        "total_debt": total_debt,
        "payments": payments,
        "total_paid": total_paid,
        "closing_debt": closing,
        "amount_in_words": so_thanh_chu(closing, style=style),
        "period_words": so_thanh_chu(grand["amount"], style=style),
        "ledger_balance": ledger_balance,
        "balanced": ledger_balance == closing,
    }


def _sum_opt(values) -> Decimal | None:
    vals = [v for v in values if v is not None]
    return sum(vals, ZERO) if vals else None


def _total_loans(per_site) -> str:
    text = asset_svc.loan_text(asset_svc.merge_balances(per_site), with_deposit_note=False)
    return ("Tổng " + text[0].lower() + text[1:]) if text else ""


# ---------------------------------------------------------------- lưu / chốt sổ
def _to_json(obj):
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(type(obj))


def save_statement(session: Session, partner_id: int, period: str, *, sign_date: date | None = None) -> Statement:
    """Lưu (hoặc tính lại) bảng kê ở trạng thái DRAFT. Bảng kê đã CONFIRMED thì không tính lại."""
    with transaction(session):
        st = session.scalar(select(Statement).where(Statement.partner_id == partner_id, Statement.period == period))
        if st and st.status == StatementStatus.CONFIRMED:
            raise BusinessError(f"Bảng kê {period} đã được khách xác nhận, mở khóa trước khi tính lại")
        data = build_statement(session, partner_id, period, sign_date=sign_date)
        snapshot = json.dumps(data, default=_to_json, ensure_ascii=False)
        if st is None:
            st = Statement(partner_id=partner_id, period=period, date_from=data["date_from"], date_to=data["date_to"],
                           sign_date=data["sign_date"], total_delivered=data["grand_total"]["amount"],
                           prior_debt=data["total_debt"] - data["grand_total"]["amount"],
                           paid_in_period=data["total_paid"], closing_debt=data["closing_debt"],
                           status=StatementStatus.DRAFT, snapshot_json=snapshot)
            session.add(st)
        else:
            st.sign_date = data["sign_date"]
            st.total_delivered = data["grand_total"]["amount"]
            st.prior_debt = data["total_debt"] - data["grand_total"]["amount"]
            st.paid_in_period = data["total_paid"]
            st.closing_debt = data["closing_debt"]
            st.snapshot_json = snapshot
        session.flush()
    return st


def get_statement(session: Session, statement_id: int) -> Statement:
    st = session.get(Statement, statement_id)
    if st is None:
        raise NotFoundError("bảng kê", statement_id)
    return st


def set_status(session: Session, statement_id: int, status: str) -> Statement:
    """DRAFT -> SENT -> CONFIRMED (khóa sổ). Mở khóa: CONFIRMED -> SENT."""
    if status not in StatementStatus.ALL:
        raise BusinessError(f"Trạng thái không hợp lệ: {status}")
    with transaction(session):
        st = get_statement(session, statement_id)
        if status == StatementStatus.CONFIRMED:
            st.confirmed_at = datetime.now()
        elif st.status == StatementStatus.CONFIRMED:
            st.confirmed_at = None
        st.status = status
    return st


def list_statements(session: Session, partner_id: int | None = None) -> list[Statement]:
    stmt = select(Statement).order_by(Statement.period.desc())
    if partner_id:
        stmt = stmt.where(Statement.partner_id == partner_id)
    return list(session.scalars(stmt))
