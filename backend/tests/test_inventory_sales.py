from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.errors import BusinessError, DuplicateError, StockError
from app.models import AssetDirection, AssetType, AuditLog, DeliverySlip, DocStatus, InventoryTransaction, PartnerType
from app.services import inventory, masterdata, purchases, sales
from app.db import transaction


@pytest.fixture()
def base(session):
    with transaction(session):
        masterdata.upsert_company(session, name="CTY TEST", tax_code="107776927")
        pc45 = masterdata.create_product(session, sku="pc 45", name="Gas 45kg", capacity_kg=45, min_stock=5)
        gun = masterdata.create_product(session, sku="SUNGMOI", name="súng mồi", kind="ACCESSORY", unit="cái",
                                        default_price_basis="UNIT", sale_price=180000)
        cus = masterdata.create_partner(session, code="hyu", name="CT04 Hyundai - Mỹ Đình", tax_code=None)
        sup = masterdata.create_partner(session, code="PLX", name="Gas Petrolimex", partner_type=PartnerType.SUPPLIER,
                                        tax_code="0100100100")
        site = masterdata.create_site(session, partner_id=cus.id, name="CT04 Mỹ Đình", aliases=["CT04", "CT4"])
        masterdata.set_price(session, product_id=pc45.id, partner_id=cus.id, price=49500, valid_from=date(2026, 1, 1))
    return {"pc45": pc45, "gun": gun, "cus": cus, "sup": sup, "site": site}


def test_codes_are_normalised(session, base):
    assert base["pc45"].sku == "PC45"
    assert base["cus"].code == "HYU"
    assert masterdata.get_company(session).tax_code == "0107776927"   # bù số 0 đầu bị Excel làm mất
    assert masterdata.find_site(session, "ct04").id == base["site"].id
    assert masterdata.find_site(session, "CT 04 Hyundai").id == base["site"].id


def test_weighted_average_cost(session, base):
    purchases.create_purchase(session, purchases.PurchaseInput(
        supplier_id=base["sup"].id, doc_date=date(2026, 10, 1), invoice_symbol="C26TAA", invoice_no="100",
        items=[purchases.PurchaseItemInput(sku="PC45", qty=10, unit_price=1500000)]))
    purchases.create_purchase(session, purchases.PurchaseInput(
        supplier_id=base["sup"].id, doc_date=date(2026, 10, 2), invoice_symbol="C26TAA", invoice_no="101",
        items=[purchases.PurchaseItemInput(sku="PC45", qty=30, unit_price=1600000)]))
    p = masterdata.get_product(session, base["pc45"].id)
    assert p.current_stock == 40
    assert p.cost_price == Decimal("1575000")          # (10*1.5tr + 30*1.6tr) / 40
    with pytest.raises(DuplicateError):
        purchases.create_purchase(session, purchases.PurchaseInput(
            supplier_id=base["sup"].id, doc_date=date(2026, 10, 3), invoice_symbol="C26TAA", invoice_no="100",
            items=[purchases.PurchaseItemInput(sku="PC45", qty=1, unit_price=1)]))


def test_slip_amounts_stock_and_cost(session, base):
    inventory_seed = purchases.create_purchase(session, purchases.PurchaseInput(
        supplier_id=base["sup"].id, doc_date=date(2026, 10, 1),
        items=[purchases.PurchaseItemInput(sku="PC45", qty=20, unit_price=1500000)]))
    assert inventory_seed[0].total == Decimal("32400000")   # 30tr + VAT 8%
    res = sales.create_slip(session, sales.SlipInput(
        partner_id=base["cus"].id, site_id=base["site"].id, slip_date=date(2026, 10, 26), slip_no="0022230",
        items=[sales.ItemInput(sku="PC45", qty=2), sales.ItemInput(sku="SUNGMOI", qty=1)],
        gas_residual_kg=10, note="test"))
    slip = res.slip
    # Hyundai 26/10: (2*45 - 10) * 49.500 = 3.960.000 ; + 1 súng mồi 180.000
    assert slip.total == Decimal("4140000")
    assert slip.residual_amount == Decimal("495000")
    assert slip.cost_total == Decimal("3000000")
    p = masterdata.get_product(session, base["pc45"].id)
    assert p.current_stock == 18
    gun = masterdata.get_product(session, base["gun"].id)
    assert gun.current_stock == -1 and any("SUNGMOI" in w for w in res.warnings)  # cho phép âm + cảnh báo
    with pytest.raises(DuplicateError):
        sales.create_slip(session, sales.SlipInput(partner_id=base["cus"].id, slip_date=date(2026, 10, 27),
                                                   slip_no="0022230", items=[sales.ItemInput(sku="PC45", qty=1)]))
    # sửa phiếu: kho được đảo và ghi lại
    sales.update_slip(session, slip.id, sales.SlipInput(
        partner_id=base["cus"].id, site_id=base["site"].id, slip_date=date(2026, 10, 26), slip_no="0022230",
        items=[sales.ItemInput(sku="PC45", qty=3)], gas_residual_kg=10))
    assert masterdata.get_product(session, base["pc45"].id).current_stock == 17
    assert masterdata.get_product(session, base["gun"].id).current_stock == 0
    # hủy phiếu: trả lại tồn
    sales.cancel_slip(session, slip.id, "nhập nhầm")
    assert masterdata.get_product(session, base["pc45"].id).current_stock == 20
    assert sales.get_slip(session, slip.id).status == DocStatus.CANCELLED


