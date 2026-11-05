"""Danh mục: khách hàng/NCC, bếp (điểm giao), hàng hóa, bảng giá."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import BusinessError, DuplicateError, NotFoundError
from ..models import (
    CompanyProfile,
    Partner,
    PartnerType,
    StatementTemplate,
    VatMode,
    PriceBasis,
    PriceList,
    Product,
    ProductKind,
    Site,
    SiteAlias,
)
from ..money import D


def normalize_text(text: str) -> str:
    """Bỏ dấu tiếng Việt, chữ thường, gộp khoảng trắng: 'Bếp Đông Anh' -> 'bep dong anh'."""
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^0-9a-zA-Z]+", " ", text).strip().lower()
    return re.sub(r"\s+", " ", text)


def normalize_tax_code(tax_code) -> str | None:
    """MST luôn là chuỗi; MST 9 chữ số (bị Excel mất số 0 đầu) được bù thành 10 số."""
    if tax_code is None:
        return None
    text = re.sub(r"[^0-9\-]", "", str(tax_code).strip())
    if not text:
        return None
    main = text.split("-")[0]
    if len(main) == 9:
        text = "0" + text
    return text


# ---------------------------------------------------------------- công ty
def get_company(session: Session) -> CompanyProfile:
    """Công ty chính (dòng đầu tiên). Các đơn vị bán hàng khác là các dòng tiếp theo."""
    company = session.scalar(select(CompanyProfile).order_by(CompanyProfile.id).limit(1))
    if company is None:
        raise BusinessError("Chưa khai báo thông tin công ty (company_profile)")
    return company


def get_seller_for(session: Session, partner: Partner) -> CompanyProfile:
    """Đơn vị bán hàng in trên bảng kê của khách: theo khai báo của khách, mặc định là công ty chính."""
    if partner.seller_company_id:
        company = session.get(CompanyProfile, partner.seller_company_id)
        if company is not None:
            return company
    return get_company(session)


def list_companies(session: Session) -> list[CompanyProfile]:
    return list(session.scalars(select(CompanyProfile).order_by(CompanyProfile.id)))


def create_company(session: Session, **fields) -> CompanyProfile:
    if not (fields.get("name") or "").strip():
        raise BusinessError("Thiếu tên đơn vị bán hàng")
    company = CompanyProfile(**fields)
    company.tax_code = normalize_tax_code(fields.get("tax_code"))
    session.add(company)
    session.flush()
    return company


def update_company(session: Session, company_id: int, **fields) -> CompanyProfile:
    company = session.get(CompanyProfile, company_id)
    if company is None:
        raise NotFoundError("đơn vị bán hàng", company_id)
    for k, v in fields.items():
        setattr(company, k, normalize_tax_code(v) if k == "tax_code" else v)
    session.flush()
    return company


def delete_company(session: Session, company_id: int) -> None:
    company = session.get(CompanyProfile, company_id)
    if company is None:
        raise NotFoundError("đơn vị bán hàng", company_id)
    if company.id == get_company(session).id:
        raise BusinessError("Không xóa được công ty chính (dòng đầu tiên) – chỉ sửa thông tin")
    for p in session.scalars(select(Partner).where(Partner.seller_company_id == company_id)):
        p.seller_company_id = None
    session.delete(company)
    session.flush()


def upsert_company(session: Session, **fields) -> CompanyProfile:
    company = session.scalar(select(CompanyProfile).order_by(CompanyProfile.id).limit(1))
    if company is None:
        company = CompanyProfile(**fields)
        session.add(company)
    else:
        for k, v in fields.items():
            setattr(company, k, v)
    if "tax_code" in fields:
        company.tax_code = normalize_tax_code(fields["tax_code"])
    session.flush()
    return company


# ---------------------------------------------------------------- đối tác
def create_partner(session: Session, *, code: str, name: str, partner_type: str = PartnerType.CUSTOMER,
                   tax_code=None, address=None, phone=None, contact_name=None, credit_limit=None,
                   workbook_path=None, note=None, payment_term_days: int = 30, email=None, representative=None,
                   representative_title=None, vat_mode: str = VatMode.INCLUDED,
                   statement_template: str | None = None, statement_cut_day: int | None = None,
                   seller_company_id: int | None = None) -> Partner:
    if partner_type not in PartnerType.ALL:
        raise BusinessError(f"Loại đối tác không hợp lệ: {partner_type}")
    statement_template = statement_template or (
        StatementTemplate.VAT_DETAIL if vat_mode == VatMode.EXCLUDED else StatementTemplate.STANDARD)
    _check_statement_fields(session, vat_mode=vat_mode, statement_template=statement_template,
                            statement_cut_day=statement_cut_day, seller_company_id=seller_company_id)
    code = code.strip().upper()
    if session.scalar(select(Partner).where(Partner.code == code)):
        raise DuplicateError(f"Mã đối tác {code} đã tồn tại")
    tax = normalize_tax_code(tax_code)
    if tax and session.scalar(select(Partner).where(Partner.tax_code == tax)):
        raise DuplicateError(f"MST {tax} đã có trong danh mục")
    partner = Partner(code=code, name=name.strip(), partner_type=partner_type, tax_code=tax,
                      address=address, phone=phone, contact_name=contact_name,
                      credit_limit=D(credit_limit) if credit_limit is not None else None,
                      workbook_path=workbook_path, note=note, payment_term_days=payment_term_days, email=email,
                      representative=representative, representative_title=representative_title,
                      vat_mode=vat_mode, statement_template=statement_template,
                      statement_cut_day=statement_cut_day or None, seller_company_id=seller_company_id)
    session.add(partner)
    session.flush()
    return partner


def _check_statement_fields(session: Session, **f) -> None:
    if f.get("vat_mode") is not None and f["vat_mode"] not in VatMode.ALL:
        raise BusinessError(f"Cách tính thuế không hợp lệ: {f['vat_mode']}")
    if f.get("statement_template") is not None and f["statement_template"] not in StatementTemplate.ALL:
        raise BusinessError(f"Mẫu bảng kê không hợp lệ: {f['statement_template']}")
    cut = f.get("statement_cut_day")
    if cut not in (None, 0) and not 1 <= int(cut) <= 27:
        raise BusinessError("Ngày chốt bảng kê phải từ 1 đến 27 (để trống = hết tháng)")
    if f.get("seller_company_id") and session.get(CompanyProfile, f["seller_company_id"]) is None:
        raise NotFoundError("đơn vị bán hàng", f["seller_company_id"])


def update_partner(session: Session, partner_id: int, **fields) -> Partner:
    partner = get_partner(session, partner_id)
    _check_statement_fields(session, **{k: v for k, v in fields.items()
                                        if k in ("vat_mode", "statement_template", "statement_cut_day",
                                                 "seller_company_id")})
    if fields.get("statement_cut_day") == 0:
        fields["statement_cut_day"] = None
    if "statement_cut_day" in fields and fields["statement_cut_day"] != partner.statement_cut_day:
        _ensure_cut_day_change_safe(session, partner)
    for key, value in fields.items():
        if key == "tax_code":
            value = normalize_tax_code(value)
        if key == "credit_limit" and value is not None:
            value = D(value)
        if key == "code":
            value = value.strip().upper()
        setattr(partner, key, value)
    session.flush()
    return partner


def _ensure_cut_day_change_safe(session: Session, partner: Partner) -> None:
    """Đổi ngày chốt kỳ làm thay đổi kỳ của phiếu cũ – không cho đổi khi đã có bảng kê khóa sổ."""
    from ..models import Statement, StatementStatus
    locked = session.scalar(select(Statement.period).where(Statement.partner_id == partner.id,
                                                           Statement.status == StatementStatus.CONFIRMED).limit(1))
    if locked:
        raise BusinessError(f"Khách đã có bảng kê khóa sổ (kỳ {locked}) – mở khóa trước khi đổi ngày chốt kỳ")


# Chứng từ phát sinh: có 1 trong các loại này thì không xóa hẳn đối tác (chỉ ngừng theo dõi)
def partner_usage(session: Session, partner_id: int) -> dict[str, int]:
    from sqlalchemy import func

    from ..models import (
        AssetMovement,
        CashVoucher,
        DebtLedgerEntry,
        DebtReconciliation,
        DeliveryNote,
        DeliverySlip,
        OpeningBalance,
        Order,
        PaymentAllocation,
        Purchase,
        Statement,
        VatInvoice,
    )
    checks = [
        ("phiếu bán hàng", DeliverySlip, DeliverySlip.partner_id),
        ("phiếu mua hàng", Purchase, Purchase.supplier_id),
        ("phiếu thu/chi", CashVoucher, CashVoucher.partner_id),
        ("phân bổ thanh toán", PaymentAllocation, PaymentAllocation.partner_id),
        ("hóa đơn GTGT", VatInvoice, VatInvoice.partner_id),
        ("vỏ/thiết bị", AssetMovement, AssetMovement.partner_id),
        ("nợ đầu kỳ", OpeningBalance, OpeningBalance.partner_id),
        ("bảng kê", Statement, Statement.partner_id),
        ("đơn đặt hàng", Order, Order.partner_id),
        ("biên bản giao nhận", DeliveryNote, DeliveryNote.partner_id),
        ("bút toán công nợ", DebtLedgerEntry, DebtLedgerEntry.partner_id),
        ("biên bản đối chiếu", DebtReconciliation, DebtReconciliation.partner_id),
    ]
    out = {}
    for label, model, col in checks:
        n = session.scalar(select(func.count()).select_from(model).where(col == partner_id)) or 0
        if n:
            out[label] = n
    return out


def delete_partner(session: Session, partner_id: int, *, deactivate_if_used: bool = False) -> dict:
    """Xóa khách hàng / NCC.

    - Chưa có chứng từ: xóa hẳn (kèm bếp, tên gọi khác, bảng giá riêng, ánh xạ mã hàng NCC).
    - Đã có chứng từ: không xóa (giữ sổ sách đúng). deactivate_if_used=True thì chuyển sang "ngừng theo dõi"
      (ẩn khỏi danh sách, bật lại được), ngược lại báo lỗi kèm số chứng từ.
    """
    from ..models import OcrJob, SupplierProductMap

    partner = get_partner(session, partner_id)
    used = partner_usage(session, partner_id)
    if used:
        detail = ", ".join(f"{n} {label}" for label, n in used.items())
        if not deactivate_if_used:
            raise BusinessError(f"{partner.name} đã có {detail} nên không xóa được. "
                                "Có thể chọn 'Ngừng theo dõi' để ẩn khỏi danh sách.", code="partner_in_use")
        partner.is_active = False
        session.flush()
        return {"deleted": False, "deactivated": True, "usage": used}
    for row in session.scalars(select(PriceList).where(PriceList.partner_id == partner_id)):
        session.delete(row)
    for row in session.scalars(select(SupplierProductMap).where(SupplierProductMap.supplier_id == partner_id)):
        session.delete(row)
    for job in session.scalars(select(OcrJob).where(OcrJob.partner_id == partner_id)):
        job.partner_id = None
    for site in list(partner.sites):
        for al in session.scalars(select(SiteAlias).where(SiteAlias.site_id == site.id)):
            session.delete(al)
        session.delete(site)
    session.flush()
    session.delete(partner)
    session.flush()
    return {"deleted": True, "deactivated": False, "usage": {}}


def get_partner(session: Session, partner_id: int) -> Partner:
    partner = session.get(Partner, partner_id)
    if partner is None:
        raise NotFoundError("đối tác", partner_id)
    return partner


def find_partner_by_tax_code(session: Session, tax_code) -> Partner | None:
    tax = normalize_tax_code(tax_code)
    if not tax:
        return None
    return session.scalar(select(Partner).where(Partner.tax_code == tax))


def list_partners(session: Session, *, partner_type: str | None = None, q: str | None = None,
                  include_inactive: bool = False) -> list[Partner]:
    stmt = select(Partner).order_by(Partner.name)
    if not include_inactive:
        stmt = stmt.where(Partner.is_active.is_(True))
    if partner_type == PartnerType.CUSTOMER:
        stmt = stmt.where(Partner.partner_type.in_([PartnerType.CUSTOMER, PartnerType.BOTH]))
    elif partner_type == PartnerType.SUPPLIER:
        stmt = stmt.where(Partner.partner_type.in_([PartnerType.SUPPLIER, PartnerType.BOTH]))
    partners = list(session.scalars(stmt))
    if q:
        key = normalize_text(q)
        partners = [p for p in partners if key in normalize_text(f"{p.code} {p.name} {p.tax_code or ''}")]
    return partners


# ---------------------------------------------------------------- bếp / điểm giao
def create_site(session: Session, *, partner_id: int, name: str, address=None, region=None,
                investment_note=None, sort_order: int | None = None, aliases: list[str] | None = None) -> Site:
    get_partner(session, partner_id)
    if sort_order is None:
        existing = session.scalars(select(Site.sort_order).where(Site.partner_id == partner_id)).all()
        sort_order = (max(existing) + 1) if existing else 1
    site = Site(partner_id=partner_id, name=name.strip(), address=address, region=region,
                investment_note=investment_note, sort_order=sort_order)
    session.add(site)
    try:
        session.flush()
    except IntegrityError as exc:
        raise DuplicateError(f"Bếp '{name}' đã tồn tại cho khách này") from exc
    for alias in [name, *(aliases or [])]:
        add_site_alias(session, site.id, alias, ignore_duplicate=True)
    return site


def add_site_alias(session: Session, site_id: int, alias: str, *, ignore_duplicate: bool = False) -> SiteAlias | None:
    key = normalize_text(alias)
    if not key:
        return None
    existing = session.scalar(select(SiteAlias).where(SiteAlias.alias_key == key))
    if existing:
        if existing.site_id == site_id or ignore_duplicate:
            return existing
        raise DuplicateError(f"Tên '{alias}' đã gán cho bếp khác (id={existing.site_id})")
    obj = SiteAlias(site_id=site_id, alias_key=key)
    session.add(obj)
    session.flush()
    return obj


def get_site(session: Session, site_id: int) -> Site:
    site = session.get(Site, site_id)
    if site is None:
        raise NotFoundError("bếp/điểm giao", site_id)
    return site


def find_site(session: Session, text: str, partner_id: int | None = None) -> Site | None:
    """Tìm bếp theo tên viết tay: khớp alias chính xác trước, sau đó khớp 'chứa'."""
    key = normalize_text(text)
    if not key:
        return None
    alias = session.scalar(select(SiteAlias).where(SiteAlias.alias_key == key))
    if alias:
        site = session.get(Site, alias.site_id)
        if partner_id is None or site.partner_id == partner_id:
            return site
    stmt = select(Site, SiteAlias).join(SiteAlias, SiteAlias.site_id == Site.id)
    if partner_id is not None:
        stmt = stmt.where(Site.partner_id == partner_id)
    best: tuple[int, Site] | None = None
    key_ns = key.replace(" ", "")
    for site, al in session.execute(stmt):
        alias_ns = al.alias_key.replace(" ", "")
        if key in al.alias_key or al.alias_key in key or key_ns in alias_ns or alias_ns in key_ns:
            score = min(len(key_ns), len(alias_ns))
            if best is None or score > best[0]:
                best = (score, site)
    return best[1] if best else None


# ---------------------------------------------------------------- hàng hóa
def create_product(session: Session, *, sku: str, name: str, kind: str = ProductKind.GAS_CYLINDER,
                   capacity_kg=None, unit: str = "bình", vat_rate="0.08", sale_price=0, wholesale_price=0,
                   default_price_basis: str = PriceBasis.KG, min_stock=0, track_stock: bool = True) -> Product:
    if kind not in ProductKind.ALL:
        raise BusinessError(f"Loại hàng không hợp lệ: {kind}")
    if default_price_basis not in PriceBasis.ALL:
        raise BusinessError(f"Cách tính giá không hợp lệ: {default_price_basis}")
    sku = sku.strip().upper().replace(" ", "")
    if session.scalar(select(Product).where(Product.sku == sku)):
        raise DuplicateError(f"Mã hàng {sku} đã tồn tại")
    if kind == ProductKind.GAS_CYLINDER and default_price_basis == PriceBasis.KG and not capacity_kg:
        raise BusinessError("Bình gas tính giá theo kg phải có số kg/bình")
    product = Product(sku=sku, name=name, kind=kind,
                      capacity_kg=D(capacity_kg) if capacity_kg is not None else None,
                      unit=unit, vat_rate=D(vat_rate), sale_price=D(sale_price),
                      wholesale_price=D(wholesale_price), default_price_basis=default_price_basis,
                      min_stock=D(min_stock), track_stock=track_stock and kind != ProductKind.SERVICE)
    session.add(product)
    session.flush()
    return product


def get_product(session: Session, product_id: int) -> Product:
    product = session.get(Product, product_id)
    if product is None:
        raise NotFoundError("hàng hóa", product_id)
    return product


def get_product_by_sku(session: Session, sku: str) -> Product:
    product = session.scalar(select(Product).where(Product.sku == sku.strip().upper().replace(" ", "")))
    if product is None:
        raise NotFoundError("mã hàng", sku)
    return product


# ---------------------------------------------------------------- bảng giá
def set_price(session: Session, *, product_id: int, price, valid_from: date, partner_id: int | None = None,
              price_basis: str = PriceBasis.KG, vat_included: bool = True) -> PriceList:
    """Đặt giá mới từ ngày valid_from; giá cũ đang hiệu lực được đóng lại ở ngày hôm trước."""
    from datetime import timedelta

    if price_basis not in PriceBasis.ALL:
        raise BusinessError(f"Cách tính giá không hợp lệ: {price_basis}")
    get_product(session, product_id)
    open_rows = session.scalars(
        select(PriceList).where(
            PriceList.product_id == product_id,
            PriceList.partner_id.is_(None) if partner_id is None else PriceList.partner_id == partner_id,
            or_(PriceList.valid_to.is_(None), PriceList.valid_to >= valid_from),
            PriceList.valid_from < valid_from,
        )
    ).all()
    for row in open_rows:
        row.valid_to = valid_from - timedelta(days=1)
    same_day = session.scalar(
        select(PriceList).where(
            PriceList.product_id == product_id,
            PriceList.partner_id.is_(None) if partner_id is None else PriceList.partner_id == partner_id,
            PriceList.valid_from == valid_from,
        )
    )
    if same_day:
        same_day.price = D(price)
        same_day.price_basis = price_basis
        same_day.vat_included = vat_included
        session.flush()
        return same_day
    row = PriceList(product_id=product_id, partner_id=partner_id, price=D(price), price_basis=price_basis,
                    vat_included=vat_included, valid_from=valid_from)
    session.add(row)
    session.flush()
    return row


def get_price(session: Session, *, product_id: int, on_date: date, partner_id: int | None = None
              ) -> tuple[Decimal, str] | None:
    """Giá áp dụng: giá riêng của khách -> giá chung -> giá bán lẻ trên danh mục."""
    for pid in ([partner_id] if partner_id else []) + [None]:
        row = session.scalar(
            select(PriceList).where(
                PriceList.product_id == product_id,
                PriceList.partner_id.is_(None) if pid is None else PriceList.partner_id == pid,
                PriceList.valid_from <= on_date,
                or_(PriceList.valid_to.is_(None), PriceList.valid_to >= on_date),
            ).order_by(PriceList.valid_from.desc()).limit(1)
        )
        if row:
            return row.price, row.price_basis
    product = get_product(session, product_id)
    if product.sale_price:
        return product.sale_price, product.default_price_basis
    return None
