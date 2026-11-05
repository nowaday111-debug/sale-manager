"""Toàn bộ bảng dữ liệu (SQLAlchemy 2.0). Tiền, kg, đơn giá dùng DecimalType."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, DecimalType
from .money import ZERO


# ---------------------------------------------------------------- hằng số kiểu
class PartnerType:
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    BOTH = "BOTH"
    ALL = {CUSTOMER, SUPPLIER, BOTH}


class ProductKind:
    GAS_CYLINDER = "GAS_CYLINDER"   # bình gas (bán theo kg hoặc theo bình)
    ACCESSORY = "ACCESSORY"         # phụ kiện bán: súng mồi, dây, van...
    SERVICE = "SERVICE"             # công lắp đặt...
    ALL = {GAS_CYLINDER, ACCESSORY, SERVICE}


class PriceBasis:
    KG = "KG"       # đơn giá theo kg: tiền = số bình × kg/bình × giá
    UNIT = "UNIT"   # đơn giá theo bình/cái: tiền = số lượng × giá
    ALL = {KG, UNIT}


class SlipType:
    SALE = "SALE"
    RETURN_SALE = "RETURN_SALE"
    ALL = {SALE, RETURN_SALE}


class PurchaseType:
    PURCHASE = "PURCHASE"
    RETURN_PURCHASE = "RETURN_PURCHASE"
    ALL = {PURCHASE, RETURN_PURCHASE}


class DocStatus:
    DRAFT = "DRAFT"           # chờ duyệt (VD: phiếu OCR) – chưa ảnh hưởng kho/công nợ
    POSTED = "POSTED"         # đã ghi sổ
    CANCELLED = "CANCELLED"   # đã hủy (bút toán đảo đã ghi)
    ALL = {DRAFT, POSTED, CANCELLED}


class SourceType:
    MANUAL = "MANUAL"
    XML = "XML"
    EXCEL = "EXCEL"
    AI_OCR = "AI_OCR"
    ALL = {MANUAL, XML, EXCEL, AI_OCR}


class InvTransType:
    OPENING = "OPENING"
    IN_PURCHASE = "IN_PURCHASE"
    OUT_SALE = "OUT_SALE"
    IN_RETURN = "IN_RETURN"          # khách trả lại
    OUT_RETURN = "OUT_RETURN"        # trả lại nhà cung cấp
    ADJUSTMENT = "ADJUSTMENT"        # kiểm kê / cân bằng kho
    REVERSAL = "REVERSAL"            # đảo khi hủy chứng từ


class AssetType:
    EMPTY_CYLINDER = "EMPTY_CYLINDER"   # vỏ bình
    STOVE = "STOVE"                     # bếp CN
    PIPING = "PIPING"                   # hệ thống dẫn gas
    EXTINGUISHER = "EXTINGUISHER"       # bình/quả cầu cứu hỏa, bộ tiêu lệnh
    VALVE = "VALVE"                     # chế, khóa, van, súng mồi đầu tư
    GIFT = "GIFT"                       # quà tặng: tạp dề, núm bếp, dây cao su
    OTHER = "OTHER"
    ALL = {EMPTY_CYLINDER, STOVE, PIPING, EXTINGUISHER, VALVE, GIFT, OTHER}


class AssetDirection:
    LEND = "LEND"         # cho mượn (còn thu hồi) – cộng vào số đang mượn
    RETURN = "RETURN"     # khách trả lại – trừ số đang mượn
    INVEST = "INVEST"     # công ty đầu tư (không thu hồi)
    GIFT = "GIFT"         # tặng
    ALL = {LEND, RETURN, INVEST, GIFT}


class VoucherType:
    RECEIPT = "RECEIPT"   # phiếu thu
    PAYMENT = "PAYMENT"   # phiếu chi
    ALL = {RECEIPT, PAYMENT}


class FundType:
    CASH = "CASH"
    BANK = "BANK"
    ALL = {CASH, BANK}


class DebtSide:
    RECEIVABLE = "RECEIVABLE"   # phải thu khách hàng
    PAYABLE = "PAYABLE"         # phải trả nhà cung cấp
    ALL = {RECEIVABLE, PAYABLE}


class StatementStatus:
    DRAFT = "DRAFT"
    SENT = "SENT"
    CONFIRMED = "CONFIRMED"   # khách đã xác nhận -> khóa sổ kỳ
    ALL = {DRAFT, SENT, CONFIRMED}


def _now() -> datetime:
    return datetime.now()


# ---------------------------------------------------------------- danh mục
class CompanyProfile(Base):
    __tablename__ = "company_profile"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_name: Mapped[str | None] = mapped_column(String(200))      # TỔNG CÔNG TY GAS PETROLIMEX
    name: Mapped[str] = mapped_column(String(200))
    tax_code: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(String(300))
    phones: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(120))
    bank_account: Mapped[str | None] = mapped_column(String(60))
    bank_name: Mapped[str | None] = mapped_column(String(200))
    contact_line: Mapped[str | None] = mapped_column(String(300))     # "Mr. Khánh: 0968.97.2011 ..."
    sign_city: Mapped[str] = mapped_column(String(60), default="Hà Nội")


class Partner(Base):
    __tablename__ = "partners"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(250))
    tax_code: Mapped[str | None] = mapped_column(String(20), index=True)   # luôn là chuỗi, giữ số 0 đầu
    address: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(200))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    partner_type: Mapped[str] = mapped_column(String(10), default=PartnerType.CUSTOMER)
    email: Mapped[str | None] = mapped_column(String(150))
    # ---- kiểm soát công nợ (quy trình Đơn hàng -> BBGN -> Đối chiếu). credit_limit NULL/0 = không giới hạn
    credit_limit: Mapped[Decimal | None] = mapped_column(DecimalType)
    payment_term_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    # Dư nợ hiện tại trên sổ cái công nợ (debt_ledger_entries) – cập nhật tức thời khi ghi bút toán
    current_debt: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO, server_default="0")      # phải thu
    current_payable: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO, server_default="0")   # phải trả NCC
    representative: Mapped[str | None] = mapped_column(String(150))   # người đại diện ký BBĐCCN
    representative_title: Mapped[str | None] = mapped_column(String(100))
    workbook_path: Mapped[str | None] = mapped_column(String(500))    # file Excel bảng kê của khách
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    sites: Mapped[list["Site"]] = relationship(back_populates="partner", order_by="Site.sort_order")


class Site(Base):
    """Bếp / điểm giao hàng của một khách hàng (mỗi bếp = một khối trên bảng kê)."""

    __tablename__ = "sites"
    __table_args__ = (UniqueConstraint("partner_id", "name", name="uq_site_partner_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))                   # "Bếp Đông Anh - Hà Nội"
    address: Mapped[str | None] = mapped_column(String(300))
    region: Mapped[str | None] = mapped_column(String(100))
    investment_note: Mapped[str | None] = mapped_column(Text)       # dòng lịch sử đầu tư đầu khối
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    partner: Mapped[Partner] = relationship(back_populates="sites")
    aliases: Mapped[list["SiteAlias"]] = relationship(cascade="all, delete-orphan")


class SiteAlias(Base):
    """Tên viết tay/viết tắt trên phiếu ("Stagate Hải Phòng", "CT04") -> bếp."""

    __tablename__ = "site_aliases"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    alias_key: Mapped[str] = mapped_column(String(200), unique=True)  # đã bỏ dấu, chữ thường


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), unique=True)       # PC45, PC48, PC12, MINI, SUNGMOI
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default=ProductKind.GAS_CYLINDER)
    capacity_kg: Mapped[Decimal | None] = mapped_column(DecimalType)  # 45, 48, 12...
    unit: Mapped[str] = mapped_column(String(20), default="bình")
    vat_rate: Mapped[Decimal] = mapped_column(DecimalType, default=Decimal("0.08"))
    cost_price: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)   # giá vốn BQGQ / 1 đơn vị (trước VAT)
    sale_price: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)   # giá bán lẻ mặc định
    wholesale_price: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    default_price_basis: Mapped[str] = mapped_column(String(4), default=PriceBasis.KG)
    min_stock: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    current_stock: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    track_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PriceList(Base):
    """Bảng giá theo khách (partner_id NULL = giá chung). Giá hiệu lực theo khoảng ngày."""

    __tablename__ = "price_lists"
    __table_args__ = (Index("ix_price_lookup", "product_id", "partner_id", "valid_from"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    price_basis: Mapped[str] = mapped_column(String(4), default=PriceBasis.KG)
    price: Mapped[Decimal] = mapped_column(DecimalType)
    vat_included: Mapped[bool] = mapped_column(Boolean, default=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


# ---------------------------------------------------------------- bán hàng
class DeliverySlip(Base):
    """Phiếu giao hàng / Hóa đơn bán lẻ (1 phiếu giấy = 1 dòng trên bảng kê)."""

    __tablename__ = "delivery_slips"
    __table_args__ = (Index("ix_slip_partner_date", "partner_id", "slip_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(12), default=SlipType.SALE)
    slip_no: Mapped[str | None] = mapped_column(String(30), unique=True)   # số seri đỏ trên phiếu
    slip_date: Mapped[date] = mapped_column(Date, index=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"))
    vat_invoice_no: Mapped[str | None] = mapped_column(String(30))       # cột "Số hóa đơn" trên bảng kê
    staff: Mapped[str | None] = mapped_column(String(200))               # nhân viên giao/thực hiện
    customer_signed: Mapped[bool] = mapped_column(Boolean, default=False)
    subtotal: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)          # tổng dòng hàng
    gas_residual_kg: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)   # gas dư (kg)
    residual_price: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)    # đơn giá trừ gas dư /kg
    residual_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    other_deduction: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)   # "trừ tiền đầu tư còn..."
    deduction_note: Mapped[str | None] = mapped_column(String(300))
    total: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)             # thành tiền phải thu
    cost_total: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)        # giá vốn
    status: Mapped[str] = mapped_column(String(10), default=DocStatus.POSTED)
    source_type: Mapped[str] = mapped_column(String(10), default=SourceType.MANUAL)
    image_path: Mapped[str | None] = mapped_column(String(500))
    ocr_json: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)                                # cột Ghi chú
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    created_by: Mapped[str | None] = mapped_column(String(60))

    partner: Mapped[Partner] = relationship()
    site: Mapped[Site | None] = relationship()
    items: Mapped[list["DeliveryItem"]] = relationship(
        back_populates="slip", cascade="all, delete-orphan", order_by="DeliveryItem.line_no"
    )

    @property
    def period(self) -> str:
        return f"{self.slip_date.year:04d}-{self.slip_date.month:02d}"

    @property
    def sign(self) -> int:
        return -1 if self.doc_type == SlipType.RETURN_SALE else 1


class DeliveryItem(Base):
    __tablename__ = "delivery_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    slip_id: Mapped[int] = mapped_column(ForeignKey("delivery_slips.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    description: Mapped[str | None] = mapped_column(String(300))
    qty: Mapped[Decimal] = mapped_column(DecimalType)                       # số bình / số cái
    capacity_kg: Mapped[Decimal | None] = mapped_column(DecimalType)        # chụp lại kg/bình lúc bán
    price_basis: Mapped[str] = mapped_column(String(4), default=PriceBasis.KG)
    unit_price: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    vat_rate: Mapped[Decimal] = mapped_column(DecimalType, default=Decimal("0.08"))
    line_total: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    cost_at_sale: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)  # giá vốn / đơn vị lúc bán

    slip: Mapped[DeliverySlip] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()

    @property
    def gross_kg(self) -> Decimal:
        return (self.qty or ZERO) * (self.capacity_kg or ZERO)


class AssetMovement(Base):
    """Vỏ bình cho mượn, thiết bị đầu tư, quà tặng theo từng bếp."""

    __tablename__ = "asset_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    move_date: Mapped[date] = mapped_column(Date, index=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"))
    slip_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_slips.id", ondelete="SET NULL"))
    asset_type: Mapped[str] = mapped_column(String(20))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))   # loại vỏ (PC48...)
    description: Mapped[str] = mapped_column(String(300))
    qty: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    direction: Mapped[str] = mapped_column(String(10))
    deposit_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)   # tiền cược (0 = ko cược)
    show_on_statement: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text)

    site: Mapped[Site | None] = relationship()
    product: Mapped[Product | None] = relationship()


# ---------------------------------------------------------------- kho
class InventoryTransaction(Base):
    """Thẻ kho: mỗi biến động tồn của một mặt hàng."""

    __tablename__ = "inventory_transactions"
    __table_args__ = (Index("ix_inv_product_date", "product_id", "trans_date", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    trans_date: Mapped[date] = mapped_column(Date)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    ref_type: Mapped[str] = mapped_column(String(12))     # SLIP | PURCHASE | STOCKTAKE | OPENING
    ref_id: Mapped[int | None] = mapped_column(Integer)
    trans_type: Mapped[str] = mapped_column(String(12))
    qty_change: Mapped[Decimal] = mapped_column(DecimalType)
    balance_after: Mapped[Decimal] = mapped_column(DecimalType)
    unit_cost: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    avg_cost_after: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Purchase(Base):
    """Phiếu nhập mua hàng / trả hàng nhà cung cấp."""

    __tablename__ = "purchases"
    __table_args__ = (
        UniqueConstraint("supplier_id", "invoice_symbol", "invoice_no", "doc_type", name="uq_purchase_invoice"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(16), default=PurchaseType.PURCHASE)
    doc_no: Mapped[str] = mapped_column(String(30), unique=True)            # PN-2026-10-0001
    supplier_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))
    invoice_symbol: Mapped[str | None] = mapped_column(String(20))           # KHHDon
    invoice_no: Mapped[str | None] = mapped_column(String(20))               # SHDon
    doc_date: Mapped[date] = mapped_column(Date, index=True)
    subtotal: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)     # trước thuế
    vat_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    total: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    status: Mapped[str] = mapped_column(String(10), default=DocStatus.POSTED)
    source_type: Mapped[str] = mapped_column(String(10), default=SourceType.MANUAL)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    supplier: Mapped[Partner] = relationship()
    items: Mapped[list["PurchaseItem"]] = relationship(
        back_populates="purchase", cascade="all, delete-orphan", order_by="PurchaseItem.line_no"
    )

    @property
    def period(self) -> str:
        return f"{self.doc_date.year:04d}-{self.doc_date.month:02d}"

    @property
    def sign(self) -> int:
        return -1 if self.doc_type == PurchaseType.RETURN_PURCHASE else 1


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    description: Mapped[str | None] = mapped_column(String(300))
    qty: Mapped[Decimal] = mapped_column(DecimalType)
    unit_price: Mapped[Decimal] = mapped_column(DecimalType)      # trước thuế
    vat_rate: Mapped[Decimal] = mapped_column(DecimalType, default=Decimal("0.08"))
    line_total: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)   # trước thuế
    vat_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)

    purchase: Mapped[Purchase] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class Stocktake(Base):
    __tablename__ = "stocktakes"
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_no: Mapped[str] = mapped_column(String(30), unique=True)
    stocktake_date: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    lines: Mapped[list["StocktakeLine"]] = relationship(cascade="all, delete-orphan")


class StocktakeLine(Base):
    __tablename__ = "stocktake_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    stocktake_id: Mapped[int] = mapped_column(ForeignKey("stocktakes.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    system_qty: Mapped[Decimal] = mapped_column(DecimalType)
    counted_qty: Mapped[Decimal] = mapped_column(DecimalType)
    diff_qty: Mapped[Decimal] = mapped_column(DecimalType)
    product: Mapped[Product] = relationship()


# ---------------------------------------------------------------- hóa đơn GTGT
class VatInvoice(Base):
    """Hóa đơn điện tử GTGT đã xuất cho khách (thay cột phụ K–M đối chiếu)."""

    __tablename__ = "vat_invoices"
    __table_args__ = (UniqueConstraint("invoice_symbol", "invoice_no", name="uq_vat_invoice"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))
    invoice_symbol: Mapped[str | None] = mapped_column(String(20))
    invoice_no: Mapped[str] = mapped_column(String(20))
    invoice_date: Mapped[date] = mapped_column(Date)
    amount_before_tax: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    vat_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    total: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    xml_path: Mapped[str | None] = mapped_column(String(500))


class VatInvoiceSlip(Base):
    __tablename__ = "vat_invoice_slips"
    vat_invoice_id: Mapped[int] = mapped_column(ForeignKey("vat_invoices.id", ondelete="CASCADE"), primary_key=True)
    slip_id: Mapped[int] = mapped_column(ForeignKey("delivery_slips.id", ondelete="CASCADE"), primary_key=True)


# ---------------------------------------------------------------- quỹ & công nợ
class CashVoucher(Base):
    __tablename__ = "cash_vouchers"
    id: Mapped[int] = mapped_column(primary_key=True)
    voucher_no: Mapped[str] = mapped_column(String(30), unique=True)     # PT-2026-10-0001 / PC-...
    voucher_date: Mapped[date] = mapped_column(Date, index=True)
    voucher_type: Mapped[str] = mapped_column(String(8))
    fund_type: Mapped[str] = mapped_column(String(4), default=FundType.BANK)
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    ref_slip_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_slips.id", ondelete="SET NULL"))
    ref_purchase_id: Mapped[int | None] = mapped_column(ForeignKey("purchases.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(String(60), default="Thu tiền khách hàng")
    amount: Mapped[Decimal] = mapped_column(DecimalType)
    payer_payee: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(10), default=DocStatus.POSTED)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")  # khóa bởi BBĐCCN RECONCILED
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    partner: Mapped[Partner | None] = relationship()
    allocations: Mapped[list["PaymentAllocation"]] = relationship(
        back_populates="voucher", cascade="all, delete-orphan", order_by="PaymentAllocation.period"
    )


class PaymentAllocation(Base):
    """Phân bổ một phiếu thu/chi vào công nợ của từng tháng."""

    __tablename__ = "payment_allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("cash_vouchers.id", ondelete="CASCADE"), index=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), index=True)
    side: Mapped[str] = mapped_column(String(10), default=DebtSide.RECEIVABLE)
    period: Mapped[str] = mapped_column(String(7))           # YYYY-MM
    amount: Mapped[Decimal] = mapped_column(DecimalType)

    voucher: Mapped[CashVoucher] = relationship(back_populates="allocations")


class OpeningBalance(Base):
    """Số dư nợ đầu kỳ chuyển từ Excel cũ, ghi theo tháng phát sinh (VD: nợ tháng 09/2026)."""

    __tablename__ = "opening_balances"
    __table_args__ = (UniqueConstraint("partner_id", "side", "period", name="uq_opening"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))
    side: Mapped[str] = mapped_column(String(10), default=DebtSide.RECEIVABLE)
    period: Mapped[str] = mapped_column(String(7))
    amount: Mapped[Decimal] = mapped_column(DecimalType)
    note: Mapped[str | None] = mapped_column(String(300))


class FundOpening(Base):
    __tablename__ = "fund_openings"
    id: Mapped[int] = mapped_column(primary_key=True)
    fund_type: Mapped[str] = mapped_column(String(4), unique=True)
    as_of: Mapped[date] = mapped_column(Date)       # số dư tại đầu ngày này
    amount: Mapped[Decimal] = mapped_column(DecimalType)


class Statement(Base):
    """Bảng kê chi tiết công nợ tháng đã chốt."""

    __tablename__ = "statements"
    __table_args__ = (UniqueConstraint("partner_id", "period", name="uq_statement"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))
    period: Mapped[str] = mapped_column(String(7))
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    sign_date: Mapped[date] = mapped_column(Date)
    total_delivered: Mapped[Decimal] = mapped_column(DecimalType)
    prior_debt: Mapped[Decimal] = mapped_column(DecimalType)
    paid_in_period: Mapped[Decimal] = mapped_column(DecimalType)
    closing_debt: Mapped[Decimal] = mapped_column(DecimalType)
    status: Mapped[str] = mapped_column(String(10), default=StatementStatus.DRAFT)
    snapshot_json: Mapped[str] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)

    partner: Mapped[Partner] = relationship()


# ---------------------------------------------------------------- hệ thống
class SequenceCounter(Base):
    __tablename__ = "sequences"
    name: Mapped[str] = mapped_column(String(10), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    table_name: Mapped[str] = mapped_column(String(60), index=True)
    record_id: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(10))     # INSERT | UPDATE | DELETE
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)
    user: Mapped[str | None] = mapped_column(String(60))
    at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), default=_now)


# ---------------------------------------------------------------- AI đọc phiếu
class OcrJobStatus:
    PENDING_REVIEW = "PENDING_REVIEW"   # chờ người duyệt
    APPLIED = "APPLIED"                 # đã tạo phiếu / ghi tài sản
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    ALL = {PENDING_REVIEW, APPLIED, REJECTED, FAILED}


class OcrJob(Base):
    """Một lần AI đọc ảnh phiếu. Kết quả chỉ là ĐỀ XUẤT; người duyệt sửa rồi mới ghi sổ."""

    __tablename__ = "ocr_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    filename: Mapped[str | None] = mapped_column(String(300))
    image_path: Mapped[str | None] = mapped_column(String(500))
    image_sha1: Mapped[str | None] = mapped_column(String(40), index=True)
    provider: Mapped[str | None] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(60))
    prompt_version: Mapped[str | None] = mapped_column(String(30))
    raw_text: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)       # NormalizedSlip + đề xuất phiếu
    usage_json: Mapped[str | None] = mapped_column(Text)
    seconds: Mapped[Decimal | None] = mapped_column(DecimalType)
    status: Mapped[str] = mapped_column(String(16), default=OcrJobStatus.PENDING_REVIEW)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"))
    slip_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_slips.id", ondelete="SET NULL"))
    reviewed_by: Mapped[str | None] = mapped_column(String(60))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class SupplierProductMap(Base):
    """Ghi nhớ: mã/tên hàng trên hóa đơn của NCC -> mã hàng nội bộ (kèm hệ số quy đổi đơn vị)."""

    __tablename__ = "supplier_product_map"
    __table_args__ = (UniqueConstraint("supplier_id", "supplier_key", name="uq_supplier_product"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))
    supplier_key: Mapped[str] = mapped_column(String(250))        # mã hàng NCC, hoặc tên đã bỏ dấu nếu không có mã
    supplier_name: Mapped[str | None] = mapped_column(String(300))
    supplier_unit: Mapped[str | None] = mapped_column(String(30))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty_factor: Mapped[Decimal] = mapped_column(DecimalType, default=Decimal(1))   # SL nội bộ = SL trên HĐ × hệ số


# ================================================================ Đơn hàng -> BBGN -> Đối chiếu công nợ
class OrderType:
    SALES = "SALES"         # đơn bán hàng  (SO-YYYYMM-XXXX) -> công nợ phải thu
    PURCHASE = "PURCHASE"   # đơn mua hàng  (PO-YYYYMM-XXXX) -> công nợ phải trả
    ALL = {SALES, PURCHASE}


class OrderStatus:
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PARTIALLY_DELIVERED = "PARTIALLY_DELIVERED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ALL = {DRAFT, PENDING_APPROVAL, APPROVED, PARTIALLY_DELIVERED, COMPLETED, CANCELLED}
    DELIVERABLE = {APPROVED, PARTIALLY_DELIVERED}


class DeliveryNoteStatus:
    PREPARING = "PREPARING"     # đang chuẩn bị hàng – được sửa SL xuất
    IN_TRANSIT = "IN_TRANSIT"   # đã xuất kho, đang giao – khóa SL xuất, mở nhập SL thực nhận
    CONFIRMED = "CONFIRMED"     # kế toán đã xác nhận (có ảnh ký nhận) – sinh bút toán công nợ
    CANCELLED = "CANCELLED"
    ALL = {PREPARING, IN_TRANSIT, CONFIRMED, CANCELLED}


class LedgerEntryType:
    DELIVERY_INCREASE = "DELIVERY_INCREASE"   # BBGN CONFIRMED
    PAYMENT_DECREASE = "PAYMENT_DECREASE"     # phiếu thu (khách) / phiếu chi (NCC)
    RETURN_DECREASE = "RETURN_DECREASE"       # hàng trả lại / giảm giá
    ADJUSTMENT = "ADJUSTMENT"                 # điều chỉnh (tăng hoặc giảm)
    OPENING = "OPENING"                       # số dư đầu chuyển sang
    ALL = {DELIVERY_INCREASE, PAYMENT_DECREASE, RETURN_DECREASE, ADJUSTMENT, OPENING}


class LedgerRefType:
    DELIVERY_NOTE = "DELIVERY_NOTE"
    PAYMENT_VOUCHER = "PAYMENT_VOUCHER"
    CREDIT_NOTE = "CREDIT_NOTE"
    ADJUSTMENT = "ADJUSTMENT"
    ALL = {DELIVERY_NOTE, PAYMENT_VOUCHER, CREDIT_NOTE, ADJUSTMENT}


class ReconciliationStatus:
    DRAFT = "DRAFT"
    SENT = "SENT"
    DISPUTED = "DISPUTED"
    RECONCILED = "RECONCILED"
    ALL = {DRAFT, SENT, DISPUTED, RECONCILED}


class Order(Base):
    """Đơn đặt hàng bán (SO) / mua (PO). Không bao giờ ghi công nợ trực tiếp từ đơn."""

    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_partner_date", "partner_id", "order_date"),
        Index("ix_orders_status", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    order_code: Mapped[str] = mapped_column(String(30), unique=True)
    order_type: Mapped[str] = mapped_column(String(10), default=OrderType.SALES)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"))          # bếp / điểm giao
    order_date: Mapped[date] = mapped_column(Date)
    expected_delivery_date: Mapped[date | None] = mapped_column(Date)
    delivery_address: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.DRAFT)
    subtotal_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)  # trước CK, trước thuế
    discount_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    tax_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    shipping_fee: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    total_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    payment_terms: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    approval_reason: Mapped[str | None] = mapped_column(Text)   # lý do phải chờ duyệt (vượt hạn mức / quá hạn)
    approved_by: Mapped[str | None] = mapped_column(String(60))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(60), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    partner: Mapped[Partner] = relationship()
    site: Mapped[Site | None] = relationship()
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan",
                                                    order_by="OrderItem.line_no")
    delivery_notes: Mapped[list["DeliveryNote"]] = relationship(back_populates="order",
                                                                order_by="DeliveryNote.id")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    product_code: Mapped[str] = mapped_column(String(40))
    product_name: Mapped[str] = mapped_column(String(250))
    unit: Mapped[str] = mapped_column(String(30))
    ordered_qty: Mapped[Decimal] = mapped_column(DecimalType)
    delivered_qty: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)   # lũy kế accepted_qty từ BBGN
    unit_price: Mapped[Decimal] = mapped_column(DecimalType)                    # đơn giá chốt (trước thuế)
    discount_rate: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)   # % (VD 5 = 5%)
    tax_rate: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)        # % (VD 8 = 8%)
    line_total: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)      # đã gồm CK + thuế

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()

    @property
    def remaining_qty(self) -> Decimal:
        return (self.ordered_qty or ZERO) - (self.delivered_qty or ZERO)


class DeliveryNote(Base):
    """Biên bản giao nhận (BBGN) DN-YYYYMM-XXXX – một đơn có nhiều BBGN (giao từng phần)."""

    __tablename__ = "delivery_notes"
    __table_args__ = (
        Index("ix_dn_partner_date", "partner_id", "delivery_date"),
        Index("ix_dn_status", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_code: Mapped[str] = mapped_column(String(30), unique=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))
    delivery_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default=DeliveryNoteStatus.PREPARING)
    warehouse_name: Mapped[str | None] = mapped_column(String(150))
    deliverer_name: Mapped[str | None] = mapped_column(String(150))
    deliverer_phone: Mapped[str | None] = mapped_column(String(50))
    receiver_name: Mapped[str | None] = mapped_column(String(150))
    receiver_signature_url: Mapped[str | None] = mapped_column(String(500))   # ảnh BBGN có chữ ký
    total_accepted_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)  # gồm phí vận chuyển nếu có
    shipping_fee: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)   # phí VC của đơn – tính ở BBGN đầu tiên
    discrepancy_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime)
    confirmed_by: Mapped[str | None] = mapped_column(String(60))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(60), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    order: Mapped[Order] = relationship(back_populates="delivery_notes")
    partner: Mapped[Partner] = relationship()
    items: Mapped[list["DeliveryNoteItem"]] = relationship(back_populates="note", cascade="all, delete-orphan",
                                                           order_by="DeliveryNoteItem.line_no")


class DeliveryNoteItem(Base):
    __tablename__ = "delivery_note_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_note_id: Mapped[int] = mapped_column(ForeignKey("delivery_notes.id", ondelete="CASCADE"), index=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    product_code: Mapped[str] = mapped_column(String(40))
    product_name: Mapped[str] = mapped_column(String(250))
    unit: Mapped[str] = mapped_column(String(30))
    ordered_qty_snapshot: Mapped[Decimal] = mapped_column(DecimalType)
    dispatched_qty: Mapped[Decimal] = mapped_column(DecimalType)
    accepted_qty: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    rejected_qty: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    unit_price: Mapped[Decimal] = mapped_column(DecimalType)          # kế thừa order_items – read-only
    discount_rate: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    tax_rate: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    accepted_line_total: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    item_condition_note: Mapped[str | None] = mapped_column(Text)

    note: Mapped[DeliveryNote] = relationship(back_populates="items")
    order_item: Mapped[OrderItem] = relationship()


class DebtLedgerEntry(Base):
    """Sổ cái công nợ nội bộ. debit = tăng nợ, credit = giảm nợ. Không sửa/xóa: chỉ ghi bút toán điều chỉnh."""

    __tablename__ = "debt_ledger_entries"
    __table_args__ = (
        Index("ix_ledger_partner_date", "partner_id", "transaction_date"),
        Index("ix_ledger_order", "order_id"),
        Index("ix_ledger_delivery_note", "delivery_note_id"),
        Index("ix_ledger_reconciliation", "reconciliation_id"),
        Index("ix_ledger_ref", "reference_type", "reference_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))
    side: Mapped[str] = mapped_column(String(10), default=DebtSide.RECEIVABLE)
    transaction_date: Mapped[date] = mapped_column(Date)
    entry_type: Mapped[str] = mapped_column(String(20))
    reference_type: Mapped[str] = mapped_column(String(20))
    reference_id: Mapped[int | None] = mapped_column(Integer)
    reference_code: Mapped[str] = mapped_column(String(40))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    order_code: Mapped[str | None] = mapped_column(String(30))
    delivery_note_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_notes.id"))
    debit_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    credit_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    running_balance: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    due_date: Mapped[date | None] = mapped_column(Date)            # hạn thanh toán (bút toán tăng)
    description: Mapped[str | None] = mapped_column(Text)
    reconciliation_id: Mapped[int | None] = mapped_column(ForeignKey("debt_reconciliations.id"))
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(60), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    partner: Mapped[Partner] = relationship()


class DebtReconciliation(Base):
    """Biên bản đối chiếu công nợ DR-YYYYMM-XXXX."""

    __tablename__ = "debt_reconciliations"
    __table_args__ = (
        Index("ix_recon_partner_period", "partner_id", "period_from", "period_to"),
        Index("ix_recon_status", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    reconciliation_code: Mapped[str] = mapped_column(String(30), unique=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))
    side: Mapped[str] = mapped_column(String(10), default=DebtSide.RECEIVABLE)
    period_from: Mapped[date] = mapped_column(Date)
    period_to: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default=ReconciliationStatus.DRAFT)
    opening_balance: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    total_increase: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    total_decrease: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    closing_balance: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    partner_confirmed_by: Mapped[str | None] = mapped_column(String(150))
    partner_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    signed_document_url: Mapped[str | None] = mapped_column(String(500))
    dispute_notes: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(String(60), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    partner: Mapped[Partner] = relationship()
    lines: Mapped[list["DebtReconciliationLine"]] = relationship(
        back_populates="reconciliation", cascade="all, delete-orphan", order_by="DebtReconciliationLine.line_no")


class DebtReconciliationLine(Base):
    __tablename__ = "debt_reconciliation_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    reconciliation_id: Mapped[int] = mapped_column(ForeignKey("debt_reconciliations.id", ondelete="CASCADE"),
                                                   index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    ledger_entry_id: Mapped[int] = mapped_column(ForeignKey("debt_ledger_entries.id"), index=True)
    transaction_date: Mapped[date] = mapped_column(Date)
    entry_type: Mapped[str] = mapped_column(String(20))
    order_id: Mapped[int | None] = mapped_column(Integer)
    order_code: Mapped[str | None] = mapped_column(String(30))
    delivery_note_id: Mapped[int | None] = mapped_column(Integer)
    document_code: Mapped[str] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)
    increase_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    decrease_amount: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    line_balance: Mapped[Decimal] = mapped_column(DecimalType, default=ZERO)
    is_disputed: Mapped[bool] = mapped_column(Boolean, default=False)
    dispute_comment: Mapped[str | None] = mapped_column(Text)

    reconciliation: Mapped[DebtReconciliation] = relationship(back_populates="lines")


class SchemaVersion(Base):
    """Các migration đã chạy (app/migrations)."""

    __tablename__ = "schema_migrations"
    version: Mapped[str] = mapped_column(String(60), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
