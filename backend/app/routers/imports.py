from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import BusinessError
from ..importers.einvoice_xml import import_einvoice
from ..importers.history import import_workbook

router = APIRouter(prefix="/api/import", tags=["Nhập dữ liệu"])


@router.post("/excel")
async def import_excel(file: UploadFile = File(...), cutover: Optional[str] = Form(None),
                       partner_code: Optional[str] = Form(None), session: Session = Depends(get_session)):
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise BusinessError("Chỉ nhận file Excel .xlsx")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(file.filename).name
        path.write_bytes(await file.read())
        report = import_workbook(session, path, partner_code=partner_code, cutover=cutover)
    data = asdict(report)
    data["mismatches"] = report.mismatches
    return data


@router.post("/xml")
async def import_xml(file: UploadFile = File(...), mapping: Optional[str] = Form(None),
                     session: Session = Depends(get_session)):
    """mapping (tùy chọn, JSON): {"1": {"sku": "PC48", "qty_factor": "1"}} cho các dòng chưa nối mã."""
    try:
        mp = {int(k): v for k, v in json.loads(mapping).items()} if mapping else None
    except (ValueError, AttributeError) as exc:
        raise BusinessError("mapping phải là JSON dạng {\"số dòng\": {\"sku\": ...}}") from exc
    res = import_einvoice(session, await file.read(), mapping=mp)
    inv = res.invoice
    return {
        "direction": res.direction, "status": res.status, "purchase_id": res.purchase_id,
        "vat_invoice_id": res.vat_invoice_id, "linked_slip_ids": res.linked_slip_ids,
        "unmapped": res.unmapped, "warnings": res.warnings,
        "invoice": {"symbol": inv.symbol, "number": inv.number, "date": inv.issue_date, "seller": asdict(inv.seller),
                    "buyer": asdict(inv.buyer), "total_before_tax": inv.total_before_tax, "vat": inv.vat_amount,
                    "total": inv.total, "lines": [asdict(ln) for ln in inv.lines]},
    }
