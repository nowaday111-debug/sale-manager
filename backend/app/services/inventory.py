"""Kho: ghi thẻ kho, giá vốn bình quân gia quyền, kiểm kê, báo cáo Nhập–Xuất–Tồn.

Mọi hàm ở đây KHÔNG commit; chúng chạy bên trong transaction của nghiệp vụ gọi nó.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import BusinessError, StockError
from ..models import InventoryTransaction, InvTransType, Product, Stocktake, StocktakeLine
from ..money import ZERO, D, round_money, round_price
from .sequences import next_number

_IN_TYPES = {InvTransType.IN_PURCHASE, InvTransType.IN_RETURN, InvTransType.OPENING}


def _warn(session: Session, message: str) -> None:
    session.info.setdefault("warnings", []).append(message)


def pop_warnings(session: Session) -> list[str]:
    return session.info.pop("warnings", [])


def apply_movement(session: Session, product: Product, *, qty_change, trans_type: str, trans_date: date,
                   ref_type: str, ref_id: int | None, unit_cost=None, note: str | None = None
                   ) -> InventoryTransaction:
    """Ghi 1 dòng thẻ kho và cập nhật tồn + giá vốn BQGQ của mặt hàng.

    - Nhập có giá (mua hàng, tồn đầu, khách trả): tính lại giá vốn bình quân.
    - Xuất: lấy giá vốn hiện tại làm unit_cost.
    """
    qty = D(qty_change)
    if not product.track_stock:
        raise BusinessError(f"Mặt hàng {product.sku} không theo dõi tồn kho")
    old_qty = D(product.current_stock)
    old_avg = D(product.cost_price)
    new_qty = old_qty + qty

    if qty < 0 and new_qty < 0:
        msg = f"Tồn kho {product.sku} không đủ: tồn {old_qty}, xuất {-qty} (sau xuất: {new_qty})"
        if not settings.allow_negative_stock:
            raise StockError(msg)
        _warn(session, msg)

    if qty > 0 and unit_cost is not None and trans_type in _IN_TYPES:
        cost = D(unit_cost)
        if old_qty <= 0 or new_qty <= 0:
            new_avg = cost
        else:
            new_avg = round_price((old_qty * old_avg + qty * cost) / new_qty)
        line_cost = cost
    else:
        new_avg = old_avg
        line_cost = D(unit_cost) if unit_cost is not None else old_avg

    product.current_stock = new_qty
    product.cost_price = new_avg
    txn = InventoryTransaction(
        trans_date=trans_date, product_id=product.id, ref_type=ref_type, ref_id=ref_id,
        trans_type=trans_type, qty_change=qty, balance_after=new_qty, unit_cost=line_cost,
        avg_cost_after=new_avg, note=note,
    )
    session.add(txn)
    if product.min_stock and new_qty <= D(product.min_stock):
        _warn(session, f"{product.sku} sắp hết: tồn {new_qty} ≤ định mức {product.min_stock}")
    return txn


def reverse_ref(session: Session, *, ref_type: str, ref_id: int, trans_date: date, note: str) -> None:
    """Đảo phần ảnh hưởng CÒN LẠI của một chứng từ lên kho (khi sửa / hủy).

    Cộng dồn mọi dòng thẻ kho của chứng từ (kể cả các lần đảo trước) để chỉ đảo phần chưa đảo.
    Với phần nhập, giá vốn bình quân được tính lại bằng cách rút đúng giá trị đã nhập."""
    rows = session.scalars(
        select(InventoryTransaction).where(InventoryTransaction.ref_type == ref_type,
                                           InventoryTransaction.ref_id == ref_id)
    ).all()
    net: dict[int, list[Decimal]] = {}
    for row in rows:
        qty_value = net.setdefault(row.product_id, [ZERO, ZERO])
        qty_value[0] += D(row.qty_change)
        qty_value[1] += D(row.qty_change) * D(row.unit_cost)
    for product_id, (net_qty, net_value) in net.items():
        if net_qty == 0:
            continue
        product = session.get(Product, product_id)
        unit_cost = round_price(net_value / net_qty)
        qty = -net_qty
        old_qty = D(product.current_stock)
        new_qty = old_qty + qty
        if net_qty > 0 and new_qty > 0:
            value = old_qty * D(product.cost_price) - net_value
            if value > 0:
                product.cost_price = round_price(value / new_qty)
        product.current_stock = new_qty
        if new_qty < 0:
            _warn(session, f"Sau khi đảo chứng từ, tồn {product.sku} âm: {new_qty}")
        session.add(InventoryTransaction(
            trans_date=trans_date, product_id=product.id, ref_type=ref_type, ref_id=ref_id,
            trans_type=InvTransType.REVERSAL, qty_change=qty, balance_after=new_qty,
            unit_cost=unit_cost, avg_cost_after=product.cost_price, note=note,
        ))
    session.flush()


def set_opening_stock(session: Session, *, product_id: int, qty, unit_cost, on_date: date) -> InventoryTransaction:
    product = session.get(Product, product_id)
    if product is None:
        raise BusinessError(f"Không có mặt hàng id={product_id}")
    return apply_movement(session, product, qty_change=qty, trans_type=InvTransType.OPENING, trans_date=on_date,
                          ref_type="OPENING", ref_id=None, unit_cost=unit_cost, note="Tồn đầu kỳ")


def create_stocktake(session: Session, *, on_date: date, counted: dict[int, object], note: str | None = None
                     ) -> Stocktake:
    """Kiểm kê: so tồn sổ sách với tồn thực tế, tự sinh dòng điều chỉnh (cân bằng kho)."""
    if not counted:
        raise BusinessError("Phiếu kiểm kê không có mặt hàng nào")
    st = Stocktake(doc_no=next_number(session, "KK", on_date), stocktake_date=on_date, note=note)
    session.add(st)
    session.flush()
    for product_id, real_qty in counted.items():
        product = session.get(Product, int(product_id))
        if product is None:
            raise BusinessError(f"Không có mặt hàng id={product_id}")
        system_qty = D(product.current_stock)
        real = D(real_qty)
        diff = real - system_qty
        st.lines.append(StocktakeLine(product_id=product.id, system_qty=system_qty, counted_qty=real, diff_qty=diff))
        if diff != 0:
            apply_movement(session, product, qty_change=diff, trans_type=InvTransType.ADJUSTMENT, trans_date=on_date,
                           ref_type="STOCKTAKE", ref_id=st.id, unit_cost=product.cost_price,
                           note=f"Cân bằng kho theo kiểm kê {st.doc_no}")
    session.flush()
    return st


@dataclass
class StockCardLine:
    trans_date: date
    trans_type: str
    ref_type: str
    ref_id: int | None
    qty_in: Decimal
    qty_out: Decimal
    balance: Decimal
    unit_cost: Decimal
    note: str | None


@dataclass
class StockCard:
    product_id: int
    sku: str
    opening_qty: Decimal
    lines: list[StockCardLine] = field(default_factory=list)
    closing_qty: Decimal = ZERO


def stock_card(session: Session, product_id: int, date_from: date, date_to: date) -> StockCard:
    product = session.get(Product, product_id)
    if product is None:
        raise BusinessError(f"Không có mặt hàng id={product_id}")
    rows = session.scalars(
        select(InventoryTransaction).where(InventoryTransaction.product_id == product_id,
                                           InventoryTransaction.trans_date <= date_to)
        .order_by(InventoryTransaction.trans_date, InventoryTransaction.id)
    ).all()
    opening = sum((D(r.qty_change) for r in rows if r.trans_date < date_from), ZERO)
    card = StockCard(product_id=product_id, sku=product.sku, opening_qty=opening)
    bal = opening
    for r in rows:
        if r.trans_date < date_from:
            continue
        q = D(r.qty_change)
        bal += q
        card.lines.append(StockCardLine(r.trans_date, r.trans_type, r.ref_type, r.ref_id,
                                        q if q > 0 else ZERO, -q if q < 0 else ZERO, bal, D(r.unit_cost), r.note))
    card.closing_qty = bal
    return card


def nxt_report(session: Session, date_from: date, date_to: date) -> list[dict]:
    """Báo cáo Nhập – Xuất – Tồn theo kỳ (số lượng và giá trị theo giá vốn BQGQ)."""
    result = []
    for product in session.scalars(select(Product).where(Product.track_stock.is_(True)).order_by(Product.sku)):
        rows = session.scalars(
            select(InventoryTransaction).where(InventoryTransaction.product_id == product.id,
                                               InventoryTransaction.trans_date <= date_to)
            .order_by(InventoryTransaction.trans_date, InventoryTransaction.id)
        ).all()
        opening = qty_in = qty_out = ZERO
        value_in = value_out = ZERO
        opening_cost = closing_cost = ZERO
        for r in rows:
            q = D(r.qty_change)
            if r.trans_date < date_from:
                opening += q
                opening_cost = D(r.avg_cost_after)
                continue
            if q > 0:
                qty_in += q
                value_in += q * D(r.unit_cost)
            else:
                qty_out += -q
                value_out += -q * D(r.unit_cost)
            closing_cost = D(r.avg_cost_after)
        if not rows:
            continue
        closing = opening + qty_in - qty_out
        if closing_cost == ZERO:
            closing_cost = opening_cost
        result.append({
            "product_id": product.id, "sku": product.sku, "name": product.name, "unit": product.unit,
            "opening_qty": opening, "opening_value": round_money(opening * opening_cost),
            "in_qty": qty_in, "in_value": round_money(value_in),
            "out_qty": qty_out, "out_value": round_money(value_out),
            "closing_qty": closing, "closing_value": round_money(closing * closing_cost),
            "below_min": bool(product.min_stock and closing <= D(product.min_stock)),
        })
    return result


def low_stock(session: Session) -> list[Product]:
    products = session.scalars(select(Product).where(Product.track_stock.is_(True), Product.is_active.is_(True)))
    return [p for p in products if p.min_stock and D(p.current_stock) <= D(p.min_stock)]
