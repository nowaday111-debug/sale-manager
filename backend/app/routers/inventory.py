from __future__ import annotations

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session, transaction
from ..schemas import AssetMovementIn, OpeningStockIn, StocktakeIn, row_dict
from ..services import assets, inventory

router = APIRouter(prefix="/api", tags=["Kho & Tài sản"])


@router.post("/inventory/opening", status_code=201)
def opening_stock(body: OpeningStockIn, session: Session = Depends(get_session)):
    with transaction(session):
        txn = inventory.set_opening_stock(session, **body.model_dump())
        warnings = inventory.pop_warnings(session)
    return {"transaction": row_dict(txn), "warnings": warnings}


@router.get("/inventory/stock-card/{product_id}")
def stock_card(product_id: int, date_from: date, date_to: date, session: Session = Depends(get_session)):
    return asdict(inventory.stock_card(session, product_id, date_from, date_to))


@router.get("/inventory/nxt")
def nxt(date_from: date, date_to: date, session: Session = Depends(get_session)):
    return inventory.nxt_report(session, date_from, date_to)


@router.get("/inventory/low-stock")
def low_stock(session: Session = Depends(get_session)):
    return [row_dict(p) for p in inventory.low_stock(session)]


@router.post("/inventory/stocktakes", status_code=201)
def stocktake(body: StocktakeIn, session: Session = Depends(get_session)):
    with transaction(session):
        st = inventory.create_stocktake(session, on_date=body.on_date, counted=body.counted, note=body.note)
        warnings = inventory.pop_warnings(session)
    return {"stocktake": row_dict(st, include=("lines",)), "warnings": warnings}


@router.post("/assets", status_code=201)
def record_asset(body: AssetMovementIn, session: Session = Depends(get_session)):
    data = body.model_dump()
    sku = data.pop("sku")
    if sku and not data.get("product_id"):
        from ..services import masterdata

        data["product_id"] = masterdata.get_product_by_sku(session, sku).id
    return row_dict(assets.record_movement(session, **data))


@router.delete("/assets/{movement_id}", status_code=204)
def delete_asset(movement_id: int, session: Session = Depends(get_session)):
    assets.delete_movement(session, movement_id)


@router.get("/assets/loans/{partner_id}")
def loans(partner_id: int, as_of: date, session: Session = Depends(get_session)):
    per_site = assets.loan_balances(session, partner_id, as_of=as_of)
    return {
        "sites": [{"site_id": sid, "text": assets.loan_text(items),
                   "items": [{"asset_type": k[0], "label": k[1], **v} for k, v in items.items()]}
                  for sid, items in per_site.items()],
        "total_text": assets.loan_text(assets.merge_balances(per_site), with_deposit_note=False),
    }
