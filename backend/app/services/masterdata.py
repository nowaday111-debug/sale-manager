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
    company = session.scalar(select(CompanyProfile).limit(1))
    if company is None:
        raise BusinessError("Chưa khai báo thông tin công ty (company_profile)")
    return company


def upsert_company(session: Session, **fields) -> CompanyProfile:
    company = session.scalar(select(CompanyProfile).limit(1))
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
                   representative_title=None) -> Partner:
    if partner_type not in PartnerType.ALL:
        raise BusinessError(f"Loại đối tác không hợp lệ: {partner_type}")
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
                      representative=representative, representative_title=representative_title)
    session.add(partner)
    session.flush()
    return partner


def update_partner(session: Session, partner_id: int, **fields) -> Partner:
    partner = get_partner(session, partner_id)
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


def list_partners(session: Session, *, partner_type: str | None = None, q: str | None = None) -> list[Partner]:
    stmt = select(Partner).where(Partner.is_active.is_(True)).order_by(Partner.name)
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
