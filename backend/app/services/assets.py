"""Vỏ bình cho mượn, thiết bị đầu tư và quà tặng theo từng bếp."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import transaction
from ..errors import BusinessError, NotFoundError
from ..models import AssetDirection, AssetMovement, AssetType, Partner, Product, Site
from ..money import ZERO, D
from .locks import ensure_period_open

ASSET_LABELS = {
    AssetType.EMPTY_CYLINDER: "vỏ",
    AssetType.STOVE: "bếp CN",
    AssetType.PIPING: "hệ thống dẫn gas",
    AssetType.EXTINGUISHER: "bình cứu hỏa",
    AssetType.VALVE: "chế/khóa CN",
    AssetType.GIFT: "quà tặng",
    AssetType.OTHER: "thiết bị",
}


def add_movement(session: Session, *, move_date: date, partner_id: int, site_id: int | None, asset_type: str,
                 direction: str, qty=0, description: str | None = None, product_id: int | None = None,
                 deposit_amount=0, slip_id: int | None = None, show_on_statement: bool = True,
                 note: str | None = None) -> AssetMovement:
    """Ghi biến động tài sản BÊN TRONG transaction hiện có."""
    if asset_type not in AssetType.ALL:
        raise BusinessError(f"Loại tài sản không hợp lệ: {asset_type}")
    if direction not in AssetDirection.ALL:
        raise BusinessError(f"Hướng biến động không hợp lệ: {direction}")
    if session.get(Partner, partner_id) is None:
        raise NotFoundError("đối tác", partner_id)
    if site_id is not None:
        site = session.get(Site, site_id)
        if site is None or site.partner_id != partner_id:
            raise BusinessError("Bếp không thuộc khách hàng này")
    product = None
    if product_id is not None:
        product = session.get(Product, product_id)
        if product is None:
            raise NotFoundError("hàng hóa", product_id)
    if asset_type == AssetType.EMPTY_CYLINDER and product is None:
        raise BusinessError("Vỏ bình phải chọn loại bình (PC45, PC48, ...)")
    qty = D(qty)
    if direction in (AssetDirection.LEND, AssetDirection.RETURN) and qty <= 0:
        raise BusinessError("Số lượng cho mượn / thu hồi phải lớn hơn 0")
    if not description:
        description = _default_description(asset_type, direction, qty, product, D(deposit_amount))
    mv = AssetMovement(move_date=move_date, partner_id=partner_id, site_id=site_id, slip_id=slip_id,
                       asset_type=asset_type, product_id=product_id, description=description, qty=qty,
                       direction=direction, deposit_amount=D(deposit_amount), show_on_statement=show_on_statement,
                       note=note)
    session.add(mv)
    return mv


def _fmt_qty(q: Decimal) -> str:
    q = D(q)
    if q == q.to_integral_value():
        return f"{int(q):02d}"
    return format(q.normalize(), "f")


def _default_description(asset_type: str, direction: str, qty: Decimal, product: Product | None,
                         deposit: Decimal) -> str:
    what = ASSET_LABELS[asset_type]
    if asset_type == AssetType.EMPTY_CYLINDER and product is not None:
        what = f"vỏ {product.sku.lower()}"
    verb = {AssetDirection.LEND: "Mượn", AssetDirection.RETURN: "Trả", AssetDirection.INVEST: "Cty đầu tư",
            AssetDirection.GIFT: "Tặng"}[direction]
    text = f"{verb} {_fmt_qty(qty)} {what}" if qty else f"{verb} {what}"
    if direction == AssetDirection.LEND and asset_type == AssetType.EMPTY_CYLINDER and deposit == 0:
        text += " - ko cược"
    return text


def record_movement(session: Session, **kwargs) -> AssetMovement:
    with transaction(session):
        ensure_period_open(session, kwargs.get("partner_id"), kwargs["move_date"])
        mv = add_movement(session, **kwargs)
        session.flush()
    return mv


def delete_movement(session: Session, movement_id: int) -> None:
    with transaction(session):
        mv = session.get(AssetMovement, movement_id)
        if mv is None:
            raise NotFoundError("biến động tài sản", movement_id)
        ensure_period_open(session, mv.partner_id, mv.move_date)
        session.delete(mv)


def loan_balances(session: Session, partner_id: int, *, as_of: date | None = None, before: date | None = None
                  ) -> dict[int | None, dict[tuple[str, str], dict]]:
    """Số đang cho mượn theo bếp: {site_id: {(asset_type, sku/label): {"qty", "deposit"}}}."""
    stmt = select(AssetMovement).where(
        AssetMovement.partner_id == partner_id,
        AssetMovement.direction.in_([AssetDirection.LEND, AssetDirection.RETURN]),
    )
    if as_of:
        stmt = stmt.where(AssetMovement.move_date <= as_of)
    if before:
        stmt = stmt.where(AssetMovement.move_date < before)
    out: dict[int | None, dict[tuple[str, str], dict]] = defaultdict(dict)
    for mv in session.scalars(stmt):
        key = (mv.asset_type, mv.product.sku if mv.product else ASSET_LABELS[mv.asset_type])
        slot = out[mv.site_id].setdefault(key, {"qty": ZERO, "deposit": ZERO})
        sign = 1 if mv.direction == AssetDirection.LEND else -1
        slot["qty"] += sign * D(mv.qty)
        slot["deposit"] += sign * D(mv.deposit_amount)
    return {sid: {k: v for k, v in items.items() if v["qty"] != 0} for sid, items in out.items()}


def loan_text(items: dict[tuple[str, str], dict], *, with_deposit_note: bool = True) -> str:
    """'Mượn 22 vỏ PC48 - ko cược + 13 bếp CN' (vỏ bình xếp trước)."""
    if not items:
        return ""
    parts = []
    order = sorted(items.items(), key=lambda kv: (kv[0][0] != AssetType.EMPTY_CYLINDER, kv[0][1]))
    for (atype, label), val in order:
        qty = _fmt_qty(val["qty"])
        if atype == AssetType.EMPTY_CYLINDER:
            text = f"{qty} vỏ {label}"
            if with_deposit_note and val["deposit"] == 0:
                text += " - ko cược"
        else:
            text = f"{qty} {label}"
        parts.append(text)
    return "Mượn " + " + ".join(parts)


def merge_balances(per_site: dict[int | None, dict[tuple[str, str], dict]]) -> dict[tuple[str, str], dict]:
    total: dict[tuple[str, str], dict] = {}
    for items in per_site.values():
        for key, val in items.items():
            slot = total.setdefault(key, {"qty": ZERO, "deposit": ZERO})
            slot["qty"] += val["qty"]
            slot["deposit"] += val["deposit"]
    return {k: v for k, v in total.items() if v["qty"] != 0}


def movements_in_range(session: Session, partner_id: int, date_from: date, date_to: date) -> list[AssetMovement]:
    return list(session.scalars(
        select(AssetMovement).where(AssetMovement.partner_id == partner_id,
                                    AssetMovement.move_date >= date_from, AssetMovement.move_date <= date_to)
        .order_by(AssetMovement.move_date, AssetMovement.id)
    ))
