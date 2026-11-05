"""Schema dữ liệu vào (Pydantic v2) + chuyển ORM -> dict cho JSON trả về. Tiền luôn là Decimal."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import inspect

from .models import AssetDirection, AssetType, DocStatus, FundType, PartnerType, PriceBasis, ProductKind, SlipType


class _In(BaseModel):
    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------- danh mục
class CompanyIn(_In):
    name: str
    parent_name: Optional[str] = None
    tax_code: Optional[str] = None
    address: Optional[str] = None
    phones: Optional[str] = None
    email: Optional[str] = None
    bank_account: Optional[str] = None
    bank_name: Optional[str] = None
    contact_line: Optional[str] = None
    sign_city: str = "Hà Nội"
    short_name: Optional[str] = None


class PartnerIn(_In):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1)
    partner_type: str = PartnerType.CUSTOMER
    tax_code: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    contact_name: Optional[str] = None
    credit_limit: Optional[Decimal] = None
    payment_term_days: int = Field(default=30, ge=0, le=3650)
    email: Optional[str] = None
    representative: Optional[str] = None
    representative_title: Optional[str] = None
    workbook_path: Optional[str] = None
    note: Optional[str] = None
    vat_mode: str = "INCLUDED"
    statement_template: Optional[str] = None
    statement_cut_day: Optional[int] = Field(default=None, ge=0, le=27)
    seller_company_id: Optional[int] = None


class PartnerPatch(_In):
    code: Optional[str] = None
    name: Optional[str] = None
    partner_type: Optional[str] = None
    tax_code: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    contact_name: Optional[str] = None
    credit_limit: Optional[Decimal] = None
    payment_term_days: Optional[int] = Field(default=None, ge=0, le=3650)
    email: Optional[str] = None
    representative: Optional[str] = None
    representative_title: Optional[str] = None
    workbook_path: Optional[str] = None
    note: Optional[str] = None
    is_active: Optional[bool] = None
    vat_mode: Optional[str] = None
    statement_template: Optional[str] = None
    statement_cut_day: Optional[int] = Field(default=None, ge=0, le=27)
    seller_company_id: Optional[int] = None


class SiteIn(_In):
    name: str
    address: Optional[str] = None
    region: Optional[str] = None
    investment_note: Optional[str] = None
    sort_order: Optional[int] = None
    aliases: list[str] = []


class AliasIn(_In):
    alias: str


class ProductIn(_In):
    sku: str
    name: str
    kind: str = ProductKind.GAS_CYLINDER
    capacity_kg: Optional[Decimal] = None
    unit: str = "bình"
    vat_rate: Decimal = Decimal("0.08")
    sale_price: Decimal = Decimal("0")
    wholesale_price: Decimal = Decimal("0")
    default_price_basis: str = PriceBasis.KG
    min_stock: Decimal = Decimal("0")
    track_stock: bool = True


class PriceIn(_In):
    product_id: int
    price: Decimal
    valid_from: date
    partner_id: Optional[int] = None
    price_basis: str = PriceBasis.KG
    vat_included: bool = True


# ---------------------------------------------------------------- bán hàng
class SlipItemIn(_In):
    product_id: Optional[int] = None
    sku: Optional[str] = None
    qty: Decimal
    unit_price: Optional[Decimal] = None
    price_basis: Optional[str] = None
    capacity_kg: Optional[Decimal] = None
    description: Optional[str] = None


class SlipAssetIn(_In):
    asset_type: str
    direction: str
    qty: Decimal = Decimal("0")
    product_id: Optional[int] = None
    sku: Optional[str] = None
    description: Optional[str] = None
    deposit_amount: Decimal = Decimal("0")

    @field_validator("asset_type")
    @classmethod
    def _atype(cls, v):
        if v not in AssetType.ALL:
            raise ValueError(f"asset_type phải thuộc {sorted(AssetType.ALL)}")
        return v

    @field_validator("direction")
    @classmethod
    def _dir(cls, v):
        if v not in AssetDirection.ALL:
            raise ValueError(f"direction phải thuộc {sorted(AssetDirection.ALL)}")
        return v


class SlipIn(_In):
    partner_id: int
    slip_date: date
    items: list[SlipItemIn]
    doc_type: str = SlipType.SALE
    site_id: Optional[int] = None
    slip_no: Optional[str] = None
    vat_invoice_no: Optional[str] = None
    staff: Optional[str] = None
    customer_signed: bool = False
    gas_residual_kg: Decimal = Decimal("0")
    residual_price: Optional[Decimal] = None
    other_deduction: Decimal = Decimal("0")
    deduction_note: Optional[str] = None
    note: Optional[str] = None
    status: str = DocStatus.POSTED
    source_type: str = "MANUAL"
    image_path: Optional[str] = None
    ocr_json: Optional[dict] = None
    assets: list[SlipAssetIn] = []
    paid_amount: Decimal = Decimal("0")
    paid_fund: str = FundType.CASH
    vat_mode: Optional[str] = None          # để trống = theo khai báo của khách
    vat_rate: Optional[Decimal] = None      # để trống = thuế suất mặt hàng (8%)


class ReasonIn(_In):
    reason: str = Field(min_length=1)


class PurchaseItemIn(_In):
    qty: Decimal
    unit_price: Decimal
    product_id: Optional[int] = None
    sku: Optional[str] = None
    vat_rate: Optional[Decimal] = None
    description: Optional[str] = None


class PurchaseIn(_In):
    supplier_id: int
    doc_date: date
    items: list[PurchaseItemIn]
    doc_type: str = "PURCHASE"
    invoice_symbol: Optional[str] = None
    invoice_no: Optional[str] = None
    note: Optional[str] = None
    paid_amount: Decimal = Decimal("0")
    paid_fund: str = FundType.BANK


# ---------------------------------------------------------------- kho / tài sản
class OpeningStockIn(_In):
    product_id: int
    qty: Decimal
    unit_cost: Decimal
    on_date: date


class StocktakeIn(_In):
    on_date: date
    counted: dict[int, Decimal]
    note: Optional[str] = None


class AssetMovementIn(SlipAssetIn):
    move_date: date
    partner_id: int
    site_id: Optional[int] = None
    show_on_statement: bool = True
    note: Optional[str] = None


# ---------------------------------------------------------------- quỹ / công nợ
class AllocationIn(_In):
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount: Decimal


class VoucherIn(_In):
    voucher_type: str
    voucher_date: date
    amount: Decimal
    fund_type: str = FundType.BANK
    partner_id: Optional[int] = None
    category: Optional[str] = None
    description: Optional[str] = None
    payer_payee: Optional[str] = None
    ref_slip_id: Optional[int] = None
    allocations: Optional[list[AllocationIn]] = None
    affects_debt: bool = True


class ReallocateIn(_In):
    allocations: list[AllocationIn]


class FundOpeningIn(_In):
    fund_type: str
    as_of: date
    amount: Decimal


class OpeningBalanceIn(_In):
    partner_id: int
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount: Decimal
    side: str = "RECEIVABLE"
    note: Optional[str] = None


class StatementSaveIn(_In):
    partner_id: int
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    sign_date: Optional[date] = None


class StatementExportIn(_In):
    partner_id: int
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    sign_date: Optional[date] = None
    workbook_path: Optional[str] = None      # để trống = dùng partners.workbook_path
    replace: bool = False                     # ghi đè sheet cùng tên
    hide_old_months: bool = True


class StatusIn(_In):
    status: str


# ---------------------------------------------------------------- ORM -> dict
def row_dict(obj, *, include: tuple[str, ...] = ()) -> dict:
    if obj is None:
        return None
    mapper = inspect(obj).mapper
    data = {c.key: getattr(obj, c.key) for c in mapper.column_attrs}
    for name in include:
        value = getattr(obj, name)
        if isinstance(value, list):
            data[name] = [row_dict(v) for v in value]
        else:
            data[name] = row_dict(value) if value is not None else None
    return data


def slip_dict(slip) -> dict:
    data = row_dict(slip)
    data["period"] = slip.period
    data["partner_name"] = slip.partner.name if slip.partner else None
    data["site_name"] = slip.site.name if slip.site else None
    data["items"] = []
    for it in slip.items:
        d = row_dict(it)
        d["sku"] = it.product.sku
        d["gross_kg"] = it.gross_kg
        data["items"].append(d)
    # tài sản (vỏ mượn, đầu tư, quà tặng) ghi kèm phiếu – để màn hình sửa phiếu nạp lại đủ
    from sqlalchemy import select as _select

    from .models import AssetMovement
    sess = inspect(slip).session
    data["assets"] = []
    if sess is not None and slip.id:
        for mv in sess.scalars(_select(AssetMovement).where(AssetMovement.slip_id == slip.id)
                               .order_by(AssetMovement.id)):
            data["assets"].append({"asset_type": mv.asset_type, "direction": mv.direction, "qty": mv.qty,
                                   "product_id": mv.product_id, "sku": mv.product.sku if mv.product else None,
                                   "description": mv.description, "deposit_amount": mv.deposit_amount})
    return data
