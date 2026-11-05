from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import PurchaseIn, ReasonIn, SlipIn, row_dict, slip_dict
from ..services import purchases, sales

router = APIRouter(prefix="/api", tags=["Bán hàng & Mua hàng"])


def _slip_input(body: SlipIn) -> sales.SlipInput:
    data = body.model_dump()
    data["items"] = [sales.ItemInput(**i) for i in data["items"]]
    data["assets"] = [sales.AssetInput(**a) for a in data["assets"]]
    return sales.SlipInput(**data)


def _slip_result(res: sales.SlipResult) -> dict:
    return {"slip": slip_dict(res.slip), "warnings": res.warnings, "voucher_id": res.voucher_id}


@router.get("/slips")
def list_slips(partner_id: Optional[int] = None, site_id: Optional[int] = None, date_from: Optional[date] = None,
               date_to: Optional[date] = None, status: Optional[str] = None, session: Session = Depends(get_session)):
    return [slip_dict(s) for s in sales.list_slips(session, partner_id=partner_id, site_id=site_id,
                                                   date_from=date_from, date_to=date_to, status=status)]


@router.post("/slips", status_code=201)
def create_slip(body: SlipIn, session: Session = Depends(get_session)):
    return _slip_result(sales.create_slip(session, _slip_input(body)))


@router.get("/slips/{slip_id}")
def get_slip(slip_id: int, session: Session = Depends(get_session)):
    return slip_dict(sales.get_slip(session, slip_id))


@router.put("/slips/{slip_id}")
def update_slip(slip_id: int, body: SlipIn, session: Session = Depends(get_session)):
    return _slip_result(sales.update_slip(session, slip_id, _slip_input(body)))


@router.post("/slips/{slip_id}/approve")
def approve_slip(slip_id: int, session: Session = Depends(get_session)):
    return _slip_result(sales.approve_slip(session, slip_id))


@router.post("/slips/{slip_id}/cancel")
def cancel_slip(slip_id: int, body: ReasonIn, session: Session = Depends(get_session)):
    return _slip_result(sales.cancel_slip(session, slip_id, body.reason))


@router.delete("/slips/{slip_id}")
def delete_slip(slip_id: int, reason: Optional[str] = None, session: Session = Depends(get_session)):
    """Xóa hẳn phiếu. Phiếu đã ghi sổ được tự đảo kho / vỏ mượn / công nợ trước khi xóa."""
    return sales.delete_slip(session, slip_id, reason=reason)


@router.get("/reports/gross-profit")
def gross_profit(date_from: date, date_to: date, partner_id: Optional[int] = None,
                 session: Session = Depends(get_session)):
    return sales.gross_profit(session, date_from=date_from, date_to=date_to, partner_id=partner_id)


@router.get("/purchases")
def list_purchases(supplier_id: Optional[int] = None, date_from: Optional[date] = None,
                   date_to: Optional[date] = None, session: Session = Depends(get_session)):
    return [row_dict(p, include=("items",)) for p in purchases.list_purchases(
        session, supplier_id=supplier_id, date_from=date_from, date_to=date_to)]


@router.post("/purchases", status_code=201)
def create_purchase(body: PurchaseIn, session: Session = Depends(get_session)):
    data = body.model_dump()
    data["items"] = [purchases.PurchaseItemInput(**i) for i in data["items"]]
    doc, warnings = purchases.create_purchase(session, purchases.PurchaseInput(**data))
    return {"purchase": row_dict(doc, include=("items",)), "warnings": warnings}


@router.post("/purchases/{purchase_id}/cancel")
def cancel_purchase(purchase_id: int, body: ReasonIn, session: Session = Depends(get_session)):
    doc, warnings = purchases.cancel_purchase(session, purchase_id, body.reason)
    return {"purchase": row_dict(doc), "warnings": warnings}
