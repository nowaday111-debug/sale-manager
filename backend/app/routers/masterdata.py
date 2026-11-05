from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session, transaction
from ..errors import BusinessError
from ..schemas import AliasIn, CompanyIn, PartnerIn, PartnerPatch, PriceIn, ProductIn, SiteIn, row_dict
from ..services import masterdata

router = APIRouter(prefix="/api", tags=["Danh mục"])


@router.get("/company")
def get_company(session: Session = Depends(get_session)):
    return row_dict(masterdata.get_company(session))


@router.put("/company")
def put_company(body: CompanyIn, session: Session = Depends(get_session)):
    with transaction(session):
        company = masterdata.upsert_company(session, **body.model_dump())
    return row_dict(company)


@router.get("/companies")
def list_companies(session: Session = Depends(get_session)):
    """Các đơn vị bán hàng (dòng đầu = công ty chính). Khách có thể chọn đơn vị bán in trên bảng kê."""
    return [row_dict(c) for c in masterdata.list_companies(session)]


@router.post("/companies", status_code=201)
def create_company(body: CompanyIn, session: Session = Depends(get_session)):
    with transaction(session):
        company = masterdata.create_company(session, **body.model_dump())
    return row_dict(company)


@router.put("/companies/{company_id}")
def update_company(company_id: int, body: CompanyIn, session: Session = Depends(get_session)):
    with transaction(session):
        company = masterdata.update_company(session, company_id, **body.model_dump())
    return row_dict(company)


@router.delete("/companies/{company_id}")
def delete_company(company_id: int, session: Session = Depends(get_session)):
    with transaction(session):
        masterdata.delete_company(session, company_id)
    return {"deleted": True}


@router.get("/partners")
def list_partners(partner_type: Optional[str] = None, q: Optional[str] = None, include_inactive: bool = False,
                  session: Session = Depends(get_session)):
    return [row_dict(p) for p in masterdata.list_partners(session, partner_type=partner_type, q=q,
                                                          include_inactive=include_inactive)]


@router.post("/partners", status_code=201)
def create_partner(body: PartnerIn, session: Session = Depends(get_session)):
    with transaction(session):
        partner = masterdata.create_partner(session, **body.model_dump())
    return row_dict(partner)


@router.get("/partners/{partner_id}")
def get_partner(partner_id: int, session: Session = Depends(get_session)):
    return row_dict(masterdata.get_partner(session, partner_id), include=("sites",))


@router.patch("/partners/{partner_id}")
def patch_partner(partner_id: int, body: PartnerPatch, session: Session = Depends(get_session)):
    with transaction(session):
        partner = masterdata.update_partner(session, partner_id, **body.model_dump(exclude_unset=True))
    return row_dict(partner)


@router.get("/partners/{partner_id}/usage")
def partner_usage(partner_id: int, session: Session = Depends(get_session)):
    """Số chứng từ đã phát sinh – để màn hình hỏi Xóa hay Ngừng theo dõi."""
    masterdata.get_partner(session, partner_id)
    used = masterdata.partner_usage(session, partner_id)
    return {"usage": used, "can_delete": not used}


@router.delete("/partners/{partner_id}")
def delete_partner(partner_id: int, deactivate_if_used: bool = False, session: Session = Depends(get_session)):
    """Xóa khách/NCC chưa có chứng từ. Đã có chứng từ: lỗi 400, hoặc ngừng theo dõi khi deactivate_if_used=true."""
    with transaction(session):
        return masterdata.delete_partner(session, partner_id, deactivate_if_used=deactivate_if_used)


@router.get("/partners/{partner_id}/sites")
def list_sites(partner_id: int, session: Session = Depends(get_session)):
    return [row_dict(s) for s in masterdata.get_partner(session, partner_id).sites]


@router.post("/partners/{partner_id}/sites", status_code=201)
def create_site(partner_id: int, body: SiteIn, session: Session = Depends(get_session)):
    with transaction(session):
        site = masterdata.create_site(session, partner_id=partner_id, **body.model_dump())
    return row_dict(site)


@router.post("/sites/{site_id}/aliases", status_code=201)
def add_alias(site_id: int, body: AliasIn, session: Session = Depends(get_session)):
    with transaction(session):
        masterdata.get_site(session, site_id)
        alias = masterdata.add_site_alias(session, site_id, body.alias)
    return row_dict(alias)


@router.get("/sites/find")
def find_site(text: str, partner_id: Optional[int] = None, session: Session = Depends(get_session)):
    site = masterdata.find_site(session, text, partner_id)
    return row_dict(site) if site else None


@router.get("/products")
def list_products(session: Session = Depends(get_session)):
    from sqlalchemy import select

    from ..models import Product

    return [row_dict(p) for p in session.scalars(select(Product).order_by(Product.sku))]


@router.post("/products", status_code=201)
def create_product(body: ProductIn, session: Session = Depends(get_session)):
    with transaction(session):
        product = masterdata.create_product(session, **body.model_dump())
    return row_dict(product)


@router.get("/products/{product_id}")
def get_product(product_id: int, session: Session = Depends(get_session)):
    return row_dict(masterdata.get_product(session, product_id))


@router.post("/prices", status_code=201)
def set_price(body: PriceIn, session: Session = Depends(get_session)):
    with transaction(session):
        row = masterdata.set_price(session, **body.model_dump())
    return row_dict(row)


@router.get("/prices/lookup")
def lookup_price(product_id: int, on_date: date, partner_id: Optional[int] = None,
                 session: Session = Depends(get_session)):
    found = masterdata.get_price(session, product_id=product_id, on_date=on_date, partner_id=partner_id)
    if found is None:
        raise BusinessError("Chưa có giá cho mặt hàng này", code="no_price")
    return {"price": found[0], "price_basis": found[1]}
