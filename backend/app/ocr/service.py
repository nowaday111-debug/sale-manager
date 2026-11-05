"""Luồng AI đọc phiếu: ảnh -> Vision AI -> chuẩn hóa + kiểm chéo -> ĐỀ XUẤT -> người duyệt -> ghi sổ."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..auth import acting_user
from ..db import transaction
from ..errors import BusinessError, NotFoundError
from ..models import AssetType, DeliverySlip, OcrJob, OcrJobStatus, Product, SourceType
from ..money import D
from ..services import assets as asset_svc
from ..services import masterdata, sales
from .normalize import Flag, NormalizedSlip, normalize
from .preprocess import prepare
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, USER_PROMPT
from .providers import VisionClient


def _json_default(obj):
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(type(obj))


def _dumps(obj) -> str:
    return json.dumps(obj, default=_json_default, ensure_ascii=False)


def _save_original(data: bytes, filename: str, sha1: str) -> str:
    ext = Path(filename or "").suffix.lower() or ".jpg"
    folder = settings.upload_dir / "ocr" / datetime.now().strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha1}{ext}"
    if not path.exists():
        path.write_bytes(data)
    return str(path)


def _match_site(session: Session, ns: NormalizedSlip, partner_id: int | None):
    """Thử lần lượt: địa chỉ, khách hàng, khách + địa chỉ (phiếu Stargate ghi tên bếp ở ô Địa chỉ)."""
    for text in (f"{ns.customer_text} {ns.address_text}", ns.address_text, ns.customer_text):
        if text and text.strip():
            site = masterdata.find_site(session, text, partner_id)
            if site:
                return site
    return None


def build_proposal(session: Session, ns: NormalizedSlip, *, partner_id: int | None = None,
                   image_path: str | None = None) -> dict:
    """Đổi kết quả chuẩn hóa thành dữ liệu phiếu (cùng cấu trúc API /api/slips) để người duyệt sửa."""
    flags: list[Flag] = list(ns.flags)
    site = _match_site(session, ns, partner_id) if ns.is_slip else None
    if site is not None:
        partner_id = site.partner_id
    elif ns.is_slip:
        flags.append(Flag("warning", "site", f"Chưa khớp được bếp/khách từ '{ns.customer_text} / {ns.address_text}' "
                                             "– chọn tay rồi lưu tên này làm tên gọi khác của bếp"))
    if ns.slip_no:
        dup = session.scalar(select(DeliverySlip).where(DeliverySlip.slip_no == ns.slip_no))
        if dup:
            flags.append(Flag("warning", "slip_no", f"Phiếu {ns.slip_no} đã nhập ngày {dup.slip_date:%d/%m/%Y} (id {dup.id})"))
    skus = {p.sku for p in session.scalars(select(Product))}
    items = []
    for g in ns.gas_lines:
        if not g.sku or not g.qty:
            continue
        if g.sku not in skus:
            flags.append(Flag("warning", "lines", f"Mã hàng {g.sku} chưa có trong danh mục"))
        unit_price, basis = g.unit_price, g.price_basis
        if unit_price is None and partner_id and g.sku in skus and ns.slip_date:
            found = masterdata.get_price(session, product_id=masterdata.get_product_by_sku(session, g.sku).id,
                                         on_date=ns.slip_date, partner_id=partner_id)
            if found:
                unit_price, basis = found
            else:
                flags.append(Flag("warning", "unit_price", f"Chưa có bảng giá {g.sku} cho khách – nhập đơn giá"))
        items.append({"sku": g.sku, "qty": g.qty, "unit_price": unit_price, "price_basis": basis})
    for e in ns.extra_lines:
        sku = None
        key = masterdata.normalize_text(e.name)
        for p in session.scalars(select(Product)):
            if masterdata.normalize_text(p.name) in key or key in masterdata.normalize_text(p.name):
                sku = p.sku
                break
        if sku is None:
            flags.append(Flag("warning", "lines", f"Phụ kiện '{e.name}' chưa có trong danh mục"))
            continue
        items.append({"sku": sku, "qty": e.qty or Decimal(1), "unit_price": e.unit_price, "price_basis": "UNIT",
                      "description": e.name})
    asset_rows = []
    for a in ns.assets:
        if a.asset_type == AssetType.EMPTY_CYLINDER and a.sku not in skus:
            flags.append(Flag("warning", "assets", f"Vỏ bình '{a.text}' chưa rõ loại bình"))
        asset_rows.append({"asset_type": a.asset_type, "direction": a.direction, "qty": a.qty or Decimal(0),
                           "sku": a.sku if a.asset_type == AssetType.EMPTY_CYLINDER else None,
                           "description": a.text or None})
    notes = [n for n in ns.notes if n and not any(n == a.text for a in ns.assets)]
    if ns.customer_note:
        notes.append(f"Ý kiến KH: {ns.customer_note}")
    proposal = {
        "kind": "SLIP" if items else "ASSETS_ONLY",
        "partner_id": partner_id,
        "site_id": site.id if site else None,
        "site_name": site.name if site else None,
        "slip_date": ns.slip_date,
        "slip_no": ns.slip_no,
        "items": items,
        "gas_residual_kg": ns.residual_kg,
        "other_deduction": ns.deduction,
        "deduction_note": ns.deduction_note,
        "note": "; ".join(notes) or None,
        "staff": ", ".join(ns.staff) or None,
        "customer_signed": ns.customer_signed,
        "assets": asset_rows,
        "image_path": image_path,
        "total_written": ns.total_written,
        "total_calc": ns.total_calc,
    }
    if not ns.slip_date:
        flags.append(Flag("warning", "date", "Chưa có ngày phiếu – nhập tay"))
    proposal["flags"] = [f.__dict__ for f in flags]
    proposal["needs_review"] = ns.confidence < 0.8 or any(f.level == "warning" for f in flags) or not ns.is_slip
    return proposal


def process_image(session: Session, data: bytes, filename: str, *, provider: str | None = None,
                  model: str | None = None, partner_id: int | None = None,
                  client: VisionClient | None = None, today: date | None = None) -> OcrJob:
    sha1 = hashlib.sha1(data).hexdigest()
    job = OcrJob(filename=filename, image_sha1=sha1, prompt_version=PROMPT_VERSION, partner_id=partner_id)
    with transaction(session):
        job.image_path = _save_original(data, filename, sha1)
        session.add(job)
    try:
        jpeg = prepare(data, filename)
        client = client or VisionClient(provider, model)
        job.provider, job.model = client.spec.name, client.model
        result = client.read(jpeg, SYSTEM_PROMPT, USER_PROMPT)
    except BusinessError as exc:
        with transaction(session):
            job.status, job.error = OcrJobStatus.FAILED, exc.message
        return job
    ns = normalize(result.data, today=today)
    proposal = build_proposal(session, ns, partner_id=partner_id, image_path=job.image_path)
    with transaction(session):
        job.raw_text = result.text
        job.usage_json = _dumps(result.usage)
        job.seconds = D(result.seconds)
        job.result_json = _dumps({"normalized": ns.to_dict(), "proposal": proposal})
        job.needs_review = proposal["needs_review"]
        job.partner_id = proposal["partner_id"]
        job.site_id = proposal["site_id"]
        if not ns.is_slip:
            job.status = OcrJobStatus.REJECTED
            job.error = "Ảnh không phải phiếu Hóa đơn bán lẻ"
    return job


def get_job(session: Session, job_id: int) -> OcrJob:
    job = session.get(OcrJob, job_id)
    if job is None:
        raise NotFoundError("lượt đọc phiếu", job_id)
    return job


def job_dict(job: OcrJob) -> dict:
    data = {c: getattr(job, c) for c in ("id", "created_at", "filename", "provider", "model", "status",
                                         "needs_review", "error", "partner_id", "site_id", "slip_id", "seconds",
                                         "reviewed_by", "reviewed_at")}
    data["result"] = json.loads(job.result_json) if job.result_json else None
    data["usage"] = json.loads(job.usage_json) if job.usage_json else None
    return data


def apply_job(session: Session, job_id: int, payload: dict | None = None, *, user: str | None = None) -> OcrJob:
    """Ghi sổ đề xuất (đã được người duyệt sửa nếu có payload)."""
    job = get_job(session, job_id)
    if job.status != OcrJobStatus.PENDING_REVIEW:
        raise BusinessError(f"Lượt đọc này đang ở trạng thái {job.status}, không ghi sổ được")
    proposal = dict(json.loads(job.result_json)["proposal"]) if job.result_json else {}
    if payload:
        proposal.update(payload)
    if not proposal.get("partner_id"):
        raise BusinessError("Chưa chọn khách hàng cho phiếu")
    if not proposal.get("slip_date"):
        raise BusinessError("Chưa có ngày phiếu")
    slip_date = proposal["slip_date"]
    if isinstance(slip_date, str):
        slip_date = date.fromisoformat(slip_date)
    job.status = OcrJobStatus.APPLIED
    job.reviewed_by = user or acting_user()
    job.reviewed_at = datetime.now()
    if proposal.get("items"):
        data = sales.SlipInput(
            partner_id=int(proposal["partner_id"]), site_id=proposal.get("site_id"), slip_date=slip_date,
            slip_no=proposal.get("slip_no"), vat_invoice_no=proposal.get("vat_invoice_no"),
            staff=proposal.get("staff"), customer_signed=bool(proposal.get("customer_signed")),
            items=[sales.ItemInput(sku=i.get("sku"), product_id=i.get("product_id"), qty=i["qty"],
                                   unit_price=i.get("unit_price"), price_basis=i.get("price_basis"),
                                   description=i.get("description")) for i in proposal["items"]],
            gas_residual_kg=proposal.get("gas_residual_kg") or 0, other_deduction=proposal.get("other_deduction") or 0,
            deduction_note=proposal.get("deduction_note"), note=proposal.get("note"),
            source_type=SourceType.AI_OCR, image_path=job.image_path, ocr_json={"ocr_job_id": job.id},
            assets=[sales.AssetInput(asset_type=a["asset_type"], direction=a["direction"], qty=a.get("qty") or 0,
                                     sku=a.get("sku"), product_id=a.get("product_id"),
                                     description=a.get("description"), deposit_amount=a.get("deposit_amount") or 0)
                    for a in proposal.get("assets") or []],
        )
        res = sales.create_slip(session, data, user=user)   # commit cả trạng thái job trong cùng transaction
        with transaction(session):
            job.slip_id = res.slip.id
    else:
        with transaction(session):
            for a in proposal.get("assets") or []:
                product_id = a.get("product_id")
                if product_id is None and a.get("sku"):
                    product_id = masterdata.get_product_by_sku(session, a["sku"]).id
                asset_svc.add_movement(session, move_date=slip_date, partner_id=int(proposal["partner_id"]),
                                       site_id=proposal.get("site_id"), asset_type=a["asset_type"],
                                       direction=a["direction"], qty=a.get("qty") or 0, product_id=product_id,
                                       description=a.get("description"), deposit_amount=a.get("deposit_amount") or 0,
                                       note=f"Từ phiếu {proposal.get('slip_no') or ''} (OCR #{job.id})")
    return job


def reject_job(session: Session, job_id: int, reason: str) -> OcrJob:
    with transaction(session):
        job = get_job(session, job_id)
        if job.status == OcrJobStatus.APPLIED:
            raise BusinessError("Lượt đọc đã ghi sổ; hãy hủy phiếu thay vì từ chối")
        job.status, job.error = OcrJobStatus.REJECTED, reason
        job.reviewed_at = datetime.now()
    return job


def list_jobs(session: Session, status: str | None = None) -> list[OcrJob]:
    stmt = select(OcrJob).order_by(OcrJob.id.desc())
    if status:
        stmt = stmt.where(OcrJob.status == status)
    return list(session.scalars(stmt))
