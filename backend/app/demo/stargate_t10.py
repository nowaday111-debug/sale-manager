"""Dữ liệu mẫu chép từ sheet T10.2026 (file 'CN T3_25 _ stargate ...xlsx') để kiểm thử và chạy thử.

Kết quả đúng phải ra: phát sinh tháng 10 = 1.195.917.000 đ; còn phải thanh toán = 1.829.047.800 đ.
Tồn kho đầu kỳ và giá vốn là số GIẢ ĐỊNH để chạy thử (file bảng kê không có).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from ..models import AssetDirection, AssetType, DebtSide, FundType, PartnerType, PriceBasis, VoucherType
from ..services import assets, cashbook, debts, inventory, masterdata, sales
from ..db import transaction

PRICE = 47500  # đ/kg, đã gồm VAT 8%

COMPANY = dict(
    parent_name="TỔNG CÔNG TY GAS PETROLIMEX",
    name="CÔNG TY TNHH CUNG ỨNG KHÍ HÓA LỎNG HÀ NỘI",
    tax_code="0107776927",
    address="Số 42, Đường Quang Tiến, Tổ dân phố An Thái, Phường Tây Mỗ,  TP Hà Nội",
    phones="ĐT: 0968.97.2011 – 0988.97.2011- 02437.533.533",
    email="Gasankhanhpt88@gmail.com",
    bank_account="21710000153851",
    bank_name="Ngân Hàng TM CP ĐT và Phát Triển Việt Nam Chi Nhánh Từ Liêm.",
    contact_line="Mr. Khánh: 0968.97.2011- 0988.97.2011",
    sign_city="Hà Nội",
)

PARTNER = dict(
    code="STG", name="CÔNG TY CỔ PHẦN TẬP ĐOÀN STARGATE", partner_type=PartnerType.CUSTOMER,
    tax_code="0101358112", address="Phòng 504, số 179 Nguyễn Ngọc Vũ, Phường Thanh Xuân, TP Hà Nội",
    phone="Anh Tuấn: 0964876425", contact_name="Anh Tuấn",
)

# (tên bếp, alias viết tay, ghi chú đầu tư, vỏ PC48 đang mượn tại 30/09/2026, bếp CN đang mượn)
SITES = [
    ("Bếp Đan Phượng - Hà Nội", ["Đan Phượng"], None, 8, 0),
    ("Bếp Thường Tín - Hà Nội", ["Thường Tín"],
     "22/7/2026 - Đầu tư lắp đặt toàn bộ hệ thống dẫn gas +4 bình cứu hỏa to + 3 bình cứu hỏa nhỏ + 01 bình cầu\n"
     "3/10/2026 - Đầu tư thêm hệ thống \n11/10/2026 - Đầu tư 8 bình cứu hỏa ( 4 bình cứu hỏa 4kg + 4 bình cứu hỏa 8kg)",
     16, 0),
    ("Bếp Đông Anh - Hà Nội", ["Đông Anh"],
     "21/7/2026 - Đầu tư lắp đặt toàn bộ hệ thống dẫn gas + 2 bình cứu hỏa to+ 4/8/2026: mượn 02 bình cứu hỏa", 16, 0),
    ("Bếp Hưng Yên", ["Hưng Yên"], "Đầu tư lắp đặt toàn bộ hệ thống dẫn gas", 2, 0),
    ("Bếp Bệnh Viện Bãi Cháy - Quảng Ninh", ["Bãi Cháy", "BV Bãi Cháy"],
     "01/12/2025 - Cty Đầu tư toàn bộ hệ thống dẫn gas", 10, 0),
    ("Bếp Tuần Châu - Quảng Ninh", ["Tuần Châu"],
     "4/3/2026 - Cty đầu tư toàn bộ hệ thống dẫn gas + 13 bếp CN + ngày 15/6/2026 Cty đầu tư lắp thêm hệ thống dẫn gas",
     25, 13),
    ("Bếp Vũng Áng - Hà Tĩnh", ["Vũng Áng", "Hà Tĩnh"],
     "01/06/2026 - Cty đầu tư toàn bộ hệ thống dẫn gas + 02 quả cầu cứu hỏa + 03 bình chữa cháy + 1 bộ tiêu lệnh", 31, 0),
    ("Bếp Thanh Hóa", ["Thanh Hóa"],
     "06/06/2026 - Cty đầu tư toàn bộ hệ thống dẫn gas + 02 quả cầu cứu hỏa + 02 bình chữa cháy + 1 bộ tiêu lệnh", 34, 0),
    ("Bếp Stargate Hải Phòng", ["Stagate Hải Phòng", "Hải Phòng"],
     "30/6/2026 - Cty Đầu tư toàn bộ hệ thống dẫn gas + 07 bình cứu hỏa", 15, 0),
]

# (bếp, ngày, số HĐ, số bình PC48, gas dư kg, ghi chú vỏ/quà trên phiếu)
SLIPS = [
    ("Bếp Đan Phượng - Hà Nội", date(2026, 10, 15), "4821", 10, "19", [("LEND", 2, "Mượn 02 vỏ pc48")]),
    ("Bếp Thường Tín - Hà Nội", date(2026, 10, 3), "4638", 24, "42.4", [("LEND", 8, "Mượn 08 vỏ pc48")]),
    ("Bếp Thường Tín - Hà Nội", date(2026, 10, 11), "4751", 20, "65.1", []),
    ("Bếp Thường Tín - Hà Nội", date(2026, 10, 20), "4959", 24, "27.3", [("GIFT", 10, "Tặng 10 tạp dề")]),
    ("Bếp Thường Tín - Hà Nội", date(2026, 10, 30), "5172", 16, "9.2", []),
    ("Bếp Đông Anh - Hà Nội", date(2026, 10, 1), "4619", 16, "58", []),
    ("Bếp Đông Anh - Hà Nội", date(2026, 10, 7), "4702", 11, "38", [("GIFT", 10, "Tặng 10 núm bếp + 2 mét dây cao su")]),
    ("Bếp Đông Anh - Hà Nội", date(2026, 10, 12), "4767", 13, "40", []),
    ("Bếp Đông Anh - Hà Nội", date(2026, 10, 15), "4822", 16, "136.4", [("LEND", 6, "Mượn 06 vỏ pc48")]),
    ("Bếp Đông Anh - Hà Nội", date(2026, 10, 20), "4963", 17, "50", []),
    ("Bếp Đông Anh - Hà Nội", date(2026, 10, 26), "5062", 19, "58.8", []),
    ("Bếp Đông Anh - Hà Nội", date(2026, 10, 30), "5178", 16, "83", []),
    ("Bếp Bệnh Viện Bãi Cháy - Quảng Ninh", date(2026, 10, 1), "4621", 4, "3", []),
    ("Bếp Bệnh Viện Bãi Cháy - Quảng Ninh", date(2026, 10, 6), "4687", 7, "19", []),
    ("Bếp Bệnh Viện Bãi Cháy - Quảng Ninh", date(2026, 10, 15), "4820", 4, "3", []),
    ("Bếp Bệnh Viện Bãi Cháy - Quảng Ninh", date(2026, 10, 23), "5002", 8, "26", []),
    ("Bếp Tuần Châu - Quảng Ninh", date(2026, 10, 6), "4686", 16, "63", []),
    ("Bếp Tuần Châu - Quảng Ninh", date(2026, 10, 12), "4763", 20, "55", []),
    ("Bếp Tuần Châu - Quảng Ninh", date(2026, 10, 18), "4876", 18, "36", []),
    ("Bếp Tuần Châu - Quảng Ninh", date(2026, 10, 26), "5063", 28, "39", [("LEND", 5, "Mượn 05 vỏ pc48")]),
    ("Bếp Vũng Áng - Hà Tĩnh", date(2026, 10, 10), "4739", 18, "26", []),
    ("Bếp Vũng Áng - Hà Tĩnh", date(2026, 10, 20), "4965", 37, "47.5", [("LEND", 6, "Mượn 06 vỏ pc48")]),
    ("Bếp Vũng Áng - Hà Tĩnh", date(2026, 10, 30), "5165", 27, "37.8", []),
    ("Bếp Thanh Hóa", date(2026, 10, 4), "4642", 24, "78", []),
    ("Bếp Thanh Hóa", date(2026, 10, 10), "4740", 17, "50.8", []),
    ("Bếp Thanh Hóa", date(2026, 10, 15), "4841", 28, "209", []),
    ("Bếp Thanh Hóa", date(2026, 10, 25), "5043", 28, "107", []),
    ("Bếp Thanh Hóa", date(2026, 10, 31), "5164", 16, "117", []),
    ("Bếp Stargate Hải Phòng", date(2026, 10, 1), "4620", 15, "11", []),
    ("Bếp Stargate Hải Phòng", date(2026, 10, 12), "4764", 15, "13.5", []),
    ("Bếp Stargate Hải Phòng", date(2026, 10, 22), "4991", 15, "62", []),
    ("Bếp Stargate Hải Phòng", date(2026, 10, 30), "5168", 14, "120", []),
]

# Đầu tư không gắn phiếu (dòng diễn giải trong khối bếp)
INVESTMENTS = [
    ("Bếp Thường Tín - Hà Nội", date(2026, 10, 11), AssetType.EXTINGUISHER, 8,
     "Cty đầu tư cho bếp 4 bình cứu hỏa 4kg + 4 bình cứu hỏa 8kg"),
    ("Bếp Thường Tín - Hà Nội", date(2026, 10, 20), AssetType.VALVE, 1, "Cty Đầu tư súng mồi 01 cái"),
    ("Bếp Thanh Hóa", date(2026, 10, 15), AssetType.VALVE, 1, "Cty Đầu tư thay 01 chế CN"),
    ("Bếp Stargate Hải Phòng", date(2026, 10, 22), AssetType.VALVE, 1, "Cty Đầu tư thay 01 chế CN"),
    ("Bếp Stargate Hải Phòng", date(2026, 10, 30), AssetType.VALVE, 2, "Cty đầu tư thay 01 chế CN + 01 khóa CN"),
]

PRIOR_DEBTS = [("2026-07", 224283700), ("2026-08", 675678850), ("2026-09", 783130800)]

PAYMENTS = [
    (date(2026, 10, 5), 150000000),
    (date(2026, 10, 14), 74283700),
    (date(2026, 10, 14), 225716300),
    (date(2026, 10, 20), 150000000),
    (date(2026, 10, 26), 299962550),
    (date(2026, 10, 28), 150000000),
]

EXPECTED_SITE_TOTALS = {
    "Bếp Đan Phượng - Hà Nội": 21897500,
    "Bếp Thường Tín - Hà Nội": 184680000,
    "Bếp Đông Anh - Hà Nội": 224190500,
    "Bếp Hưng Yên": 0,
    "Bếp Bệnh Viện Bãi Cháy - Quảng Ninh": 50017500,
    "Bếp Tuần Châu - Quảng Ninh": 177792500,
    "Bếp Vũng Áng - Hà Tĩnh": 181673250,
    "Bếp Thanh Hóa": 230954500,
    "Bếp Stargate Hải Phòng": 124711250,
}
EXPECTED_MONTH_TOTAL = 1195917000
EXPECTED_CLOSING = 1829047800


def seed(session: Session) -> dict:
    """Nạp toàn bộ dữ liệu mẫu. Trả về id các đối tượng chính."""
    with transaction(session):
        masterdata.upsert_company(session, **COMPANY)
        pc48 = masterdata.create_product(session, sku="PC48", name="Gas Petrolimex bình 48kg", capacity_kg=48,
                                         default_price_basis=PriceBasis.KG, min_stock=20)
        masterdata.create_product(session, sku="PC45", name="Gas Petrolimex bình 45kg", capacity_kg=45)
        masterdata.create_product(session, sku="PC12", name="Gas Petrolimex bình 12kg", capacity_kg=12)
        masterdata.create_product(session, sku="SUNGMOI", name="súng mồi", kind="ACCESSORY", unit="cái",
                                  default_price_basis=PriceBasis.UNIT, sale_price=180000)
        partner = masterdata.create_partner(session, **PARTNER)
        site_ids = {}
        for name, aliases, note, _cyl, _stove in SITES:
            site_ids[name] = masterdata.create_site(session, partner_id=partner.id, name=name, aliases=aliases,
                                                    investment_note=note).id
        masterdata.set_price(session, product_id=pc48.id, partner_id=partner.id, price=PRICE,
                             valid_from=date(2026, 1, 1), price_basis=PriceBasis.KG)
        # tồn kho đầu kỳ GIẢ ĐỊNH để chạy thử
        inventory.set_opening_stock(session, product_id=pc48.id, qty=700, unit_cost=1650000, on_date=date(2026, 9, 30))
        # vỏ / bếp CN đang cho mượn tại 30/09/2026 (số dư chuyển sang, không in thành dòng)
        for name, _aliases, _note, cyl, stove in SITES:
            if cyl:
                assets.add_movement(session, move_date=date(2026, 9, 30), partner_id=partner.id,
                                    site_id=site_ids[name], asset_type=AssetType.EMPTY_CYLINDER,
                                    direction=AssetDirection.LEND, qty=cyl, product_id=pc48.id,
                                    show_on_statement=False, note="Số dư chuyển sang")
            if stove:
                assets.add_movement(session, move_date=date(2026, 9, 30), partner_id=partner.id,
                                    site_id=site_ids[name], asset_type=AssetType.STOVE,
                                    direction=AssetDirection.LEND, qty=stove, show_on_statement=False,
                                    note="Số dư chuyển sang")
    for period, amount in PRIOR_DEBTS:
        debts.set_opening_balance(session, partner_id=partner.id, period=period, amount=amount,
                                  side=DebtSide.RECEIVABLE, note="Chuyển từ Excel cũ")
    slip_ids = []
    for site_name, d, inv_no, qty, residual, extras in SLIPS:
        asset_inputs = []
        for kind, q, text in extras:
            if kind == "LEND":
                asset_inputs.append(sales.AssetInput(asset_type=AssetType.EMPTY_CYLINDER, direction=AssetDirection.LEND,
                                                     qty=q, sku="PC48", description=text))
            else:
                asset_inputs.append(sales.AssetInput(asset_type=AssetType.GIFT, direction=AssetDirection.GIFT,
                                                     qty=q, description=text))
        res = sales.create_slip(session, sales.SlipInput(
            partner_id=partner.id, site_id=site_ids[site_name], slip_date=d, vat_invoice_no=inv_no,
            items=[sales.ItemInput(sku="PC48", qty=qty)], gas_residual_kg=residual, assets=asset_inputs,
        ))
        slip_ids.append(res.slip.id)
    for site_name, d, atype, q, text in INVESTMENTS:
        assets.record_movement(session, move_date=d, partner_id=partner.id, site_id=site_ids[site_name],
                               asset_type=atype, direction=AssetDirection.INVEST, qty=q, description=text)
    for d, amount in PAYMENTS:
        cashbook.create_voucher(session, voucher_type=VoucherType.RECEIPT, voucher_date=d, amount=amount,
                                fund_type=FundType.BANK, partner_id=partner.id,
                                description=f"Stargate chuyển khoản {d:%d/%m/%Y}")
    return {"partner_id": partner.id, "site_ids": site_ids, "slip_ids": slip_ids, "pc48_id": pc48.id}
