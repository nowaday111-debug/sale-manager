from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import (
    FundOpeningIn,
    OpeningBalanceIn,
    ReallocateIn,
    ReasonIn,
    StatementExportIn,
    StatementSaveIn,
    StatusIn,
    VoucherIn,
    row_dict,
)
from ..exporters import bangke_excel
from ..services import cashbook, debts, reports, statements

router = APIRouter(prefix="/api", tags=["Thu chi, Công nợ, Bảng kê"])


def _voucher(v) -> dict:
    return row_dict(v, include=("allocations",))


@router.get("/cash/vouchers")
def list_vouchers(date_from: Optional[date] = None, date_to: Optional[date] = None, partner_id: Optional[int] = None,
                  voucher_type: Optional[str] = None, session: Session = Depends(get_session)):
    return [_voucher(v) for v in cashbook.list_vouchers(session, date_from=date_from, date_to=date_to,
                                                        partner_id=partner_id, voucher_type=voucher_type)]


@router.post("/cash/vouchers", status_code=201)
def create_voucher(body: VoucherIn, session: Session = Depends(get_session)):
    data = body.model_dump()
    allocs = data.pop("allocations")
    v = cashbook.create_voucher(session, allocations=[(a["period"], a["amount"]) for a in allocs] if allocs else None,
                                **data)
    return _voucher(v)


@router.post("/cash/vouchers/{voucher_id}/reallocate")
def reallocate(voucher_id: int, body: ReallocateIn, session: Session = Depends(get_session)):
    return _voucher(cashbook.reallocate_voucher(session, voucher_id, [(a.period, a.amount) for a in body.allocations]))


@router.post("/cash/vouchers/{voucher_id}/cancel")
def cancel_voucher(voucher_id: int, body: ReasonIn, session: Session = Depends(get_session)):
    return _voucher(cashbook.cancel_voucher(session, voucher_id, body.reason))


@router.post("/cash/opening", status_code=201)
def fund_opening(body: FundOpeningIn, session: Session = Depends(get_session)):
    return row_dict(cashbook.set_fund_opening(session, body.fund_type, body.as_of, body.amount))


@router.get("/cash/book")
def cash_book(fund_type: str, date_from: date, date_to: date, session: Session = Depends(get_session)):
    return cashbook.cash_book(session, fund_type, date_from, date_to)


@router.post("/debts/opening", status_code=201)
def opening_balance(body: OpeningBalanceIn, session: Session = Depends(get_session)):
    return row_dict(debts.set_opening_balance(session, **body.model_dump()))


@router.get("/debts/summary")
def debt_summary(side: str, date_from: date, date_to: date, session: Session = Depends(get_session)):
    return [asdict(r) for r in debts.debt_summary(session, side, date_from, date_to)]


@router.get("/debts/detail/{partner_id}")
def debt_detail(partner_id: int, side: str, date_from: date, date_to: date, session: Session = Depends(get_session)):
    return debts.debt_detail(session, partner_id, side, date_from, date_to)


@router.get("/debts/outstanding/{partner_id}")
def outstanding(partner_id: int, side: str = "RECEIVABLE", as_of: Optional[date] = None,
                session: Session = Depends(get_session)):
    rows = debts.outstanding_by_period(session, partner_id, side, as_of=as_of)
    return {"periods": [{"period": k, "amount": v} for k, v in rows.items()],
            "balance": debts.balance(session, partner_id, side, as_of=as_of)}


@router.get("/statements/preview/{partner_id}/{period}")
def preview_statement(partner_id: int, period: str, sign_date: Optional[date] = None,
                      session: Session = Depends(get_session)):
    return statements.build_statement(session, partner_id, period, sign_date=sign_date)


@router.get("/statements/export/{partner_id}/{period}")
def export_statement_file(partner_id: int, period: str, sign_date: Optional[date] = None,
                          session: Session = Depends(get_session)):
    """Tải file Excel bảng kê riêng của tháng (1 sheet T{m}.{yyyy})."""
    from urllib.parse import quote
    data, filename = bangke_excel.build_workbook_bytes(session, partner_id, period, sign_date=sign_date)
    return Response(content=data,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.post("/statements/export-to-workbook")
def export_statement_to_workbook(body: StatementExportIn, session: Session = Depends(get_session)):
    """Thêm sheet tháng vào workbook bảng kê sẵn có của khách (tự sao lưu file trước khi ghi)."""
    return bangke_excel.export_to_workbook(session, body.partner_id, body.period, workbook_path=body.workbook_path,
                                           sign_date=body.sign_date, replace=body.replace,
                                           hide_old_months=body.hide_old_months)


@router.post("/statements", status_code=201)
def save_statement(body: StatementSaveIn, session: Session = Depends(get_session)):
    st = statements.save_statement(session, body.partner_id, body.period, sign_date=body.sign_date)
    data = row_dict(st)
    data["snapshot_json"] = json.loads(st.snapshot_json)
    return data


@router.get("/statements")
def list_statements(partner_id: Optional[int] = None, session: Session = Depends(get_session)):
    out = []
    for st in statements.list_statements(session, partner_id):
        d = row_dict(st)
        d.pop("snapshot_json")
        out.append(d)
    return out


@router.post("/statements/{statement_id}/status")
def statement_status(statement_id: int, body: StatusIn, session: Session = Depends(get_session)):
    d = row_dict(statements.set_status(session, statement_id, body.status))
    d.pop("snapshot_json")
    return d


@router.get("/reports/dashboard")
def dashboard(date_from: date, date_to: date, session: Session = Depends(get_session)):
    return reports.dashboard(session, date_from, date_to)


@router.get("/reports/customers/{partner_id}/overview")
def customer_overview(partner_id: int, period: str, months: int = 12, session: Session = Depends(get_session)):
    return reports.customer_overview(session, partner_id, period=period, months=months)
