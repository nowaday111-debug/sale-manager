"""Phiếu giao hàng / hóa đơn bán lẻ (và phiếu khách trả hàng).

Một lần lưu = một transaction gồm: phiếu + dòng hàng + thẻ kho + vỏ mượn/đầu tư + phiếu thu (nếu trả ngay).
Công nợ phải thu được tính trực tiếp từ phiếu đã ghi sổ nên luôn khớp với kho.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..auth import acting_user
from ..db import transaction
from ..errors import BusinessError, DuplicateError, NotFoundError
from ..models import (
    AssetMovement,
    DeliveryItem,
    DeliverySlip,
    DocStatus,
    FundType,
    InvTransType,
    PartnerType,
    PriceBasis,
    Product,
    ProductKind,
    SlipType,
    SourceType,
    VatInvoiceSlip,
    VatMode,
    VoucherType,
)
from ..money import ZERO, D, round_money
from . import assets as asset_svc
from . import cashbook, debts, inventory, masterdata
from .locks import ensure_period_open, period_range

REF_TYPE = "SLIP"


@dataclass
class ItemInput:
    product_id: int | None = None
    sku: str | None = None
    qty: object = 0
    unit_price: object | None = None        # None -> lấy theo bảng giá khách
    price_basis: str | None = None          # None -> theo bảng giá / danh mục
    capacity_kg: object | None = None       # None -> theo danh mục
    description: str | None = None


@dataclass
class AssetInput:
    asset_type: str
    direction: str
    qty: object = 0
    product_id: int | None = None
    sku: str | None = None
    description: str | None = None
    deposit_amount: object = 0
    show_on_statement: bool = True


@dataclass
class SlipInput:
    partner_id: int
    slip_date: date
    items: list[ItemInput] = field(default_factory=list)
    doc_type: str = SlipType.SALE
    site_id: int | None = None
    slip_no: str | None = None
    vat_invoice_no: str | None = None
    staff: str | None = None
    customer_signed: bool = False
    gas_residual_kg: object = 0
    residual_price: object | None = None     # None -> đơn giá/kg của dòng gas đầu tiên
    other_deduction: object = 0
    deduction_note: str | None = None
    note: str | None = None
    status: str = DocStatus.POSTED           # DRAFT: chờ duyệt (OCR), không ảnh hưởng kho/công nợ
    source_type: str = SourceType.MANUAL
    image_path: str | None = None
    ocr_json: dict | str | None = None
    assets: list[AssetInput] = field(default_factory=list)
    paid_amount: object = 0                  # khách trả ngay -> tự sinh phiếu thu
    paid_fund: str = FundType.CASH
    skip_inventory: bool = False             # nhập lịch sử từ Excel: không trừ kho (tồn kho khai báo bằng kiểm kê)
    vat_mode: str | None = None              # None -> theo khai báo của khách (INCLUDED/EXCLUDED)
    vat_rate: object | None = None           # None -> thuế suất của mặt hàng gas đầu tiên (8%)


@dataclass
class SlipResult:
    slip: DeliverySlip
    warnings: list[str]
    voucher_id: int | None = None


# ---------------------------------------------------------------- tính tiền
def _resolve_product(session: Session, product_id: int | None, sku: str | None) -> Product:
    if product_id is not None:
        return masterdata.get_product(session, product_id)
    if sku:
        return masterdata.get_product_by_sku(session, sku)
    raise BusinessError("Dòng hàng thiếu mã hàng")


def _build_items(session: Session, data: SlipInput) -> tuple[list[DeliveryItem], Decimal, Decimal]:
    """Trả về (dòng hàng, tổng tiền chưa làm tròn, đơn giá/kg mặc định cho gas dư)."""
    if not data.items:
        raise BusinessError("Phiếu phải có ít nhất một dòng hàng")
    rows: list[DeliveryItem] = []
    raw_total = ZERO
    first_kg_price: Decimal | None = None
    first_unit_kg_price: Decimal | None = None
    for idx, it in enumerate(data.items, start=1):
        product = _resolve_product(session, it.product_id, it.sku)
        qty = D(it.qty)
        if qty <= 0:
            raise BusinessError(f"Dòng {idx}: số lượng phải lớn hơn 0")
        price, basis = None, it.price_basis
        if it.unit_price is None:
            found = masterdata.get_price(session, product_id=product.id, on_date=data.slip_date,
                                         partner_id=data.partner_id)
            if found is None:
                raise BusinessError(f"Dòng {idx}: chưa có giá cho {product.sku}, hãy nhập đơn giá")
            price = found[0]
            basis = basis or found[1]
        else:
            price = D(it.unit_price)
            basis = basis or product.default_price_basis
        if basis not in PriceBasis.ALL:
            raise BusinessError(f"Dòng {idx}: cách tính giá không hợp lệ {basis}")
        if price < 0:
            raise BusinessError(f"Dòng {idx}: đơn giá không được âm")
        capacity = D(it.capacity_kg) if it.capacity_kg is not None else (D(product.capacity_kg) if product.capacity_kg else None)
        if basis == PriceBasis.KG:
            if not capacity:
                raise BusinessError(f"Dòng {idx}: {product.sku} tính theo kg nhưng chưa có số kg/bình")
            raw = qty * capacity * price
            if first_kg_price is None:
                first_kg_price = price
        else:
            raw = qty * price
            if product.kind == ProductKind.GAS_CYLINDER and capacity and first_unit_kg_price is None:
                first_unit_kg_price = price / capacity
        raw_total += raw
        rows.append(DeliveryItem(
            line_no=idx, product_id=product.id, description=it.description or product.name, qty=qty,
            capacity_kg=capacity, price_basis=basis, unit_price=price, vat_rate=D(product.vat_rate),
            line_total=round_money(raw), cost_at_sale=D(product.cost_price),
        ))
    default_residual = first_kg_price if first_kg_price is not None else (first_unit_kg_price or ZERO)
    return rows, raw_total, default_residual


_EXACT_Q = Decimal("0.0001")


def _exact(v) -> Decimal:
    """Giữ số lẻ như Excel (tối đa 4 chữ số thập phân để SQLite / PostgreSQL lưu giống nhau)."""
    return D(v).quantize(_EXACT_Q)


def _apply_amounts(slip: DeliverySlip, rows: list[DeliveryItem], raw_total: Decimal, default_residual: Decimal,
                   data: SlipInput, *, vat_mode: str = VatMode.INCLUDED,
                   vat_rate: Decimal = Decimal("0.08")) -> None:
    residual_kg = D(data.gas_residual_kg)
    if residual_kg < 0:
        raise BusinessError("Gas dư không được âm")
    residual_price = D(data.residual_price) if data.residual_price is not None else default_residual
    deduction = D(data.other_deduction)
    if deduction < 0:
        raise BusinessError("Khoản trừ không được âm")
    raw_residual = residual_kg * residual_price
    slip.subtotal = round_money(raw_total)
    slip.gas_residual_kg = residual_kg
    slip.residual_price = residual_price
    slip.residual_amount = round_money(raw_residual)
    slip.other_deduction = round_money(deduction)
    slip.vat_mode = vat_mode
    slip.vat_rate = vat_rate
    if vat_mode == VatMode.EXCLUDED:
        # Đơn giá CHƯA thuế: tiền chưa thuế = thực giao × đơn giá; thuế = tiền chưa thuế × thuế suất (giữ số lẻ
        # như file Excel mẫu Welstory). Thành tiền phiếu được làm tròn lũy kế trong kỳ – xem rebalance_vat_period.
        before = _exact(raw_total - raw_residual)
        vat = _exact(before * vat_rate)
        slip.amount_before_tax = before
        slip.vat_amount = vat
        slip.total = round_money(before + vat - deduction)
    else:
        # Làm tròn một lần ở cuối, giống Excel: (số bình × kg − gas dư) × đơn giá (đã gồm VAT)
        slip.total = round_money(raw_total - raw_residual - deduction)
        before = round_money(slip.total / (1 + vat_rate)) if vat_rate else slip.total
        slip.amount_before_tax = before
        slip.vat_amount = slip.total - before
    if slip.total < 0:
        raise BusinessError("Thành tiền phiếu âm – kiểm tra lại gas dư / khoản trừ")
    slip.cost_total = round_money(sum((D(r.qty) * D(r.cost_at_sale) for r in rows), ZERO))


def rebalance_vat_period(session: Session, partner_id: int, period: str) -> None:
    """Khách tính thuế riêng (giữ số lẻ như Excel): làm tròn LŨY KẾ trong kỳ bảng kê.

    Thành tiền từng phiếu = làm tròn(tổng cộng dồn tới phiếu này) − làm tròn(tổng cộng dồn tới phiếu trước),
    nên tổng công nợ của kỳ đúng bằng ô "Tổng cộng thanh toán" Excel tính từ các số lẻ (không lệch 1–2 đồng).
    """
    partner = masterdata.get_partner(session, partner_id)
    date_from, date_to = period_range(period, partner.statement_cut_day)
    session.flush()
    slips = session.scalars(
        select(DeliverySlip).where(DeliverySlip.partner_id == partner_id, DeliverySlip.status == DocStatus.POSTED,
                                   DeliverySlip.vat_mode == VatMode.EXCLUDED,
                                   DeliverySlip.slip_date >= date_from, DeliverySlip.slip_date <= date_to)
        .order_by(DeliverySlip.slip_date, DeliverySlip.id)).all()
    cum = ZERO
    prev = ZERO
    for sl in slips:
        exact = D(sl.amount_before_tax) + D(sl.vat_amount) - D(sl.other_deduction)
        cum += sl.sign * exact
        rounded = round_money(cum)
        new_total = sl.sign * (rounded - prev)
        prev = rounded
        if new_total != D(sl.total):
            sl.total = new_total
    session.flush()


def _rebalance_for(session: Session, slip: DeliverySlip) -> None:
    if slip.vat_mode == VatMode.EXCLUDED:
        partner = masterdata.get_partner(session, slip.partner_id)
        rebalance_vat_period(session, slip.partner_id, partner.period_of(slip.slip_date))


def _validate_header(session: Session, data: SlipInput, *, current_id: int | None = None) -> None:
    if data.doc_type not in SlipType.ALL:
        raise BusinessError(f"Loại phiếu không hợp lệ: {data.doc_type}")
    if data.status not in (DocStatus.DRAFT, DocStatus.POSTED):
        raise BusinessError("Trạng thái phiếu khi lưu chỉ được DRAFT hoặc POSTED")
    if data.source_type not in SourceType.ALL:
        raise BusinessError(f"Nguồn dữ liệu không hợp lệ: {data.source_type}")
    partner = masterdata.get_partner(session, data.partner_id)
    if partner.partner_type == PartnerType.SUPPLIER:
        raise BusinessError(f"{partner.name} là nhà cung cấp, không lập phiếu bán được")
    if data.site_id is not None:
        site = masterdata.get_site(session, data.site_id)
        if site.partner_id != partner.id:
            raise BusinessError("Bếp/điểm giao không thuộc khách hàng đã chọn")
    if data.slip_no:
        dup = session.scalar(select(DeliverySlip).where(DeliverySlip.slip_no == data.slip_no.strip()))
        if dup and dup.id != current_id:
            raise DuplicateError(f"Số phiếu {data.slip_no} đã được nhập (ngày {dup.slip_date:%d/%m/%Y})")


def _post_effects(session: Session, slip: DeliverySlip, data: SlipInput) -> None:
    """Thẻ kho + tài sản cho phiếu POSTED."""
    for row in slip.items:
        product = session.get(Product, row.product_id)
        if not product.track_stock or data.skip_inventory:
            continue
        if slip.doc_type == SlipType.SALE:
            inventory.apply_movement(session, product, qty_change=-D(row.qty), trans_type=InvTransType.OUT_SALE,
                                     trans_date=slip.slip_date, ref_type=REF_TYPE, ref_id=slip.id,
                                     note=f"Xuất bán {slip.slip_no or slip.id}")
        else:
            inventory.apply_movement(session, product, qty_change=D(row.qty), trans_type=InvTransType.IN_RETURN,
                                     trans_date=slip.slip_date, ref_type=REF_TYPE, ref_id=slip.id,
                                     unit_cost=row.cost_at_sale, note=f"Khách trả hàng {slip.slip_no or slip.id}")
    for a in data.assets:
        product_id = a.product_id
        if product_id is None and a.sku:
            product_id = masterdata.get_product_by_sku(session, a.sku).id
        asset_svc.add_movement(session, move_date=slip.slip_date, partner_id=slip.partner_id, site_id=slip.site_id,
                               asset_type=a.asset_type, direction=a.direction, qty=a.qty, description=a.description,
                               product_id=product_id, deposit_amount=a.deposit_amount, slip_id=slip.id,
                               show_on_statement=a.show_on_statement)


def _fill_slip(session: Session, slip: DeliverySlip, data: SlipInput) -> None:
    rows, raw_total, default_residual = _build_items(session, data)
    slip.doc_type = data.doc_type
    slip.slip_no = data.slip_no.strip() if data.slip_no else None
    slip.slip_date = data.slip_date
    slip.partner_id = data.partner_id
    slip.site_id = data.site_id
    slip.vat_invoice_no = str(data.vat_invoice_no).strip() if data.vat_invoice_no not in (None, "") else None
    slip.staff = data.staff
    slip.customer_signed = data.customer_signed
    slip.deduction_note = data.deduction_note
    slip.note = data.note
    slip.source_type = data.source_type
    slip.image_path = data.image_path
    slip.ocr_json = json.dumps(data.ocr_json, ensure_ascii=False) if isinstance(data.ocr_json, dict) else data.ocr_json
    slip.items = rows
    partner = masterdata.get_partner(session, data.partner_id)
    vat_mode = data.vat_mode or partner.vat_mode or VatMode.INCLUDED
    if vat_mode not in VatMode.ALL:
        raise BusinessError(f"Cách tính thuế không hợp lệ: {vat_mode}")
    if data.vat_rate not in (None, ""):
        vat_rate = D(data.vat_rate)
        if vat_rate > 1:          # nhập 8 hiểu là 8%
            vat_rate = vat_rate / 100
    else:
        gas = [r for r in rows if session.get(Product, r.product_id).kind == ProductKind.GAS_CYLINDER]
        vat_rate = D((gas or rows)[0].vat_rate)
    if vat_rate < 0 or vat_rate >= 1:
        raise BusinessError("Thuế suất GTGT không hợp lệ")
    _apply_amounts(slip, rows, raw_total, default_residual, data, vat_mode=vat_mode, vat_rate=vat_rate)


def create_slip(session: Session, data: SlipInput, *, user: str | None = None) -> SlipResult:
    session.info["user"] = user or acting_user()
    voucher_id = None
    with transaction(session):
        _validate_header(session, data)
        ensure_period_open(session, data.partner_id, data.slip_date)
        slip = DeliverySlip(status=data.status, created_by=session.info["user"])
        _fill_slip(session, slip, data)
        session.add(slip)
        session.flush()
        if slip.status == DocStatus.POSTED:
            partner = masterdata.get_partner(session, slip.partner_id)
            warn = debts.check_credit_limit(session, partner, ZERO, slip.slip_date) if slip.doc_type == SlipType.SALE else None
            _post_effects(session, slip, data)
            _rebalance_for(session, slip)
            if warn:
                session.info.setdefault("warnings", []).append(warn)
            paid = round_money(data.paid_amount)
            if paid > 0:
                if slip.doc_type != SlipType.SALE:
                    raise BusinessError("Phiếu trả hàng không kèm thu tiền; hãy lập phiếu chi hoàn tiền")
                if paid > slip.total:
                    raise BusinessError("Số tiền khách trả lớn hơn thành tiền phiếu")
                voucher = cashbook._add_voucher(
                    session, voucher_type=VoucherType.RECEIPT, voucher_date=slip.slip_date, amount=paid,
                    fund_type=data.paid_fund, partner_id=slip.partner_id, category=None,
                    description=f"Thu tiền phiếu {slip.slip_no or slip.id}", payer_payee=None,
                    ref_slip_id=slip.id, ref_purchase_id=None,
                    allocations=[(partner.period_of(slip.slip_date), paid)], affects_debt=True)
                voucher_id = voucher.id
        elif data.assets:
            # phiếu nháp: lưu tạm thông tin tài sản trong ocr_json để áp dụng khi duyệt
            payload = json.loads(slip.ocr_json) if slip.ocr_json else {}
            payload["_pending_assets"] = [a.__dict__ for a in data.assets]
            slip.ocr_json = json.dumps(payload, ensure_ascii=False, default=str)
        warnings = inventory.pop_warnings(session)
    return SlipResult(slip=slip, warnings=warnings, voucher_id=voucher_id)


def get_slip(session: Session, slip_id: int) -> DeliverySlip:
    slip = session.get(DeliverySlip, slip_id)
    if slip is None:
        raise NotFoundError("phiếu giao hàng", slip_id)
    return slip


def _remove_effects(session: Session, slip: DeliverySlip, reason: str) -> None:
    inventory.reverse_ref(session, ref_type=REF_TYPE, ref_id=slip.id, trans_date=slip.slip_date, note=reason)
    for mv in session.scalars(select(AssetMovement).where(AssetMovement.slip_id == slip.id)):
        session.delete(mv)


def update_slip(session: Session, slip_id: int, data: SlipInput, *, user: str | None = None) -> SlipResult:
    """Sửa phiếu: với phiếu đã ghi sổ thì đảo kho/tài sản cũ rồi ghi lại – tất cả trong 1 transaction."""
    session.info["user"] = user or acting_user()
    with transaction(session):
        slip = get_slip(session, slip_id)
        if slip.status == DocStatus.CANCELLED:
            raise BusinessError("Phiếu đã hủy, không sửa được")
        ensure_period_open(session, slip.partner_id, slip.slip_date)
        ensure_period_open(session, data.partner_id, data.slip_date)
        _validate_header(session, data, current_id=slip.id)
        was_posted = slip.status == DocStatus.POSTED
        old = (slip.partner_id, slip.slip_date, slip.vat_mode)
        if was_posted:
            _remove_effects(session, slip, f"Sửa phiếu {slip.slip_no or slip.id}")
        slip.items.clear()
        session.flush()
        _fill_slip(session, slip, data)
        slip.status = data.status if not was_posted else DocStatus.POSTED
        session.flush()
        if slip.status == DocStatus.POSTED:
            _post_effects(session, slip, data)
            _rebalance_for(session, slip)
        if was_posted and old[2] == VatMode.EXCLUDED:
            old_partner = masterdata.get_partner(session, old[0])
            rebalance_vat_period(session, old[0], old_partner.period_of(old[1]))
        warnings = inventory.pop_warnings(session)
    return SlipResult(slip=slip, warnings=warnings)


def approve_slip(session: Session, slip_id: int, *, user: str | None = None) -> SlipResult:
    """Duyệt phiếu nháp (VD: sau khi kiểm tra kết quả OCR) -> ghi sổ kho, tài sản, công nợ."""
    session.info["user"] = user or acting_user()
    with transaction(session):
        slip = get_slip(session, slip_id)
        if slip.status != DocStatus.DRAFT:
            raise BusinessError("Chỉ duyệt được phiếu đang ở trạng thái nháp")
        ensure_period_open(session, slip.partner_id, slip.slip_date)
        pending = []
        if slip.ocr_json:
            try:
                payload = json.loads(slip.ocr_json)
                pending = payload.pop("_pending_assets", []) if isinstance(payload, dict) else []
                slip.ocr_json = json.dumps(payload, ensure_ascii=False)
            except json.JSONDecodeError:
                pending = []
        data = SlipInput(partner_id=slip.partner_id, slip_date=slip.slip_date,
                         assets=[AssetInput(**{k: v for k, v in a.items()}) for a in pending])
        slip.status = DocStatus.POSTED
        session.flush()
        _post_effects(session, slip, data)
        _rebalance_for(session, slip)
        warnings = inventory.pop_warnings(session)
    return SlipResult(slip=slip, warnings=warnings)


def cancel_slip(session: Session, slip_id: int, reason: str, *, user: str | None = None) -> SlipResult:
    session.info["user"] = user or acting_user()
    with transaction(session):
        slip = get_slip(session, slip_id)
        if slip.status == DocStatus.CANCELLED:
            raise BusinessError("Phiếu đã hủy trước đó")
        ensure_period_open(session, slip.partner_id, slip.slip_date)
        if slip.status == DocStatus.POSTED:
            _remove_effects(session, slip, f"Hủy phiếu {slip.slip_no or slip.id}: {reason}")
        slip.status = DocStatus.CANCELLED
        slip.note = f"{slip.note or ''} [HỦY: {reason}]".strip()
        session.flush()
        if slip.vat_mode == VatMode.EXCLUDED:
            partner = masterdata.get_partner(session, slip.partner_id)
            rebalance_vat_period(session, slip.partner_id, partner.period_of(slip.slip_date))
        warnings = inventory.pop_warnings(session)
        linked = session.scalars(select(cashbook.CashVoucher).where(
            cashbook.CashVoucher.ref_slip_id == slip.id,
            cashbook.CashVoucher.status == DocStatus.POSTED)).all()
        if linked:
            warnings.append("Phiếu có phiếu thu đi kèm: " + ", ".join(v.voucher_no for v in linked)
                            + " – hãy hủy hoặc phân bổ lại phiếu thu.")
    return SlipResult(slip=slip, warnings=warnings)


def delete_slip(session: Session, slip_id: int, *, reason: str | None = None, user: str | None = None) -> dict:
    """Xóa hẳn phiếu (nút Xóa ở Danh sách phiếu).

    - Phiếu nháp / đã hủy: xóa ngay.
    - Phiếu đã ghi sổ: tự đảo thẻ kho + xóa vỏ mượn/đầu tư kèm phiếu (công nợ tự giảm vì tính từ phiếu), rồi xóa.
    - Chặn khi kỳ của phiếu đã khóa sổ, hoặc phiếu còn phiếu thu đi kèm (hủy phiếu thu trước).
    Nhật ký (audit log) vẫn giữ nội dung phiếu đã xóa.
    """
    session.info["user"] = user or acting_user()
    with transaction(session):
        slip = get_slip(session, slip_id)
        ensure_period_open(session, slip.partner_id, slip.slip_date)
        linked = session.scalars(select(cashbook.CashVoucher).where(
            cashbook.CashVoucher.ref_slip_id == slip.id,
            cashbook.CashVoucher.status == DocStatus.POSTED)).all()
        if linked and slip.status == DocStatus.POSTED:
            raise BusinessError("Phiếu có phiếu thu đi kèm (" + ", ".join(v.voucher_no for v in linked)
                                + ") – hãy hủy phiếu thu ở màn hình Thu – Chi trước khi xóa phiếu.")
        label = slip.slip_no or f"#{slip.id}"
        was_posted = slip.status == DocStatus.POSTED
        info = {"id": slip.id, "slip_no": slip.slip_no, "partner_id": slip.partner_id,
                "slip_date": slip.slip_date, "total": slip.total, "was_posted": was_posted}
        if was_posted:
            _remove_effects(session, slip, f"Xóa phiếu {label}" + (f": {reason}" if reason else ""))
        for v in session.scalars(select(cashbook.CashVoucher).where(cashbook.CashVoucher.ref_slip_id == slip.id)):
            v.ref_slip_id = None
        for mv in session.scalars(select(AssetMovement).where(AssetMovement.slip_id == slip.id)):
            mv.slip_id = None
        for link in session.scalars(select(VatInvoiceSlip).where(VatInvoiceSlip.slip_id == slip.id)):
            session.delete(link)
        from ..models import OcrJob
        for job in session.scalars(select(OcrJob).where(OcrJob.slip_id == slip.id)):
            job.slip_id = None
        vat_mode, partner_id, slip_date = slip.vat_mode, slip.partner_id, slip.slip_date
        session.delete(slip)
        session.flush()
        if was_posted and vat_mode == VatMode.EXCLUDED:
            partner = masterdata.get_partner(session, partner_id)
            rebalance_vat_period(session, partner_id, partner.period_of(slip_date))
        info["warnings"] = inventory.pop_warnings(session)
    return info


def list_slips(session: Session, *, partner_id: int | None = None, site_id: int | None = None,
               date_from: date | None = None, date_to: date | None = None, status: str | None = None
               ) -> list[DeliverySlip]:
    stmt = select(DeliverySlip).order_by(DeliverySlip.slip_date, DeliverySlip.id)
    if partner_id:
        stmt = stmt.where(DeliverySlip.partner_id == partner_id)
    if site_id:
        stmt = stmt.where(DeliverySlip.site_id == site_id)
    if date_from:
        stmt = stmt.where(DeliverySlip.slip_date >= date_from)
    if date_to:
        stmt = stmt.where(DeliverySlip.slip_date <= date_to)
    if status:
        stmt = stmt.where(DeliverySlip.status == status)
    return list(session.scalars(stmt))


def gross_profit(session: Session, *, date_from: date, date_to: date, partner_id: int | None = None) -> dict:
    """Doanh thu (đã gồm VAT), doanh thu trước thuế, giá vốn, lãi gộp."""
    revenue = revenue_before_tax = cost = ZERO
    for slip in list_slips(session, partner_id=partner_id, date_from=date_from, date_to=date_to,
                           status=DocStatus.POSTED):
        sign = slip.sign
        revenue += sign * D(slip.total)
        if slip.vat_mode == VatMode.EXCLUDED and slip.amount_before_tax is not None:
            revenue_before_tax += sign * round_money(D(slip.total) - D(slip.vat_amount))
        else:
            rate = slip.vat_rate if slip.vat_rate is not None else (slip.items[0].vat_rate if slip.items else D("0"))
            revenue_before_tax += sign * round_money(D(slip.total) / (1 + D(rate)))
        cost += sign * D(slip.cost_total)
    return {"revenue": revenue, "revenue_before_tax": revenue_before_tax, "cost": cost,
            "gross_profit": revenue_before_tax - cost}