def test_per_unit_price_and_deduction(session, base):
    # Phiếu 0011117: 4 bình PC45 x 1.100.000, gas dư 25,6 kg trừ theo giá/kg = 1.100.000/45, trừ tiền đầu tư 1.100.000
    res = sales.create_slip(session, sales.SlipInput(
        partner_id=base["cus"].id, slip_date=date(2026, 3, 3),
        items=[sales.ItemInput(sku="PC45", qty=4, unit_price=1100000, price_basis="UNIT")],
        gas_residual_kg="25.6", other_deduction=1100000, deduction_note="Trừ tiền đầu tư còn"))
    assert res.slip.subtotal == Decimal("4400000")
    assert res.slip.residual_amount == Decimal("625778")
    assert res.slip.total == Decimal("2674222")


def test_rollback_when_any_step_fails(session, base):
    before_slips = session.scalar(select(func.count(DeliverySlip.id)))
    before_txn = session.scalar(select(func.count(InventoryTransaction.id)))
    with pytest.raises(BusinessError):
        # dòng vỏ bình không chọn loại bình -> lỗi SAU khi đã ghi thẻ kho -> phải rollback hết
        sales.create_slip(session, sales.SlipInput(
            partner_id=base["cus"].id, slip_date=date(2026, 10, 5), items=[sales.ItemInput(sku="PC45", qty=1)],
            assets=[sales.AssetInput(asset_type=AssetType.EMPTY_CYLINDER, direction=AssetDirection.LEND, qty=1)]))
    assert session.scalar(select(func.count(DeliverySlip.id))) == before_slips
    assert session.scalar(select(func.count(InventoryTransaction.id))) == before_txn
    assert masterdata.get_product(session, base["pc45"].id).current_stock == 0


def test_block_negative_stock_when_configured(session, base, monkeypatch):
    monkeypatch.setattr(inventory, "settings", replace(settings, allow_negative_stock=False))
    with pytest.raises(StockError):
        sales.create_slip(session, sales.SlipInput(partner_id=base["cus"].id, slip_date=date(2026, 10, 5),
                                                   items=[sales.ItemInput(sku="PC45", qty=1)]))


def test_draft_then_approve(session, base):
    res = sales.create_slip(session, sales.SlipInput(
        partner_id=base["cus"].id, site_id=base["site"].id, slip_date=date(2026, 10, 8), status=DocStatus.DRAFT,
        source_type="AI_OCR", ocr_json={"confidence": 0.82}, items=[sales.ItemInput(sku="PC45", qty=2)],
        gas_residual_kg=2,
        assets=[sales.AssetInput(asset_type=AssetType.EMPTY_CYLINDER, direction=AssetDirection.LEND, qty=1,
                                 sku="PC45")]))
    assert masterdata.get_product(session, base["pc45"].id).current_stock == 0   # nháp chưa trừ kho
    sales.approve_slip(session, res.slip.id)
    assert masterdata.get_product(session, base["pc45"].id).current_stock == -2
    from app.services import assets

    loans = assets.loan_balances(session, base["cus"].id, as_of=date(2026, 10, 31))
    assert assets.loan_text(loans[base["site"].id]) == "Mượn 01 vỏ PC45 - ko cược"


def test_stocktake_and_nxt(session, base):
    with transaction(session):
        inventory.set_opening_stock(session, product_id=base["pc45"].id, qty=50, unit_cost=1500000,
                                    on_date=date(2026, 9, 30))
    sales.create_slip(session, sales.SlipInput(partner_id=base["cus"].id, slip_date=date(2026, 10, 3),
                                               items=[sales.ItemInput(sku="PC45", qty=12)]))
    with transaction(session):
        st = inventory.create_stocktake(session, on_date=date(2026, 10, 31), counted={base["pc45"].id: 36})
    assert st.lines[0].diff_qty == Decimal("-2")
    rpt = {r["sku"]: r for r in inventory.nxt_report(session, date(2026, 10, 1), date(2026, 10, 31))}
    assert rpt["PC45"]["opening_qty"] == 50
    assert rpt["PC45"]["out_qty"] == 14
    assert rpt["PC45"]["closing_qty"] == 36
    assert rpt["PC45"]["closing_value"] == Decimal("54000000")
    card = inventory.stock_card(session, base["pc45"].id, date(2026, 10, 1), date(2026, 10, 31))
    assert [line.balance for line in card.lines] == [38, 36]


def test_audit_log_written(session, base):
    sales.create_slip(session, sales.SlipInput(partner_id=base["cus"].id, slip_date=date(2026, 10, 3),
                                               items=[sales.ItemInput(sku="PC45", qty=1)]))
    actions = session.execute(select(AuditLog.table_name, AuditLog.action)).all()
    assert ("delivery_slips", "INSERT") in actions
    assert ("products", "UPDATE") in actions
