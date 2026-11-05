"""Dữ liệu mẫu: Cty TNHH Welstory Việt Nam – bảng kê T10.2026 (26/09 → 25/10/2026) do Cty CP Metro Toàn Cầu bán.

Lấy từ file "CN. CTy Welstory - Bắc Ninh làm việc với CTy TOÀN CẦU.xlsx", sheet T10.2026:
giá CHƯA thuế, thuế GTGT 8% tách riêng, kỳ chốt ngày 25.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import transaction
from ..models import AssetDirection, AssetType, PriceBasis, Product, StatementTemplate, VatMode
from ..services import assets, masterdata, sales

SELLER = dict(
    name="CÔNG TY CỔ PHẦN METRO TOÀN CẦU", short_name="Metro Toàn Cầu", tax_code="2300878624",
    address="Lô E3, Cụm CN Đa nghề Đông Thọ, Xã Văn Môn, Tỉnh Bắc Ninh, Việt Nam",
    phones="0968.97.2011", bank_account="1242163929",
    bank_name="Ngân hàng TMCP Đầu tư và phát triển Việt Nam - CN Hoàn Kiếm",
    contact_line="anh Khánh: 0968.97.2011", sign_city="Hà Nội",
)
PARTNER = dict(
    code="WELSTORY", name="CÔNG TY TNHH WELSTORY VIỆT NAM", tax_code="2300873626",
    address="Số 21, Đường 8, KCN Thương mại và Dịch vụ VSIP Bắc Ninh II, Xã Tam Giang, Tỉnh Bắc Ninh, Việt Nam",
    vat_mode=VatMode.EXCLUDED, statement_template=StatementTemplate.VAT_DETAIL, statement_cut_day=25,
)
# (ngày, số bình PC45, gas dư kg, đơn giá chưa thuế, [(hướng, số vỏ, ghi chú)])
SLIPS = [
    (date(2026, 9, 27), 15, Decimal("13.3"), 33061, []),
    (date(2026, 9, 30), 20, Decimal("4"), 33061, []),
    (date(2026, 10, 4), 20, Decimal("11.5"), 34682, []),
    (date(2026, 10, 7), 20, Decimal("52"), 34682, []),
    (date(2026, 10, 11), 20, Decimal("13"), 34682, []),
    (date(2026, 10, 14), 20, Decimal("48.9"), 34682, [("LEND", 3, "Mượn 03 vỏ pc45 ( 2 van )")]),
    (date(2026, 10, 19), 20, Decimal("36"), 34682, [("RETURN", 3, "Thu 03 vỏ pc45 ( 2 vạn )")]),
    (date(2026, 10, 22), 20, Decimal("32"), 34682, [("LEND", 3, "Mượn 03 vỏ pc45 ( 2 van )")]),
]
# số liệu Excel gốc tính ra (không làm tròn): Σ (B*45−C)*E*1.08
EXPECTED_BEFORE_TAX = sum(((q * 45 - Decimal(r)) * p for _d, q, r, p, _a in SLIPS), Decimal(0))
EXPECTED_TOTAL_EXACT = EXPECTED_BEFORE_TAX * Decimal("1.08")


def seed(session: Session, *, with_company: bool = True) -> dict:
    with transaction(session):
        if with_company:
            try:
                masterdata.get_company(session)
            except Exception:  # noqa: BLE001 – DB trống: tạo công ty chính trước
                masterdata.upsert_company(session, name="CÔNG TY TNHH CUNG ỨNG KHÍ HÓA LỎNG HÀ NỘI")
        seller = masterdata.create_company(session, **SELLER)
        pc45 = session.scalar(select(Product).where(Product.sku == "PC45"))
        if pc45 is None:
            pc45 = masterdata.create_product(session, sku="PC45", name="Gas Petrolimex bình 45kg", capacity_kg=45)
        partner = masterdata.create_partner(session, **PARTNER, seller_company_id=seller.id)
        site = masterdata.create_site(session, partner_id=partner.id, name="Bếp Welstory Bắc Ninh")
        masterdata.set_price(session, product_id=pc45.id, partner_id=partner.id, price=33061,
                             valid_from=date(2026, 9, 1), price_basis=PriceBasis.KG, vat_included=False)
        masterdata.set_price(session, product_id=pc45.id, partner_id=partner.id, price=34682,
                             valid_from=date(2026, 10, 1), price_basis=PriceBasis.KG, vat_included=False)
        assets.add_movement(session, move_date=date(2026, 8, 1), partner_id=partner.id, site_id=site.id,
                            asset_type=AssetType.EMPTY_CYLINDER, direction=AssetDirection.LEND, qty=23,
                            product_id=pc45.id, show_on_statement=False, note="Số dư chuyển sang")
    ids = []
    for d, qty, residual, _price, extras in SLIPS:
        res = sales.create_slip(session, sales.SlipInput(
            partner_id=partner.id, site_id=site.id, slip_date=d, items=[sales.ItemInput(sku="PC45", qty=qty)],
            gas_residual_kg=residual, skip_inventory=True,
            assets=[sales.AssetInput(asset_type=AssetType.EMPTY_CYLINDER, direction=dirn, qty=n, sku="PC45",
                                     description=text) for dirn, n, text in extras]))
        ids.append(res.slip.id)
    return {"partner_id": partner.id, "seller_id": seller.id, "site_id": site.id, "slip_ids": ids,
            "pc45_id": pc45.id}
